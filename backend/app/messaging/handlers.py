import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.whatsapp import WhatsApp
from app.jobs.queue import Handler
from app.messaging.delivery import deliver


def make_delivery_handlers(settings: Settings, whatsapp: WhatsApp | None) -> dict[str, Handler]:
    def run(db: Session, payload: dict[str, Any]) -> None:
        deliver(db, settings, whatsapp, uuid.UUID(payload["message_id"]))

    return {"message.deliver": run}
