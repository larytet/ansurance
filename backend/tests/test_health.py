"""T-M0-1, T-M0-5."""

import json
import logging

from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine

from app.config import Profile, Settings
from app.main import create_app


def test_health_ok(settings: Settings, engine: Engine) -> None:
    client = TestClient(create_app(settings, engine))
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["profile"] == "demo" and body["version"]


def test_ready_when_database_up(settings: Settings, engine: Engine) -> None:
    client = TestClient(create_app(settings, engine))
    assert client.get("/api/ready").status_code == 200


def test_ready_503_when_database_down(settings: Settings) -> None:
    dead = create_engine(
        "postgresql+psycopg://x:y@127.0.0.1:1/none", connect_args={"connect_timeout": 1}
    )
    client = TestClient(create_app(settings, dead))
    r = client.get("/api/ready")
    assert r.status_code == 503
    assert client.get("/api/health").status_code == 200  # liveness independent of the DB


def test_request_id_generated_and_echoed(settings: Settings, engine: Engine) -> None:
    client = TestClient(create_app(settings, engine))
    assert len(client.get("/api/health").headers["x-request-id"]) >= 16
    assert (
        client.get("/api/health", headers={"x-request-id": "abc-123"}).headers["x-request-id"]
        == "abc-123"
    )


def test_logs_are_json_with_request_id(settings: Settings, engine: Engine, caplog: object) -> None:
    from app.logging import JsonFormatter, request_id_var

    token = request_id_var.set("rid-1")
    try:
        rec = logging.LogRecord("t", logging.INFO, __file__, 1, "hello %s", ("x",), None)
        out = json.loads(JsonFormatter().format(rec))
    finally:
        request_id_var.reset(token)
    assert out["msg"] == "hello x" and out["request_id"] == "rid-1" and out["level"] == "INFO"


def test_profile_enum_values() -> None:
    assert {p.value for p in Profile} == {"onprem", "cloud", "demo"}
