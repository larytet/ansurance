"""Screens support: the conversation with a client, and which integrations are configured."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.adapters.antivirus import FakeAntivirus
from app.blob.fake import FakeBlobStore
from app.config import Settings
from app.consent import service as consent
from app.integrations.whatsapp import FakeWhatsApp
from app.main import create_app
from app.models import AdapterCall, Client, InboundMessage, SyncState
from app.text import build_search
from tests.conftest import login, make_account


@pytest.fixture
def api(settings: Settings, fresh_db: Engine) -> TestClient:
    app = create_app(
        settings,
        fresh_db,
        blobs=FakeBlobStore(),
        antivirus=FakeAntivirus(),
        whatsapp=FakeWhatsApp(),
    )
    return TestClient(app, base_url="https://testserver")


def as_role(api: TestClient, db: Session, role: str) -> tuple[TestClient, dict[str, str], str]:
    c = TestClient(api.app, base_url="https://testserver")  # type: ignore[arg-type]
    a = make_account(db, f"{role}@agency.il", [role])
    return c, login(c, db, a), a.id


def test_the_conversation_lists_both_directions_and_blocked_sends(
    api: TestClient, db: Session
) -> None:
    c, h, uid = as_role(api, db, "agent")
    plain, skel = build_search("Dana Levi", "000000026")
    cl = Client(
        id_number="000000026",
        name="Dana Levi",
        search_text=plain,
        search_skel=skel,
        phone="050-1234567",
        agent_id=uid,
    )  # type: ignore[arg-type]
    db.add(cl)
    db.flush()
    consent.record(db, cl, "whatsapp", "opt_in", "test", None, None)
    db.add(
        InboundMessage(
            channel="whatsapp",
            provider_id="m1",
            from_address="972501234567",
            client_id=cl.id,
            kind="text",
            body="hello",
            received_at=datetime.now(UTC),
        )
    )
    db.commit()
    assert (
        c.post(
            "/api/messages",
            headers=h,
            json={
                "client_id": str(cl.id),
                "channel": "whatsapp",
                "purpose": "service",
                "body": "your policy",
            },
        ).status_code
        == 201
    )
    consent.record(db, cl, "whatsapp", "opt_out", "STOP", None, None)
    db.commit()
    assert (
        c.post(
            "/api/messages",
            headers=h,
            json={
                "client_id": str(cl.id),
                "channel": "whatsapp",
                "purpose": "marketing",
                "body": "offer",
            },
        ).status_code
        == 409
    )
    rows = c.get(f"/api/clients/{cl.id}/messages").json()
    assert sorted(
        (r["direction"], r.get("status", r.get("kind")), r.get("block_reason")) for r in rows
    ) == [("in", "text", None), ("out", "blocked", "opted_out"), ("out", "queued", None)]
    # another agent cannot read it, and marketing has no client.read
    c2, _, _ = as_role(api, db, "backoffice")
    assert (
        c2.get(f"/api/clients/{cl.id}/messages").status_code == 200
    )  # back-office sees every client
    mk, _, _ = as_role(api, db, "marketing")
    assert mk.get(f"/api/clients/{cl.id}/messages").status_code == 403


def test_integration_status_shows_configuration_calls_and_no_secrets(
    api: TestClient, db: Session
) -> None:
    c, _, _ = as_role(api, db, "manager")
    db.add(
        AdapterCall(
            adapter="whatsapp", operation="send_text", target="+972", ok=True, duration_ms=5
        )
    )
    db.add(
        AdapterCall(
            adapter="whatsapp",
            operation="send_text",
            target="+972",
            ok=False,
            duration_ms=5,
            error="ConnectionError: down",
        )
    )
    db.add(SyncState(system="agency", cursor="x", last_run=datetime.now(UTC), last_result={}))
    db.commit()
    r = c.get("/api/integrations/status")
    assert r.status_code == 200
    by = {a["name"]: a for a in r.json()["adapters"]}
    assert by["whatsapp"] == {
        "name": "whatsapp",
        "configured": True,
        "approved": True,
        "calls_24h": {"ok": 1, "failed": 1},
        "last_error": "ConnectionError: down",
    }
    assert by["imap"]["configured"] is False and by["esign"]["configured"] is True
    assert [s["system"] for s in r.json()["syncs"]] == ["agency"]
    assert "token" not in r.text.lower() and "secret" not in r.text.lower()
    a, _, _ = as_role(api, db, "agent")
    assert a.get("/api/integrations/status").status_code == 403
