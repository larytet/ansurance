"""Desk work areas and F11 (get the ID and register the client)."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.adapters.antivirus import FakeAntivirus
from app.blob.fake import FakeBlobStore
from app.config import Settings
from app.main import create_app
from tests.conftest import Account, login, make_account

PDF = b"%PDF-1.4 id card scan"


class Env:
    def __init__(self, app: Any, db: Session, av: FakeAntivirus) -> None:
        self.app, self.db, self.av = app, db, av

    def user(self, role: str, n: int = 1) -> tuple[TestClient, dict[str, str], Account]:
        a = make_account(self.db, f"{role}{n}@agency.il", [role])
        c = TestClient(self.app, base_url="https://testserver")
        return c, login(c, self.db, a), a


@pytest.fixture
def env(settings: Settings, fresh_db: Engine, db: Session) -> Env:
    av = FakeAntivirus()
    app = create_app(settings, fresh_db, blobs=FakeBlobStore(), antivirus=av)
    return Env(app, db, av)


def lead(c: TestClient, h: dict[str, str]) -> str:
    return str(
        c.post(
            "/api/clients", headers=h, json={"id_number": "123456782", "name": "David Cohen"}
        ).json()["id"]
    )


def upload_id(c: TestClient, h: dict[str, str], cid: str, data: bytes = PDF) -> Any:
    return c.post(
        f"/api/clients/{cid}/documents",
        headers=h,
        files={"file": ("id.pdf", data)},
        data={"doc_type": "id_card"},
    )


def test_f11_full_flow(env: Env) -> None:
    ca, ha, agent = env.user("agent")
    cb, hb, _ = env.user("backoffice")
    cid = lead(ca, ha)
    # 1. the agent asks the back-office to get the ID
    r = ca.post(f"/api/clients/{cid}/request-id", headers=ha)
    assert r.status_code == 201
    assert ca.get(f"/api/clients/{cid}").json()["status"] == "id_pending"
    desk = cb.get("/api/desk").json()
    area = next(a for a in desk["areas"] if a["area"] == "id_registration")
    assert area == {"area": "id_registration", "open": 1, "urgent": 0} and desk["total_open"] == 1
    item = cb.get("/api/inbox").json()["items"][0]
    assert item["type"] == "id_missing" and item["client_id"] == cid
    # 2. registration is refused until a clean ID card is on file
    assert cb.post(f"/api/clients/{cid}/register", headers=hb).json()["detail"] == (
        "clean_id_card_required"
    )
    # 3. the back-office files the ID card and registers
    assert upload_id(cb, hb, cid).status_code == 201
    assert cb.post(f"/api/clients/{cid}/register", headers=hb).json() == {"status": "registered"}
    # 4. the request is closed, the agent is told, the desk is clear again
    assert cb.get("/api/inbox").json()["items"] == []
    done = cb.get("/api/inbox", params={"status": "done"}).json()["items"]
    assert [i["id"] for i in done] == [item["id"]]
    told = ca.get("/api/inbox").json()["items"]
    assert len(told) == 1 and "registered" in told[0]["title"]
    assert (
        next(a for a in cb.get("/api/desk").json()["areas"] if a["area"] == "id_registration")[
            "open"
        ]
        == 0
    )
    assert ca.post(f"/api/clients/{cid}/register", headers=ha).status_code == 403  # not agents


def test_infected_or_unscanned_id_card_does_not_count(env: Env) -> None:
    from app.adapters.antivirus import EICAR

    ca, ha, _ = env.user("agent")
    cb, hb, _ = env.user("backoffice")
    cid = lead(ca, ha)
    assert upload_id(cb, hb, cid, b"%PDF-1.4 " + EICAR).json()["scan_status"] == "infected"
    assert cb.post(f"/api/clients/{cid}/register", headers=hb).status_code == 422
    env.av.available = False
    assert upload_id(cb, hb, cid, PDF).json()["scan_status"] == "pending"
    assert cb.post(f"/api/clients/{cid}/register", headers=hb).status_code == 422


def test_a_registered_client_cannot_be_registered_or_requested_again(env: Env) -> None:
    ca, ha, _ = env.user("agent")
    cb, hb, _ = env.user("backoffice")
    cid = lead(ca, ha)
    upload_id(cb, hb, cid)
    assert cb.post(f"/api/clients/{cid}/register", headers=hb).status_code == 200
    assert cb.post(f"/api/clients/{cid}/register", headers=hb).status_code == 409
    assert ca.post(f"/api/clients/{cid}/request-id", headers=ha).status_code == 409


def test_desk_is_for_backoffice_and_manager_only(env: Env) -> None:
    for role, status in (("agent", 403), ("marketing", 403), ("backoffice", 200), ("manager", 200)):
        c, _, _ = env.user(role)
        assert c.get("/api/desk").status_code == status, role


def test_desk_counts_every_area_and_flags_urgent(env: Env) -> None:
    cb, hb, _ = env.user("backoffice")
    for typ, urgency in (
        ("id_missing", "urgent"),
        ("fill_template", "normal"),
        ("print_label", "normal"),
        ("send_post", "normal"),
        ("insurer_request", "important"),
        ("tax_certificate", "normal"),
        ("review", "normal"),
        ("claim_gap", "normal"),
    ):  # claim_gap: no area
        cb.post(
            "/api/inbox",
            headers=hb,
            json={"type": typ, "role": "backoffice", "title": f"{typ} item", "urgency": urgency},
        )
    desk = cb.get("/api/desk").json()
    by = {a["area"]: a for a in desk["areas"]}
    assert by["id_registration"] == {"area": "id_registration", "open": 1, "urgent": 1}
    assert by["post_email"]["open"] == 2 and by["templates"]["open"] == 1
    assert by["insurer_requests"]["open"] == 1 and by["tax"]["open"] == 1
    assert by["intake"]["open"] == 1
    assert desk["total_open"] == 8 and desk["total_urgent"] == 1
