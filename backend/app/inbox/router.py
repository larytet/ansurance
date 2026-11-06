import base64
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, func, or_, select, tuple_
from sqlalchemy.orm import Session as Db

from app.audit import chain
from app.capabilities import ROLES
from app.deps import Principal, get_db, require
from app.models import InboxEvent, InboxItem, User

router = APIRouter(prefix="/api")

URGENCIES = ("urgent", "important", "normal")
TYPES = {
    "renewal_due",
    "client_reply",
    "auto_filed",
    "unsigned_application",
    "callback",
    "claim_update",
    "nudge",
    "review",
    "unassigned",
    "id_missing",
    "register_client",
    "fill_template",
    "print_label",
    "send_post",
    "insurer_request",
    "tax_certificate",
    "application_submit",
    "endorsement",
    "claim_gap",
    "commission_mismatch",
    "send_failure",
    "opt_out",
    "bounce",
    "campaign_draft",
    "campaign_result",
    "approval",
    "complaint_sla",
    "compliance_expiry",
    "tax_deadline",
    "misfile_report",
    "consent_late",
    "other",
}
# Back-office work areas (the Desk). Types not listed belong to no area.
AREAS = {
    "id_missing": "id_registration",
    "register_client": "id_registration",
    "fill_template": "templates",
    "print_label": "post_email",
    "send_post": "post_email",
    "insurer_request": "insurer_requests",
    "tax_certificate": "tax",
    "tax_deadline": "tax",
    "review": "intake",
    "unassigned": "intake",
}
AREA_NAMES = ["id_registration", "templates", "post_email", "insurer_requests", "tax", "intake"]

EFFECTIVE = case(
    (
        or_(
            InboxItem.urgency == "urgent",
            and_(InboxItem.urgent_from.is_not(None), InboxItem.urgent_from <= func.now()),
        ),
        "urgent",
    ),
    else_=InboxItem.urgency,
)
RANK = case((EFFECTIVE == "urgent", 0), (EFFECTIVE == "important", 1), else_=2)


class ItemIn(BaseModel):
    type: str
    role: str
    title: str = Field(min_length=2, max_length=200)
    detail: str = ""
    urgency: str = "normal"
    urgent_from: datetime | None = None
    client_id: uuid.UUID | None = None
    assignee_id: uuid.UUID | None = None


class ReassignIn(BaseModel):
    role: str
    note: str = Field(min_length=3)
    assignee_id: uuid.UUID | None = None


class DoneIn(BaseModel):
    note: str = ""


class DismissIn(BaseModel):
    reason: str = Field(min_length=3)


def item_dict(i: InboxItem, effective: str) -> dict[str, Any]:
    return {
        "id": str(i.id),
        "type": i.type,
        "role": i.role,
        "title": i.title,
        "detail": i.detail,
        "urgency": effective,
        "status": i.status,
        "client_id": str(i.client_id) if i.client_id else None,
        "assignee_id": str(i.assignee_id) if i.assignee_id else None,
        "created_at": i.created_at.isoformat(),
        "area": AREAS.get(i.type),
    }


def _mine(p: Principal) -> Any:
    return and_(
        InboxItem.role.in_(p.roles),
        or_(InboxItem.assignee_id.is_(None), InboxItem.assignee_id == p.user.id),
    )


def _scope(p: Principal, role: str | None) -> tuple[Any, bool]:
    """(filter, read_only). A manager may look at any role's Inbox, read-only."""
    if role is not None and role not in p.roles:
        if "inbox.view_all" not in p.capabilities:
            raise HTTPException(403, "forbidden")
        return InboxItem.role == role, True
    return _mine(p), False


def _encode(rank: int, created: datetime, item_id: uuid.UUID) -> str:
    raw = json.dumps([rank, created.isoformat(), str(item_id)])
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode(cursor: str) -> tuple[int, datetime, uuid.UUID]:
    try:
        r, c, i = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return int(r), datetime.fromisoformat(c), uuid.UUID(i)
    except (ValueError, TypeError) as e:
        raise HTTPException(422, "bad_cursor") from e


