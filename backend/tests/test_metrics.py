"""T-M7-5 (observability): the Prometheus endpoint and the metrics the alerts depend on."""

import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app import ops
from app.config import Settings
from app.jobs import queue
from app.main import create_app
from tests.conftest import login, make_account


@pytest.fixture
def client(settings: Settings, fresh_db: Engine) -> TestClient:
    return TestClient(create_app(settings, fresh_db), base_url="https://testserver")


def value(body: str, name: str, labels: str = "") -> float | None:
    m = re.search(rf"^{re.escape(name)}{re.escape(labels)} ([0-9.e+-]+)$", body, re.M)
    return float(m.group(1)) if m else None


def test_metrics_are_served_as_prometheus_text_without_a_login(client: TestClient) -> None:
    r = client.get("/metrics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert "# TYPE ansurance_http_requests_total counter" in r.text
    assert value(r.text, "ansurance_database_up") == 1.0


def test_it_is_not_part_of_the_documented_api(client: TestClient) -> None:
    schema = client.get("/api/openapi.json").json()
    assert "/metrics" not in schema["paths"]


def test_requests_are_counted_by_route_template_never_by_raw_path(
    client: TestClient, db: Session
) -> None:
    login(client, db, make_account(db, "ann@agency.il", ["agent"]))
    client.get("/api/health")
    client.get("/api/clients/00000000-0000-0000-0000-000000000001")
    client.get("/api/clients/00000000-0000-0000-0000-000000000002")
    body = client.get("/metrics").text
    assert 'route="/api/clients/{client_id}"' in body  # one series, not one per id
    assert "00000000-0000-0000-0000-000000000001" not in body
    assert (
        value(
            body, "ansurance_http_requests_total", '{method="GET",route="/api/health",status="200"}'
        )
        == 1.0
    )
    assert 'status="404"' in body
    assert "ansurance_http_request_duration_seconds_bucket" in body


def test_queue_depth_and_the_age_of_the_oldest_waiting_job(client: TestClient, db: Session) -> None:
    body = client.get("/metrics").text
    assert value(body, "ansurance_jobs_oldest_queued_seconds") == 0.0
    queue.enqueue(db, "t", {}, run_at=datetime.now(UTC) - timedelta(minutes=20))
    queue.enqueue(db, "t", {})
    db.commit()
    body = client.get("/metrics").text
    assert value(body, "ansurance_jobs", '{status="queued"}') == 2.0
    assert 1190 < (value(body, "ansurance_jobs_oldest_queued_seconds") or 0) < 1300  # about 20 min


def test_intake_and_inbox_counts(client: TestClient, db: Session) -> None:
    db.execute(
        text(
            "INSERT INTO inbox_item (id, type, role, title, detail, urgency, status) "
            "VALUES (gen_random_uuid(), 'other', 'manager', 'x', '', 'normal', 'open')"
        )
    )
    db.commit()
    body = client.get("/metrics").text
    assert value(body, "ansurance_inbox_open", '{role="manager"}') == 1.0
    assert value(body, "ansurance_intake_misfiled_30d") == 0.0


def test_audit_and_backup_freshness_gauges(client: TestClient, db: Session) -> None:
    body = client.get("/metrics").text
    assert value(body, "ansurance_audit_verify_ok") is None  # nothing recorded yet: no sample
    assert value(body, "ansurance_backup_age_seconds") is None
    now = datetime.now(UTC)
    ops.set_info(db, "audit.last_verify", f"{(now - timedelta(hours=2)).isoformat()}|ok")
    ops.set_info(db, "backup.last_success", (now - timedelta(hours=30)).isoformat())
    ops.set_info(db, "backup.last_restore_test", f"{now.isoformat()}|FAILED:dump checksum")
    db.commit()
    body = client.get("/metrics").text
    assert value(body, "ansurance_audit_verify_ok") == 1.0
    assert 7100 < (value(body, "ansurance_audit_verify_age_seconds") or 0) < 7400
    assert 107_000 < (value(body, "ansurance_backup_age_seconds") or 0) < 109_000
    assert value(body, "ansurance_restore_test_ok") == 0.0  # a failed self-test is visible
    ops.set_info(db, "audit.last_verify", f"{now.isoformat()}|FAILED")
    db.commit()
    assert value(client.get("/metrics").text, "ansurance_audit_verify_ok") == 0.0


def test_read_audit_buffer_state_is_exposed(client: TestClient, db: Session) -> None:
    login(client, db, make_account(db, "ann@agency.il", ["agent"]))
    client.app.state.read_audit.record("u", "client.read", "client:1")  # type: ignore[attr-defined]
    body = client.get("/metrics").text
    assert value(body, "ansurance_read_audit_pending") == 1.0
    assert value(body, "ansurance_read_audit_dropped_total") == 0.0


def test_a_broken_database_does_not_break_the_endpoint(settings: Settings) -> None:
    from sqlalchemy import create_engine

    dead = create_engine(
        "postgresql+psycopg://x:y@127.0.0.1:1/none", connect_args={"connect_timeout": 1}
    )
    c = TestClient(create_app(settings, dead), base_url="https://testserver")
    r = c.get("/metrics")
    assert r.status_code == 200 and value(r.text, "ansurance_database_up") == 0.0
