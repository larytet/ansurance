"""F10: targeted outreach. The consent gate is a hard stop: a client with no consent, an
opt-out or a recent marketing contact is never selected, and the gate is checked again for
every message when it is sent."""

import re
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session as Db

from app.audit import chain
from app.config import Settings
from app.jobs import queue
from app.messaging import gate
from app.models import (
    Campaign,
    CampaignRecipient,
    CampaignTranslation,
    Client,
    InboxItem,
    OutboxMessage,
    Policy,
)

TYPES = {  # type -> purpose. A regulatory notice is a service message, an offer is advertising.
    "regulatory": "service",
    "renewal": "service",
    "offer": "marketing",
    "gap": "marketing",
    "life_event": "marketing",
}
NEEDS_APPROVAL = {"offer", "gap", "regulatory"}  # product offers and anything about regulation
LANGS = ("he", "ar", "ru", "fr", "en")
CHANNEL_ORDER = ("whatsapp", "email", "sms")
SALES_WORDS = re.compile(
    r"\b(offer|discount|sale|buy now|promotion|% off|free gift)\b|מבצע|הנחה|הצעה מיוחדת|קנו|"
    r"скидк|акци|promotion|réduction|عرض|خصم",
    re.IGNORECASE,
)
AD_LABEL = {
    "he": "פרסומת",
    "ar": "إعلان",
    "ru": "Реклама",
    "fr": "Publicité",
    "en": "Advertisement",
}
UNSUBSCRIBE = {
    "he": "להסרה השיבו הסר",
    "ar": "لإلغاء الاشتراك أرسل STOP",
    "ru": "Чтобы отписаться, ответьте STOP",
    "fr": "Pour vous désabonner, répondez STOP",
    "en": "To stop receiving messages reply STOP",
}


class CampaignError(Exception):
    def __init__(self, code: str, status: int = 409, detail: Any = None) -> None:
        super().__init__(code)
        self.code, self.status, self.detail = code, status, detail


def create(
    db: Db,
    actor: uuid.UUID,
    roles: list[str],
    *,
    name: str,
    type: str,
    benefit: str,
    segment: dict[str, Any],
    base_language: str,
    body: str,
) -> Campaign:
    if type not in TYPES:
        raise CampaignError("invalid_type", 422)
    if base_language not in LANGS:
        raise CampaignError("invalid_language", 422)
    if len(benefit.strip()) < 5:
        raise CampaignError("client_benefit_required", 422)  # advice, not spam
    purpose = TYPES[type]
    if purpose == "service" and SALES_WORDS.search(body):
        raise CampaignError("sales_content_in_service_campaign", 422)
    _validate_segment(segment)
    manager = "manager" in roles
    c = Campaign(
        name=name,
        type=type,
        purpose=purpose,
        benefit=benefit,
        segment=segment,
        base_language=base_language,
        body=body,
        created_by=actor,
        requires_approval=type in NEEDS_APPROVAL and not manager,
    )
    c.status = "pending_approval" if c.requires_approval else "draft"
    if manager:
        c.approved_by, c.approved_at = actor, datetime.now(UTC)
    db.add(c)
    db.flush()
    chain.append(db, str(actor), "campaign.create", f"campaign:{c.id}", {"type": type})
    return c


SEGMENT_KEYS = {"has_product", "lacks_product", "renewing_within_days", "language", "status"}


def _validate_segment(segment: dict[str, Any]) -> None:
    unknown = set(segment) - SEGMENT_KEYS
    if unknown:
        raise CampaignError("unknown_segment_rule", 422, sorted(unknown))
    for k in ("has_product", "lacks_product", "language"):
        if k in segment and not (
            isinstance(segment[k], list) and all(isinstance(x, str) for x in segment[k])
        ):
            raise CampaignError("invalid_segment_rule", 422, k)
    if "renewing_within_days" in segment and not isinstance(segment["renewing_within_days"], int):
        raise CampaignError("invalid_segment_rule", 422, "renewing_within_days")


