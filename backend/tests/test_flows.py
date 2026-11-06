"""M9: flows F1 to F5 and e-signature. Each class of test follows the acceptance line of the
flow in REQUIREMENTS section 3. The signature provider is a fake: nothing here proves a real
Comsign or DocuSign integration."""

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.adapters.antivirus import FakeAntivirus
from app.adapters.esign import FakeESign, sign_payload
from app.blob.fake import FakeBlobStore
from app.config import Settings
from app.flows import service
from app.main import create_app
from tests.conftest import Account, login, make_account

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\n%%EOF form"
SECRET = "esign-secret"
TODAY = datetime.now(UTC).date()  # the service works in UTC dates
KYC = {"identity_verified": True, "beneficiary": "the client"}


class Env:
    def __init__(self, api: TestClient, es: FakeESign, db: Session, settings: Settings) -> None:
        self.api, self.es, self.db, self.settings = api, es, db, settings
        self.users: dict[str, tuple[TestClient, dict[str, str], Account]] = {}

    def who(self, role: str) -> tuple[TestClient, dict[str, str]]:
        if role not in self.users:
            a = make_account(self.db, f"{role}@agency.il", [role])
            c = TestClient(self.api.app, base_url="https://testserver")  # type: ignore[arg-type]
            self.users[role] = (c, login(c, self.db, a), a)
        c, h, _ = self.users[role]
        return c, h

    def uid(self, role: str) -> str:
        self.who(role)
        return str(self.users[role][2].id)


@pytest.fixture
def env(settings: Settings, fresh_db: Engine, db: Session) -> Env:
    settings = settings.model_copy(update={"esign_webhook_secret": SecretStr(SECRET)})
    es = FakeESign()
    app = create_app(settings, fresh_db, blobs=FakeBlobStore(), antivirus=FakeAntivirus(), esign=es)
    return Env(TestClient(app, base_url="https://testserver"), es, db, settings)


