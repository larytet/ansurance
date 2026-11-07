"""M10: complaints and compliance (F8), commission reconciliation (F7), manager Overview."""

import io
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.adapters.antivirus import FakeAntivirus
from app.blob.fake import FakeBlobStore
from app.config import Settings
from app.main import create_app
from app.manager import service
from app.models import Client, Insurer, KycForm, Policy
from app.text import build_search
from tests.conftest import login, make_account


class Env:
    def __init__(self, api: TestClient, db: Session, settings: Settings) -> None:
        self.api, self.db, self.settings = api, db, settings
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
    app = create_app(settings, fresh_db, blobs=FakeBlobStore(), antivirus=FakeAntivirus())
    return Env(TestClient(app, base_url="https://testserver"), db, settings)


_n = iter(range(1, 10_000))


def client(e: Env, name: str = "David Cohen", agent: str = "agent") -> Client:
    n = next(_n)
    plain, skel = build_search(name, f"{n:09d}")
    cl = Client(
        id_number=f"{n:09d}",
        name=name,
        search_text=plain,
        search_skel=skel,
        status="active",
        agent_id=e.uid(agent),
    )
    e.db.add(cl)
    e.db.commit()
    return cl


def policy(e: Env, cl: Client, insurer: str, number: str, product: str, premium: str) -> Policy:
    ins = e.db.query(Insurer).filter_by(name=insurer).first() or Insurer(name=insurer)
    e.db.add(ins)
    e.db.flush()
    p = Policy(
        client_id=cl.id,
        insurer_id=ins.id,
        policy_number=number,
        product=product,
        premium_ils=Decimal(premium),
    )
    e.db.add(p)
    e.db.commit()
    return p


# ---- F8 ---------------------------------------------------------------------------------------------
def test_a_complaint_is_logged_assigned_answered_and_exported(env: Env) -> None:
    cl = client(env)
    c, h = env.who("agent")
    r = c.post(
        "/api/complaints",
        headers=h,
        json={"client_id": str(cl.id), "channel": "phone", "text": "the claim took too long"},
    )
    assert r.status_code == 201 and r.json()["status"] == "open" and r.json()["overdue"] is False
    cid = r.json()["id"]
    due = datetime.fromisoformat(r.json()["due_at"]) - datetime.fromisoformat(r.json()["opened_at"])
    assert due == timedelta(days=env.settings.complaint_sla_days)
    assert (
        env.db.execute(text("SELECT role FROM inbox_item WHERE type='complaint'")).scalar_one()
        == "manager"
    )
    m, mh = env.who("manager")
    assert m.put(
        f"/api/complaints/{cid}/owner", headers=mh, json={"owner_id": env.uid("backoffice")}
    ).json()["owner_id"] == env.uid("backoffice")
    assert (
        c.put(
            f"/api/complaints/{cid}/owner", headers=h, json={"owner_id": env.uid("agent")}
        ).status_code
        == 403
    )
    bo, bh = env.who("backoffice")
    assert (
        bo.post(
            f"/api/complaints/{cid}/respond", headers=bh, json={"response": "we apologise"}
        ).json()["status"]
        == "answered"
    )
    assert (
        bo.post(
            f"/api/complaints/{cid}/respond", headers=bh, json={"response": "again"}
        ).status_code
        == 409
    )
    csv = m.get("/api/complaints/export.csv").text
    assert "the claim took too long" in csv and "we apologise" in csv
    assert (
        env.db.execute(
            text("SELECT count(*) FROM audit_entry WHERE action='export.complaints'")
        ).scalar_one()
        == 1
    )
    assert c.get("/api/complaints/export.csv").status_code == 403


def test_a_complaint_past_the_sla_turns_red_and_raises_one_urgent_item(env: Env) -> None:
    c, h = env.who("agent")
    cid = c.post("/api/complaints", headers=h, json={"text": "no reply for weeks"}).json()["id"]
    now = datetime.now(UTC) + timedelta(days=env.settings.complaint_sla_days + 1)
    m, _ = env.who("manager")
    assert m.get("/api/complaints").json()[0]["overdue"] is False  # not yet
    env.db.execute(text("UPDATE complaint SET due_at = now() - interval '1 day'"))
    env.db.commit()
    assert m.get("/api/complaints").json()[0]["overdue"] is True
    assert service.complaint_tick(env.db, now) == 1
    env.db.commit()
    assert service.complaint_tick(env.db, now) == 0
    assert env.db.execute(
        text("SELECT urgency, role FROM inbox_item WHERE type='complaint_overdue'")
    ).one() == ("urgent", "manager")
    assert cid


