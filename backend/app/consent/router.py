import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session as Db

from app.audit import chain
from app.clients.access import get_visible
from app.deps import Principal, get_db, require
from app.messaging.gate import CHANNELS
from app.models import Client, ConsentEvent, InboxItem

router = APIRouter(prefix="/api")
OPT_OUT_DEADLINE = timedelta(seconds=60)


class ConsentIn(BaseModel):
    channel: str
    state: str
    source: str = Field(min_length=2, max_length=120)
    received_at: datetime | None = None


def _client_for_consent(db: Db, p: Principal, client_id: uuid.UUID) -> Client:
    """Opt-outs must be processable by marketing for any client; others see their own."""
    if "client.read_min" in p.capabilities and "client.read" not in p.capabilities:
        c = db.get(Client, client_id)
        if c is None:
            raise HTTPException(404, "not_found")
        return c
    return get_visible(db, p, client_id)


@router.post("/clients/{client_id}/consent", status_code=201)
def record_consent(
    client_id: uuid.UUID,
    body: ConsentIn,
    p: Principal = Depends(require("consent.write")),
    db: Db = Depends(get_db),
) -> dict[str, Any]:
    if body.channel not in CHANNELS or body.state not in ("opt_in", "opt_out"):
        raise HTTPException(422, "invalid_channel_or_state")
    client = _client_for_consent(db, p, client_id)
    now = datetime.now(UTC)
    received = body.received_at or now
    late = body.state == "opt_out" and now - received > OPT_OUT_DEADLINE
    ev = ConsentEvent(
        client_id=client.id,
        channel=body.channel,
        state=body.state,
        source=body.source,
        received_at=received,
        recorded_by=p.user.id,
        late=late,
    )
    db.add(ev)
    db.flush()
    chain.append(
        db,
        str(p.user.id),
        "consent.record",
        f"client:{client.id}",
        {"channel": body.channel, "state": body.state, "source": body.source, "late": late},
    )
    if late:  # the one-minute rule was missed: the manager must know
        db.add(
            InboxItem(
                type="consent_late",
                role="manager",
                client_id=client.id,
                urgency="urgent",
                title="Opt-out applied after the one-minute limit",
                detail=f"{body.channel} opt-out received {received.isoformat()}, "
                f"applied {now.isoformat()}",
                created_by=p.user.id,
            )
        )
    return {"id": str(ev.id), "state": body.state, "channel": body.channel, "late": late}


@router.get("/clients/{client_id}/consent")
def get_consent(
    client_id: uuid.UUID,
    p: Principal = Depends(require("consent.read")),
    db: Db = Depends(get_db),
) -> dict[str, Any]:
    client = _client_for_consent(db, p, client_id)
    current = (
        db.execute(
            text(
                "SELECT channel, state, recorded_at, source FROM consent_current "
                "WHERE client_id = :c"
            ),
            {"c": client.id},
        )
        .mappings()
        .all()
    )
    history = db.scalars(
        select(ConsentEvent)
        .where(ConsentEvent.client_id == client.id)
        .order_by(ConsentEvent.recorded_at, ConsentEvent.id)
    )
    return {
        "current": {r["channel"]: r["state"] for r in current},
        "history": [
            {
                "channel": e.channel,
                "state": e.state,
                "source": e.source,
                "recorded_at": e.recorded_at.isoformat(),
                "late": e.late,
            }
            for e in history
        ],
    }
