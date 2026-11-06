"""T-M1-7 (role matrix), T-M1-8, T-M1-9, T-M1-11. Also P-1, P-2, P-3."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.conftest import Account, login, make_account

ROLES = ["agent", "backoffice", "marketing", "manager"]


def acct(db: Session, role: str, n: int = 1) -> Account:
    return make_account(db, f"{role}{n}@agency.il", [role])


def new_client(
    api: TestClient,
    hdr: dict[str, str],
    id_number: str = "123456782",
    name: str = "David Cohen",
    **kw: Any,
) -> dict[str, Any]:
    r = api.post("/api/clients", json={"id_number": id_number, "name": name, **kw}, headers=hdr)
    assert r.status_code == 201, r.text
    return dict(r.json())


# T-M1-7: role x endpoint matrix -----------------------------------------------------------
# (method, path template, body, {role: expected status})
MATRIX = [
    (
        "POST",
        "/api/clients",
        {"id_number": "000000026", "name": "Test Person"},
        {"agent": 201, "backoffice": 201, "marketing": 403, "manager": 201},
    ),
    (
        "GET",
        "/api/clients?q=test",
        None,
        {"agent": 200, "backoffice": 200, "marketing": 403, "manager": 200},
    ),
    (
        "GET",
        "/api/marketing/contacts",
        None,
        {"agent": 403, "backoffice": 403, "marketing": 200, "manager": 200},
    ),
    (
        "GET",
        "/api/audit/verify",
        None,
        {"agent": 403, "backoffice": 403, "marketing": 403, "manager": 200},
    ),
    (
        "POST",
        "/api/users",
        {
            "email": "n@agency.il",
            "display_name": "N",
            "roles": ["agent"],
            "password": "a-fine-password-1",
        },
        {"agent": 403, "backoffice": 403, "marketing": 403, "manager": 201},
    ),
]


@pytest.mark.parametrize("method,path,body,expected", MATRIX)
def test_role_matrix(
    api: TestClient,
    db: Session,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    expected: dict[str, int],
) -> None:
    for role in ROLES:
        a = acct(db, role)
        client = TestClient(api.app, base_url="https://testserver")  # type: ignore[arg-type]
        hdr = login(client, db, a)
        if body and "email" in body:
            body = {**body, "email": f"{role}-new@agency.il"}
        if body and "id_number" in body:
            body = {
                **body,
                "id_number": {
                    "agent": "000000026",
                    "backoffice": "000000034",
                    "marketing": "000000018",
                    "manager": "123456782",
                }[role],
            }
        r = client.request(method, path, json=body, headers=hdr)
        assert r.status_code == expected[role], (role, method, path, r.status_code, r.text)


def test_unauthenticated_everything_denied(api: TestClient) -> None:
    for method, path, body, _ in MATRIX:
        assert api.request(method, path, json=body).status_code == 401, path


def test_denials_are_audited(api: TestClient, db: Session) -> None:
    a = acct(db, "marketing")
    login(api, db, a)
    assert api.get("/api/audit/verify").status_code == 403
    assert (
        db.execute(text("SELECT count(*) FROM audit_entry WHERE action='authz.deny'")).scalar_one()
        == 1
    )


# T-M1-8 -----------------------------------------------------------------------------------
def test_create_validates_id_and_rejects_duplicates(api: TestClient, db: Session) -> None:
    hdr = login(api, db, acct(db, "agent"))
    bad = api.post("/api/clients", json={"id_number": "123456789", "name": "Bad Id"}, headers=hdr)
    assert bad.status_code == 422 and bad.json()["detail"] == "invalid_id_number"
    new_client(api, hdr)
    dup = api.post(
        "/api/clients", json={"id_number": "123-456-782", "name": "Other Name"}, headers=hdr
    )
    assert dup.status_code == 409
    lang = api.post(
        "/api/clients",
        json={"id_number": "000000026", "name": "Lang Test", "preferred_language": "xx"},
        headers=hdr,
    )
    assert lang.status_code == 422


# T-M1-9 -----------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "query",
    [
        "david",
        "DAVID COHEN",
        "cohen david",
        "Dávid",
        "דוד",
        "כהן",
        "דוד כהן",
        "Давид",
        "Коэн",
        "123456782",
        "3456",
        "6782",
        "david 4567",
        "dvd kohen",
        "co",
    ],
)
def test_search_finds_across_scripts_accents_order_and_id_digits(
    api: TestClient, db: Session, query: str
) -> None:
    hdr = login(api, db, acct(db, "backoffice"))
    c = new_client(api, hdr)
    new_client(api, hdr, "000000026", "Moshe Levi")
    ids = [x["id"] for x in api.get("/api/clients", params={"q": query}).json()]
    assert c["id"] in ids, query
    assert len(ids) == 1, (query, ids)  # the other client does not match


def test_search_misses_unrelated_and_empty_query_lists(api: TestClient, db: Session) -> None:
    hdr = login(api, db, acct(db, "backoffice"))
    new_client(api, hdr)
    assert api.get("/api/clients", params={"q": "zzzz"}).json() == []
    assert api.get("/api/clients", params={"q": "99999"}).json() == []
    assert len(api.get("/api/clients").json()) == 1


def test_transliterations_are_stored(api: TestClient, db: Session) -> None:
    hdr = login(api, db, acct(db, "agent"))
    c = new_client(api, hdr, name="David Cohen")
    assert c["name_translit_he"] and c["name_translit_lat"] == "david cohen"


# P-1: IDOR / ownership --------------------------------------------------------------------
def test_agent_cannot_see_another_agents_client(api: TestClient, db: Session) -> None:
    a1, a2 = acct(db, "agent", 1), acct(db, "agent", 2)
    c1 = TestClient(api.app, base_url="https://testserver")  # type: ignore[arg-type]
    h1, h2 = login(c1, db, a1), login(api, db, a2)
    mine = new_client(c1, h1)
    assert api.get(f"/api/clients/{mine['id']}").status_code == 404
    assert api.get(f"/api/clients/{mine['id']}/policies").status_code == 404
    assert (
        api.post(
            f"/api/clients/{mine['id']}/policies",
            headers=h2,
            json={"insurer": "Harel", "policy_number": "1", "product": "car", "premium_ils": 1},
        ).status_code
        == 404
    )
    assert api.get("/api/clients", params={"q": "david"}).json() == []
    assert c1.get(f"/api/clients/{mine['id']}").status_code == 200


def test_backoffice_and_manager_see_all_clients(api: TestClient, db: Session) -> None:
    a1 = acct(db, "agent")
    c1 = TestClient(api.app, base_url="https://testserver")  # type: ignore[arg-type]
    mine = new_client(c1, login(c1, db, a1))
    for role in ("backoffice", "manager"):
        c = TestClient(api.app, base_url="https://testserver")  # type: ignore[arg-type]
        login(c, db, acct(db, role))
        assert c.get(f"/api/clients/{mine['id']}").status_code == 200


def test_malformed_client_id_is_422_not_500(api: TestClient, db: Session) -> None:
    login(api, db, acct(db, "agent"))
    assert api.get("/api/clients/not-a-uuid").status_code == 422


# P-3: marketing sees only the projection ---------------------------------------------------
def test_marketing_projection_exposes_only_contact_fields(api: TestClient, db: Session) -> None:
    c = TestClient(api.app, base_url="https://testserver")  # type: ignore[arg-type]
    hdr = login(c, db, acct(db, "agent"))
    cl = new_client(c, hdr, phone="050-1", email="d@x.il", preferred_language="ru")
    api.app.state.sessionmaker  # noqa: B018  # (same app, shared database)
    login(api, db, acct(db, "marketing"))
    rows = api.get("/api/marketing/contacts").json()
    assert len(rows) == 1 and rows[0]["id"] == cl["id"]
    assert set(rows[0]) == {
        "id",
        "name",
        "name_translit_he",
        "name_translit_lat",
        "preferred_language",
        "phone",
        "email",
        "consent_whatsapp",
        "consent_email",
        "consent_sms",
    }
    assert "id_number" not in rows[0] and "status" not in rows[0] and "agent_id" not in rows[0]
    assert api.get(f"/api/clients/{cl['id']}").status_code == 403
    assert api.get("/api/clients").status_code == 403
    # and at the database level the view itself has no ID number column
    cols = {
        r[0]
        for r in db.execute(
            text(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name='marketing_contact'"
            )
        )
    }
    assert "id_number" not in cols


# T-M1-11 ----------------------------------------------------------------------------------
def test_reads_and_searches_are_audited(api: TestClient, db: Session) -> None:
    a = acct(db, "agent")
    hdr = login(api, db, a)
    cl = new_client(api, hdr)
    api.get(f"/api/clients/{cl['id']}")
    api.get("/api/clients", params={"q": "david"})
    api.get(f"/api/clients/{cl['id']}/policies")
    rows = db.execute(
        text(
            "SELECT actor, action, object FROM audit_entry "
            "WHERE action IN ('client.read','client.search','policy.read')"
        )
    ).all()
    assert {r[1] for r in rows} == {"client.read", "client.search", "policy.read"}
    assert all(r[0] == a.id for r in rows)
    assert any(r[2] == f"client:{cl['id']}" for r in rows)


# policies and family --------------------------------------------------------------------
def test_policies_and_family(api: TestClient, db: Session) -> None:
    hdr = login(api, db, acct(db, "backoffice"))
    head = new_client(api, hdr)
    kid = new_client(api, hdr, "000000026", "Noa Cohen")
    p = {
        "insurer": "Harel",
        "policy_number": "P-1",
        "product": "car",
        "premium_ils": 1450.5,
        "renewal_date": "2027-03-01",
    }
    assert api.post(f"/api/clients/{head['id']}/policies", json=p, headers=hdr).status_code == 201
    assert api.post(f"/api/clients/{head['id']}/policies", json=p, headers=hdr).status_code == 409
    pols = api.get(f"/api/clients/{head['id']}/policies").json()
    assert pols[0]["insurer"] == "Harel" and pols[0]["premium_ils"] == 1450.5
    link = {"member_id": kid["id"], "relation": "child"}
    assert api.post(f"/api/clients/{head['id']}/family", json=link, headers=hdr).status_code == 201
    assert api.post(f"/api/clients/{head['id']}/family", json=link, headers=hdr).status_code == 409
    fam = api.get(f"/api/clients/{head['id']}").json()["family"]
    assert fam == [{"head": head["id"], "member": kid["id"], "relation": "child"}]
    self_link = {"member_id": head["id"], "relation": "self"}
    assert (
        api.post(f"/api/clients/{head['id']}/family", json=self_link, headers=hdr).status_code
        == 422
    )


# P-17: over-posting -----------------------------------------------------------------------
def test_mass_assignment_ignored(api: TestClient, db: Session) -> None:
    a = acct(db, "agent")
    hdr = login(api, db, a)
    other = acct(db, "agent", 2)
    r = api.post(
        "/api/clients",
        headers=hdr,
        json={
            "id_number": "123456782",
            "name": "David Cohen",
            "agent_id": other.id,
            "status": "active",
            "id": "00000000-0000-0000-0000-000000000000",
        },
    )
    assert r.status_code == 201
    assert r.json()["agent_id"] == a.id and r.json()["status"] == "lead"
