import io
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from openpyxl import load_workbook
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as Db

from app.audit import chain
from app.clients.access import get_visible
from app.config import Settings
from app.deps import Principal, get_db, get_settings_dep, require
from app.manager import service
from app.models import AgentLicence, CommissionStatement, Complaint, User

router = APIRouter(prefix="/api")


def _fail(e: service.ManagerError) -> HTTPException:
    return HTTPException(e.status, {"error": e.code, "detail": e.detail} if e.detail else e.code)


# ---- F8 ------------------------------------------------------------------------------------------
class ComplaintIn(BaseModel):
    client_id: uuid.UUID | None = None
    policy_id: uuid.UUID | None = None
    kind: str = "complaint"
    channel: str = "other"
    text: str = Field(min_length=3, max_length=4000)


@router.post("/complaints", status_code=201)
def open_complaint(
    body: ComplaintIn,
    p: Principal = Depends(require("complaint.write")),
    db: Db = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings_dep),
) -> dict[str, Any]:
    if body.client_id:
        get_visible(db, p, body.client_id)
    try:
        c = service.open_complaint(db, settings, p.user.id, **body.model_dump())
    except service.ManagerError as e:
        raise _fail(e) from e
    return service.complaint_dict(c, datetime.now(UTC))


def _visible(p: Principal, c: Complaint) -> bool:
    return "manager" in p.roles or p.user.id in (c.opened_by, c.owner_id)


@router.get("/complaints")
def list_complaints(
    p: Principal = Depends(require("complaint.write")), db: Db = Depends(get_db, scope="function")
) -> list[dict[str, Any]]:
    now = datetime.now(UTC)
    rows = [c for c in db.scalars(select(Complaint).order_by(Complaint.due_at)) if _visible(p, c)]
    return [service.complaint_dict(c, now) for c in rows]


@router.get("/complaints/export.csv")
def export_complaints(
    p: Principal = Depends(require("flow.approve")), db: Db = Depends(get_db, scope="function")
) -> Response:
    chain.append(
        db, str(p.user.id), "export.complaints", "complaint", {}
    )  # exports are audited at once
    return Response(service.complaints_csv(db, datetime.now(UTC)), media_type="text/csv")


class OwnerIn(BaseModel):
    owner_id: uuid.UUID


@router.put("/complaints/{cid}/owner")
def assign(
    cid: uuid.UUID,
    body: OwnerIn,
    p: Principal = Depends(require("flow.approve")),
    db: Db = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    c = db.get(Complaint, cid)
    if c is None or db.get(User, body.owner_id) is None:
        raise HTTPException(404, "not_found")
    c.owner_id = body.owner_id
    chain.append(
        db, str(p.user.id), "complaint.assign", f"complaint:{c.id}", {"owner": str(body.owner_id)}
    )
    return service.complaint_dict(c, datetime.now(UTC))


class RespondIn(BaseModel):
    response: str = Field(min_length=3, max_length=4000)


@router.post("/complaints/{cid}/respond")
def respond(
    cid: uuid.UUID,
    body: RespondIn,
    p: Principal = Depends(require("complaint.write")),
    db: Db = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    c = db.get(Complaint, cid)
    if c is None or not _visible(p, c):
        raise HTTPException(404, "not_found")
    try:
        service.respond(db, p.user.id, c, body.response)
    except service.ManagerError as e:
        raise _fail(e) from e
    return service.complaint_dict(c, datetime.now(UTC))


class LicenceIn(BaseModel):
    licence_no: str = Field(min_length=1, max_length=32)
    expires_on: date
    cpd_required_hours: int = Field(ge=0, default=0)
    cpd_done_hours: int = Field(ge=0, default=0)


@router.put("/users/{user_id}/licence")
def set_licence(
    user_id: uuid.UUID,
    body: LicenceIn,
    p: Principal = Depends(require("flow.approve")),
    db: Db = Depends(get_db, scope="function"),
) -> dict[str, str]:
    if db.get(User, user_id) is None:
        raise HTTPException(404, "not_found")
    row = db.get(AgentLicence, user_id)
    if row is None:
        db.add(AgentLicence(user_id=user_id, **body.model_dump()))
    else:
        for k, v in body.model_dump().items():
            setattr(row, k, v)
    chain.append(db, str(p.user.id), "licence.set", f"user:{user_id}", {})
    return {"status": "saved"}


@router.get("/compliance")
def compliance(
    p: Principal = Depends(require("flow.approve")),
    db: Db = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings_dep),
) -> dict[str, Any]:
    return service.compliance(db, settings)


@router.get("/overview")
def overview(
    p: Principal = Depends(require("flow.approve")),
    db: Db = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings_dep),
) -> dict[str, Any]:
    return service.overview(db, settings)


