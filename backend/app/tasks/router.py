import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as Db

from app.audit import chain
from app.clients.access import get_visible
from app.deps import Principal, get_db, require
from app.models import Client, Meeting, Task, User
from app.tasks import service

router = APIRouter(prefix="/api")


class TaskIn(BaseModel):
    text: str = Field(min_length=1, max_length=200)
    due: date
    client_id: uuid.UUID | None = None


class TaskPatch(BaseModel):
    done: bool | None = None
    owner_id: uuid.UUID | None = None


class MeetingIn(BaseModel):
    kind: str
    held_on: date
    subject: str = Field(min_length=1, max_length=200)
    summary: str | None = None


class SummaryIn(BaseModel):
    summary: str


def _task_dict(
    t: Task, today: date, who: dict[uuid.UUID, str], client: str | None
) -> dict[str, Any]:
    return {
        "id": str(t.id),
        "text": t.text,
        "due": t.due_on.isoformat(),
        "done": t.done_at is not None,
        "overdue": t.done_at is None and t.due_on < today,
        "client_id": str(t.client_id) if t.client_id else None,
        "client": client,
        "owner_id": str(t.owner_id),
        "owner": who.get(t.owner_id, ""),
        "delegated_by": str(t.delegated_by) if t.delegated_by else None,
    }


def _render(db: Db, rows: list[Task]) -> list[dict[str, Any]]:
    today = service.today_utc()
    who = service.names(db, {t.owner_id for t in rows})
    cids = {t.client_id for t in rows if t.client_id}
    clients = (
        {c.id: c.name for c in db.scalars(select(Client).where(Client.id.in_(cids)))}
        if cids
        else {}
    )
    return [
        _task_dict(t, today, who, clients.get(t.client_id) if t.client_id else None) for t in rows
    ]


@router.get("/tasks")
def list_tasks(
    view: str = "today",
    owner: uuid.UUID | None = None,
    p: Principal = Depends(require("task.write")),
    db: Db = Depends(get_db, scope="function"),
) -> list[dict[str, Any]]:
    """Own tasks per chip. The manager may look at another person's with `owner`; nobody else."""
    if view not in service.VIEWS:
        raise HTTPException(422, "view")
    if owner is not None and owner != p.user.id and "inbox.view_all" not in p.capabilities:
        raise HTTPException(403, "forbidden")
    q = service.select_view(view, owner or p.user.id, service.today_utc())
    return _render(db, list(db.scalars(q.order_by(Task.due_on, Task.created_at))))


@router.get("/tasks/people")
def people(
    p: Principal = Depends(require("task.write")),
    db: Db = Depends(get_db, scope="function"),
) -> list[dict[str, str]]:
    """Who a task can be handed to: active staff, names only (no e-mail, role or secret)."""
    rows = db.scalars(select(User).where(User.active.is_(True)).order_by(User.display_name))
    return [{"id": str(u.id), "name": u.display_name} for u in rows]


@router.post("/tasks", status_code=201)
def create_task(
    body: TaskIn,
    p: Principal = Depends(require("task.write")),
    db: Db = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    client = get_visible(db, p, body.client_id) if body.client_id else None
    t = Task(
        text=body.text.strip(),
        due_on=body.due,
        client_id=client.id if client else None,
        owner_id=p.user.id,
        created_by=p.user.id,
    )
    db.add(t)
    db.flush()
    chain.append(
        db, str(p.user.id), "task.create", f"task:{t.id}", {"client": str(t.client_id or "-")}
    )
    return _render(db, [t])[0]


@router.patch("/tasks/{task_id}")
def update_task(
    task_id: uuid.UUID,
    body: TaskPatch,
    p: Principal = Depends(require("task.write")),
    db: Db = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    t = db.get(Task, task_id)
    mine = t is not None and p.user.id in (t.owner_id, t.delegated_by, t.created_by)
    if t is None or not (mine or "inbox.view_all" in p.capabilities):
        raise HTTPException(404, "not_found")  # not found and not allowed look the same
    if body.done is not None:
        from datetime import UTC, datetime

        t.done_at = datetime.now(UTC) if body.done else None
        chain.append(
            db, str(p.user.id), "task.done" if body.done else "task.reopen", f"task:{t.id}", {}
        )
    if body.owner_id is not None and body.owner_id != t.owner_id:
        target = db.get(User, body.owner_id)
        if target is None or not target.active:
            raise HTTPException(422, "unknown_user")
        t.delegated_by = p.user.id
        t.owner_id = target.id
        chain.append(db, str(p.user.id), "task.delegate", f"task:{t.id}", {"to": str(target.id)})
    db.flush()
    return _render(db, [t])[0]


def _meeting_dict(m: Meeting) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "kind": m.kind,
        "held_on": m.held_on.isoformat(),
        "subject": m.subject,
        "summary": m.summary,
    }


@router.get("/clients/{client_id}/meetings")
def list_meetings(
    client_id: uuid.UUID,
    p: Principal = Depends(require("task.write")),
    db: Db = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    c = get_visible(db, p, client_id)
    rows = db.scalars(
        select(Meeting).where(Meeting.client_id == c.id).order_by(Meeting.held_on.desc())
    )
    due = service.service_contact_due(db, c)
    return {
        "items": [_meeting_dict(m) for m in rows],
        "service_contact_due": due.isoformat() if due else None,
    }


@router.post("/clients/{client_id}/meetings", status_code=201)
def add_meeting(
    client_id: uuid.UUID,
    body: MeetingIn,
    p: Principal = Depends(require("task.write")),
    db: Db = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    c = get_visible(db, p, client_id)
    if body.kind not in service.MEETING_KINDS:
        raise HTTPException(422, "kind")
    m = Meeting(
        client_id=c.id,
        kind=body.kind,
        held_on=body.held_on,
        subject=body.subject.strip(),
        summary=body.summary,
        created_by=p.user.id,
    )
    db.add(m)
    db.flush()
    chain.append(db, str(p.user.id), "meeting.create", f"meeting:{m.id}", {"client": str(c.id)})
    return _meeting_dict(m)


@router.put("/meetings/{meeting_id}/summary")
def set_summary(
    meeting_id: uuid.UUID,
    body: SummaryIn,
    p: Principal = Depends(require("task.write")),
    db: Db = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    m = db.get(Meeting, meeting_id)
    if m is None:
        raise HTTPException(404, "not_found")
    get_visible(db, p, m.client_id)
    m.summary = body.summary
    chain.append(db, str(p.user.id), "meeting.summary", f"meeting:{m.id}", {})
    return _meeting_dict(m)
