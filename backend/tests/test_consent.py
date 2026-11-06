"""T-M3-3, T-M3-4, T-M3-5. Consent registry and the send gate."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session

from app.config import Settings
from app.main import create_app
from tests.conftest import Account, login, make_account


class Env:
    def __init__(self, app: Any, db: Session) -> None:
        self.app, self.db = app, db

    def user(self, role: str, n: int = 1) -> tuple[TestClient, dict[str, str], Account]:
        a = make_account(self.db, f"{role}{n}@agency.il", [role])
        c = TestClient(self.app, base_url="https://testserver")
        return c, login(c, self.db, a), a


@pytest.fixture
def env(settings: Settings, fresh_db: Engine, db: Session) -> Env:
    return Env(create_app(settings, fresh_db), db)


def client_for(c: TestClient, h: dict[str, str], id_number: str = "123456782") -> str:
    r = c.post(
        "/api/clients",
        headers=h,
        json={"id_number": id_number, "name": "David Cohen", "preferred_language": "ru"},
    )
    return str(r.json()["id"])


def consent(c: TestClient, h: dict[str, str], cid: str, channel: str, state: str, **kw: Any) -> Any:
    return c.post(
        f"/api/clients/{cid}/consent",
        headers=h,
        json={"channel": channel, "state": state, "source": "web form", **kw},
    )


def send(
    c: TestClient, h: dict[str, str], cid: str, purpose: str, channel: str = "whatsapp"
) -> Any:
    return c.post(
        "/api/messages",
        headers=h,
        json={"client_id": cid, "channel": channel, "purpose": purpose, "body": "Hello"},
    )


# registry ----------------------------------------------------------------------------------
def test_no_consent_by_default_and_latest_event_wins(env: Env) -> None:
    c, h, _ = env.user("agent")
    cid = client_for(c, h)
    assert c.get(f"/api/clients/{cid}/consent").json()["current"] == {}
    assert consent(c, h, cid, "whatsapp", "opt_in").status_code == 201
    assert c.get(f"/api/clients/{cid}/consent").json()["current"] == {"whatsapp": "opt_in"}
    assert consent(c, h, cid, "whatsapp", "opt_out").status_code == 201
    assert consent(c, h, cid, "email", "opt_in").status_code == 201
    got = c.get(f"/api/clients/{cid}/consent").json()
    assert got["current"] == {"whatsapp": "opt_out", "email": "opt_in"}
    assert [e["state"] for e in got["history"]] == ["opt_in", "opt_out", "opt_in"]


def test_consent_registry_is_append_only(env: Env) -> None:
    c, h, _ = env.user("agent")
    cid = client_for(c, h)
    consent(c, h, cid, "email", "opt_in")
    for sql in (
        "UPDATE consent_event SET state = 'opt_in'",
        "DELETE FROM consent_event",
        "TRUNCATE consent_event",
    ):
        with pytest.raises(DatabaseError, match="append-only"):
            env.db.execute(text(sql))
        env.db.rollback()


def test_validation(env: Env) -> None:
    c, h, _ = env.user("agent")
    cid = client_for(c, h)
    assert consent(c, h, cid, "pigeon", "opt_in").status_code == 422
    assert consent(c, h, cid, "email", "maybe").status_code == 422
    assert (
        c.post(
            f"/api/clients/{cid}/consent",
            headers=h,
            json={"channel": "email", "state": "opt_in", "source": "x"},
        ).status_code
        == 422
    )


def test_agents_cannot_touch_another_agents_client_consent(env: Env) -> None:
    c1, h1, _ = env.user("agent", 1)
    c2, h2, _ = env.user("agent", 2)
    cid = client_for(c1, h1)
    assert consent(c2, h2, cid, "email", "opt_in").status_code == 404
    assert c2.get(f"/api/clients/{cid}/consent").status_code == 404


def test_consent_changes_are_audited(env: Env) -> None:
    c, h, a = env.user("agent")
    cid = client_for(c, h)
    consent(c, h, cid, "sms", "opt_out")
    row = env.db.execute(
        text("SELECT actor, detail FROM audit_entry WHERE action='consent.record'")
    ).one()
    assert row[0] == a.id and row[1]["channel"] == "sms" and row[1]["state"] == "opt_out"


# one-minute opt-out rule ----------------------------------------------------------------------
def test_opt_out_is_effective_immediately(env: Env) -> None:
    c, h, _ = env.user("agent")
    cm, hm, _ = env.user("marketing")
    cid = client_for(c, h)
    consent(c, h, cid, "whatsapp", "opt_in")
    assert send(c, h, cid, "marketing").status_code == 201
    got = consent(cm, hm, cid, "whatsapp", "opt_out")  # processed by marketing
    assert got.status_code == 201 and got.json()["late"] is False
    assert send(c, h, cid, "marketing").status_code == 409  # the very next send is blocked


def test_late_opt_out_is_flagged_and_reaches_the_manager(env: Env) -> None:
    c, h, _ = env.user("agent")
    cm, hm, _ = env.user("manager")
    cid = client_for(c, h)
    recent = (datetime.now(UTC) - timedelta(seconds=30)).isoformat()
    assert consent(c, h, cid, "email", "opt_out", received_at=recent).json()["late"] is False
    old = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    r = consent(c, h, cid, "whatsapp", "opt_out", received_at=old)
    assert r.json()["late"] is True
    items = cm.get("/api/inbox").json()["items"]
    assert len(items) == 1 and items[0]["type"] == "consent_late"
    assert items[0]["urgency"] == "urgent" and items[0]["client_id"] == cid
    # an old opt-IN is not a deadline breach
    assert consent(c, h, cid, "sms", "opt_in", received_at=old).json()["late"] is False


# T-M3-4: the gate --------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "state,purpose,allowed,reason",
    [
        (None, "marketing", False, "no_consent"),
        ("opt_in", "marketing", True, ""),
        ("opt_out", "marketing", False, "opted_out"),
        (None, "service", True, ""),
        ("opt_in", "service", True, ""),
        ("opt_out", "service", False, "opted_out"),
        (None, "legal", True, ""),
        ("opt_out", "legal", True, ""),
    ],
)
def test_gate_matrix(env: Env, state: str | None, purpose: str, allowed: bool, reason: str) -> None:
    c, h, _ = env.user("agent")
    cid = client_for(c, h)
    if state:
        consent(c, h, cid, "whatsapp", state)
    r = send(c, h, cid, purpose)
    assert (r.status_code == 201) is allowed, r.text
    if allowed:
        assert r.json()["status"] == "queued"
        assert r.json()["legal_override"] is (state == "opt_out")
    else:
        assert r.status_code == 409 and r.json()["detail"] == reason


def test_consent_is_per_channel(env: Env) -> None:
    c, h, _ = env.user("agent")
    cid = client_for(c, h)
    consent(c, h, cid, "whatsapp", "opt_out")
    consent(c, h, cid, "email", "opt_in")
    assert send(c, h, cid, "marketing", "whatsapp").status_code == 409
    assert send(c, h, cid, "marketing", "email").status_code == 201
    assert send(c, h, cid, "marketing", "sms").status_code == 409  # never asked


def test_blocked_sends_are_recorded_and_audited_and_nothing_is_queued(env: Env) -> None:
    c, h, _ = env.user("agent")
    cid = client_for(c, h)
    consent(c, h, cid, "whatsapp", "opt_out")
    send(c, h, cid, "marketing")
    send(c, h, cid, "service")
    rows = env.db.execute(text("SELECT status, block_reason FROM outbox_message ORDER BY id")).all()
    assert [r[0] for r in rows] == ["blocked", "blocked"]
    assert {r[1] for r in rows} == {"opted_out"}
    assert (
        env.db.execute(
            text("SELECT count(*) FROM audit_entry WHERE action='message.blocked'")
        ).scalar_one()
        == 2
    )
    assert (
        env.db.execute(
            text("SELECT count(*) FROM outbox_message WHERE status='queued'")
        ).scalar_one()
        == 0
    )


def test_message_uses_the_clients_language_and_validates(env: Env) -> None:
    c, h, _ = env.user("agent")
    cid = client_for(c, h)
    ok = send(c, h, cid, "service")
    assert ok.status_code == 201 and ok.json()["language"] == "ru"
    bad = c.post(
        "/api/messages",
        headers=h,
        json={"client_id": cid, "channel": "fax", "purpose": "service", "body": "x"},
    )
    assert bad.status_code == 422
    other = c.post(
        "/api/messages",
        headers=h,
        json={"client_id": cid, "channel": "sms", "purpose": "bribe", "body": "x"},
    )
    assert other.status_code == 422


def test_marketing_can_only_send_marketing_and_still_hits_the_gate(env: Env) -> None:
    c, h, _ = env.user("agent")
    cm, hm, _ = env.user("marketing")
    cid = client_for(c, h)
    assert send(cm, hm, cid, "service").status_code == 403
    assert send(cm, hm, cid, "marketing").status_code == 409  # no consent yet
    consent(c, h, cid, "whatsapp", "opt_in")
    assert send(cm, hm, cid, "marketing").status_code == 201


def test_agent_cannot_message_another_agents_client(env: Env) -> None:
    c1, h1, _ = env.user("agent", 1)
    c2, h2, _ = env.user("agent", 2)
    cid = client_for(c1, h1)
    assert send(c2, h2, cid, "service").status_code == 404


# T-M3-5: marketing projection carries consent and nothing sensitive
def test_marketing_projection_shows_consent_state(env: Env) -> None:
    c, h, _ = env.user("agent")
    cm, hm, _ = env.user("marketing")
    cid = client_for(c, h)
    consent(c, h, cid, "whatsapp", "opt_in")
    consent(c, h, cid, "email", "opt_out")
    row = cm.get("/api/marketing/contacts").json()[0]
    assert (row["consent_whatsapp"], row["consent_email"], row["consent_sms"]) == (
        "opt_in",
        "opt_out",
        "none",
    )
    assert "id_number" not in row and "agent_id" not in row
