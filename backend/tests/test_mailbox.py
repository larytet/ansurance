"""M8: mailbox ingestion. The IMAP adapter against a stand-in connection, and polling."""

import email.message
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.adapters.antivirus import FakeAntivirus
from app.blob.fake import FakeBlobStore
from app.config import Settings
from app.intake.context import IntakeContext
from app.intake.ocr import FakeOcr
from app.integrations.calls import AdapterNotApproved
from app.integrations.mailbox import FakeMailbox, ImapMailbox, message_id, sender_and_body
from app.integrations.mailbox_poll import poll
from app.main import make_mailbox
from app.models import AdapterCall, Client, InboundMessage, InboxItem, IntakeItem, MailSeen, User
from app.text import build_search
from tests.sandbox_inline import InlineSandbox

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def mail(
    frm: str = "David <david@x.il>",
    subject: str = "my id",
    body: str = "see attached",
    mid: str | None = "<m1@x.il>",
    attach: bool = True,
    html: bool = False,
) -> bytes:
    m = email.message.EmailMessage()
    m["From"], m["To"], m["Subject"] = frm, "office@agency.il", subject
    if mid:
        m["Message-ID"] = mid
    if html:  # an HTML-only message
        m.set_content(f"<html><body><p>{body}</p></body></html>", subtype="html")
    else:
        m.set_content(body)
    if attach:
        m.add_attachment(PNG, maintype="image", subtype="png", filename="id.png")
    return m.as_bytes()


@pytest.fixture
def ctx(settings: Settings, fresh_db: Engine) -> IntakeContext:
    return IntakeContext(settings, FakeBlobStore(), FakeAntivirus(), FakeOcr(), InlineSandbox())


def client(db: Session, email_addr: str = "david@x.il", agent: User | None = None) -> Client:
    plain, skel = build_search("David Cohen", "123456782")
    c = Client(
        id_number="123456782",
        name="David Cohen",
        search_text=plain,
        search_skel=skel,
        email=email_addr,
        agent_id=agent.id if agent else None,
    )
    db.add(c)
    db.commit()
    return c


# ---- parsing --------------------------------------------------------------------------------------
def test_message_id_and_fallback() -> None:
    assert message_id(mail(mid="<abc@x>")) == "<abc@x>"
    raw = mail(mid=None)  # no Message-ID: the content identifies the message
    assert message_id(raw).startswith("sha256:") and message_id(raw) == message_id(raw)


def test_sender_subject_and_body_preview_including_html() -> None:
    assert sender_and_body(mail(frm="Ann B <ANN@X.IL>", subject="hi", body="hello world")) == (
        "ann@x.il",
        "hi",
        "hello world\n".strip(),
    )
    sender, subject, body = sender_and_body(mail(body="<b>bold</b> text", html=True, attach=False))
    assert "bold" in body and "<" not in body
    assert sender_and_body(b"Subject: x\r\n\r\n")[0] == ""


# ---- polling -----------------------------------------------------------------------------------------
def test_new_mail_attachments_enter_intake_and_the_agent_is_told(
    db: Session, ctx: IntakeContext
) -> None:
    agent = User(email="a@agency.il", display_name="A", auth_mode="sso")
    db.add(agent)
    db.flush()
    c = client(db, agent=agent)
    mb = FakeMailbox(mail())
    res = poll(db, ctx, mb)
    assert res == {"ingested": 1, "skipped": 0, "failed": 0} and mb.seen == ["1"]
    item = db.scalars(select(IntakeItem)).one()
    assert (item.channel, item.sender, item.filename) == ("email", "david@x.il", "id.png")
    inbound = db.scalars(select(InboundMessage)).one()
    assert inbound.client_id == c.id and "my id" in inbound.body and inbound.channel == "email"
    reply = db.scalars(select(InboxItem)).one()
    assert (reply.role, reply.assignee_id, reply.type) == ("agent", agent.id, "client_reply")
    assert "David Cohen" in reply.title and "1 attachment" in reply.title
    assert db.get(MailSeen, "<m1@x.il>") is not None
    assert db.scalars(select(AdapterCall)).one().operation == "fetch_new"


def test_mail_without_attachments_is_still_a_task(db: Session, ctx: IntakeContext) -> None:
    poll(db, ctx, FakeMailbox(mail(attach=False, subject="Question", body="When is my renewal?")))
    assert db.scalars(select(IntakeItem)).all() == []
    item = db.scalars(select(InboxItem)).one()
    assert item.role == "backoffice" and "david@x.il" in item.title  # unknown sender
    assert item.detail == "Question"


def test_known_sender_match_is_case_insensitive(db: Session, ctx: IntakeContext) -> None:
    c = client(db, email_addr="David@X.IL")
    poll(db, ctx, FakeMailbox(mail(frm="david@x.il")))
    assert db.scalars(select(InboundMessage)).one().client_id == c.id


def test_a_message_is_never_ingested_twice(db: Session, ctx: IntakeContext) -> None:
    mb = FakeMailbox(mail())
    poll(db, ctx, mb)
    mb.seen.clear()  # the server forgot the Seen flag (or it never reached it)
    res = poll(db, ctx, mb)
    assert res == {"ingested": 0, "skipped": 1, "failed": 0} and mb.seen == ["1"]
    assert len(db.scalars(select(IntakeItem)).all()) == 1
    assert len(db.scalars(select(InboxItem)).all()) == 1


def test_two_different_messages_are_both_taken(db: Session, ctx: IntakeContext) -> None:
    res = poll(db, ctx, FakeMailbox(mail(mid="<a@x>"), mail(mid="<b@x>", subject="other")))
    assert res["ingested"] == 2 and len(db.scalars(select(InboxItem)).all()) == 2


