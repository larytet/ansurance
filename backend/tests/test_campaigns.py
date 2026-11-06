"""M10: campaigns (F10). The consent gate is a hard stop; everything else is checked around it.
WhatsApp is the fake adapter; e-mail and SMS messages are queued but have no delivery adapter."""

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.adapters.antivirus import FakeAntivirus
from app.blob.fake import FakeBlobStore
from app.config import Settings
from app.consent import service as consent
from app.integrations.whatsapp import FakeWhatsApp
from app.jobs import queue
from app.main import create_app
from app.messaging.handlers import make_delivery_handlers
from app.models import Client, Insurer, OutboxMessage, Policy
from app.text import build_search
from tests.conftest import login, make_account

SECRET = "wa-secret"
SEG = {"has_product": ["pension"]}


class Env:
    def __init__(self, api: TestClient, wa: FakeWhatsApp, db: Session, settings: Settings) -> None:
        self.api, self.wa, self.db, self.settings = api, wa, db, settings
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

    def run_jobs(self, limit: int = 200) -> None:
        handlers = make_delivery_handlers(self.settings, self.wa)
        for _ in range(limit):
            if not queue.run_one(self.db, handlers):
                return
            self.db.commit()


@pytest.fixture
def env(settings: Settings, fresh_db: Engine, db: Session) -> Env:
    settings = settings.model_copy(
        update={
            "whatsapp_app_secret": SecretStr(SECRET),
            "campaign_batch_size": 2,
            "campaign_batch_seconds": 0,
        }
    )
    wa = FakeWhatsApp()
    app = create_app(
        settings, fresh_db, blobs=FakeBlobStore(), antivirus=FakeAntivirus(), whatsapp=wa
    )
    return Env(TestClient(app, base_url="https://testserver"), wa, db, settings)


_n = iter(range(1, 10_000))


def person(
    e: Env,
    name: str,
    lang: str = "he",
    consent_state: str | None = "opt_in",
    products: tuple[str, ...] = ("pension",),
    agent: str = "agent",
    channel: str = "whatsapp",
) -> Client:
    n = next(_n)
    plain, skel = build_search(name, f"{n:09d}")
    cl = Client(
        id_number=f"{n:09d}",
        name=name,
        search_text=plain,
        search_skel=skel,
        preferred_language=lang,
        phone=f"050-{n:07d}",
        email=f"c{n}@x.il",
        status="active",
        agent_id=e.uid(agent),
    )
    e.db.add(cl)
    e.db.flush()
    ins = e.db.query(Insurer).first() or Insurer(name="Harel")
    e.db.add(ins)
    e.db.flush()
    for i, p in enumerate(products):
        e.db.add(
            Policy(
                client_id=cl.id,
                insurer_id=ins.id,
                policy_number=f"P{n}-{i}",
                product=p,
                premium_ils=10,
                renewal_date=datetime.now(UTC).date() + timedelta(days=40),
            )
        )
    if consent_state:
        consent.record(e.db, cl, channel, consent_state, "test", None, None)
    e.db.commit()
    return cl


