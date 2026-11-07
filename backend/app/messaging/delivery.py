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
from app.integrations.smtp import Mailer, MailError
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


AD_LABEL = {
    "he": "פרסומת",
    "ar": "إعلان",
    "ru": "Реклама",
    "fr": "Publicité",
    "en": "Advertisement",
}


def subject_for(msg: OutboxMessage) -> str:
    """The first line of the text; advertising always carries the label in the subject."""
    first = msg.body.strip().splitlines()[0][:78] if msg.body.strip() else ""
    label = AD_LABEL.get(msg.language, AD_LABEL["en"])
    if msg.purpose == "marketing" and not first.startswith(label):
        first = f"{label}: {first}"
    return first


def _deliver_email(
    db: Session, settings: Settings, mailer: Mailer | None, msg: OutboxMessage, client: Client
) -> str:
    if not client.email:
        _fail(db, msg, client, "no e-mail address")
        return "failed"
    if mailer is None:
        raise RetryDelivery("e-mail is not configured")
    maker = sessionmaker(db.get_bind())
    msg.attempts += 1
    subject = subject_for(msg)
    unsub = settings.smtp_from if msg.purpose == "marketing" else None
    try:
        mid = logged_call(
            maker,
            "smtp",
            "send",
            "email",
            lambda: mailer.send(
                to=client.email or "", subject=subject, body=msg.body, unsubscribe=unsub
            ),
        )
    except MailError as e:
        msg.last_error = str(e)[:500]
        if not e.retryable or msg.attempts >= settings.max_delivery_attempts:
            _fail(db, msg, client, msg.last_error)
            return "failed"
        db.commit()
        raise RetryDelivery(msg.last_error) from e
    msg.status, msg.delivery, msg.provider_id = "sent", "sent", mid
    msg.sent_at, msg.last_error = datetime.now(UTC), None
    chain.append(
        db,
        "system",
        "message.sent",
        f"message:{msg.id}",
        {"provider_id": mid, "channel": "email", "purpose": msg.purpose},
    )
    return "sent"


def deliver(
    db: Session,
    settings: Settings,
    whatsapp: WhatsApp | None,
    message_id: uuid.UUID,
    mailer: Mailer | None = None,
) -> str:
    msg = db.scalar(select(OutboxMessage).where(OutboxMessage.id == message_id).with_for_update())
    if msg is None or msg.status != "queued" or msg.channel not in ("whatsapp", "email"):
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

    if msg.channel == "email":
        return _deliver_email(db, settings, mailer, msg, client)

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