def test_complaint_visibility_and_validation(env: Env) -> None:
    c, h = env.who("agent")
    c.post("/api/complaints", headers=h, json={"text": "mine"})
    bo, bh = env.who("backoffice")
    assert bo.get("/api/complaints").json() == []  # others see only what they opened or own
    assert len(env.who("manager")[0].get("/api/complaints").json()) == 1
    assert c.post("/api/complaints", headers=h, json={"text": "x"}).status_code == 422
    assert (
        c.post(
            "/api/complaints", headers=h, json={"text": "valid text", "kind": "gossip"}
        ).status_code
        == 422
    )
    other = client(env, "Other", "backoffice")
    assert (
        c.post(
            "/api/complaints", headers=h, json={"text": "valid text", "client_id": str(other.id)}
        ).status_code
        == 404
    )
    mk, mh = env.who("marketing")
    assert mk.post("/api/complaints", headers=mh, json={"text": "valid text"}).status_code == 403


def test_compliance_dashboard(env: Env) -> None:
    m, mh = env.who("manager")
    today = datetime.now(UTC).date()
    for role, delta in (("agent", -5), ("backoffice", 30)):
        assert (
            m.put(
                f"/api/users/{env.uid(role)}/licence",
                headers=mh,
                json={
                    "licence_no": role,
                    "expires_on": (today + timedelta(days=delta)).isoformat(),
                    "cpd_required_hours": 20,
                    "cpd_done_hours": 12,
                },
            ).status_code
            == 200
        )
    cl = client(env)
    env.db.add(
        KycForm(
            client_id=cl.id,
            data={},
            risk="normal",
            completed_at=datetime.now(UTC) - timedelta(days=350),
        )
    )
    env.db.commit()
    c, h = env.who("agent")
    c.post("/api/complaints", headers=h, json={"text": "privacy question", "kind": "privacy"})
    out = m.get("/api/compliance").json()
    states = {x["licence_no"]: (x["state"], x["cpd_missing_hours"]) for x in out["licences"]}
    assert states == {"agent": ("expired", 8), "backoffice": ("expiring", 8)}
    assert (
        out["kyc_expiring"] == 1
        and out["complaints_unanswered"] == 1
        and out["privacy_requests_open"] == 1
    )
    assert c.get("/api/compliance").status_code == 403


def test_the_overview_counts_what_needs_the_manager(env: Env) -> None:
    cl = client(env)
    p = policy(env, cl, "Harel", "O1", "car", "1000")
    env.db.execute(
        text("UPDATE policy SET renewal_date = :d"), {"d": date.today() - timedelta(days=5)}
    )
    env.db.add(KycForm(client_id=cl.id, data={}, risk="flagged"))
    env.db.commit()
    m, _ = env.who("manager")
    o = m.get("/api/overview").json()
    assert (
        o["renewals_overdue"] == 1
        and o["kyc_waiting_approval"] == 1
        and o["clients"] == 1
        and o["claims_open"] == 0
    )
    assert env.who("agent")[0].get("/api/overview").status_code == 403
    assert p


# ---- F7 ---------------------------------------------------------------------------------------------------
def set_terms(env: Env) -> None:
    bo, bh = env.who("backoffice")
    for product, rate in (("car", "10"), ("pension", "2.5")):
        assert (
            bo.put(
                "/api/commissions/terms",
                headers=bh,
                json={"insurer": "Harel", "product": product, "rate_pct": rate},
            ).status_code
            == 200
        )


def statement(
    env: Env, lines: list[tuple[str, str]], total: str | None = None, period: str = "2026-10"
) -> Any:
    bo, bh = env.who("backoffice")
    total = total if total is not None else str(sum(Decimal(a) for _, a in lines))
    return bo.post(
        "/api/commissions/statements",
        headers=bh,
        json={
            "insurer": "Harel",
            "period": period,
            "declared_total": total,
            "lines": [{"policy_number": n, "amount": a} for n, a in lines],
        },
    )


