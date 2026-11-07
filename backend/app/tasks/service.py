"""Tasks and meetings (agent Today, client file). REQUIREMENTS: agent home and F3 service duty."""

import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.orm import Session as Db

from app.flows.service import add_months
from app.models import Client, InboxItem, Meeting, Task, User, UserRole

VIEWS = ("today", "over", "next", "done", "deleg")
MEETING_KINDS = ("in_person", "phone", "online")
SERVICE_CONTACT_MONTHS = 24  # working assumption, to confirm with counsel (REQUIREMENTS F3)
_INBOX_ROLES = ("agent", "backoffice", "manager")


def today_utc() -> date:
    return datetime.now(UTC).date()


def select_view(view: str, me: uuid.UUID, today: date) -> Select[Any]:
    """The rows of one Today chip. Done tasks are the user's own; delegated are tasks the user
    handed to someone else that are still open."""
    q = select(Task)
    if view == "today":
        return q.where(Task.owner_id == me, Task.done_at.is_(None), Task.due_on == today)
    if view == "over":
        return q.where(Task.owner_id == me, Task.done_at.is_(None), Task.due_on < today)
    if view == "next":
        return q.where(Task.owner_id == me, Task.done_at.is_(None), Task.due_on > today)
    if view == "done":
        return q.where(Task.owner_id == me, Task.done_at.is_not(None))
    return q.where(Task.delegated_by == me, Task.owner_id != me, Task.done_at.is_(None))


def overdue_tick(db: Db, today: date | None = None) -> int:
    """Daily: an open task past its date becomes one Inbox item for its owner, once (the task id
    is in the title, so a second run finds it)."""
    today = today or today_utc()
    made = 0
    for t in db.scalars(select(Task).where(Task.done_at.is_(None), Task.due_on < today)):
        title = f"Task overdue: {t.text[:80]} ({t.id})"
        if db.scalar(
            select(InboxItem.id).where(InboxItem.type == "task_overdue", InboxItem.title == title)
        ):
            continue
        roles = set(db.scalars(select(UserRole.role).where(UserRole.user_id == t.owner_id)))
        role = next((r for r in _INBOX_ROLES if r in roles), "manager")
        db.add(
            InboxItem(
                type="task_overdue",
                role=role,
                assignee_id=t.owner_id,
                client_id=t.client_id,
                urgency="important",
                title=title,
                detail=f"Due {t.due_on.isoformat()}",
            )
        )
        made += 1
    return made


def service_contact_due(db: Db, client: Client) -> date | None:
    last = db.scalar(
        select(Meeting.held_on)
        .where(Meeting.client_id == client.id)
        .order_by(Meeting.held_on.desc())
        .limit(1)
    )
    return add_months(last, SERVICE_CONTACT_MONTHS) if last else None


def names(db: Db, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    return {u.id: u.display_name for u in db.scalars(select(User).where(User.id.in_(ids)))}
