import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.smtp import Mailer
from app.integrations.whatsapp import WhatsApp
from app.jobs.queue import Handler
from app.messaging.delivery import deliver


def make_delivery_handlers(
    settings: Settings, whatsapp: WhatsApp | None, mailer: Mailer | None = None
) -> dict[str, Handler]:
    def run(db: Session, payload: dict[str, Any]) -> None:
        deliver(db, settings, whatsapp, uuid.UUID(payload["message_id"]), mailer)

    def batch(db: Session, payload: dict[str, Any]) -> None:
        from app.campaigns.service import run_batch

        run_batch(db, settings, uuid.UUID(payload["campaign_id"]))

    return {"message.deliver": run, "campaign.batch": batch}