def test_a_message_that_cannot_be_ingested_stays_unread_and_the_others_continue(
    db: Session, ctx: IntakeContext
) -> None:
    mb = FakeMailbox(mail(mid="<bad@x>", attach=False), mail(mid="<good@x>", attach=False))
    from app.intake import pipeline

    real = pipeline.submit

    def flaky(*a: Any, **kw: Any) -> Any:
        if b"<bad@x>" in kw["data"]:
            raise RuntimeError("storage down")
        return real(*a, **kw)

    pipeline.submit = flaky  # type: ignore[assignment]
    try:
        res = poll(db, ctx, mb)
    finally:
        pipeline.submit = real  # type: ignore[assignment]
    assert res == {"ingested": 1, "skipped": 0, "failed": 1}
    assert mb.seen == ["2"]  # only the good one is marked read: the bad one is retried
    assert db.get(MailSeen, "<bad@x>") is None and db.get(MailSeen, "<good@x>") is not None
    res = poll(db, ctx, mb)
    assert res["ingested"] == 1 and mb.seen == ["2", "1"]  # the retry succeeds


def test_a_mail_server_outage_is_logged_and_raised(
    db: Session, ctx: IntakeContext, fresh_db: Engine
) -> None:
    mb = FakeMailbox()
    mb.fail_fetch = OSError("connection refused")
    with pytest.raises(OSError):
        poll(db, ctx, mb)
    call = db.scalars(select(AdapterCall)).one()
    assert call.ok is False and "connection refused" in (call.error or "")


# ---- the IMAP adapter against a stand-in connection ---------------------------------------------------------
class FakeImap:
    """Just enough of imaplib.IMAP4_SSL for the calls the adapter makes."""

    log: list[tuple[Any, ...]] = []
    inbox: dict[str, bytes] = {}
    fail_login = False
    folder_ok = True

    def __init__(self, host: str, port: int) -> None:
        FakeImap.log.append(("connect", host, port))

    def login(self, user: str, password: str) -> None:
        if FakeImap.fail_login:
            raise OSError("auth failed")
        FakeImap.log.append(("login", user, password))

    def select(self, folder: str) -> tuple[str, list[bytes]]:
        FakeImap.log.append(("select", folder))
        return ("OK", [b"1"]) if FakeImap.folder_ok else ("NO", [b""])

    def uid(self, command: str, *args: str) -> tuple[str, list[Any]]:
        FakeImap.log.append((command, *args))
        if command == "SEARCH":
            return "OK", [b" ".join(k.encode() for k in FakeImap.inbox)]
        if command == "FETCH":
            return "OK", [(f"{args[0]} (BODY[] {{n}}".encode(), FakeImap.inbox[args[0]]), b")"]
        return "OK", [b""]

    def logout(self) -> None:
        FakeImap.log.append(("logout",))


@pytest.fixture(autouse=True)
def reset_imap() -> None:
    FakeImap.log, FakeImap.inbox, FakeImap.fail_login, FakeImap.folder_ok = [], {}, False, True


def test_imap_fetches_unseen_with_peek_and_flags_only_on_request() -> None:
    FakeImap.inbox = {"7": mail(mid="<x7@x>"), "9": mail(mid="<x9@x>")}
    mb = ImapMailbox("imap.test", 993, "u", "pw", "INBOX", connect=FakeImap)  # type: ignore[arg-type]
    got = mb.fetch_new()
    assert [m.uid for m in got] == ["7", "9"] and message_id(got[0].raw) == "<x7@x>"
    assert ("SEARCH", "UNSEEN") in FakeImap.log
    assert ("FETCH", "7", "(BODY.PEEK[])") in FakeImap.log  # reading does not mark anything read
    assert not any(c[0] == "STORE" for c in FakeImap.log)
    mb.mark_seen("7")
    assert ("STORE", "7", "+FLAGS", "(\\Seen)") in FakeImap.log
    assert [c for c in FakeImap.log if c[0] == "connect"] == [
        ("connect", "imap.test", 993)
    ]  # reused
    mb.close()
    assert FakeImap.log[-1] == ("logout",)


def test_imap_empty_inbox_and_limit() -> None:
    mb = ImapMailbox("h", 993, "u", "p", connect=FakeImap)  # type: ignore[arg-type]
    assert mb.fetch_new() == []
    FakeImap.inbox = {str(i): mail(mid=f"<{i}@x>") for i in range(1, 8)}
    assert len(mb.fetch_new(limit=3)) == 3


def test_imap_login_and_folder_failures_raise() -> None:
    FakeImap.fail_login = True
    with pytest.raises(OSError):
        ImapMailbox("h", 993, "u", "p", connect=FakeImap).fetch_new()  # type: ignore[arg-type]
    FakeImap.fail_login, FakeImap.folder_ok = False, False
    with pytest.raises(OSError, match="cannot open folder"):
        ImapMailbox("h", 993, "u", "p", "Nope", connect=FakeImap).fetch_new()  # type: ignore[arg-type]


# ---- configuration -----------------------------------------------------------------------------------------------
def test_mailbox_is_off_until_configured_and_must_be_approved(settings: Settings) -> None:
    assert make_mailbox(settings) is None
    cfg = settings.model_copy(
        update={"imap_host": "imap.test", "imap_user": "u", "imap_password": SecretStr("p")}
    )
    assert isinstance(make_mailbox(cfg), ImapMailbox)
    with pytest.raises(AdapterNotApproved):
        make_mailbox(cfg.model_copy(update={"approved_adapters": ["whatsapp"]}))
