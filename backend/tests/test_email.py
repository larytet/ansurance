"""E-mail delivery: the same queue, consent re-check and retry rules as WhatsApp, over SMTP.
The SMTP client is tested against a small local server (no real mail leaves the machine)."""

import socketserver
import threading
from datetime import UTC, datetime
from email import message_from_bytes
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.consent import service as consent
from app.integrations.smtp import FakeMailer, MailError, SmtpMailer
from app.jobs import queue
from app.messaging.delivery import RetryDelivery, deliver, subject_for
from app.messaging.handlers import make_delivery_handlers
from app.models import Client, OutboxMessage
from app.text import build_search
from tests.conftest import make_account


# ---- a tiny SMTP server ---------------------------------------------------------------------------
class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        srv: Any = self.server
        self.wfile.write(b"220 stub\r\n")
        data_mode, buf = False, []
        for raw in self.rfile:
            line = raw.rstrip(b"\r\n")
            if data_mode:
                if line == b".":
                    srv.messages.append(b"\r\n".join(buf))
                    data_mode, buf = False, []
                    self.wfile.write(b"250 queued\r\n")
                else:
                    buf.append(line[1:] if line.startswith(b"..") else line)
                continue
            cmd = line.split(b" ")[0].upper()
            if cmd in (b"EHLO", b"HELO"):
                self.wfile.write(b"250 stub\r\n")
            elif cmd == b"RCPT" and srv.reject:
                self.wfile.write(srv.reject + b"\r\n")
            elif cmd in (b"MAIL", b"RCPT"):
                self.wfile.write(b"250 ok\r\n")
            elif cmd == b"DATA":
                data_mode = True
                self.wfile.write(b"354 go\r\n")
            elif cmd == b"QUIT":
                self.wfile.write(b"221 bye\r\n")
                return
            else:
                self.wfile.write(b"250 ok\r\n")


@pytest.fixture
def smtp_server() -> Any:
    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        messages: list[bytes] = []
        reject: bytes = b""

    srv = Server(("127.0.0.1", 0), _Handler)
    srv.messages = []
    srv.reject = b""
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def test_smtp_mailer_sends_a_proper_message_with_unsubscribe(smtp_server: Any) -> None:
    host, port = smtp_server.server_address
    m = SmtpMailer(host, port, "", "", "agency@agency.il", starttls=False)
    mid = m.send(
        to="dana@x.il",
        subject="פרסומת: offer",
        body="hello\nSTOP to leave",
        unsubscribe="agency@agency.il",
    )
    (raw,) = smtp_server.messages
    msg = message_from_bytes(raw)
    assert (
        msg["To"] == "dana@x.il" and msg["From"] == "agency@agency.il" and msg["Message-ID"] == mid
    )
    assert msg["List-Unsubscribe"] == "<mailto:agency@agency.il?subject=STOP>"
    assert msg["Subject"].startswith("=?utf-8?") or "פרסומת" in msg["Subject"]


def test_smtp_errors_are_classified_for_retry(smtp_server: Any) -> None:
    host, port = smtp_server.server_address
    m = SmtpMailer(host, port, "", "", "agency@agency.il", starttls=False)
    smtp_server.reject = b"550 no such user"
    with pytest.raises(MailError) as e:
        m.send(to="ghost@x.il", subject="s", body="b")
    assert e.value.retryable is False  # a permanent refusal is not retried
    smtp_server.reject = b"451 try later"
    with pytest.raises(MailError) as e2:
        m.send(to="dana@x.il", subject="s", body="b")
    assert e2.value.retryable is True  # a temporary one is
    with pytest.raises(MailError) as e3:
        SmtpMailer("127.0.0.1", 1, "", "", "a@b.c", starttls=False, timeout=1).send(
            to="a@b.c", subject="s", body="b"
        )
    assert e3.value.retryable is True  # the server is down


# ---- delivery ---------------------------------------------------------------------------------------------
def person(db: Session, email: str | None = "dana@x.il", opt_in: bool = True) -> Client:
    plain, skel = build_search("Dana Levi", "000000026")
    c = Client(
        id_number="000000026",
        name="Dana Levi",
        search_text=plain,
        search_skel=skel,
        email=email,
        preferred_language="he",
        status="active",
    )
    db.add(c)
    db.flush()
    if opt_in:
        consent.record(db, c, "email", "opt_in", "test", None, None)
    db.commit()
    return c