def test_reconciliation_classifies_every_line_and_drops_nothing(env: Env) -> None:
    a, b = client(env, "A"), client(env, "B")
    policy(env, a, "Harel", "C1", "car", "1000")  # expected 100.00
    policy(env, a, "Harel", "C2", "pension", "400")  # expected 10.00
    policy(env, b, "Harel", "C3", "car", "500")  # expected 50.00, absent from the statement
    policy(env, b, "Harel", "C4", "gemel", "300")  # no terms
    policy(env, b, "Harel", "C5", "car", "200")  # a clawback
    set_terms(env)
    r = statement(
        env, [("C1", "100.00"), ("C2", "9.00"), ("C4", "5.00"), ("C5", "-20.00"), ("ZZ9", "7.50")]
    )
    assert r.status_code == 201, r.text
    res = {ln["policy_number"]: ln["result"] for ln in r.json()["lines"]}
    assert res == {
        "C1": "matched",
        "C2": "wrong_amount",
        "C3": "missing",
        "C4": "no_terms",
        "C5": "clawback",
        "ZZ9": "unknown_policy",
    }
    assert (
        r.json()["lines_total"] == "101.50"
        and r.json()["declared_total"] == "101.50"
        and r.json()["ties"] is True
    )
    assert r.json()["counts"]["matched"] == 1 and len(r.json()["lines"]) == 6
    # every difference is a task for the back-office
    assert (
        env.db.execute(
            text(
                "SELECT count(*) FROM inbox_item WHERE type='commission_diff' AND role='backoffice'"
            )
        ).scalar_one()
        == 5
    )


def test_totals_must_tie_to_the_agora_and_a_period_imports_once(env: Env) -> None:
    set_terms(env)
    r = statement(env, [("A1", "10.00"), ("A2", "5.50")], total="15.49")
    assert r.status_code == 422 and r.json()["detail"]["detail"] == {
        "declared": "15.49",
        "lines": "15.50",
        "difference": "0.01",
    }
    assert (
        env.db.execute(text("SELECT count(*) FROM commission_statement")).scalar_one() == 0
    )  # nothing half-imported
    assert statement(env, [("A1", "10.00")]).status_code == 201
    assert statement(env, [("A1", "10.00")]).json()["detail"] == "statement_already_imported"
    assert statement(env, [("A1", "10.00")], period="26-10").status_code == 422
    assert statement(env, [], total="0").status_code == 422


def test_approval_and_payout_per_agent(env: Env) -> None:
    a = client(env, "A", "agent")
    b = client(env, "B", "backoffice")
    policy(env, a, "Harel", "P1", "car", "1000")
    policy(env, b, "Harel", "P2", "car", "500")
    set_terms(env)
    sid = statement(env, [("P1", "100.00"), ("P2", "50.00"), ("NOPE", "3.00")]).json()["id"]
    m, mh = env.who("manager")
    assert m.get(f"/api/commissions/statements/{sid}/payout").json()["detail"] == "not_approved"
    bo, bh = env.who("backoffice")
    assert (
        bo.post(f"/api/commissions/statements/{sid}/approve", headers=bh).status_code == 403
    )  # the manager approves
    assert (
        m.post(f"/api/commissions/statements/{sid}/approve", headers=mh).json()["status"]
        == "approved"
    )
    assert m.post(f"/api/commissions/statements/{sid}/approve", headers=mh).status_code == 409
    out = m.get(f"/api/commissions/statements/{sid}/payout").json()
    assert (
        out["agents"] == {"agent": "100.00", "backoffice": "50.00", "unassigned": "3.00"}
        and out["total"] == "153.00"
    )  # unmatched lines are not dropped


def test_excel_statement_import(env: Env) -> None:
    cl = client(env)
    policy(env, cl, "Harel", "X1", "car", "1000")
    set_terms(env)
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.append(["פוליסה", "עמלה"])
    ws.append(["X1", 100])
    ws.append(["X2", 12.5])
    buf = io.BytesIO()
    wb.save(buf)
    bo, bh = env.who("backoffice")
    r = bo.post(
        "/api/commissions/statements/xlsx",
        headers=bh,
        data={"insurer": "Harel", "period": "2026-09", "declared_total": "112.5"},
        files={"file": ("s.xlsx", buf.getvalue(), "application/octet-stream")},
    )
    assert r.status_code == 201 and r.json()["counts"] == {"matched": 1, "unknown_policy": 1}
    bad = bo.post(
        "/api/commissions/statements/xlsx",
        headers=bh,
        data={"insurer": "Harel", "period": "2026-08", "declared_total": "1"},
        files={"file": ("s.xlsx", b"junk", "application/octet-stream")},
    )
    assert bad.status_code == 422
    assert (
        env.who("agent")[0].get("/api/commissions/statements/" + r.json()["id"]).status_code == 403
    )


def test_user_list_for_choosing_an_owner_has_no_secrets_and_is_for_the_manager(env: Env) -> None:
    env.who("agent")
    env.who("backoffice")
    m, _ = env.who("manager")
    rows = m.get("/api/users").json()
    assert sorted(r["roles"][0] for r in rows) == ["agent", "backoffice", "manager"]
    assert set(rows[0]) == {"id", "name", "email", "roles", "active"}
    assert (
        "password" not in m.get("/api/users").text.lower()
        and "totp" not in m.get("/api/users").text.lower()
    )
    assert env.who("agent")[0].get("/api/users").status_code == 403