def make(e: Env, role: str = "marketing", **over: Any) -> dict[str, Any]:
    c, h = e.who(role)
    body = {
        "name": "Pension notice",
        "type": "offer",
        "benefit": "a cheaper fund for the same cover",
        "segment": SEG,
        "base_language": "he",
        "body": "יש לנו הצעה לקרן פנסיה",
        **over,
    }
    r = c.post("/api/campaigns", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


def start(e: Env, cid: str, role: str = "manager") -> Any:
    c, h = e.who(role)
    return c.post(f"/api/campaigns/{cid}/start", headers=h)


# ---- creation rules ---------------------------------------------------------------------------
def test_a_campaign_needs_a_stated_client_benefit_and_valid_rules(env: Env) -> None:
    c, h = env.who("marketing")
    base = {
        "name": "n1",
        "type": "offer",
        "segment": SEG,
        "base_language": "he",
        "body": "text here",
    }
    assert (
        c.post("/api/campaigns", json={**base, "benefit": " "}, headers=h).json()["detail"]
        == "client_benefit_required"
    )
    assert (
        c.post(
            "/api/campaigns", json={**base, "benefit": "a real benefit", "type": "spam"}, headers=h
        ).status_code
        == 422
    )
    r = c.post(
        "/api/campaigns",
        json={**base, "benefit": "a real benefit", "segment": {"salary": 1}},
        headers=h,
    )
    assert r.json()["detail"]["error"] == "unknown_segment_rule"
    r = c.post(
        "/api/campaigns",
        json={**base, "benefit": "a real benefit", "segment": {"has_product": "pension"}},
        headers=h,
    )
    assert r.status_code == 422


@pytest.mark.parametrize(
    "sales", ["Special offer: 20% off", "מבצע חם לכולם", "Buy now and save", "скидка 20%"]
)
def test_a_service_notice_may_not_contain_sales_wording(env: Env, sales: str) -> None:
    c, h = env.who("marketing")
    r = c.post(
        "/api/campaigns",
        headers=h,
        json={
            "name": "notice",
            "type": "regulatory",
            "benefit": "keeps you informed",
            "segment": {},
            "base_language": "en",
            "body": sales,
        },
    )
    assert r.status_code == 422 and r.json()["detail"] == "sales_content_in_service_campaign"
    ok = c.post(
        "/api/campaigns",
        headers=h,
        json={
            "name": "notice",
            "type": "regulatory",
            "benefit": "keeps you informed",
            "segment": {},
            "base_language": "en",
            "body": "A rule change affects your policy",
        },
    )
    assert ok.status_code == 201 and ok.json()["purpose"] == "service"


def test_roles_and_visibility(env: Env) -> None:
    c, h = env.who("backoffice")
    assert c.post("/api/campaigns", headers=h, json={}).status_code == 403
    mine = make(env, "agent")
    other, _ = env.who("marketing")
    assert (
        other.get(f"/api/campaigns/{mine['id']}").status_code == 200
    )  # marketing sees every campaign
    env.who("agent")
    a2 = make_account(env.db, "agent2@agency.il", ["agent"])
    c2 = TestClient(env.api.app, base_url="https://testserver")  # type: ignore[arg-type]
    login(c2, env.db, a2)
    assert c2.get(f"/api/campaigns/{mine['id']}").status_code == 404  # another agent's is invisible
    assert c2.get("/api/campaigns").json() == []


# ---- audience and the consent gate ------------------------------------------------------------
def test_the_audience_is_rule_based_and_consent_is_a_hard_stop(env: Env) -> None:
    person(env, "Opted In")
    person(env, "No Consent", consent_state=None)
    person(env, "Opted Out", consent_state="opt_out")
    person(env, "Wrong Product", products=("car",))
    person(env, "Email Only", consent_state="opt_in", channel="email")
    cid = make(env)["id"]
    c, h = env.who("marketing")
    pv = c.get(f"/api/campaigns/{cid}/preview", headers=h).json()
    assert pv["matching"] == 4 and pv["selected"] == 2
    assert pv["excluded"] == {"no_consent": 1, "opted_out": 1}
    assert sorted(s["channel"] for s in pv["sample"]) == ["email", "whatsapp"]
    assert pv["languages"] == {"he": 2}
    assert not any("No Consent" == s["name"] for s in pv["sample"])


def test_lacks_product_and_renewing_rules(env: Env) -> None:
    person(env, "Has Both", products=("pension", "life"))
    person(env, "Only Pension")
    cid = make(env, segment={"has_product": ["pension"], "lacks_product": ["life"]})["id"]
    c, h = env.who("marketing")
    assert c.get(f"/api/campaigns/{cid}/preview", headers=h).json()["matching"] == 1
    r = make(
        env,
        name="renew",
        type="renewal",
        segment={"renewing_within_days": 30},
        body="Your policy renews soon",
    )
    assert (
        c.get(f"/api/campaigns/{r['id']}/preview", headers=h).json()["matching"] == 0
    )  # renewals are in 40 days


def test_a_recent_marketing_contact_blocks_the_next_campaign(env: Env) -> None:
    cl = person(env, "Frequent")
    env.db.add(
        OutboxMessage(
            client_id=cl.id,
            channel="whatsapp",
            purpose="marketing",
            language="he",
            body="earlier",
            status="queued",
            created_by=env.uid("agent"),
        )
    )
    env.db.commit()
    cid = make(env)["id"]
    c, h = env.who("marketing")
    assert c.get(f"/api/campaigns/{cid}/preview", headers=h).json()["excluded"] == {
        "frequency_cap": 1
    }
    env.db.execute(text("UPDATE outbox_message SET created_at = now() - interval '31 days'"))
    env.db.commit()
    assert c.get(f"/api/campaigns/{cid}/preview", headers=h).json()["selected"] == 1


# ---- approval, translation, sending --------------------------------------------------------------
def test_an_offer_from_marketing_needs_the_manager_before_it_can_start(env: Env) -> None:
    person(env, "Client A")
    camp = make(env)
    assert camp["status"] == "pending_approval" and camp["requires_approval"]
    assert start(env, camp["id"], "marketing").json()["detail"] == "campaign_is_pending_approval"
    c, h = env.who("marketing")
    assert c.post(f"/api/campaigns/{camp['id']}/approve", headers=h).status_code == 403
    m, mh = env.who("manager")
    assert m.post(f"/api/campaigns/{camp['id']}/approve", headers=mh).json()["approved"] is True
    assert start(env, camp["id"], "marketing").status_code == 200
    assert m.post(f"/api/campaigns/{camp['id']}/approve", headers=mh).status_code == 409


def test_a_manager_campaign_and_a_renewal_notice_need_no_approval(env: Env) -> None:
    assert make(env, "manager")["status"] == "draft"
    n = make(env, type="renewal", body="Your policy renews soon", name="renew notice")
    assert n["status"] == "draft" and not n["requires_approval"]


def test_each_language_needs_a_reviewed_translation_before_the_first_send(env: Env) -> None:
    person(env, "Hebrew", "he")
    person(env, "Russian", "ru")
    person(env, "Arabic", "ar")
    cid = make(env, "manager")["id"]
    r = start(env, cid)
    assert r.status_code == 409 and r.json()["detail"] == {
        "error": "translation_review_missing",
        "detail": ["ar", "ru"],
    }
    assert (
        env.db.execute(text("SELECT count(*) FROM campaign_recipient")).scalar_one() == 0
    )  # nothing frozen yet
    c, h = env.who("marketing")
    assert (
        c.put(
            f"/api/campaigns/{cid}/translations/he", headers=h, json={"body": "same language"}
        ).status_code
        == 422
    )
    for lang, body in (("ru", "У нас есть предложение"), ("ar", "لدينا عرض")):
        assert (
            c.put(
                f"/api/campaigns/{cid}/translations/{lang}", headers=h, json={"body": body}
            ).status_code
            == 200
        )
    assert start(env, cid).status_code == 200


def test_batches_are_throttled_and_each_message_carries_the_label_and_unsubscribe(env: Env) -> None:
    for i in range(5):
        person(env, f"Client {i}", "he")
    cid = make(env, "manager")["id"]
    assert start(env, cid).json() == {"selected": 5, "excluded": 0}
    handlers = make_delivery_handlers(env.settings, env.wa)
    assert queue.run_one(env.db, handlers)  # the first batch: two recipients
    env.db.commit()
    assert env.db.execute(text("SELECT count(*) FROM outbox_message")).scalar_one() == 2
    env.run_jobs()
    assert len(env.wa.sent) == 5
    assert all(b.startswith("פרסומת: ") and "הסר" in b for _, b in env.wa.sent)
    st = env.db.execute(text("SELECT status FROM campaign")).scalar_one()
    assert st == "done"
    assert (
        env.db.execute(
            text("SELECT count(*) FROM audit_entry WHERE action='campaign.done'")
        ).scalar_one()
        == 1
    )


def test_translated_text_is_used_per_language_and_service_notices_have_no_ad_label(
    env: Env,
) -> None:
    person(env, "Russian", "ru", consent_state=None)  # a service notice needs no marketing consent
    cid = make(
        env,
        "manager",
        type="regulatory",
        segment={},
        body="A rule affects your policy",
        base_language="en",
    )["id"]
    c, h = env.who("manager")
    c.put(
        f"/api/campaigns/{cid}/translations/ru",
        headers=h,
        json={"body": "Правило влияет на ваш полис"},
    )
    assert start(env, cid).status_code == 200
    env.run_jobs()
    assert [b for _, b in env.wa.sent] == ["Правило влияет на ваш полис"]


def test_consent_is_checked_again_when_the_batch_is_sent(env: Env) -> None:
    person(env, "Keeps")
    gone = person(env, "Changes mind")
    cid = make(env, "manager")["id"]
    start(env, cid)
    consent.record(
        env.db, gone, "whatsapp", "opt_out", "STOP after the snapshot", None, None
    )  # between snapshot and send
    env.db.commit()
    env.run_jobs()
    assert len(env.wa.sent) == 1
    rows = dict(
        env.db.execute(
            text(
                "SELECT c.name, r.status FROM campaign_recipient r JOIN client c ON c.id=r.client_id"
            )
        ).all()
    )
    assert rows == {"Keeps": "queued", "Changes mind": "blocked"}
    res = env.api.app.state  # noqa: F841
    c, h = env.who("manager")
    out = c.get(f"/api/campaigns/{cid}/results", headers=h).json()
    assert out["exclusions"] == {"opted_out": 1} and out["unsubscribed"] == 1


def test_the_audience_snapshot_keeps_who_and_why(env: Env) -> None:
    person(env, "In")
    person(env, "Out", consent_state="opt_out")
    cid = make(env, "manager")["id"]
    start(env, cid)
    c, h = env.who("manager")
    snap = {
        r["name"]: (r["status"], r["reason"])
        for r in c.get(f"/api/campaigns/{cid}/audience", headers=h).json()
    }
    assert snap == {"In": ("selected", ""), "Out": ("excluded", "opted_out")}
    assert start(env, cid).status_code == 409  # a campaign starts once


# ---- replies and results -----------------------------------------------------------------------------
def inbound(e: Env, phone: str, body: str, pid: str = "in1") -> Any:
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": pid,
                                    "from": phone,
                                    "type": "text",
                                    "text": {"body": body},
                                    "timestamp": str(int(datetime.now(UTC).timestamp())),
                                }
                            ],
                            "statuses": [],
                        }
                    }
                ]
            }
        ]
    }
    raw = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return e.api.post("/api/webhooks/whatsapp", content=raw, headers={"x-hub-signature-256": sig})


