"""Delivering queued messages. The consent gate is checked again at send time: a client who
opted out after the message was queued must not receive it."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.audit import chain
from app.config import Settings
from app.integrations.calls import logged_call
from app.integrations.phone import to_e164
from app.integrations.whatsapp import WhatsApp, WhatsAppError
from app.messaging import gate
from app.models import Client, InboxItem, OutboxMessage


class RetryDelivery(Exception):
    """Raised to let the job queue retry later, with backoff."""


def _fail(db: Session, msg: OutboxMessage, client: Client, reason: str) -> None:
    msg.status, msg.last_error = "failed", reason
    chain.append(db, "system", "message.failed", f"message:{msg.id}", {"reason": reason})
    db.add(
        InboxItem(
            type="send_failure",
            role="backoffice",
            client_id=client.id,
            urgency="important",
            title=f"Message to {client.name} could not be sent",
            detail=f"{msg.channel}, {msg.purpose}: {reason}",
        )
    )


def deliver(
    db: Session, settings: Settings, whatsapp: WhatsApp | None, message_id: uuid.UUID
) -> str:
    msg = db.scalar(select(OutboxMessage).where(OutboxMessage.id == message_id).with_for_update())
    if msg is None or msg.status != "queued" or msg.channel != "whatsapp":
        return msg.status if msg else "missing"
    client = db.get(Client, msg.client_id)
    if client is None:
        return "missing"

    decision = gate.decide(db, client.id, msg.channel, msg.purpose)
    if not decision.allowed:  # consent changed since the message was queued
        msg.status, msg.block_reason = "blocked", decision.reason
        chain.append(
            db,
            "system",
            "message.blocked_at_send",
            f"message:{msg.id}",
            {"reason": decision.reason},
        )
        return "blocked"

    phone = to_e164(client.phone or "", settings.default_country_code)
    if phone is None:
        _fail(db, msg, client, "no usable phone number")
        return "failed"
    if whatsapp is None:
        raise RetryDelivery("WhatsApp is not configured")

    maker = sessionmaker(db.get_bind())
    msg.attempts += 1
    try:
        pid = logged_call(
            maker, "whatsapp", "send_text", phone, lambda: whatsapp.send_text(phone, msg.body)
        )
    except WhatsAppError as e:
        msg.last_error = str(e)[:500]
        if not e.retryable or msg.attempts >= settings.max_delivery_attempts:
            _fail(db, msg, client, msg.last_error)
            return "failed"
        db.commit()  # keep the attempt count, then let the queue retry with backoff
        raise RetryDelivery(msg.last_error) from e
    msg.status, msg.delivery, msg.provider_id = "sent", "sent", pid
    msg.sent_at, msg.last_error = datetime.now(UTC), None
    chain.append(
        db,
        "system",
        "message.sent",
        f"message:{msg.id}",
        {"provider_id": pid, "channel": msg.channel, "purpose": msg.purpose},
    )
    return "sent"
