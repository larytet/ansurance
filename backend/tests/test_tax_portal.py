"""M10: tax work (F15), translation, and the client's read-only view."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.adapters.antivirus import EICAR, FakeAntivirus
from app.adapters.translate import FakeTranslator
from app.blob.fake import FakeBlobStore
from app.config import Settings
from app.consent import service as consent
from app.main import create_app
from app.models import Client, Insurer, Policy
from app.tax import service as tax
from app.text import build_search
from tests.conftest import login, make_account

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\n%%EOF cert"
YEAR = 2026


class Env:
    def __init__(
        self, api: TestClient, db: Session, settings: Settings, tr: FakeTranslator
    ) -> None:
        self.api, self.db, self.settings, self.tr = api, db, settings, tr
        self.users: dict[str, tuple[TestClient, dict[str, str], str]] = {}

    def who(self, role: str) -> tuple[TestClient, dict[str, str]]:
        if role not in self.users:
            a = make_account(self.db, f"{role}@agency.il", [role])
            c = TestClient(self.api.app, base_url="https://testserver")  # type: ignore[arg-type]
            self.users[role] = (c, login(c, self.db, a), a.id)
        return self.users[role][0], self.users[role][1]

    def uid(self, role: str) -> str:
        self.who(role)
        return self.users[role][2]


@pytest.fixture
def env(settings: Settings, fresh_db: Engine, db: Session) -> Env:
    tr = FakeTranslator()
    app = create_app(
        settings, fresh_db, blobs=FakeBlobStore(), antivirus=FakeAntivirus(), translator=tr
    )
    return Env(TestClient(app, base_url="https://testserver"), db, settings, tr)


_n = iter(range(1, 10_000))


def client(
    e: Env, name: str = "David Cohen", agent: str = "agent", with_consent: bool = False
) -> Client:
    n = next(_n)
    plain, skel = build_search(name, f"{n:09d}")
    cl = Client(
        id_number=f"{n:09d}",
        name=name,
        search_text=plain,
        search_skel=skel,
        status="active",
        agent_id=e.uid(agent),
        phone=f"050-{n:07d}",
        email=f"c{n}@x.il",
    )
    e.db.add(cl)
    e.db.flush()
    if with_consent:
        consent.record(e.db, cl, "whatsapp", "opt_in", "test", None, None)
    e.db.commit()
    return cl


def policy(e: Env, cl: Client, number: str, product: str, insurer: str = "Harel") -> Policy:
    ins = e.db.query(Insurer).filter_by(name=insurer).first() or Insurer(name=insurer)
    e.db.add(ins)
    e.db.flush()
    p = Policy(
        client_id=cl.id,
        insurer_id=ins.id,
        policy_number=number,
        product=product,
        premium_ils=Decimal(100),
    )
    e.db.add(p)
    e.db.commit()
    return p


def upload(e: Env, cid: Any, data: bytes = PDF) -> str:
    c, h = e.who("backoffice")
    r = c.post(
        f"/api/clients/{cid}/documents",
        headers=h,
        files={"file": ("c.pdf", data, "application/pdf")},
        data={"doc_type": "other"},
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


# ---- F15 A: certificates ----------------------------------------------------------------------------
def test_certificates_per_client_and_product_with_the_three_marks(env: Env) -> None:
    a, b = client(env, "A"), client(env, "B")
    policy(env, a, "P1", "pension")
    policy(env, a, "P2", "car")  # no deposit credit: no certificate
    policy(env, b, "P3", "life", "Migdal")
    bo, bh = env.who("backoffice")
    assert bo.post(
        "/api/tax/certificates/generate", headers=bh, json={"tax_year": YEAR}
    ).json() == {"created": 2}
    assert bo.post(
        "/api/tax/certificates/generate", headers=bh, json={"tax_year": YEAR}
    ).json() == {"created": 0}  # repeatable
    r = bo.post("/api/tax/certificates/request-all", headers=bh, json={"tax_year": YEAR}).json()
    assert r == {"requested": 2, "letters_per_insurer": {"Harel": 1, "Migdal": 1}}
    assert (
        env.db.execute(
            text("SELECT count(*) FROM inbox_item WHERE type='dispatch' AND role='backoffice'")
        ).scalar_one()
        == 2
    )
    m, _ = env.who("manager")
    rows = m.get(f"/api/tax/certificates?tax_year={YEAR}").json()
    assert [
        (x["client"], x["product"], x["requested"], x["received"], x["sent"]) for x in rows
    ] == [("A", "pension", True, False, False), ("B", "life", True, False, False)]
    cid = rows[0]["id"]
    doc = upload(env, a.id)
    assert bo.put(
        f"/api/tax/certificates/{cid}/received", headers=bh, json={"document_id": doc}
    ).json() == {"status": "received"}
    assert bo.put(f"/api/tax/certificates/{cid}/received", headers=bh, json={}).status_code == 409
    other_doc = upload(env, b.id)
    rid = rows[1]["id"]
    assert (
        bo.put(
            f"/api/tax/certificates/{rid}/received", headers=bh, json={"document_id": doc}
        ).status_code
        == 422
    )  # another client's file
    assert other_doc


def test_send_all_goes_through_the_consent_gate(env: Env) -> None:
    ok = client(env, "Reachable", with_consent=True)
    gone = client(env, "Opted out")
    consent.record(env.db, gone, "whatsapp", "opt_out", "STOP", None, None)
    consent.record(env.db, gone, "email", "opt_out", "STOP", None, None)
    env.db.commit()
    for c, n in ((ok, "S1"), (gone, "S2")):
        policy(env, c, n, "pension")
    bo, bh = env.who("backoffice")
    bo.post("/api/tax/certificates/generate", headers=bh, json={"tax_year": YEAR})
    for x in bo.get(f"/api/tax/certificates?tax_year={YEAR}").json():
        bo.put(f"/api/tax/certificates/{x['id']}/received", headers=bh, json={})
    assert bo.post(
        "/api/tax/certificates/send-all", headers=bh, json={"tax_year": YEAR}
    ).json() == {"sent": 1, "clients_blocked": 1}
    assert env.db.execute(text("SELECT purpose, status FROM outbox_message")).one() == (
        "service",
        "queued",
    )
    st = {x["client"]: x["status"] for x in bo.get(f"/api/tax/certificates?tax_year={YEAR}").json()}
    assert st == {
        "Reachable": "sent",
        "Opted out": "received",
    }  # a blocked client's certificate stays "received"


def test_a_missing_certificate_raises_one_item_after_the_wait(env: Env) -> None:
    policy(env, client(env), "W1", "gemel")
    bo, bh = env.who("backoffice")
    bo.post("/api/tax/certificates/generate", headers=bh, json={"tax_year": YEAR})
    bo.post("/api/tax/certificates/request-all", headers=bh, json={"tax_year": YEAR})
    soon = datetime.now(UTC) + timedelta(days=env.settings.tax_cert_wait_days - 1)
    assert tax.tick(env.db, env.settings, soon)["certificate_items"] == 0
    late = datetime.now(UTC) + timedelta(days=env.settings.tax_cert_wait_days + 1)
    assert tax.tick(env.db, env.settings, late)["certificate_items"] == 1
    env.db.commit()
    assert tax.tick(env.db, env.settings, late)["certificate_items"] == 0


# ---- F15 C and D -----------------------------------------------------------------------------------------
def test_residency_gaps(env: Env) -> None:
    a, b, c = client(env, "Declared"), client(env, "Foreign no form"), client(env, "Nothing")
    bo, bh = env.who("backoffice")
    assert (
        bo.put(f"/api/tax/residency/{a.id}", headers=bh, json={"residency": "israel"}).status_code
        == 200
    )
    assert (
        bo.put(f"/api/tax/residency/{b.id}", headers=bh, json={"residency": "us"}).status_code
        == 200
    )
    assert (
        bo.put(f"/api/tax/residency/{a.id}", headers=bh, json={"residency": "mars"}).status_code
        == 422
    )
    assert {(g["client"], g["gap"]) for g in bo.get("/api/tax/residency/gaps").json()} == {
        ("Foreign no form", "foreign_residency_without_form"),
        ("Nothing", "no_declaration"),
    }
    bo.put(
        f"/api/tax/residency/{b.id}", headers=bh, json={"residency": "us", "form_received": True}
    )  # "mark received"
    assert [g["client"] for g in bo.get("/api/tax/residency/gaps").json()] == ["Nothing"]
    assert c


def test_an_agency_deadline_cannot_pass_without_a_manager_item(env: Env) -> None:
    bo, bh = env.who("backoffice")
    today = datetime.now(UTC).date()
    ids = {}
    for kind, title, days in (
        ("vat_report", "VAT report", 10),
        ("withholding_cert", "Withholding certificates", 40),
        ("commission_invoice", "Invoice to Harel", -2),
    ):
        ids[kind] = bo.post(
            "/api/tax/deadlines",
            headers=bh,
            json={
                "kind": kind,
                "title": title,
                "due_on": (today + timedelta(days=days)).isoformat(),
            },
        ).json()["id"]
    assert (
        bo.post(
            "/api/tax/deadlines",
            headers=bh,
            json={"kind": "bad", "title": "x", "due_on": today.isoformat()},
        ).status_code
        == 422
    )
    assert (
        tax.tick(env.db, env.settings)["deadline_items"] == 2
    )  # within 14 days, and overdue; the 40-day one waits
    env.db.commit()
    assert tax.tick(env.db, env.settings)["deadline_items"] == 0
    rows = env.db.execute(
        text("SELECT urgency, title FROM inbox_item WHERE type='tax_deadline' ORDER BY title")
    ).all()
    assert [r[0] for r in rows] == ["urgent", "important"] and "PASSED" in rows[0][1]
    assert (
        bo.put(f"/api/tax/deadlines/{ids['commission_invoice']}/done", headers=bh).status_code
        == 200
    )
    assert [d["done"] for d in bo.get("/api/tax/deadlines").json()] == [True, False, False]


def test_the_accountant_gets_a_read_only_yearly_package(env: Env) -> None:
    policy(env, client(env, "Pkg"), "K1", "pension")
    bo, bh = env.who("backoffice")
    bo.post("/api/tax/certificates/generate", headers=bh, json={"tax_year": YEAR})
    ac, ah = env.who("accountant")
    r = ac.get(f"/api/tax/package.xlsx?tax_year={YEAR}")
    assert r.status_code == 200 and r.content[:2] == b"PK"
    assert ac.get(f"/api/tax/certificates?tax_year={YEAR}").status_code == 200
    for call in (
        ac.post("/api/tax/certificates/generate", headers=ah, json={"tax_year": YEAR}),
        ac.get("/api/clients"),
        ac.get("/api/inbox"),
        ac.get("/api/overview"),
    ):
        assert call.status_code == 403
    assert env.who("agent")[0].get(f"/api/tax/package.xlsx?tax_year={YEAR}").status_code == 403
    assert (
        env.db.execute(
            text("SELECT count(*) FROM audit_entry WHERE action='export.tax_package'")
        ).scalar_one()
        == 1
    )


# ---- translation -------------------------------------------------------------------------------------------
def test_translation_keeps_original_and_translation_and_flags_risky_passages(env: Env) -> None:
    cl = client(env)
    c, h = env.who("agent")
    r = c.post(
        "/api/translate",
        headers=h,
        json={
            "text": "The premium is ₪1,250 from 01/12/2026",
            "source": "en",
            "target": "he",
            "client_id": str(cl.id),
        },
    )
    assert r.status_code == 201
    out = r.json()
    assert (
        out["translated"] == "[he] The premium is ₪1,250 from 01/12/2026"
        and "machine translation" in out["notice"]
    )
    assert out["needs_human_check"] == ["amount", "coverage", "date"]
    ok = c.post(
        "/api/translate", headers=h, json={"text": "Good morning", "source": "en", "target": "ru"}
    ).json()
    assert ok["needs_human_check"] == []
    tl = c.get(f"/api/clients/{cl.id}/translations").json()
    assert (
        len(tl) == 1
        and tl[0]["original"].startswith("The premium")
        and tl[0]["translated"].startswith("[he]")
    )


def test_translation_errors(env: Env) -> None:
    c, h = env.who("agent")
    assert (
        c.post(
            "/api/translate", headers=h, json={"text": "x", "source": "en", "target": "en"}
        ).status_code
        == 422
    )
    assert (
        c.post(
            "/api/translate", headers=h, json={"text": "x", "source": "en", "target": "xx"}
        ).status_code
        == 422
    )
    env.tr.fail = True
    r = c.post("/api/translate", headers=h, json={"text": "hello", "source": "en", "target": "he"})
    assert r.status_code == 502 and r.json()["detail"] == "translation_provider_unavailable"
    assert (
        env.db.execute(text("SELECT ok FROM adapter_call WHERE adapter='translate'")).scalar_one()
        is False
    )
    other = client(env, "Not mine", "backoffice")
    env.tr.fail = False
    assert (
        c.post(
            "/api/translate",
            headers=h,
            json={"text": "hello", "source": "en", "target": "he", "client_id": str(other.id)},
        ).status_code
        == 404
    )


# ---- the client's view ---------------------------------------------------------------------------------------
def portal(e: Env, cl: Client) -> tuple[str, str]:
    c, h = e.who("agent")
    r = c.post(f"/api/clients/{cl.id}/portal-link", headers=h)
    assert r.status_code == 201, r.text
    return r.json()["token"], r.json()["id"]


def test_the_client_sees_only_their_own_data_and_nothing_internal(env: Env) -> None:
    mine, other = client(env, "Mine", with_consent=True), client(env, "Someone Else")
    policy(env, mine, "M1", "car")
    policy(env, other, "O1", "car")
    d_ok = upload(env, mine.id)
    d_secret = upload(env, mine.id, PDF + b" medical")
    env.db.execute(text("UPDATE document SET restricted = true WHERE id = :d"), {"d": d_secret})
    upload(env, mine.id, PDF + b"\n" + EICAR)  # an infected file is never offered
    env.db.execute(
        text(
            "INSERT INTO inbox_item (id, type, role, urgency, title, detail, status, client_id) SELECT gen_random_uuid(), 'note', 'agent', 'normal', 'INTERNAL NOTE', 'secret remark', 'open', id FROM client WHERE name = 'Mine'"
        )
    )
    env.db.commit()
    token, _ = portal(env, mine)
    anon = TestClient(env.api.app, base_url="https://testserver")  # type: ignore[arg-type]
    r = anon.get("/api/portal/summary", headers={"x-portal-token": token})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Mine" and [p["policy_number"] for p in body["policies"]] == ["M1"]
    assert len(body["documents"]) == 1 and body["consent"] == {"whatsapp": "opt_in"}
    flat = r.text
    assert (
        "INTERNAL NOTE" not in flat
        and "secret remark" not in flat
        and "O1" not in flat
        and "audit" not in flat.lower()
    )
    assert d_ok


def test_portal_links_expire_revoke_and_never_reveal_why(env: Env) -> None:
    cl = client(env)
    token, link_id = portal(env, cl)
    anon = TestClient(env.api.app, base_url="https://testserver")  # type: ignore[arg-type]
    get = lambda t: anon.get("/api/portal/summary", headers={"x-portal-token": t})  # noqa: E731
    assert get(token).status_code == 200
    assert get("wrong").status_code == 404 and anon.get("/api/portal/summary").status_code == 404
    assert (
        env.db.execute(text("SELECT token_hash FROM portal_link")).scalar_one() != token
    )  # only the hash is stored
    c, h = env.who("agent")
    assert c.delete(f"/api/portal-links/{link_id}", headers=h).status_code == 200
    assert get(token).status_code == 404
    token2, _ = portal(env, cl)
    env.db.execute(
        text(
            "UPDATE portal_link SET expires_at = now() - interval '1 minute' WHERE revoked_at IS NULL"
        )
    )
    env.db.commit()
    assert get(token2).status_code == 404
    assert get(token2).text == get("wrong").text  # expired, revoked and unknown look identical


def test_portal_links_need_the_right_to_issue_and_a_visible_client(env: Env) -> None:
    mine = client(env)
    colleague = client(env, "Colleague's", "backoffice")
    c, h = env.who("agent")
    assert c.post(f"/api/clients/{colleague.id}/portal-link", headers=h).status_code == 404
    mk, mh = env.who("marketing")
    assert mk.post(f"/api/clients/{mine.id}/portal-link", headers=mh).status_code == 403
    token, _ = portal(env, mine)
    anon = TestClient(env.api.app, base_url="https://testserver")  # type: ignore[arg-type]
    assert anon.post("/api/clients", json={}, headers={"x-portal-token": token}).status_code in (
        401,
        403,
    )  # a portal token opens nothing else
    assert anon.get("/api/clients", headers={"x-portal-token": token}).status_code == 401
    assert date.today()
