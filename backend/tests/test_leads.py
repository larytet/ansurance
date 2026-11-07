"""T-M13-1 .. T-M13-3: F1 duplicate check on the phone number, and the leads list."""

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.clients.router import phone_digits
from tests.conftest import Account, login, make_account


def _id(base8: str) -> str:
    """Complete eight digits with the Israeli ID check digit."""
    total = 0
    for i, ch in enumerate(base8):
        n = int(ch) * (1 if i % 2 == 0 else 2)
        total += n - 9 if n > 9 else n
    return base8 + str((10 - total % 10) % 10)


def _who(
    api: TestClient, db: Session, email: str, role: str
) -> tuple[TestClient, dict[str, str], Account]:
    a = make_account(db, email, [role])
    c = TestClient(api.app, base_url="https://testserver")
    return c, login(c, db, a), a


def _create(c: TestClient, h: dict[str, str], n: int, phone: str | None) -> dict:  # type: ignore[type-arg]
    r = c.post(
        "/api/clients",
        json={"id_number": _id(f"{n:08d}"), "name": f"Client {n}", "phone": phone},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


# T-M13-1 ------------------------------------------------------------------------------
def test_phone_numbers_are_compared_by_their_digits() -> None:
    assert phone_digits("050-111-2222") == "0501112222"
    assert phone_digits("+972 (50) 111 2222") == "0501112222"
    assert phone_digits("972501112222") == "0501112222"
    assert phone_digits("12") == "" and phone_digits(None) == "" and phone_digits("") == ""


def test_the_same_phone_on_another_client_is_a_warning_not_a_refusal(
    api: TestClient, db: Session
) -> None:
    ann, ha, _ = _who(api, db, "ann@agency.il", "agent")
    first = _create(ann, ha, 1, "050-111-2222")
    assert first["phone_matches"] == [] and first["phone_shared_with_other_agent"] is False
    second = _create(ann, ha, 2, "+972-50-111-2222")  # the same number written another way
    assert [m["name"] for m in second["phone_matches"]] == ["Client 1"]
    assert second["phone_matches"][0]["id"] == first["id"]
    assert second["phone_shared_with_other_agent"] is False
    third = _create(ann, ha, 3, "052-999-0000")
    assert third["phone_matches"] == []
    assert _create(ann, ha, 4, None)["phone_matches"] == []  # no number, nothing to match
    assert _create(ann, ha, 5, "")["phone_matches"] == []


def test_a_match_on_another_agents_client_shows_no_name(api: TestClient, db: Session) -> None:
    ann, ha, _ = _who(api, db, "ann@agency.il", "agent")
    bob, hb, _ = _who(api, db, "bob@agency.il", "agent")
    mgr, hm, _ = _who(api, db, "mgr@agency.il", "manager")
    _create(ann, ha, 1, "050-111-2222")
    seen_by_bob = _create(bob, hb, 2, "0501112222")
    assert (
        seen_by_bob["phone_matches"] == [] and seen_by_bob["phone_shared_with_other_agent"] is True
    )
    assert "Client 1" not in str(seen_by_bob)  # not even the name leaks
    seen_by_manager = _create(mgr, hm, 3, "050 111 2222")
    assert sorted(m["name"] for m in seen_by_manager["phone_matches"]) == ["Client 1", "Client 2"]
    assert seen_by_manager["phone_shared_with_other_agent"] is False


def test_a_duplicate_id_is_still_refused(api: TestClient, db: Session) -> None:
    ann, ha, _ = _who(api, db, "ann@agency.il", "agent")
    _create(ann, ha, 1, "050-111-2222")
    again = ann.post(
        "/api/clients",
        json={"id_number": _id("00000001"), "name": "Other", "phone": "1"},
        headers=ha,
    )
    assert again.status_code == 409


# T-M13-2 ------------------------------------------------------------------------------
def test_the_leads_list_is_the_agents_own_clients_with_status_lead(
    api: TestClient, db: Session
) -> None:
    ann, ha, _ = _who(api, db, "ann@agency.il", "agent")
    bob, hb, _ = _who(api, db, "bob@agency.il", "agent")
    mine = _create(ann, ha, 1, None)
    other = _create(ann, ha, 2, None)
    _create(bob, hb, 3, None)
    db.execute(text("UPDATE client SET status = 'active' WHERE id = :i"), {"i": other["id"]})
    db.commit()
    leads = ann.get("/api/clients", params={"status": "lead"}).json()
    assert [c["id"] for c in leads] == [mine["id"]]  # not the active one, not bob's
    assert {c["id"] for c in ann.get("/api/clients").json()} == {mine["id"], other["id"]}
    assert bob.get("/api/clients", params={"status": "lead"}).json()[0]["name"] == "Client 3"


# T-M13-3 ------------------------------------------------------------------------------
def test_the_leads_list_respects_roles(api: TestClient, db: Session) -> None:
    m, _, _ = _who(api, db, "m@agency.il", "marketing")
    assert m.get("/api/clients", params={"status": "lead"}).status_code == 403