def queued(
    db: Session, c: Client, purpose: str = "service", body: str = "Your policy renews soon\nmore"
) -> OutboxMessage:
    uid = (
        db.execute(text("SELECT id FROM user_account LIMIT 1")).scalar()
        or make_account(db, "a@agency.il", ["agent"]).id
    )
    m = OutboxMessage(
        client_id=c.id,
        channel="email",
        purpose=purpose,
        language="he",
        body=body,
        status="queued",
        created_by=uid,
    )  # type: ignore[arg-type]
    db.add(m)
    db.commit()
    return m


def test_a_queued_email_is_sent_and_recorded(
    fresh_db: Engine, db: Session, settings: Settings
) -> None:
    mailer = FakeMailer()
    m = queued(db, person(db))
    assert deliver(db, settings, None, m.id, mailer) == "sent"
    db.commit()
    assert mailer.sent == [
        {
            "to": "dana@x.il",
            "subject": "Your policy renews soon",
            "body": "Your policy renews soon\nmore",
            "unsubscribe": None,
            "id": mailer.sent[0]["id"],
        }
    ]
    db.refresh(m)
    assert (m.status, m.delivery, m.provider_id) == ("sent", "sent", mailer.sent[0]["id"])
    assert db.execute(text("SELECT ok FROM adapter_call WHERE adapter='smtp'")).scalar_one() is True


def test_advertising_carries_the_label_in_the_subject_and_a_list_unsubscribe(
    fresh_db: Engine, db: Session, settings: Settings
) -> None:
    mailer = FakeMailer()
    s = settings.model_copy(update={"smtp_from": "agency@agency.il"})
    m = queued(db, person(db), "marketing", "A cheaper fund\nreply STOP")
    deliver(db, s, None, m.id, mailer)
    assert (
        mailer.sent[0]["subject"] == "פרסומת: A cheaper fund"
        and mailer.sent[0]["unsubscribe"] == "agency@agency.il"
    )
    assert (
        subject_for(OutboxMessage(body="פרסומת: x\nSTOP", purpose="marketing", language="he"))
        == "פרסומת: x"
    )  # not doubled


def test_consent_is_rechecked_at_send_time(
    fresh_db: Engine, db: Session, settings: Settings
) -> None:
    mailer = FakeMailer()
    c = person(db)
    m = queued(db, c, "marketing", "offer")
    consent.record(db, c, "email", "opt_out", "unsubscribe link", datetime.now(UTC), None)
    db.commit()
    assert deliver(db, settings, None, m.id, mailer) == "blocked"
    assert mailer.sent == []


def test_missing_address_fails_and_raises_a_task_and_an_outage_retries(
    fresh_db: Engine, db: Session, settings: Settings
) -> None:
    m = queued(db, person(db, email=None, opt_in=False))
    assert deliver(db, settings, None, m.id, FakeMailer()) == "failed"
    assert (
        db.execute(text("SELECT count(*) FROM inbox_item WHERE type='send_failure'")).scalar_one()
        == 1
    )
    c2 = Client(
        id_number="000000034",
        name="B",
        search_text="b",
        search_skel="b",
        email="b@x.il",
        status="active",
    )
    db.add(c2)
    db.commit()
    m2 = queued_for(db, c2)
    down = FakeMailer(fail_with=MailError("connection refused", retryable=True))
    with pytest.raises(RetryDelivery):
        deliver(db, settings, None, m2.id, down)
    db.refresh(m2)
    assert m2.status == "queued" and m2.attempts == 1
    with pytest.raises(RetryDelivery):  # not configured at all: it waits in the queue
        deliver(db, settings, None, m2.id, None)


def queued_for(db: Session, c: Client) -> OutboxMessage:
    m = OutboxMessage(
        client_id=c.id,
        channel="email",
        purpose="service",
        language="en",
        body="hi",
        status="queued",
        created_by=db.execute(text("SELECT id FROM user_account LIMIT 1")).scalar_one(),
    )
    db.add(m)
    db.commit()
    return m


def test_permanent_refusal_fails_at_once_and_the_queue_job_delivers_email(
    fresh_db: Engine, db: Session, settings: Settings
) -> None:
    c = person(db)
    m = queued(db, c)
    queue.enqueue(db, "message.deliver", {"message_id": str(m.id)}, dedupe_key=f"deliver:{m.id}")
    db.commit()
    mailer = FakeMailer()
    assert queue.run_one(db, make_delivery_handlers(settings, None, mailer))
    db.commit()
    assert len(mailer.sent) == 1
    m2 = queued(db, c)
    refuse = FakeMailer(fail_with=MailError("550 no such user", retryable=False))
    assert deliver(db, settings, None, m2.id, refuse) == "failed"
