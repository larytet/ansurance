import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as Db

from app.campaigns import service
from app.config import Settings
from app.deps import Principal, get_db, get_settings_dep, require
from app.models import Campaign, CampaignRecipient, Client

router = APIRouter(prefix="/api")


def _fail(e: service.CampaignError) -> HTTPException:
    return HTTPException(e.status, {"error": e.code, "detail": e.detail} if e.detail else e.code)


def _get(db: Db, p: Principal, cid: uuid.UUID) -> Campaign:
    c = db.get(Campaign, cid)
    if c is None:
        raise HTTPException(404, "not_found")
    # agents see their own campaigns; marketing and manager see all
    if "manager" not in p.roles and "marketing" not in p.roles and c.created_by != p.user.id:
        raise HTTPException(404, "not_found")
    return c


def _dict(c: Campaign) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "name": c.name,
        "type": c.type,
        "purpose": c.purpose,
        "status": c.status,
        "benefit": c.benefit,
        "segment": c.segment,
        "base_language": c.base_language,
        "body": c.body,
        "requires_approval": c.requires_approval,
        "approved": c.approved_at is not None,
    }


class CampaignIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    type: str
    benefit: str
    segment: dict[str, Any] = {}
    base_language: str = "he"
    body: str = Field(min_length=3, max_length=3000)


@router.post("/campaigns", status_code=201)
def create(
    body: CampaignIn, p: Principal = Depends(require("campaign.write")), db: Db = Depends(get_db)
) -> dict[str, Any]:
    try:
        c = service.create(db, p.user.id, p.roles, **body.model_dump())
    except service.CampaignError as e:
        raise _fail(e) from e
    return _dict(c)


@router.get("/campaigns")
def list_campaigns(
    p: Principal = Depends(require("campaign.write")), db: Db = Depends(get_db)
) -> list[dict[str, Any]]:
    q = select(Campaign).order_by(Campaign.created_at.desc())
    if "manager" not in p.roles and "marketing" not in p.roles:
        q = q.where(Campaign.created_by == p.user.id)
    return [_dict(c) for c in db.scalars(q)]


@router.get("/campaigns/{cid}")
def get(
    cid: uuid.UUID, p: Principal = Depends(require("campaign.write")), db: Db = Depends(get_db)
) -> dict[str, Any]:
    return _dict(_get(db, p, cid))


@router.get("/campaigns/{cid}/preview")
def preview(
    cid: uuid.UUID,
    p: Principal = Depends(require("campaign.write")),
    db: Db = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> dict[str, Any]:
    return service.preview(db, settings, _get(db, p, cid))


class TranslationIn(BaseModel):
    body: str = Field(min_length=3, max_length=3000)


@router.put("/campaigns/{cid}/translations/{language}")
def translate(
    cid: uuid.UUID,
    language: str,
    body: TranslationIn,
    p: Principal = Depends(require("campaign.write")),
    db: Db = Depends(get_db),
) -> dict[str, str]:
    try:
        service.save_translation(db, p.user.id, _get(db, p, cid), language, body.body)
    except service.CampaignError as e:
        raise _fail(e) from e
    return {"status": "reviewed"}


@router.post("/campaigns/{cid}/approve")
def approve(
    cid: uuid.UUID, p: Principal = Depends(require("flow.approve")), db: Db = Depends(get_db)
) -> dict[str, Any]:
    c = _get(db, p, cid)
    try:
        service.approve(db, p.user.id, c)
    except service.CampaignError as e:
        raise _fail(e) from e
    return _dict(c)


@router.post("/campaigns/{cid}/start")
def start(
    cid: uuid.UUID,
    p: Principal = Depends(require("campaign.write")),
    db: Db = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> dict[str, Any]:
    try:
        return service.start(db, settings, p.user.id, _get(db, p, cid))
    except service.CampaignError as e:
        raise _fail(e) from e


@router.get("/campaigns/{cid}/results")
def results(
    cid: uuid.UUID, p: Principal = Depends(require("campaign.write")), db: Db = Depends(get_db)
) -> dict[str, Any]:
    return service.results(db, _get(db, p, cid))


@router.get("/campaigns/{cid}/audience")
def snapshot(
    cid: uuid.UUID, p: Principal = Depends(require("campaign.write")), db: Db = Depends(get_db)
) -> list[dict[str, Any]]:
    """Who was selected or excluded and why: the evidence kept for every campaign."""
    c = _get(db, p, cid)
    rows = db.execute(
        select(CampaignRecipient, Client.name)
        .join(Client, Client.id == CampaignRecipient.client_id)
        .where(CampaignRecipient.campaign_id == c.id)
        .order_by(Client.name)
    )
    return [
        {
            "client_id": str(r.client_id),
            "name": n,
            "status": r.status,
            "channel": r.channel,
            "language": r.language,
            "reason": r.reason,
        }
        for r, n in rows
    ]