def mk_client(e: Env, role: str = "agent", idn: str = "123456782") -> str:
    c, h = e.who(role)
    r = c.post(
        "/api/clients", json={"id_number": idn, "name": "David Cohen", "email": "d@x.il"}, headers=h
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


def upload(e: Env, cid: str, doc_type: str = "other", data: bytes = PDF) -> str:
    c, h = e.who("backoffice")
    r = c.post(
        f"/api/clients/{cid}/documents",
        headers=h,
        files={"file": ("f.pdf", data, "application/pdf")},
        data={"doc_type": doc_type},
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


def send(e: Env, cid: str, kind: str, role: str = "agent") -> dict[str, Any]:
    c, h = e.who(role)
    doc = upload(e, cid)
    r = c.post(
        f"/api/clients/{cid}/esign",
        headers=h,
        json={
            "kind": kind,
            "title": f"{kind} form",
            "document_id": doc,
            "signer_contact": "d@x.il",
        },
    )
    assert r.status_code == 201, r.text
    return {"id": r.json()["id"], "ref": e.es.sent[-1]["ref"]}


def webhook(e: Env, ref: str, status: str = "signed", secret: str = SECRET) -> Any:
    body = json.dumps({"provider_ref": ref, "status": status}).encode()
    return e.api.post(
        "/api/webhooks/esign", content=body, headers={"x-signature": sign_payload(secret, body)}
    )


def sign(e: Env, cid: str, kind: str) -> None:
    assert webhook(e, send(e, cid, kind)["ref"]).status_code == 200


def register(e: Env, cid: str) -> None:
    upload(e, cid, "id_card")
    c, h = e.who("backoffice")
    assert c.post(f"/api/clients/{cid}/register", headers=h).status_code == 200


def status(e: Env, cid: str) -> dict[str, Any]:
    c, _ = e.who("agent")
    return c.get(f"/api/clients/{cid}/flow-status").json()  # type: ignore[no-any-return]


# ---- e-signature --------------------------------------------------------------------------
def test_a_signed_form_is_filed_in_the_client_file_and_audited(env: Env) -> None:
    cid = mk_client(env)
    r = send(env, cid, "mandate")
    assert webhook(env, r["ref"]).json() == {"status": "signed"}
    c, _ = env.who("agent")
    rows = c.get(f"/api/clients/{cid}/esign").json()
    assert rows[0]["status"] == "signed" and rows[0]["signed_document_id"]
    docs = c.get(f"/api/clients/{cid}/documents").json()
    assert any(
        d["id"] == rows[0]["signed_document_id"]
        for d in (docs["items"] if isinstance(docs, dict) else docs)
    )
    acts = [
        a
        for (a,) in env.db.execute(
            text("SELECT action FROM audit_entry WHERE action LIKE 'esign.%' ORDER BY seq")
        )
    ]
    assert acts == ["esign.send", "esign.signed"]
    assert (
        env.db.execute(text("SELECT count(*) FROM adapter_call WHERE adapter='esign'")).scalar_one()
        == 2
    )


def test_the_signature_webhook_rejects_forgery_and_replays(env: Env) -> None:
    cid = mk_client(env)
    ref = send(env, cid, "mandate")["ref"]
    assert webhook(env, ref, secret="wrong").status_code == 403
    assert env.api.post("/api/webhooks/esign", content=b"{}").status_code == 403
    assert webhook(env, "unknown-envelope").status_code == 404
    assert webhook(env, ref, "bogus").status_code == 400
    assert webhook(env, ref).status_code == 200
    assert webhook(env, ref).json() == {"status": "already_signed"}  # a retry changes nothing
    assert (
        env.db.execute(
            text("SELECT count(*) FROM document WHERE doc_type='power_of_attorney'")
        ).scalar_one()
        == 1
    )


def test_declined_is_recorded_and_does_not_count_as_signed(env: Env) -> None:
    cid = mk_client(env)
    assert webhook(env, send(env, cid, "mandate")["ref"], "declined").status_code == 200
    assert status(env, cid)["mandate_signed"] is False


def test_provider_outage_is_reported_and_nothing_is_recorded(env: Env) -> None:
    cid = mk_client(env)
    doc = upload(env, cid)
    env.es.fail = True
    c, h = env.who("agent")
    with pytest.raises(ConnectionError):
        c.post(
            f"/api/clients/{cid}/esign",
            headers=h,
            json={"kind": "mandate", "title": "xx", "document_id": doc, "signer_contact": "a@b.c"},
        )
    assert env.db.execute(text("SELECT count(*) FROM esign_request")).scalar_one() == 0
    assert (
        env.db.execute(text("SELECT ok FROM adapter_call WHERE adapter='esign'")).scalar_one()
        is False
    )


def test_unscanned_or_foreign_documents_cannot_be_sent(env: Env) -> None:
    a, b = mk_client(env), mk_client(env, "backoffice", "000000026")
    doc_b = upload(env, b)
    c, h = env.who("agent")
    r = c.post(
        f"/api/clients/{a}/esign",
        headers=h,
        json={"kind": "mandate", "title": "xx", "document_id": doc_b, "signer_contact": "a@b.c"},
    )
    assert r.status_code == 422 and r.json()["detail"] == "document_not_in_client_file"
    r = c.post(
        f"/api/clients/{a}/esign",
        headers=h,
        json={"kind": "nonsense", "title": "xx", "document_id": doc_b, "signer_contact": "a@b.c"},
    )
    assert r.status_code == 422


# ---- F1 ---------------------------------------------------------------------------------------
def test_f1_a_client_cannot_become_active_without_id_kyc_and_signed_consent(env: Env) -> None:
    cid = mk_client(env)
    c, h = env.who("agent")
    r = c.post(f"/api/clients/{cid}/activate", headers=h)
    assert r.status_code == 409
    assert set(r.json()["detail"]["detail"]) == {
        "client_not_registered",
        "kyc_form_missing",
        "signed_mandate_missing",
        "signed_data_lookup_consent_missing",
    }
    register(env, cid)
    assert (
        c.put(f"/api/clients/{cid}/kyc", headers=h, json={"data": KYC}).json()["risk"] == "normal"
    )
    assert set(status(env, cid)["activation_blockers"]) == {
        "signed_mandate_missing",
        "signed_data_lookup_consent_missing",
    }
    sign(env, cid, "mandate")
    sign(env, cid, "lookup_insurers")
    assert status(env, cid)["activation_blockers"] == []
    assert c.post(f"/api/clients/{cid}/activate", headers=h).json() == {"status": "active"}


def test_f1_kyc_rules(env: Env) -> None:
    cid = mk_client(env)
    c, h = env.who("agent")
    r = c.put(f"/api/clients/{cid}/kyc", headers=h, json={"data": {}})
    assert r.status_code == 422 and set(r.json()["detail"]["detail"]) == {
        "identity_verified",
        "beneficiary",
    }
    r = c.put(
        f"/api/clients/{cid}/kyc",
        headers=h,
        json={"data": {**KYC, "corporate": True, "large_single_premium": True}},
    )
    assert set(r.json()["detail"]["detail"]) == {"controlling_shareholder", "source_of_funds"}


def test_f1_flagged_cases_need_the_manager_and_raise_a_task(env: Env) -> None:
    cid = mk_client(env)
    register(env, cid)
    c, h = env.who("agent")
    r = c.put(f"/api/clients/{cid}/kyc", headers=h, json={"data": {**KYC, "pep": True}})
    assert r.json() == {"risk": "flagged", "flags": ["pep"]}
    sign(env, cid, "mandate")
    sign(env, cid, "lookup_insurers")
    assert status(env, cid)["activation_blockers"] == ["manager_approval_missing"]
    assert (
        c.post(f"/api/clients/{cid}/kyc/approve", headers=h).status_code == 403
    )  # an agent cannot approve
    m, mh = env.who("manager")
    assert env.db.execute(
        text("SELECT role, urgency FROM inbox_item WHERE type='kyc_review'")
    ).one() == ("manager", "urgent")
    assert m.post(f"/api/clients/{cid}/kyc/approve", headers=mh).status_code == 200
    assert c.post(f"/api/clients/{cid}/activate", headers=h).status_code == 200
    assert (
        env.db.execute(text("SELECT status FROM inbox_item WHERE type='kyc_review'")).scalar_one()
        == "done"
    )
    c.put(
        f"/api/clients/{cid}/kyc",
        headers=h,
        json={"data": {**KYC, "pep": True, "beneficiary": "changed"}},
    )
    assert status(env, cid)["kyc"]["approved"] is False  # a changed form needs a fresh decision


def test_f1_an_expired_mandate_does_not_count(env: Env) -> None:
    cid = mk_client(env)
    sign(env, cid, "lookup_insurers")
    env.db.execute(
        text("UPDATE esign_request SET expires_at = :d"), {"d": TODAY - timedelta(days=1)}
    )
    env.db.commit()
    assert "signed_data_lookup_consent_missing" in status(env, cid)["activation_blockers"]


# ---- F5 ---------------------------------------------------------------------------------------
def test_f5_no_lookup_without_all_three_valid_authorizations(env: Env) -> None:
    cid = mk_client(env)
    c, h = env.who("agent")
    for kind in ("lookup_insurers", "lookup_har"):
        sign(env, cid, kind)
        r = c.post(f"/api/clients/{cid}/lookup-requests", headers=h)
        assert r.status_code == 409
    assert r.json()["detail"]["detail"] == ["lookup_clearing"]
    sign(env, cid, "lookup_clearing")
    r = c.post(f"/api/clients/{cid}/lookup-requests", headers=h)
    assert r.status_code == 201 and set(r.json()["authorizations"]) == set(service.LOOKUP_KINDS)
    assert (
        env.db.execute(
            text("SELECT count(*) FROM audit_entry WHERE action='lookup.request'")
        ).scalar_one()
        == 1
    )


def test_f5_an_authorization_expiring_within_six_months_is_flagged_with_resign(env: Env) -> None:
    cid = mk_client(env)
    for kind in service.LOOKUP_KINDS:
        sign(env, cid, kind)
    st = status(env, cid)["authorizations"]
    assert all(v["state"] == "valid" and v["resign_offered"] is False for v in st.values())
    soon = TODAY + timedelta(days=100)
    env.db.execute(
        text("UPDATE esign_request SET expires_at = :d WHERE kind='lookup_har'"), {"d": soon}
    )
    env.db.commit()
    st = status(env, cid)["authorizations"]
    assert st["lookup_har"] == {**st["lookup_har"], "state": "expiring", "resign_offered": True}
    assert st["lookup_insurers"]["state"] == "valid"
    sign(env, cid, "lookup_har")  # the re-sign replaces it
    assert status(env, cid)["authorizations"]["lookup_har"]["state"] == "valid"


def test_add_months_clamps_to_month_end() -> None:
    assert service.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert service.add_months(date(2026, 11, 6), 12) == date(2027, 11, 6)
    assert service.add_months(date(2026, 12, 15), 2) == date(2027, 2, 15)


# ---- F2 ---------------------------------------------------------------------------------------
def sale(e: Env, cid: str, with_advice: bool = True) -> str:
    c, h = e.who("agent")
    advice = None
    if with_advice:
        advice = c.post(
            f"/api/clients/{cid}/advice",
            headers=h,
            json={"product": "car", "needs": "comprehensive", "advice": "insurer A"},
        ).json()["id"]
    return str(
        c.post(
            f"/api/clients/{cid}/applications",
            headers=h,
            json={"insurer": "Harel", "product": "car", "advice_id": advice},
        ).json()["id"]
    )


def test_f2_a_policy_needs_advice_a_signed_application_and_a_renewal_date(env: Env) -> None:
    cid = mk_client(env)
    c, h = env.who("agent")
    bo, bh = env.who("backoffice")
    app = sale(env, cid, with_advice=False)
    doc = upload(env, cid)
    r = c.post(
        f"/api/applications/{app}/send-for-signature",
        headers=h,
        json={"document_id": doc, "signer_contact": "d@x.il"},
    )
    assert r.status_code == 409 and r.json()["detail"] == "advice_record_missing"
    app = sale(env, cid)
    body = {"policy_number": "P-1", "premium_ils": "1500.00", "renewal_date": "2027-06-01"}
    assert (
        bo.post(f"/api/applications/{app}/issue", headers=bh, json=body).json()["detail"]
        == "signed_application_missing"
    )
    out = c.post(
        f"/api/applications/{app}/send-for-signature",
        headers=h,
        json={"document_id": doc, "signer_contact": "d@x.il"},
    ).json()
    ref = env.es.sent[-1]["ref"]
    assert (
        env.db.execute(
            text("SELECT doc_type FROM document ORDER BY received_at DESC LIMIT 1")
        ).scalar_one()
        == "other"
    )
    webhook(env, ref)
    assert (
        env.db.execute(
            text("SELECT doc_type FROM document ORDER BY received_at DESC LIMIT 1")
        ).scalar_one()
        == "application"
    )
    assert (
        bo.post(
            f"/api/applications/{app}/issue", headers=bh, json={**body, "renewal_date": None}
        ).json()["detail"]
        == "renewal_date_required"
    )
    r = bo.post(f"/api/applications/{app}/issue", headers=bh, json=body)
    assert r.status_code == 201 and r.json()["checklist"]["missing"] == ["insurer_confirmation"]
    pid = r.json()["policy_id"]
    assert out["status"] == "sent"
    conf = upload(env, cid, "policy")
    assert (
        bo.put(
            f"/api/applications/{app}/confirmation", headers=bh, json={"document_id": conf}
        ).status_code
        == 200
    )
    assert bo.get(f"/api/policies/{pid}/checklist").json() == {
        "items": {
            "advice_record": True,
            "signed_application": True,
            "insurer_confirmation": True,
            "renewal_date": True,
        },
        "complete": True,
        "missing": [],
    }
    assert (
        bo.post(f"/api/applications/{app}/issue", headers=bh, json=body).json()["detail"]
        == "already_issued"
    )


def test_f2_a_policy_added_by_hand_shows_what_is_missing(env: Env) -> None:
    cid = mk_client(env)
    c, h = env.who("agent")
    pid = c.post(
        f"/api/clients/{cid}/policies",
        headers=h,
        json={"insurer": "Harel", "policy_number": "X", "product": "car", "premium_ils": "10"},
    ).json()["id"]
    r = c.get(f"/api/policies/{pid}/checklist").json()
    assert r["complete"] is False and set(r["missing"]) == {
        "advice_record",
        "signed_application",
        "insurer_confirmation",
        "renewal_date",
    }


# ---- F3 ---------------------------------------------------------------------------------------
def policy(
    e: Env, cid: str, renew: date, number: str = "R-1", product: str = "car", role: str = "agent"
) -> str:
    c, h = e.who(role)
    return str(
        c.post(
            f"/api/clients/{cid}/policies",
            headers=h,
            json={
                "insurer": "Harel",
                "policy_number": number,
                "product": product,
                "premium_ils": "100",
                "renewal_date": renew.isoformat(),
            },
        ).json()["id"]
    )


def test_f3_queue_stages_and_agent_scope(env: Env) -> None:
    mine, other = mk_client(env), mk_client(env, "backoffice", "000000026")
    t = TODAY
    for i, d in enumerate((70, 55, 25, 8, -3)):
        policy(env, mine, t + timedelta(days=d), f"A{i}")
    policy(env, other, t + timedelta(days=5), "B1", role="backoffice")
    c, _ = env.who("agent")
    q = c.get("/api/renewals").json()
    assert [(r["days"], r["stage"]) for r in q] == [
        (-3, "overdue"),
        (8, "t10"),
        (25, "t30"),
        (55, "t60"),
    ]
    assert all(
        r["agent_id"] == env.uid("agent") for r in q
    )  # the back-office client's renewal is not shown to this agent
    bo, _ = env.who("backoffice")
    assert len(bo.get("/api/renewals").json()) == 5
    assert len(bo.get("/api/renewals?within=10").json()) == 3


def test_f3_outcomes_are_required_and_validated(env: Env) -> None:
    cid = mk_client(env)
    pid = policy(env, cid, TODAY + timedelta(days=20))
    c, h = env.who("agent")
    post = lambda **b: c.post(f"/api/policies/{pid}/renewal", headers=h, json=b)  # noqa: E731
    assert post(outcome="maybe").status_code == 422
    assert post(outcome="cancelled").json()["detail"] == "reason_required"
    assert post(outcome="renewed").json()["detail"] == "new_renewal_date_required"
    nxt = (TODAY + timedelta(days=385)).isoformat()
    r = post(outcome="renewed", new_renewal_date=nxt)
    assert r.status_code == 201 and r.json()["renewal_date"] == nxt
    assert c.get("/api/renewals").json() == []  # the cycle is closed and the next one is far away


def test_f3_cancellation_goes_to_the_manager_and_lapse_leaves_the_queue(env: Env) -> None:
    cid = mk_client(env)
    p1, p2 = (
        policy(env, cid, TODAY + timedelta(days=5), "C1"),
        policy(env, cid, TODAY + timedelta(days=6), "C2"),
    )
    c, h = env.who("agent")
    assert (
        c.post(
            f"/api/policies/{p1}/renewal",
            headers=h,
            json={"outcome": "cancelled", "reason": "moved abroad"},
        ).json()["status"]
        == "cancelled"
    )
    assert (
        c.post(
            f"/api/policies/{p2}/renewal",
            headers=h,
            json={"outcome": "lapsed", "reason": "no payment"},
        ).json()["status"]
        == "lapsed"
    )
    assert (
        env.db.execute(text("SELECT role, title FROM inbox_item WHERE type='retention'")).one()[0]
        == "manager"
    )
    assert c.get("/api/renewals").json() == []


def test_f3_leaks_list_is_for_the_manager_and_shows_policies_past_their_date(env: Env) -> None:
    cid = mk_client(env)
    policy(env, cid, TODAY - timedelta(days=2), "L1")
    policy(env, cid, TODAY + timedelta(days=2), "L2")
    c, _ = env.who("agent")
    assert c.get("/api/renewals/leaks").status_code == 403
    m, _ = env.who("manager")
    leaks = m.get("/api/renewals/leaks").json()
    assert [r["stage"] for r in leaks] == ["overdue"]


def test_f3_the_daily_tick_makes_one_item_per_stage(env: Env) -> None:
    cid = mk_client(env)
    policy(env, cid, TODAY + timedelta(days=9), "T1")
    assert service.renewal_tick(env.db) == 1
    env.db.commit()
    assert service.renewal_tick(env.db) == 0  # repeated runs add nothing
    row = env.db.execute(
        text("SELECT role, urgency, assignee_id FROM inbox_item WHERE type='renewal'")
    ).one()
    assert row[0] == "agent" and row[1] == "urgent" and str(row[2]) == env.uid("agent")
    assert service.renewal_tick(env.db, TODAY + timedelta(days=20)) == 1  # now overdue: a new item


# ---- F4 ---------------------------------------------------------------------------------------
def claim(e: Env, cid: str, pid: str) -> dict[str, Any]:
    c, h = e.who("agent")
    r = c.post(
        f"/api/clients/{cid}/claims",
        headers=h,
        json={
            "policy_id": pid,
            "event_date": "2026-10-30",
            "place": "Haifa",
            "description": "rear-end collision",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


def test_f4_a_claim_has_an_owner_a_timeline_and_a_checklist_with_completion(env: Env) -> None:
    cid = mk_client(env)
    pid = policy(env, cid, TODAY + timedelta(days=200))
    cl = claim(env, cid, pid)
    assert cl["owner_id"] == env.uid("agent") and cl["status"] == "open"
    assert [i["label"] for i in cl["checklist"]] == [
        "driving_licence",
        "photos",
        "police_report",
        "repair_invoice",
    ]
    assert cl["completion_percent"] == 0 and [t["kind"] for t in cl["timeline"]] == ["opened"]
    c, h = env.who("agent")
    for item in cl["checklist"][:2]:
        d = upload(env, cid)
        r = c.put(
            f"/api/claims/{cl['id']}/checklist/{item['id']}", headers=h, json={"document_id": d}
        )
    assert r.json()["completion_percent"] == 50
    r = c.post(f"/api/claims/{cl['id']}/submit", headers=h, json={"insurer_ref": "H-99"})
    assert r.json()["status"] == "submitted" and r.json()["insurer_ref"] == "H-99"
    assert (
        c.post(
            f"/api/claims/{cl['id']}/submit", headers=h, json={"insurer_ref": "H-99"}
        ).status_code
        == 409
    )
    r = c.post(
        f"/api/claims/{cl['id']}/outcome", headers=h, json={"outcome": "paid", "paid_ils": "4200"}
    )
    assert r.json()["status"] == "paid" and float(r.json()["paid_ils"]) == 4200.0
    assert [t["kind"] for t in r.json()["timeline"]] == [
        "opened",
        "document",
        "document",
        "submitted",
        "paid",
    ]


def test_f4_reassigning_keeps_an_owner_and_validation(env: Env) -> None:
    cid = mk_client(env)
    other = mk_client(env, "backoffice", "000000026")
    pid, opid = (
        policy(env, cid, TODAY + timedelta(days=200)),
        policy(env, other, TODAY + timedelta(days=200), "O1", role="backoffice"),
    )
    c, h = env.who("agent")
    assert (
        c.post(
            f"/api/clients/{cid}/claims",
            headers=h,
            json={"policy_id": opid, "event_date": "2026-10-30", "description": "x y z"},
        ).status_code
        == 422
    )
    cl = claim(env, cid, pid)
    bo, _ = env.who("backoffice")
    r = c.put(f"/api/claims/{cl['id']}/owner", headers=h, json={"owner_id": env.uid("backoffice")})
    assert r.json()["owner_id"] == env.uid("backoffice")
    assert (
        c.put(f"/api/claims/{cl['id']}/owner", headers=h, json={"owner_id": None}).status_code
        == 422
    )
    assert (
        c.post(
            f"/api/claims/{cl['id']}/outcome", headers=h, json={"outcome": "paid", "paid_ils": "1"}
        ).status_code
        == 409
    )  # not submitted yet
    d = upload(env, other)
    foreign_item = cl["checklist"][0]["id"]
    assert (
        c.put(
            f"/api/claims/{cl['id']}/checklist/{foreign_item}", headers=h, json={"document_id": d}
        ).status_code
        == 422
    )  # another client's document


def test_f4_an_unhappy_rejection_opens_a_complaint_for_the_manager(env: Env) -> None:
    cid = mk_client(env)
    cl = claim(env, cid, policy(env, cid, TODAY + timedelta(days=200), product="health"))
    c, h = env.who("agent")
    c.post(f"/api/claims/{cl['id']}/submit", headers=h, json={"insurer_ref": "Z1"})
    assert (
        c.post(f"/api/claims/{cl['id']}/outcome", headers=h, json={"outcome": "paid"}).status_code
        == 422
    )
    r = c.post(
        f"/api/claims/{cl['id']}/outcome",
        headers=h,
        json={"outcome": "rejected", "note": "pre-existing", "client_unhappy": True},
    )
    assert r.json()["status"] == "rejected"
    assert (
        env.db.execute(text("SELECT role FROM inbox_item WHERE type='complaint'")).scalar_one()
        == "manager"
    )


def test_f4_late_insurer_reminds_the_owner_once_per_period(env: Env) -> None:
    cid = mk_client(env)
    cl = claim(env, cid, policy(env, cid, TODAY + timedelta(days=200)))
    c, h = env.who("agent")
    c.post(f"/api/claims/{cl['id']}/submit", headers=h, json={"insurer_ref": "Z2"})
    now = datetime.now(UTC)
    assert service.late_claims(env.db, now + timedelta(days=13)) == 0
    assert service.late_claims(env.db, now + timedelta(days=15)) == 1
    env.db.commit()
    assert service.late_claims(env.db, now + timedelta(days=16)) == 0
    assert service.late_claims(env.db, now + timedelta(days=30)) == 1
    env.db.commit()
    assert [t["kind"] for t in c.get(f"/api/claims/{cl['id']}").json()["timeline"]].count(
        "reminder"
    ) == 2


# ---- access ---------------------------------------------------------------------------------------
def test_flow_endpoints_respect_visibility_and_roles(env: Env) -> None:
    cid = mk_client(env, "backoffice")  # the agent's colleague's client: not visible to this agent
    c, h = env.who("agent")
    assert c.put(f"/api/clients/{cid}/kyc", headers=h, json={"data": KYC}).status_code == 404
    assert c.get(f"/api/clients/{cid}/flow-status").status_code == 404
    mk, mh = env.who("marketing")
    assert mk.get(f"/api/clients/{cid}/flow-status").status_code == 403
    assert mk.post(f"/api/clients/{cid}/activate", headers=mh).status_code == 403
    assert c.post(f"/api/clients/{cid}/activate").status_code in (401, 403)  # no CSRF token
