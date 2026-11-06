"""Inbound WhatsApp: verification handshake, signed webhook, messages and delivery statuses.

The webhook has no session: it is authenticated by the HMAC signature Meta adds with the app
secret. Without a valid signature nothing is read, stored or acted on."""

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session as Db

from app.audit import chain
from app.config import Settings
from app.consent import service as consent_service
from app.deps import get_db, get_settings_dep
from app.intake import pipeline
from app.integrations import whatsapp as wa
from app.integrations.calls import logged_call
from app.integrations.phone import digits
from app.models import Client, InboundMessage, InboxItem, OutboxMessage

router = APIRouter(prefix="/api/webhooks")


def find_client(db: Db, from_address: str) -> Client | None:
    last9 = digits(from_address)[-9:]
    if len(last9) < 9:
        return None
    col = func.regexp_replace(Client.phone, "[^0-9]", "", "g")
    return db.scalars(
        select(Client).where(Client.phone.is_not(None), col.like(f"%{last9}")).limit(1)
    ).first()


@router.get("/whatsapp")
def verify_handshake(
    mode: str = Query("", alias="hub.mode"),
    token: str = Query("", alias="hub.verify_token"),
    challenge: str = Query("", alias="hub.challenge"),
    settings: Settings = Depends(get_settings_dep),
) -> PlainTextResponse:
    if (
        mode == "subscribe"
        and settings.whatsapp_verify_token
        and token == (settings.whatsapp_verify_token)
    ):
        return PlainTextResponse(challenge)
    raise HTTPException(403, "forbidden")


def process(db: Db, request: Request, payload: dict[str, Any]) -> dict[str, int]:
    inbound, statuses = wa.parse_webhook(payload)
    stored = 0
    for item in inbound:
        exists = db.scalar(
            select(InboundMessage.id).where(
                InboundMessage.channel == "whatsapp", InboundMessage.provider_id == item.provider_id
            )
        )
        if exists is not None:
            continue  # Meta retries: each message is handled once
        client = find_client(db, item.from_address)
        db.add(
            InboundMessage(
                channel="whatsapp",
                provider_id=item.provider_id,
                from_address=item.from_address,
                client_id=client.id if client else None,
                kind=item.kind,
                body=item.body[:4000],
                received_at=item.at,
            )
        )
        stored += 1
        who = client.name if client else f"+{digits(item.from_address)}"
        if item.kind == "text" and wa.is_stop(item.body):
            if client is not None:  # applied at once, with the time the message was received
                consent_service.record(
                    db, client, "whatsapp", "opt_out", "WhatsApp STOP reply", item.at, None
                )
            else:
                db.add(
                    InboxItem(
                        type="opt_out",
                        role="marketing",
                        urgency="urgent",
                        title=f"STOP from an unknown number {who}",
                        detail="Could not match a client: check the numbers by hand.",
                    )
                )
            continue
        agent = client.agent_id if client else None
        db.add(
            InboxItem(
                type="client_reply",
                role="agent" if agent else "backoffice",
                assignee_id=agent,
                client_id=client.id if client else None,
                urgency="important",
                title=f"WhatsApp from {who}",
                detail=(item.body or "(media)")[:200],
            )
        )
        if item.kind == "media" and item.media_id:
            _ingest_media(db, request, item, who)
    for st in statuses:
        msg = db.scalar(select(OutboxMessage).where(OutboxMessage.provider_id == st.provider_id))
        if msg is None:
            continue
        msg.delivery = st.status
        if st.status == "failed":
            msg.status = "failed"
            db.add(
                InboxItem(
                    type="send_failure",
                    role="backoffice",
                    client_id=msg.client_id,
                    urgency="important",
                    title="WhatsApp reported a delivery failure",
                )
            )
    return {"messages": stored, "statuses": len(statuses)}


def _ingest_media(db: Db, request: Request, item: wa.InboundItem, who: str) -> None:
    adapter = request.app.state.whatsapp
    if adapter is None:
        return
    maker = request.app.state.sessionmaker
    try:
        data, _mime = logged_call(
            maker,
            "whatsapp",
            "download_media",
            item.media_id,
            lambda: adapter.download_media(item.media_id),
        )
        pipeline.submit(
            db,
            request.app.state.intake,
            data=data,
            filename=f"whatsapp-{item.media_id}",
            channel="whatsapp",
            sender=f"+{digits(item.from_address)}",
            actor=None,
        )
    except Exception as e:  # noqa: BLE001  (a bad attachment must not lose the message)
        db.add(
            InboxItem(
                type="send_failure",
                role="backoffice",
                urgency="important",
                title=f"Could not read a WhatsApp attachment from {who}",
                detail=str(e)[:200],
            )
        )


@router.post("/whatsapp")
async def receive(
    request: Request, db: Db = Depends(get_db), settings: Settings = Depends(get_settings_dep)
) -> dict[str, int]:
    secret = settings.whatsapp_app_secret.get_secret_value() if settings.whatsapp_app_secret else ""
    if not secret:
        raise HTTPException(503, "whatsapp_not_configured")
    body = await request.body()
    if not wa.verify_signature(secret, body, request.headers.get("x-hub-signature-256")):
        chain.append(db, "anonymous", "webhook.rejected", "whatsapp", {})
        db.commit()
        raise HTTPException(403, "bad_signature")
    try:
        payload = json.loads(body)
    except ValueError as e:
        raise HTTPException(400, "bad_json") from e
    return await run_in_threadpool(process, db, request, payload)