@router.post("/inbox", status_code=201)
def create_item(
    body: ItemIn, p: Principal = Depends(require("inbox.create")), db: Db = Depends(get_db)
) -> dict[str, Any]:
    if body.role not in ROLES or body.type not in TYPES or body.urgency not in URGENCIES:
        raise HTTPException(422, "invalid_role_type_or_urgency")
    item = InboxItem(
        type=body.type,
        role=body.role,
        title=body.title,
        detail=body.detail,
        urgency=body.urgency,
        urgent_from=body.urgent_from,
        client_id=body.client_id,
        assignee_id=body.assignee_id,
        created_by=p.user.id,
    )
    db.add(item)
    db.flush()
    db.add(InboxEvent(item_id=item.id, actor=p.user.id, action="created", to_role=item.role))
    chain.append(db, str(p.user.id), "inbox.create", f"inbox:{item.id}", {"role": item.role})
    return item_dict(item, item.urgency)


@router.get("/inbox")
def list_items(
    status: str = "open",
    type: str | None = None,
    urgency: str | None = None,
    role: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    p: Principal = Depends(require("inbox.read")),
    db: Db = Depends(get_db),
) -> dict[str, Any]:
    scope, read_only = _scope(p, role)
    stmt = select(InboxItem, EFFECTIVE.label("eff"), RANK.label("rank")).where(
        scope, InboxItem.status == status
    )
    if type:
        stmt = stmt.where(InboxItem.type == type)
    if urgency:
        stmt = stmt.where(EFFECTIVE == urgency)
    if cursor:
        r, c, i = _decode(cursor)
        stmt = stmt.where(tuple_(RANK, InboxItem.created_at, InboxItem.id) > tuple_(r, c, i))
    rows = db.execute(
        stmt.order_by(RANK, InboxItem.created_at, InboxItem.id).limit(limit + 1)
    ).all()
    page = rows[:limit]
    nxt = None
    if len(rows) > limit:
        last = page[-1]
        nxt = _encode(last.rank, last.InboxItem.created_at, last.InboxItem.id)
    return {
        "items": [item_dict(r.InboxItem, r.eff) for r in page],
        "next": nxt,
        "read_only": read_only,
    }


@router.get("/inbox/counts")
def counts(
    role: str | None = None,
    p: Principal = Depends(require("inbox.read")),
    db: Db = Depends(get_db),
) -> dict[str, Any]:
    scope, read_only = _scope(p, role)
    open_n, urgent_n = db.execute(
        select(func.count(), func.count().filter(EFFECTIVE == "urgent"))
        .select_from(InboxItem)
        .where(scope, InboxItem.status == "open")
    ).one()
    today = datetime.combine(datetime.now(UTC).date(), datetime.min.time(), tzinfo=UTC)
    done_today = db.scalar(
        select(func.count())
        .select_from(InboxItem)
        .where(scope, InboxItem.status == "done", InboxItem.resolved_at >= today)
    )
    return {
        "open": open_n,
        "urgent": urgent_n,
        "done_today": done_today or 0,
        "read_only": read_only,
    }


def _open_item(db: Db, p: Principal, item_id: uuid.UUID) -> InboxItem:
    item = db.scalar(select(InboxItem).where(InboxItem.id == item_id, _mine(p)))
    if item is None and "inbox.view_all" in p.capabilities:
        item = db.get(InboxItem, item_id)  # the manager covers absences
    if item is None:
        raise HTTPException(404, "not_found")
    if item.status != "open":
        raise HTTPException(409, "not_open")
    return item


