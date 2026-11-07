import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session as Db

from app.config import Settings
from app.deps import Principal, get_db, get_settings_dep, require
from app.documents.service import RejectedUpload
from app.intake import pipeline
from app.intake.context import IntakeContext
from app.models import Client, IntakeFeedback, IntakeItem

router = APIRouter(prefix="/api")


class ConfirmIn(BaseModel):
    client_id: uuid.UUID


class WrongClientIn(BaseModel):
    client_id: uuid.UUID
    reason: str = Field(min_length=3)


def _ctx(request: Request) -> IntakeContext:
    ctx: IntakeContext = request.app.state.intake
    return ctx


def _item(db: Db, item_id: uuid.UUID) -> IntakeItem:
    item = db.get(IntakeItem, item_id)
    if item is None:
        raise HTTPException(404, "not_found")
    return item


@router.post("/intake", status_code=201)
def submit(
    request: Request,
    file: UploadFile = File(...),
    channel: str = Form("upload"),
    sender: str = Form(""),
    p: Principal = Depends(require("intake.write")),
    db: Db = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings_dep),
) -> dict[str, Any]:
    data = file.file.read(settings.max_upload_bytes + 1)
    try:
        items = pipeline.submit(
            db,
            _ctx(request),
            data=data,
            filename=file.filename or "file",
            channel=channel,
            sender=sender,
            actor=p.user.id,
        )
    except RejectedUpload as e:
        raise HTTPException(e.status, e.code) from e
    return {"items": [pipeline.result_summary(i) for i in items]}


@router.get("/intake")
def list_items(
    state: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    p: Principal = Depends(require("intake.read")),
    db: Db = Depends(get_db, scope="function"),
) -> list[dict[str, Any]]:
    stmt = select(IntakeItem)
    if state:
        stmt = stmt.where(IntakeItem.state == state)
    rows = db.scalars(stmt.order_by(IntakeItem.received_at.desc()).limit(limit))
    return [pipeline.result_summary(i) for i in rows]


@router.get("/intake/metrics")
def metrics(
    days: int = Query(30, ge=1, le=365),
    p: Principal = Depends(require("intake.read")),
    db: Db = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(days=days)
    by_state = dict(
        db.execute(
            select(IntakeItem.state, func.count())
            .where(IntakeItem.received_at >= since)
            .group_by(IntakeItem.state)
        ).all()
    )
    auto = (
        db.scalar(
            select(func.count())
            .select_from(IntakeItem)
            .where(
                IntakeItem.received_at >= since,
                IntakeItem.state == "filed",
                IntakeItem.route == "filed",
            )
        )
        or 0
    )
    misfiled = (
        db.scalar(
            select(func.count())
            .select_from(IntakeFeedback)
            .where(IntakeFeedback.at >= since, IntakeFeedback.kind == "wrong_client")
        )
        or 0
    )
    done = sum(v for k, v in by_state.items() if k in ("filed", "review", "unassigned"))
    return {
        "days": days,
        "by_state": by_state,
        "auto_filed": auto,
        "misfiled": misfiled,
        "auto_file_rate": round(auto / done, 4) if done else None,
        "misfile_rate": round(misfiled / auto, 4) if auto else None,
    }


@router.get("/intake/{item_id}")
def get_item(
    item_id: uuid.UUID,
    p: Principal = Depends(require("intake.read")),
    db: Db = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    item = _item(db, item_id)
    return {**pipeline.result_summary(item), "text": item.ocr_text, "entities": item.entities}


@router.post("/intake/{item_id}/confirm")
def confirm(
    item_id: uuid.UUID,
    body: ConfirmIn,
    request: Request,
    p: Principal = Depends(require("intake.review")),
    db: Db = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    item = _item(db, item_id)
    client = db.get(Client, body.client_id)
    if client is None:
        raise HTTPException(404, "client_not_found")
    try:
        pipeline.confirm(db, _ctx(request), item, client, p.user.id)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return pipeline.result_summary(item)


@router.post("/intake/{item_id}/wrong-client")
def wrong_client(
    item_id: uuid.UUID,
    body: WrongClientIn,
    p: Principal = Depends(require("intake.review")),
    db: Db = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    item = _item(db, item_id)
    client = db.get(Client, body.client_id)
    if client is None:
        raise HTTPException(404, "client_not_found")
    try:
        pipeline.wrong_client(db, item, client, p.user.id, body.reason)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return pipeline.result_summary(item)


@router.get("/intake/{item_id}/feedback")
def feedback(
    item_id: uuid.UUID,
    p: Principal = Depends(require("intake.read")),
    db: Db = Depends(get_db, scope="function"),
) -> list[dict[str, Any]]:
    _item(db, item_id)
    rows = db.scalars(
        select(IntakeFeedback).where(IntakeFeedback.item_id == item_id).order_by(IntakeFeedback.at)
    )
    return [
        {
            "kind": f.kind,
            "from": str(f.from_client_id) if f.from_client_id else None,
            "to": str(f.to_client_id) if f.to_client_id else None,
            "note": f.note,
            "actor": str(f.actor),
            "at": f.at.isoformat(),
        }
        for f in rows
    ]