def matching_clients(db: Db, segment: dict[str, Any], today: date | None = None) -> list[Client]:
    today = today or datetime.now(UTC).date()
    q = select(Client).where(Client.status.in_(("registered", "active")))
    if segment.get("language"):
        q = q.where(Client.preferred_language.in_(segment["language"]))
    for prod in segment.get("has_product", []):
        q = q.where(
            select(Policy.id)
            .where(Policy.client_id == Client.id, Policy.product == prod, Policy.status == "active")
            .exists()
        )
    for prod in segment.get("lacks_product", []):
        q = q.where(
            ~select(Policy.id)
            .where(Policy.client_id == Client.id, Policy.product == prod, Policy.status == "active")
            .exists()
        )
    if segment.get("renewing_within_days") is not None:
        horizon = today + timedelta(days=segment["renewing_within_days"])
        q = q.where(
            select(Policy.id)
            .where(
                Policy.client_id == Client.id,
                Policy.status == "active",
                Policy.renewal_date.is_not(None),
                Policy.renewal_date <= horizon,
                Policy.renewal_date >= today,
            )
            .exists()
        )
    return list(db.scalars(q.order_by(Client.name)))


def _recent_marketing(
    db: Db, client_id: uuid.UUID, days: int, now: datetime, own: uuid.UUID
) -> bool:
    since = now - timedelta(days=days)
    return (
        db.scalar(
            select(OutboxMessage.id)
            .where(
                OutboxMessage.client_id == client_id,
                OutboxMessage.purpose == "marketing",
                OutboxMessage.status != "blocked",
                OutboxMessage.created_at >= since,
                OutboxMessage.campaign_id.is_distinct_from(own),
            )
            .limit(1)
        )
        is not None
    )


def audience(
    db: Db, settings: Settings, c: Campaign, now: datetime | None = None
) -> list[tuple[Client, str | None, str]]:
    """(client, channel or None, reason). Channel None means excluded, with the reason."""
    now = now or datetime.now(UTC)
    out: list[tuple[Client, str | None, str]] = []
    for cl in matching_clients(db, c.segment, now.date()):
        if c.purpose == "marketing" and _recent_marketing(
            db, cl.id, settings.campaign_frequency_days, now, c.id
        ):
            out.append((cl, None, "frequency_cap"))
            continue
        reason = ""
        chosen = None
        for ch in CHANNEL_ORDER:
            d = gate.decide(db, cl.id, ch, c.purpose)
            if (
                d.allowed
                and not (ch == "whatsapp" and not cl.phone)
                and not (ch == "email" and not cl.email)
            ):
                chosen = ch
                break
            reason = reason or d.reason
        out.append((cl, chosen, "" if chosen else (reason or "no_contact_detail")))
    return out


def preview(db: Db, settings: Settings, c: Campaign) -> dict[str, Any]:
    rows = audience(db, settings, c)
    sel = [r for r in rows if r[1]]
    excluded: dict[str, int] = {}
    for _, ch, why in rows:
        if ch is None:
            excluded[why] = excluded.get(why, 0) + 1
    langs: dict[str, int] = {}
    for cl, _, _ in sel:
        langs[cl.preferred_language] = langs.get(cl.preferred_language, 0) + 1
    return {
        "matching": len(rows),
        "selected": len(sel),
        "excluded": excluded,
        "languages": langs,
        "sample": [
            {"name": cl.name, "language": cl.preferred_language, "channel": ch}
            for cl, ch, _ in sel[:10]
        ],
    }


def save_translation(db: Db, actor: uuid.UUID, c: Campaign, language: str, body: str) -> None:
    if language not in LANGS or language == c.base_language:
        raise CampaignError("invalid_language", 422)
    if c.purpose == "service" and SALES_WORDS.search(body):
        raise CampaignError("sales_content_in_service_campaign", 422)
    row = db.get(CampaignTranslation, (c.id, language))
    if row is None:
        db.add(
            CampaignTranslation(campaign_id=c.id, language=language, body=body, reviewed_by=actor)
        )
    else:
        row.body, row.reviewed_by, row.reviewed_at = body, actor, datetime.now(UTC)
    chain.append(db, str(actor), "campaign.translation", f"campaign:{c.id}", {"language": language})


