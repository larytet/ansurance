"""Recording consent. One place, used by the API and by inbound messages (a WhatsApp STOP)."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.audit import chain
from app.models import Client, ConsentEvent, InboxItem

OPT_OUT_DEADLINE = timedelta(seconds=60)


def record(
    db: Session,
    client: Client,
    channel: str,
    state: str,
    source: str,
    received_at: datetime | None,
    actor: uuid.UUID | None,
) -> tuple[ConsentEvent, bool]:
    """Append a consent event. An opt-out applied more than a minute after it was received
    is flagged and escalated to the manager. Returns (event, late)."""
    now = datetime.now(UTC)
    received = received_at or now
    late = state == "opt_out" and now - received > OPT_OUT_DEADLINE
    ev = ConsentEvent(
        client_id=client.id,
        channel=channel,
        state=state,
        source=source,
        received_at=received,
        recorded_by=actor,
        late=late,
    )
    db.add(ev)
    db.flush()
    chain.append(
        db,
        str(actor or "system"),
        "consent.record",
        f"client:{client.id}",
        {"channel": channel, "state": state, "source": source, "late": late},
    )
    if late:
        db.add(
            InboxItem(
                type="consent_late",
                role="manager",
                client_id=client.id,
                urgency="urgent",
                title="Opt-out applied after the one-minute limit",
                detail=f"{channel} opt-out received {received.isoformat()}, "
                f"applied {now.isoformat()}",
                created_by=actor,
            )
        )
    return ev, late
