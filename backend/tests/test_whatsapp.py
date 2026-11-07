"""M8: WhatsApp. Adapter, delivery with the consent gate re-checked at send time, signed
inbound webhook, STOP handling, delivery statuses. Also P-16 (webhook forgery)."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from app.adapters.antivirus import FakeAntivirus
from app.blob.fake import FakeBlobStore
from app.config import Settings
from app.intake.context import IntakeContext
from app.intake.ocr import FakeOcr
from app.integrations import whatsapp as wa
from app.integrations.calls import AdapterNotApproved, logged_call, require_approved
from app.integrations.phone import same_number, to_e164
from app.jobs import queue
from app.main import create_app, make_whatsapp
from app.messaging.handlers import make_delivery_handlers
from app.models import (
    AdapterCall,
    Client,
    ConsentEvent,
    InboundMessage,
    InboxItem,
    IntakeItem,
    OutboxMessage,
)
from app.text import build_search
from tests.conftest import Account, login, make_account

SECRET = "app-secret-123"


# ---- phone numbers ------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("050-123-4567", "+972501234567"),
        ("0501234567", "+972501234567"),
        ("+972 (50) 123 4567", "+972501234567"),
        ("972501234567", "+972501234567"),
        ("00972501234567", "+972501234567"),
        ("+1 415 555 2671", "+14155552671"),
        ("", None),
        ("12345", None),
        ("050-123", None),
        ("abc", None),
    ],
)
def test_to_e164(raw: str, expected: str | None) -> None:
    assert to_e164(raw) == expected


def test_same_number_ignores_formatting() -> None:
    assert same_number("050-123-4567", "+972501234567")
    assert not same_number("050-123-4567", "050-123-4568")
    assert not same_number("", "")


# ---- signatures and parsing -----------------------------------------------------------------------
def test_signature_verification() -> None:
    body = b'{"a":1}'
    good = wa.sign(SECRET, body)
    assert good.startswith("sha256=") and wa.verify_signature(SECRET, body, good)
    assert not wa.verify_signature(SECRET, body + b" ", good)  # body changed
    assert not wa.verify_signature("other-secret", body, good)
    assert not wa.verify_signature(SECRET, body, None)
    assert not wa.verify_signature(SECRET, body, "")
    assert not wa.verify_signature("", body, good)  # an unconfigured secret never validates


def payload(*msgs: dict[str, Any], statuses: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "entry": [{"changes": [{"value": {"messages": list(msgs), "statuses": statuses or []}}]}]
    }


def text_msg(pid: str, frm: str, body: str, ts: datetime | None = None) -> dict[str, Any]:
    return {
        "id": pid,
        "from": frm,
        "type": "text",
        "text": {"body": body},
        "timestamp": str(int((ts or datetime.now(UTC)).timestamp())),
    }


def test_parse_webhook() -> None:
    p = payload(
        text_msg("m1", "972501234567", "hello"),
        {
            "id": "m2",
            "from": "972509999999",
            "type": "image",
            "image": {"id": "media1", "caption": "my id"},
            "timestamp": "1700000000",
        },
        {"id": "m3", "from": "1", "type": "sticker"},
        statuses=[{"id": "w1", "status": "delivered"}],
    )
    inbound, statuses = wa.parse_webhook(p)
    assert [(i.provider_id, i.kind) for i in inbound] == [("m1", "text"), ("m2", "media")]
    assert inbound[1].media_id == "media1" and inbound[1].body == "my id"
    assert [(s.provider_id, s.status) for s in statuses] == [("w1", "delivered")]
    assert wa.parse_webhook({}) == ([], [])


@pytest.mark.parametrize(
    "word,stop",
    [
        ("STOP", True),
        ("stop.", True),
        (" הפסק ", True),
        ("Стоп", True),
        ("please stop sending", False),
        ("thanks", False),
    ],
)
def test_stop_words(word: str, stop: bool) -> None:
    assert wa.is_stop(word) is stop


# ---- the HTTP adapter against a mock server ----------------------------------------------------------
def meta(handler: Any) -> wa.MetaCloudWhatsApp:
    return wa.MetaCloudWhatsApp(
        "PHONE1",
        "tok",
        "https://graph.test/v20.0",
        httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_send_text_request_shape_and_result() -> None:
    seen: list[httpx.Request] = []

    def handler(r: httpx.Request) -> httpx.Response:
        seen.append(r)
        return httpx.Response(200, json={"messages": [{"id": "wamid.ABC"}]})

    assert meta(handler).send_text("+972501234567", "שלום") == "wamid.ABC"
    (r,) = seen
    assert str(r.url) == "https://graph.test/v20.0/PHONE1/messages"
    assert r.headers["authorization"] == "Bearer tok"
    assert json.loads(r.content) == {
        "messaging_product": "whatsapp",
        "to": "972501234567",
        "type": "text",
        "text": {"body": "שלום"},
    }


@pytest.mark.parametrize(
    "status,retryable",
    [(400, False), (401, False), (404, False), (429, True), (500, True), (503, True)],
)
def test_send_errors_are_classified(status: int, retryable: bool) -> None:
    adapter = meta(lambda r: httpx.Response(status, text="nope"))
    with pytest.raises(wa.WhatsAppError) as e:
        adapter.send_text("+972501234567", "x")
    assert e.value.retryable is retryable


def test_unexpected_success_body_is_an_error() -> None:
    with pytest.raises(wa.WhatsAppError):
        meta(lambda r: httpx.Response(200, json={"oops": 1})).send_text("+9725", "x")


def test_media_download_is_two_steps_with_the_token() -> None:
    urls: list[str] = []

    def handler(r: httpx.Request) -> httpx.Response:
        urls.append(str(r.url))
        assert r.headers["authorization"] == "Bearer tok"
        if str(r.url).endswith("/media1"):
            return httpx.Response(200, json={"url": "https://cdn.test/f", "mime_type": "image/png"})
        return httpx.Response(200, content=b"PNGDATA")

    assert meta(handler).download_media("media1") == (b"PNGDATA", "image/png")
    assert urls == ["https://graph.test/v20.0/media1", "https://cdn.test/f"]
    with pytest.raises(wa.WhatsAppError) as e:
        meta(lambda r: httpx.Response(404)).download_media("gone")
    assert e.value.retryable is False


# ---- logging every call; approved adapters ---------------------------------------------------------------
def test_every_adapter_call_is_logged_even_when_it_fails(fresh_db: Engine, db: Session) -> None:
    from sqlalchemy.orm import sessionmaker

    maker = sessionmaker(fresh_db)
    assert logged_call(maker, "whatsapp", "send_text", "+972501234567", lambda: "ok") == "ok"
    with pytest.raises(RuntimeError):
        logged_call(
            maker,
            "whatsapp",
            "send_text",
            "+972500000000",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
    rows = db.scalars(select(AdapterCall).order_by(AdapterCall.at)).all()
    assert [(r.ok, r.target) for r in rows] == [(True, "+972501234567"), (False, "+972500000000")]
    assert rows[1].error == "RuntimeError: boom" and rows[0].duration_ms >= 0


def test_an_adapter_off_the_approved_list_never_runs(settings: Settings) -> None:
    s = settings.model_copy(
        update={
            "approved_adapters": ["imap"],
            "whatsapp_phone_id": "P",
            "whatsapp_token": SecretStr("t"),
        }
    )
    with pytest.raises(AdapterNotApproved):
        require_approved(s, "whatsapp")
    with pytest.raises(AdapterNotApproved):
        make_whatsapp(s)
    ok = s.model_copy(update={"approved_adapters": ["whatsapp"]})
    assert isinstance(make_whatsapp(ok), wa.MetaCloudWhatsApp)
    assert make_whatsapp(settings.model_copy(update={"profile": "cloud"})) is None  # unconfigured


# ---- the environment for delivery and webhook tests ----------------------------------------------------
class Env:
    def __init__(self, settings: Settings, engine: Engine, db: Session) -> None:
        self.settings = settings.model_copy(
            update={
                "whatsapp_app_secret": SecretStr(SECRET),
                "whatsapp_verify_token": "verify-me",
                "max_delivery_attempts": 3,
            }
        )
        self.engine, self.db = engine, db
        self.fake = wa.FakeWhatsApp()
        self.intake = IntakeContext(self.settings, FakeBlobStore(), FakeAntivirus(), FakeOcr())
        self.app = create_app(self.settings, engine, whatsapp=self.fake, intake=self.intake)
        self.handlers = make_delivery_handlers(self.settings, self.fake)
        self.users: dict[str, tuple[TestClient, dict[str, str], Account]] = {}

    def user(self, role: str) -> tuple[TestClient, dict[str, str], Account]:
        if role not in self.users:
            a = make_account(self.db, f"{role}@agency.il", [role])
            c = TestClient(self.app, base_url="https://testserver")
            self.users[role] = (c, login(c, self.db, a), a)
        return self.users[role]

    def client(
        self,
        name: str = "David Cohen",
        phone: str | None = "050-123-4567",
        id_number: str = "123456782",
        agent: Account | None = None,
    ) -> Client:
        plain, skel = build_search(name, id_number)
        c = Client(
            id_number=id_number,
            name=name,
            search_text=plain,
            search_skel=skel,
            phone=phone,
            agent_id=agent.id if agent else None,
        )
        self.db.add(c)
        self.db.commit()
        return c

    def consent(self, c: Client, state: str, channel: str = "whatsapp") -> None:
        self.db.add(
            ConsentEvent(
                client_id=c.id,
                channel=channel,
                state=state,
                source="t",
                received_at=datetime.now(UTC),
            )
        )
        self.db.commit()

    def send(self, c: Client, purpose: str = "service", body: str = "Hello") -> str:
        mgr, hdr, _ = self.user("manager")
        r = mgr.post(
            "/api/messages",
            headers=hdr,
            json={"client_id": str(c.id), "channel": "whatsapp", "purpose": purpose, "body": body},
        )
        assert r.status_code == 201, r.text
        return str(r.json()["id"])

    def drain(self) -> int:
        self.db.expire_all()
        return queue.drain(self.db, self.handlers)

    def message(self, mid: str) -> OutboxMessage:
        self.db.expire_all()
        m = self.db.get(OutboxMessage, mid)
        assert m is not None
        return m

    def post(
        self, body: dict[str, Any], sig: str | None = "valid", raw: bytes | None = None
    ) -> Any:
        data = raw if raw is not None else json.dumps(body).encode()
        headers = {"content-type": "application/json"}
        if sig == "valid":
            headers["x-hub-signature-256"] = wa.sign(SECRET, data)
        elif sig:
            headers["x-hub-signature-256"] = sig
        return TestClient(self.app, base_url="https://testserver").post(
            "/api/webhooks/whatsapp", content=data, headers=headers
        )


@pytest.fixture
def env(settings: Settings, fresh_db: Engine, db: Session) -> Env:
    return Env(settings, fresh_db, db)


# ---- delivery ------------------------------------------------------------------------------------------------
def test_a_queued_message_is_delivered_by_the_worker(env: Env) -> None:
    c = env.client()
    mid = env.send(c, body="Your renewal is due")
    assert env.message(mid).status == "queued"
    assert env.drain() == 1
    m = env.message(mid)
    assert (m.status, m.delivery, m.provider_id) == ("sent", "sent", "wamid.FAKE1")
    assert m.sent_at is not None and m.attempts == 1
    assert env.fake.sent == [("+972501234567", "Your renewal is due")]
    call = env.db.scalars(select(AdapterCall)).one()
    assert (call.adapter, call.operation, call.ok, call.target) == (
        "whatsapp",
        "send_text",
        True,
        "+972501234567",
    )
    assert (
        env.db.execute(
            text("SELECT count(*) FROM audit_entry WHERE action='message.sent'")
        ).scalar_one()
        == 1
    )


def test_a_client_who_opts_out_after_queueing_gets_nothing(env: Env) -> None:
    c = env.client()
    env.consent(c, "opt_in")
    mid = env.send(c, purpose="marketing")
    env.consent(c, "opt_out")  # the client replied STOP before the worker ran
    env.drain()
    m = env.message(mid)
    assert (m.status, m.block_reason) == ("blocked", "opted_out") and env.fake.sent == []
    assert (
        env.db.execute(
            text("SELECT count(*) FROM audit_entry WHERE action='message.blocked_at_send'")
        ).scalar_one()
        == 1
    )


def test_legal_notices_still_go_out_after_an_opt_out(env: Env) -> None:
    c = env.client()
    env.consent(c, "opt_out")
    mid = env.send(c, purpose="legal", body="Regulatory notice")
    env.drain()
    assert env.message(mid).status == "sent" and len(env.fake.sent) == 1


def test_no_usable_phone_fails_with_a_task_for_the_back_office(env: Env) -> None:
    c = env.client(phone=None)
    mid = env.send(c)
    env.drain()
    assert env.message(mid).status == "failed"
    item = env.db.scalars(select(InboxItem).where(InboxItem.type == "send_failure")).one()
    assert item.role == "backoffice" and "could not be sent" in item.title and env.fake.sent == []


def test_a_temporary_failure_is_retried_with_backoff_then_succeeds(env: Env) -> None:
    c = env.client()
    mid = env.send(c)
    env.fake.fail_with = wa.WhatsAppError("503 busy", retryable=True)
    env.drain()
    m = env.message(mid)
    assert m.status == "queued" and m.attempts == 1 and "503" in (m.last_error or "")
    job = env.db.scalars(select(queue.Job)).one()
    assert job.status == "queued" and job.run_at > datetime.now(UTC)  # waits before the retry
    env.fake.fail_with = None
    env.db.execute(text("UPDATE job SET run_at = now() - interval '1 second'"))
    env.db.commit()
    env.drain()
    m = env.message(mid)
    assert (m.status, m.attempts) == ("sent", 2) and m.last_error is None


def test_a_permanent_failure_is_not_retried(env: Env) -> None:
    c = env.client()
    mid = env.send(c)
    env.fake.fail_with = wa.WhatsAppError("400 invalid number", retryable=False)
    env.drain()
    m = env.message(mid)
    assert (m.status, m.attempts) == ("failed", 1)
    assert env.db.scalars(select(queue.Job)).one().status == "done"  # nothing left to retry
    assert env.db.scalars(select(InboxItem).where(InboxItem.type == "send_failure")).one()


def test_retries_stop_after_the_limit(env: Env) -> None:
    c = env.client()
    mid = env.send(c)
    env.fake.fail_with = wa.WhatsAppError("500 down", retryable=True)
    for _ in range(5):
        env.drain()
        env.db.execute(text("UPDATE job SET run_at = now() - interval '1 second'"))
        env.db.commit()
    m = env.message(mid)
    assert (m.status, m.attempts) == ("failed", 3)  # max_delivery_attempts in the fixture
    assert len(env.db.scalars(select(InboxItem).where(InboxItem.type == "send_failure")).all()) == 1


def test_an_unconfigured_adapter_keeps_the_message_queued(
    settings: Settings, fresh_db: Engine, db: Session
) -> None:
    e = Env(settings, fresh_db, db)
    e.handlers = make_delivery_handlers(e.settings, None)  # no WhatsApp configured
    c = e.client()
    mid = e.send(c)
    e.drain()
    assert e.message(mid).status == "queued"  # never lost, never sent


def test_a_message_is_delivered_once_even_if_the_job_runs_twice(env: Env) -> None:
    from app.messaging.delivery import deliver

    c = env.client()
    mid = env.send(c)
    env.drain()
    assert deliver(env.db, env.settings, env.fake, env.message(mid).id) == "sent"  # no-op now
    assert len(env.fake.sent) == 1


def test_sms_is_not_picked_up_by_any_worker_and_whatsapp_sends_nothing_for_it(
    env: Env,
) -> None:  # e-mail has its own adapter
    c = env.client()
    mgr, hdr, _ = env.user("manager")
    mgr.post(
        "/api/messages",
        headers=hdr,
        json={"client_id": str(c.id), "channel": "sms", "purpose": "service", "body": "x"},
    )
    assert env.drain() == 0 and env.fake.sent == []


# ---- the webhook ---------------------------------------------------------------------------------------------
def test_verification_handshake(env: Env) -> None:
    c = TestClient(env.app, base_url="https://testserver")
    ok = c.get(
        "/api/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "12345"},
    )
    assert ok.status_code == 200 and ok.text == "12345"
    for bad in (
        {"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "1"},
        {"hub.mode": "subscribe", "hub.challenge": "1"},
        {"hub.mode": "unsubscribe", "hub.verify_token": "verify-me", "hub.challenge": "1"},
    ):
        assert c.get("/api/webhooks/whatsapp", params=bad).status_code == 403


@pytest.mark.parametrize("sig", [None, "sha256=deadbeef", "valid-but-for-another-body"])
def test_forged_or_unsigned_webhooks_are_rejected_and_change_nothing(
    env: Env, sig: str | None
) -> None:
    """P-16: nothing is read, stored or acted on without a valid signature."""
    env.client()
    body = payload(text_msg("m1", "972501234567", "STOP"))
    sig_header = wa.sign(SECRET, b"another body") if sig == "valid-but-for-another-body" else sig
    r = env.post(body, sig=sig_header)
    assert r.status_code == 403
    assert env.db.scalars(select(InboundMessage)).all() == []
    assert env.db.scalars(select(ConsentEvent)).all() == []  # a forged STOP opts nobody out
    assert (
        env.db.execute(
            text("SELECT count(*) FROM audit_entry WHERE action='webhook.rejected'")
        ).scalar_one()
        == 1
    )


def test_the_webhook_is_closed_until_a_secret_is_configured(
    settings: Settings, fresh_db: Engine
) -> None:
    c = TestClient(create_app(settings, fresh_db), base_url="https://testserver")
    assert c.post("/api/webhooks/whatsapp", json={}).status_code == 503


def test_malformed_signed_bodies_are_400(env: Env) -> None:
    assert env.post({}, raw=b"not json").status_code == 400


def test_a_message_from_a_client_goes_to_their_agent(env: Env) -> None:
    agent = env.user("agent")[2]
    c = env.client(agent=agent)
    r = env.post(payload(text_msg("m1", "972501234567", "Can you call me about my policy?")))
    assert r.status_code == 200 and r.json() == {"messages": 1, "statuses": 0}
    inbound = env.db.scalars(select(InboundMessage)).one()
    assert inbound.client_id == c.id and inbound.kind == "text" and "call me" in inbound.body
    item = env.db.scalars(select(InboxItem).where(InboxItem.type == "client_reply")).one()
    assert (item.role, str(item.assignee_id)) == ("agent", agent.id) and item.client_id == c.id
    assert "David Cohen" in item.title and "call me" in item.detail


def test_a_message_from_an_unknown_number_goes_to_the_back_office(env: Env) -> None:
    env.post(payload(text_msg("m1", "972521112222", "Hi, who are you?")))
    item = env.db.scalars(select(InboxItem)).one()
    assert item.role == "backoffice" and item.client_id is None and "+972521112222" in item.title


def test_the_same_message_delivered_twice_is_handled_once(env: Env) -> None:
    env.client()
    p = payload(text_msg("m1", "972501234567", "hello"))
    assert env.post(p).json()["messages"] == 1
    assert env.post(p).json()["messages"] == 0  # Meta retried
    assert len(env.db.scalars(select(InboundMessage)).all()) == 1
    assert len(env.db.scalars(select(InboxItem)).all()) == 1


def test_phone_matching_ignores_formatting(env: Env) -> None:
    c = env.client(phone="+972 (50) 123-4567")
    env.post(payload(text_msg("m1", "972501234567", "hi")))
    assert env.db.scalars(select(InboundMessage)).one().client_id == c.id


def test_stop_opts_the_client_out_at_once_and_blocks_the_next_send(env: Env) -> None:
    c = env.client()
    env.consent(c, "opt_in")
    env.post(payload(text_msg("m1", "972501234567", "STOP")))
    ev = env.db.scalars(select(ConsentEvent).order_by(ConsentEvent.recorded_at.desc())).first()
    assert ev is not None and (ev.state, ev.channel, ev.late) == ("opt_out", "whatsapp", False)
    assert ev.source == "WhatsApp STOP reply"
    assert env.db.scalars(select(InboxItem).where(InboxItem.type == "client_reply")).all() == []
    mgr, hdr, _ = env.user("manager")
    r = mgr.post(
        "/api/messages",
        headers=hdr,
        json={
            "client_id": str(c.id),
            "channel": "whatsapp",
            "purpose": "marketing",
            "body": "offer",
        },
    )
    assert r.status_code == 409 and r.json()["detail"] == "opted_out"


def test_a_stop_that_was_sent_long_ago_is_flagged_late_to_the_manager(env: Env) -> None:
    env.client()
    old = datetime.now(UTC) - timedelta(minutes=10)
    env.post(payload(text_msg("m1", "972501234567", "הפסק", ts=old)))
    ev = env.db.scalars(select(ConsentEvent)).one()
    assert ev.late is True
    item = env.db.scalars(select(InboxItem).where(InboxItem.type == "consent_late")).one()
    assert item.role == "manager" and item.urgency == "urgent"


def test_a_stop_from_an_unknown_number_alerts_marketing(env: Env) -> None:
    env.post(payload(text_msg("m1", "972529998888", "stop")))
    item = env.db.scalars(select(InboxItem)).one()
    assert (item.type, item.role, item.urgency) == ("opt_out", "marketing", "urgent")
    assert env.db.scalars(select(ConsentEvent)).all() == []


def test_a_photo_of_an_id_enters_the_intake_pipeline(env: Env) -> None:
    c = env.client()
    env.fake.media["media1"] = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, "image/png")
    env.post(
        payload(
            {
                "id": "m9",
                "from": "972501234567",
                "type": "image",
                "image": {"id": "media1", "caption": "my id card"},
                "timestamp": "1700000000",
            }
        )
    )
    item = env.db.scalars(select(IntakeItem)).one()
    assert (item.channel, item.sender, item.state) == ("whatsapp", "+972501234567", "received")
    assert env.db.scalars(select(queue.Job).where(queue.Job.kind == "intake.run")).one()
    reply = env.db.scalars(select(InboxItem).where(InboxItem.type == "client_reply")).one()
    assert reply.client_id == c.id and "my id card" in reply.detail
    call = env.db.scalars(
        select(AdapterCall).where(AdapterCall.operation == "download_media")
    ).one()
    assert call.ok and call.target == "media1"


def test_a_missing_attachment_does_not_lose_the_message_and_tells_the_back_office(
    env: Env,
) -> None:
    env.client()
    env.post(
        payload(
            {
                "id": "m9",
                "from": "972501234567",
                "type": "image",
                "image": {"id": "nope"},
                "timestamp": "1700000000",
            }
        )
    )
    assert env.db.scalars(select(InboundMessage)).one().kind == "media"
    assert env.db.scalars(select(IntakeItem)).all() == []
    assert any("attachment" in i.title for i in env.db.scalars(select(InboxItem)))
    assert env.db.scalars(select(AdapterCall).where(AdapterCall.ok.is_(False))).one()


def test_delivery_statuses_update_the_message_and_failures_alert(env: Env) -> None:
    c = env.client()
    mid = env.send(c)
    env.drain()
    pid = env.message(mid).provider_id
    assert pid
    env.post(payload(statuses=[{"id": pid, "status": "delivered"}]))
    assert env.message(mid).delivery == "delivered"
    env.post(payload(statuses=[{"id": pid, "status": "read"}, {"id": "unknown", "status": "read"}]))
    assert env.message(mid).delivery == "read" and env.message(mid).status == "sent"
    env.post(payload(statuses=[{"id": pid, "status": "failed"}]))
    assert env.message(mid).status == "failed"
    assert env.db.scalars(select(InboxItem).where(InboxItem.type == "send_failure")).one()