def test_a_reply_lands_in_the_agent_inbox_as_a_campaign_reply_and_results_count_it(
    env: Env,
) -> None:
    cl = person(env, "Replier")
    cid = make(env, "manager")["id"]
    start(env, cid)
    env.run_jobs()
    digits = "972" + (cl.phone or "").replace("-", "")[1:]
    assert inbound(env, digits, "I am interested").status_code == 200
    row = env.db.execute(
        text("SELECT type, role, assignee_id, title FROM inbox_item WHERE type='campaign_reply'")
    ).one()
    assert (
        row[0] == "campaign_reply"
        and row[1] == "agent"
        and str(row[2]) == env.uid("agent")
        and "Pension notice" in row[3]
    )
    c, h = env.who("manager")
    assert c.get(f"/api/campaigns/{cid}/results", headers=h).json()["replied"] == 1
    assert (
        inbound(env, digits, "stop", "in2").status_code == 200
    )  # an unsubscribe is honoured at once
    assert (
        env.db.execute(
            text("SELECT state FROM consent_current WHERE client_id=:c AND channel='whatsapp'"),
            {"c": cl.id},
        ).scalar_one()
        == "opt_out"
    )
    assert c.get(f"/api/campaigns/{cid}/results", headers=h).json()["unsubscribed"] == 1


def test_delivery_statuses_feed_the_results(env: Env) -> None:
    person(env, "Reads")
    cid = make(env, "manager")["id"]
    start(env, cid)
    env.run_jobs()
    env.db.execute(text("UPDATE outbox_message SET delivery='read'"))
    env.db.commit()
    c, h = env.who("manager")
    out = c.get(f"/api/campaigns/{cid}/results", headers=h).json()
    assert out["delivery"] == {"read": 1} and out["recipients"] == {"queued": 1}
