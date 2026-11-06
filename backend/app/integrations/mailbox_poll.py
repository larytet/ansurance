"""Turn new mail into work: attachments enter the intake pipeline, the text becomes an Inbox
item for the client's agent. A message is marked read on the server only after it is stored."""

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.intake import pipeline
from app.intake.context import IntakeContext
from app.integrations.calls import logged_call
from app.integrations.mailbox import Mailbox, message_id, sender_and_body
from app.models import Client, InboundMessage, InboxItem, MailSeen

log = logging.getLogger("ansurance.mailbox")


def poll(db: Session, ctx: IntakeContext, mailbox: Mailbox) -> dict[str, int]:
    maker = sessionmaker(db.get_bind())
    messages = logged_call(maker, "imap", "fetch_new", "inbox", mailbox.fetch_new)
    done = skipped = failed = 0
    for m in messages:
        mid = message_id(m.raw)
        if db.get(MailSeen, mid) is not None:
            mailbox.mark_seen(m.uid)  # handled before (the mark was lost): just flag it
            skipped += 1
            continue
        try:
            handle(db, ctx, m.raw, mid)
            db.add(MailSeen(message_id=mid))
            db.commit()
        except Exception:  # noqa: BLE001  (leave it unread: it is tried again next poll)
            db.rollback()
            log.exception("could not ingest message %s", mid)
            failed += 1
            continue
        mailbox.mark_seen(m.uid)
        done += 1
    return {"ingested": done, "skipped": skipped, "failed": failed}


def handle(db: Session, ctx: IntakeContext, raw: bytes, mid: str) -> None:
    sender, subject, body = sender_and_body(raw)
    items = pipeline.submit(
        db, ctx, data=raw, filename="mail.eml", channel="email", sender=sender, actor=None
    )
    client = (
        db.scalars(
            select(Client)
            .where(Client.email.is_not(None), func.lower(Client.email) == sender)
            .limit(1)
        ).first()
        if sender
        else None
    )
    db.add(
        InboundMessage(
            channel="email",
            provider_id=mid,
            from_address=sender or "unknown",
            client_id=client.id if client else None,
            kind="text",
            body=f"{subject}\n{body}"[:4000],
            received_at=func.now(),
        )
    )
    agent = client.agent_id if client else None
    attached = f" with {len(items)} attachment(s)" if items else ""
    db.add(
        InboxItem(
            type="client_reply",
            role="agent" if agent else "backoffice",
            assignee_id=agent,
            client_id=client.id if client else None,
            urgency="normal",
            title=f"E-mail from {client.name if client else sender or 'unknown sender'}{attached}",
            detail=(subject or body)[:200],
        )
    )