# ---- F7 ------------------------------------------------------------------------------------------
class TermIn(BaseModel):
    insurer: str = Field(min_length=2, max_length=120)
    product: str = Field(min_length=2, max_length=32)
    rate_pct: Decimal = Field(ge=0, le=100)


@router.put("/commissions/terms")
def set_term(
    body: TermIn,
    p: Principal = Depends(require("commission.manage")),
    db: Db = Depends(get_db, scope="function"),
) -> dict[str, str]:
    service.set_term(db, p.user.id, body.insurer, body.product, body.rate_pct)
    return {"status": "saved"}


class LineIn(BaseModel):
    policy_number: str
    amount: Decimal


class StatementIn(BaseModel):
    insurer: str = Field(min_length=2, max_length=120)
    period: str
    declared_total: Decimal
    lines: list[LineIn]


@router.post("/commissions/statements", status_code=201)
def import_json(
    body: StatementIn,
    p: Principal = Depends(require("commission.manage")),
    db: Db = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings_dep),
) -> dict[str, Any]:
    try:
        st = service.import_statement(
            db,
            settings,
            p.user.id,
            insurer=body.insurer,
            period=body.period,
            declared_total=body.declared_total,
            lines=[(x.policy_number, x.amount) for x in body.lines],
        )
    except service.ManagerError as e:
        raise _fail(e) from e
    return service.statement_view(db, st)


POLICY_HEADERS = {"policy", "policy_number", "policy number", "פוליסה", "מספר פוליסה"}
AMOUNT_HEADERS = {"amount", "commission", "עמלה", "סכום"}


@router.post("/commissions/statements/xlsx", status_code=201)
def import_xlsx(
    insurer: str = Form(...),
    period: str = Form(...),
    declared_total: Decimal = Form(...),
    file: UploadFile = File(...),
    p: Principal = Depends(require("commission.manage")),
    db: Db = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings_dep),
) -> dict[str, Any]:
    try:
        wb = load_workbook(io.BytesIO(file.file.read()), read_only=True, data_only=True)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, "not_a_readable_xlsx") from e
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True)) if ws is not None else []
    if not rows:
        raise HTTPException(422, "empty_sheet")
    head = [str(h or "").strip().lower() for h in rows[0]]
    pi = next((i for i, h in enumerate(head) if h in POLICY_HEADERS), None)
    ai = next((i for i, h in enumerate(head) if h in AMOUNT_HEADERS), None)
    if pi is None or ai is None:
        raise HTTPException(422, "policy_or_amount_column_not_found")
    lines = [(str(r[pi]), r[ai]) for r in rows[1:] if r[pi] not in (None, "") and r[ai] is not None]
    try:
        st = service.import_statement(
            db,
            settings,
            p.user.id,
            insurer=insurer,
            period=period,
            declared_total=declared_total,
            lines=lines,
        )
    except service.ManagerError as e:
        raise _fail(e) from e
    return service.statement_view(db, st)


def _stmt(db: Db, sid: uuid.UUID) -> CommissionStatement:
    st = db.get(CommissionStatement, sid)
    if st is None:
        raise HTTPException(404, "not_found")
    return st


@router.get("/commissions/statements/{sid}")
def get_statement(
    sid: uuid.UUID,
    p: Principal = Depends(require("commission.manage")),
    db: Db = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    return service.statement_view(db, _stmt(db, sid))


@router.post("/commissions/statements/{sid}/approve")
def approve_statement(
    sid: uuid.UUID,
    p: Principal = Depends(require("flow.approve")),
    db: Db = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    st = _stmt(db, sid)
    try:
        service.approve_statement(db, p.user.id, st)
    except service.ManagerError as e:
        raise _fail(e) from e
    return service.statement_view(db, st)


@router.get("/commissions/statements/{sid}/payout")
def get_payout(
    sid: uuid.UUID,
    p: Principal = Depends(require("flow.approve")),
    db: Db = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    try:
        return service.payout(db, _stmt(db, sid))
    except service.ManagerError as e:
        raise _fail(e) from e