@router.post("/inbox/{item_id}/reassign")
def reassign(
    item_id: uuid.UUID,
    body: ReassignIn,
    p: Principal = Depends(require("inbox.read")),
    db: Db = Depends(get_db),
) -> dict[str, Any]:
    item = _open_item(db, p, item_id)
    if body.role not in ROLES:
        raise HTTPException(422, "invalid_role")
    if body.assignee_id is not None:
        target = db.get(User, body.assignee_id)
        if target is None or not target.active:
            raise HTTPException(422, "invalid_assignee")
    old = item.role
    item.role, item.assignee_id = body.role, body.assignee_id
    db.add(
        InboxEvent(
            item_id=item.id,
            actor=p.user.id,
            action="reassigned",
            from_role=old,
            to_role=body.role,
            note=body.note,
        )
    )
    chain.append(
        db, str(p.user.id), "inbox.reassign", f"inbox:{item.id}", {"from": old, "to": body.role}
    )
    return item_dict(item, item.urgency)


@router.post("/inbox/{item_id}/done")
def done(
    item_id: uuid.UUID,
    body: DoneIn,
    p: Principal = Depends(require("inbox.read")),
    db: Db = Depends(get_db),
) -> dict[str, Any]:
    item = _open_item(db, p, item_id)
    item.status, item.resolved_at, item.resolved_by = "done", datetime.now(UTC), p.user.id
    item.resolution = body.note
    db.add(InboxEvent(item_id=item.id, actor=p.user.id, action="done", note=body.note))
    chain.append(db, str(p.user.id), "inbox.done", f"inbox:{item.id}", {})
    return item_dict(item, item.urgency)


@router.post("/inbox/{item_id}/dismiss")
def dismiss(
    item_id: uuid.UUID,
    body: DismissIn,
    p: Principal = Depends(require("inbox.read")),
    db: Db = Depends(get_db),
) -> dict[str, Any]:
    item = _open_item(db, p, item_id)
    item.status, item.resolved_at, item.resolved_by = "dismissed", datetime.now(UTC), p.user.id
    item.resolution = body.reason
    db.add(InboxEvent(item_id=item.id, actor=p.user.id, action="dismissed", note=body.reason))
    chain.append(db, str(p.user.id), "inbox.dismiss", f"inbox:{item.id}", {})
    return item_dict(item, item.urgency)


@router.get("/inbox/{item_id}/events")
def events(
    item_id: uuid.UUID, p: Principal = Depends(require("inbox.read")), db: Db = Depends(get_db)
) -> list[dict[str, Any]]:
    item = db.get(InboxItem, item_id)
    visible = item is not None and (item.role in p.roles or "inbox.view_all" in p.capabilities)
    if not visible:
        raise HTTPException(404, "not_found")
    rows = db.scalars(
        select(InboxEvent)
        .where(InboxEvent.item_id == item_id)
        .order_by(InboxEvent.at, InboxEvent.id)
    )
    return [
        {
            "at": e.at.isoformat(),
            "action": e.action,
            "from_role": e.from_role,
            "to_role": e.to_role,
            "note": e.note,
            "actor": str(e.actor) if e.actor else None,
        }
        for e in rows
    ]


@router.get("/desk")
def desk(p: Principal = Depends(require("desk.read")), db: Db = Depends(get_db)) -> dict[str, Any]:
    """Back-office work areas with counts, the first thing the back-office sees."""
    rows = db.execute(
        select(InboxItem.type, func.count(), func.count().filter(EFFECTIVE == "urgent"))
        .where(InboxItem.role == "backoffice", InboxItem.status == "open")
        .group_by(InboxItem.type)
    ).all()
    open_by: dict[str, int] = dict.fromkeys(AREA_NAMES, 0)
    urgent_by: dict[str, int] = dict.fromkeys(AREA_NAMES, 0)
    for typ, n, urgent in rows:
        area = AREAS.get(typ)
        if area:
            open_by[area] += n
            urgent_by[area] += urgent
    return {
        "areas": [{"area": a, "open": open_by[a], "urgent": urgent_by[a]} for a in AREA_NAMES],
        "total_open": sum(r[1] for r in rows),
        "total_urgent": sum(r[2] for r in rows),
    }