def approve(db: Db, actor: uuid.UUID, c: Campaign) -> None:
    if c.status != "pending_approval":
        raise CampaignError(f"campaign_is_{c.status}", 409)
    c.status, c.approved_by, c.approved_at = "draft", actor, datetime.now(UTC)
    chain.append(db, str(actor), "campaign.approve", f"campaign:{c.id}", {})


def start(db: Db, settings: Settings, actor: uuid.UUID, c: Campaign) -> dict[str, Any]:
    """Freeze the audience, check approval and translations, and queue the first batch."""
    if c.status != "draft":
        raise CampaignError(f"campaign_is_{c.status}", 409)
    if c.requires_approval and c.approved_at is None:
        raise CampaignError("approval_required", 409)
    rows = audience(db, settings, c)
    need = sorted(
        {
            cl.preferred_language
            for cl, ch, _ in rows
            if ch and cl.preferred_language != c.base_language
        }
    )
    have = {
        t.language
        for t in db.scalars(
            select(CampaignTranslation).where(CampaignTranslation.campaign_id == c.id)
        )
    }
    missing = [lang for lang in need if lang not in have]
    if missing:
        raise CampaignError("translation_review_missing", 409, missing)
    for cl, ch, why in rows:
        db.add(
            CampaignRecipient(
                campaign_id=c.id,
                client_id=cl.id,
                status="selected" if ch else "excluded",
                channel=ch,
                language=cl.preferred_language,
                reason=why,
            )
        )
    c.status, c.snapshot_at = "sending", datetime.now(UTC)
    chain.append(
        db,
        str(actor),
        "campaign.start",
        f"campaign:{c.id}",
        {"selected": sum(1 for r in rows if r[1]), "excluded": sum(1 for r in rows if not r[1])},
    )
    db.flush()
    queue.enqueue(db, "campaign.batch", {"campaign_id": str(c.id)}, dedupe_key=f"campaign:{c.id}:0")
    return {"selected": sum(1 for r in rows if r[1]), "excluded": sum(1 for r in rows if not r[1])}


def render(c: Campaign, language: str, translations: dict[str, str]) -> str:
    text = c.body if language == c.base_language else translations.get(language, c.body)
    if c.purpose == "marketing":  # advertising label and an unsubscribe line, client's language
        label = AD_LABEL.get(language, AD_LABEL["en"])
        stop = UNSUBSCRIBE.get(language, UNSUBSCRIBE["en"])
        return f"{label}: {text}\n{stop}"
    return text


