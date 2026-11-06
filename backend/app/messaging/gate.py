"""The consent gate. Every outbound message passes through here, with no exceptions.

Rules (REQUIREMENTS F10, consent registry):
- the latest consent event per client and channel wins;
- opt-out blocks every purpose on that channel, except `legal` (a regulatory notice the
  agency must send), which is allowed and audited as an override;
- `marketing` needs an explicit opt-in; no event means no consent;
- `service` (renewal reminders, claim status) is allowed unless the client opted out.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session as Db

CHANNELS = ("whatsapp", "email", "sms", "phone", "post")
SEND_CHANNELS = ("whatsapp", "email", "sms")
PURPOSES = ("marketing", "service", "legal")


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""
    override: bool = False


def current_state(db: Db, client_id: uuid.UUID, channel: str) -> str | None:
    return db.execute(
        text("SELECT state FROM consent_current WHERE client_id = :c AND channel = :ch"),
        {"c": client_id, "ch": channel},
    ).scalar()


def decide(db: Db, client_id: uuid.UUID, channel: str, purpose: str) -> Decision:
    state = current_state(db, client_id, channel)
    if state == "opt_out":
        if purpose == "legal":
            return Decision(True, "legal_notice_despite_opt_out", override=True)
        return Decision(False, "opted_out")
    if purpose == "marketing" and state != "opt_in":
        return Decision(False, "no_consent")
    return Decision(True)
