import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as Db

from app.audit import chain
from app.clients.access import get_visible
from app.deps import Principal, get_db, require
from app.jobs import queue
from app.messaging import gate
from app.models import Client, OutboxMessage

router = APIRouter(prefix="/api")


class MessageIn(BaseModel):
    client_id: uuid.UUID
    channel: str
    purpose: str
    body: str = Field(min_length=1, max_length=4000)


@router.post("/messages", status_code=201)
def queue_message(
    body: MessageIn, p: Principal = Depends(require("message.send")), db: Db = Depends(get_db)
) -> dict[str, Any]:
    """The only way a message leaves the system (delivery adapters arrive in M8)."""
    if body.channel not in gate.SEND_CHANNELS or body.purpose not in gate.PURPOSES:
        raise HTTPException(422, "invalid_channel_or_purpose")
    if "client.read_min" in p.capabilities and "client.read" not in p.capabilities:
        # marketing sends campaigns to any client in the projection, never one-to-one service
        if body.purpose != "marketing":
            raise HTTPException(403, "forbidden")
        client = db.get(Client, body.client_id)
        if client is None:
            raise HTTPException(404, "not_found")
    else:
        client = get_visible(db, p, body.client_id)
    decision = gate.decide(db, client.id, body.channel, body.purpose)
    msg = OutboxMessage(
        client_id=client.id,
        channel=body.channel,
        purpose=body.purpose,
        language=client.preferred_language,
        body=body.body,
        status="queued" if decision.allowed else "blocked",
        block_reason=None if decision.allowed else decision.reason,
        created_by=p.user.id,
    )
    db.add(msg)
    db.flush()
    action = "message.queue" if decision.allowed else "message.blocked"
    chain.append(
        db,
        str(p.user.id),
        action,
        f"message:{msg.id}",
        {
            "client": str(client.id),
            "channel": body.channel,
            "purpose": body.purpose,
            "reason": decision.reason,
        },
    )
    if not decision.allowed:
        db.commit()  # keep the blocked record and its audit entry
        raise HTTPException(409, decision.reason)
    if msg.channel == "whatsapp":
        queue.enqueue(
            db, "message.deliver", {"message_id": str(msg.id)}, dedupe_key=f"deliver:{msg.id}"
        )
    return {
        "id": str(msg.id),
        "status": msg.status,
        "language": msg.language,
        "legal_override": decision.override,
    }