def run_batch(
    db: Db, settings: Settings, campaign_id: uuid.UUID, now: datetime | None = None
) -> int:
    """Queue one throttled batch. The gate runs again for every recipient (consent may have
    changed since the snapshot). Returns how many recipients are still waiting."""
    now = now or datetime.now(UTC)
    c = db.get(Campaign, campaign_id)
    if c is None or c.status != "sending":
        return 0
    trans = {
        t.language: t.body
        for t in db.scalars(
            select(CampaignTranslation).where(CampaignTranslation.campaign_id == c.id)
        )
    }
    batch = list(
        db.scalars(
            select(CampaignRecipient)
            .where(CampaignRecipient.campaign_id == c.id, CampaignRecipient.status == "selected")
            .order_by(CampaignRecipient.id)
            .limit(settings.campaign_batch_size)
            .with_for_update()
        )
    )
    for r in batch:
        if r.channel is None:
            continue
        d = gate.decide(db, r.client_id, r.channel, c.purpose)
        if not d.allowed:
            r.status, r.reason = "blocked", d.reason
            continue
        if c.purpose == "marketing" and _recent_marketing(
            db, r.client_id, settings.campaign_frequency_days, now, c.id
        ):
            r.status, r.reason = "blocked", "frequency_cap"
            continue
        msg = OutboxMessage(
            client_id=r.client_id,
            channel=r.channel,
            purpose=c.purpose,
            language=r.language,
            body=render(c, r.language, trans),
            status="queued",
            created_by=c.created_by,
            campaign_id=c.id,
        )
        db.add(msg)
        db.flush()
        r.status, r.outbox_id = "queued", msg.id
        chain.append(
            db,
            "system",
            "message.queue",
            f"message:{msg.id}",
            {
                "client": str(r.client_id),
                "channel": r.channel,
                "purpose": c.purpose,
                "campaign": str(c.id),
            },
        )
        if r.channel == "whatsapp":
            queue.enqueue(
                db, "message.deliver", {"message_id": str(msg.id)}, dedupe_key=f"deliver:{msg.id}"
            )
    left = (
        db.scalar(
            select(func.count())
            .select_from(CampaignRecipient)
            .where(CampaignRecipient.campaign_id == c.id, CampaignRecipient.status == "selected")
        )
        or 0
    )
    if left:
        n = (
            db.scalar(
                select(func.count())
                .select_from(CampaignRecipient)
                .where(
                    CampaignRecipient.campaign_id == c.id, CampaignRecipient.status != "selected"
                )
            )
            or 0
        )
        queue.enqueue(
            db,
            "campaign.batch",
            {"campaign_id": str(c.id)},
            dedupe_key=f"campaign:{c.id}:{n}",
            run_at=now + timedelta(seconds=settings.campaign_batch_seconds),
        )
    else:
        c.status = "done"
        chain.append(db, "system", "campaign.done", f"campaign:{c.id}", {})
    return left


def results(db: Db, c: Campaign) -> dict[str, Any]:
    recs = dict(
        db.execute(
            select(CampaignRecipient.status, func.count())
            .where(CampaignRecipient.campaign_id == c.id)
            .group_by(CampaignRecipient.status)
        ).all()
    )
    exclusions = dict(
        db.execute(
            select(CampaignRecipient.reason, func.count())
            .where(
                CampaignRecipient.campaign_id == c.id,
                CampaignRecipient.status.in_(("excluded", "blocked")),
            )
            .group_by(CampaignRecipient.reason)
        ).all()
    )
    dl = dict(
        db.execute(
            select(OutboxMessage.delivery, func.count())
            .where(OutboxMessage.campaign_id == c.id, OutboxMessage.delivery.is_not(None))
            .group_by(OutboxMessage.delivery)
        ).all()
    )
    replies = (
        db.scalar(
            select(func.count())
            .select_from(InboxItem)
            .where(
                InboxItem.type == "campaign_reply", InboxItem.detail.like(f"%[campaign:{c.id}]%")
            )
        )
        or 0
    )
    from app.models import ConsentEvent

    unsub = (
        db.scalar(
            select(func.count(func.distinct(ConsentEvent.client_id))).where(
                ConsentEvent.state == "opt_out",
                ConsentEvent.client_id.in_(
                    select(CampaignRecipient.client_id).where(
                        CampaignRecipient.campaign_id == c.id,
                        CampaignRecipient.status.in_(("queued", "blocked")),
                    )
                ),
                ConsentEvent.recorded_at >= (c.snapshot_at or datetime.now(UTC)),
            )
        )
        or 0
    )
    return {
        "status": c.status,
        "recipients": recs,
        "exclusions": exclusions,
        "delivery": dl,
        "replied": replies,
        "unsubscribed": unsub,
    }


def recent_campaign_for_reply(
    db: Db, client_id: uuid.UUID, now: datetime, days: int = 14
) -> Campaign | None:
    return db.scalar(
        select(Campaign)
        .join(OutboxMessage, OutboxMessage.campaign_id == Campaign.id)
        .where(
            OutboxMessage.client_id == client_id,
            OutboxMessage.created_at >= now - timedelta(days=days),
        )
        .order_by(OutboxMessage.created_at.desc())
        .limit(1)
    )
