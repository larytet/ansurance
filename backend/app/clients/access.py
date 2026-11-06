"""Who may see which client. One place, used by every router."""

import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session as Db

from app.deps import Principal
from app.models import Client


def visible(p: Principal) -> Any:
    """Agents see their own clients; roles with client.read_all see every client."""
    if "client.read_all" in p.capabilities:
        return True
    return Client.agent_id == p.user.id


def get_visible(db: Db, p: Principal, client_id: uuid.UUID) -> Client:
    c = db.scalar(select(Client).where(Client.id == client_id, visible(p)))
    if c is None:  # not found and not allowed look the same
        raise HTTPException(404, "not_found")
    return c


def can_see_restricted(p: Principal, client: Client) -> bool:
    """Restricted (for example medical) documents: the agent of record, back-office and
    manager only. Other roles never get here (no document capability)."""
    return "client.read_all" in p.capabilities or client.agent_id == p.user.id
