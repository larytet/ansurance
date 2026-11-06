"""T-M3-1, T-M3-2. REQUIREMENTS section 6."""

import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session

from app.config import Settings
from app.main import create_app
from tests.conftest import Account, login, make_account


class Env:
    def __init__(self, app: Any, db: Session) -> None:
        self.app, self.db = app, db

    def user(self, role: str, n: int = 1) -> tuple[TestClient, dict[str, str], Account]:
        a = make_account(self.db, f"{role}{n}@agency.il", [role])
        c = TestClient(self.app, base_url="https://testserver")
        return c, login(c, self.db, a), a


@pytest.fixture
def env(settings: Settings, fresh_db: Engine, db: Session) -> Env:
    return Env(create_app(settings, fresh_db), db)


def new(
    c: TestClient, hdr: dict[str, str], role: str, title: str = "Do the thing", **kw: Any
) -> dict[str, Any]:
    r = c.post(
        "/api/inbox", headers=hdr, json={"type": "other", "role": role, "title": title, **kw}
    )
    assert r.status_code == 201, r.text
    return dict(r.json())


def ids(c: TestClient, **params: Any) -> list[str]:
    return [i["id"] for i in c.get("/api/inbox", params=params).json()["items"]]


# visibility ---------------------------------------------------------------------------------
def test_each_role_sees_only_its_own_inbox(env: Env) -> None:
    ca, ha, _ = env.user("agent")
    cb, hb, _ = env.user("backoffice")
    cm, hm, _ = env.user("marketing")
    to_agent, to_bo = new(ca, ha, "agent"), new(ca, ha, "backoffice")
    assert ids(ca) == [to_agent["id"]] and ids(cb) == [to_bo["id"]] and ids(cm) == []
    assert cb.get("/api/inbox", params={"role": "agent"}).status_code == 403
    assert cm.post(f"/api/inbox/{to_agent['id']}/done", headers=hm, json={}).status_code == 404


def test_assignee_restricts_visibility_within_a_role(env: Env) -> None:
    c1, h1, a1 = env.user("agent", 1)
    c2, _, _ = env.user("agent", 2)
    mine = new(c1, h1, "agent", assignee_id=a1.id)
    shared = new(c1, h1, "agent")
    assert set(ids(c1)) == {mine["id"], shared["id"]}
    assert ids(c2) == [shared["id"]]  # an item assigned to a colleague is not theirs


def test_manager_can_view_other_inboxes_read_only(env: Env) -> None:
    ca, ha, _ = env.user("agent")
    cm, hm, _ = env.user("manager")
    item = new(ca, ha, "agent")
    r = cm.get("/api/inbox", params={"role": "agent"}).json()
    assert [i["id"] for i in r["items"]] == [item["id"]] and r["read_only"] is True
    assert cm.get("/api/inbox/counts", params={"role": "agent"}).json()["open"] == 1
    assert cm.get("/api/inbox", params={"role": "nonsense"}).json()["items"] == []


# nothing disappears silently ----------------------------------------------------------------
def test_no_delete_and_dismiss_needs_a_reason(env: Env) -> None:
    c, h, _ = env.user("agent")
    item = new(c, h, "agent")
    assert c.delete(f"/api/inbox/{item['id']}", headers=h).status_code in (404, 405)
    assert c.post(f"/api/inbox/{item['id']}/dismiss", headers=h, json={}).status_code == 422
    assert (
        c.post(f"/api/inbox/{item['id']}/dismiss", headers=h, json={"reason": "no"}).status_code
        == 422
    )
    r = c.post(
        f"/api/inbox/{item['id']}/dismiss",
        headers=h,
        json={"reason": "client called back, handled"},
    )
    assert r.status_code == 200 and r.json()["status"] == "dismissed"
    assert ids(c) == [] and ids(c, status="dismissed") == [item["id"]]
    assert c.post(f"/api/inbox/{item['id']}/done", headers=h, json={}).status_code == 409


def test_database_refuses_to_delete_or_edit_history(env: Env) -> None:
    c, h, _ = env.user("agent")
    new(c, h, "agent")
    for sql in ("DELETE FROM inbox_event", "UPDATE inbox_event SET note = 'x'"):
        with pytest.raises(DatabaseError):
            env.db.execute(text(sql))
        env.db.rollback()


def test_reassign_between_roles_needs_a_note_and_records_history(env: Env) -> None:
    ca, ha, a = env.user("agent")
    cb, hb, _ = env.user("backoffice")
    item = new(ca, ha, "agent")
    assert (
        ca.post(
            f"/api/inbox/{item['id']}/reassign", headers=ha, json={"role": "backoffice"}
        ).status_code
        == 422
    )
    assert (
        ca.post(
            f"/api/inbox/{item['id']}/reassign",
            headers=ha,
            json={"role": "backoffice", "note": "needs the ID chased"},
        ).status_code
        == 200
    )
    assert ids(ca) == [] and ids(cb) == [item["id"]]
    ev = cb.get(f"/api/inbox/{item['id']}/events").json()
    assert [e["action"] for e in ev] == ["created", "reassigned"]
    assert ev[1]["from_role"] == "agent" and ev[1]["to_role"] == "backoffice"
    assert ev[1]["note"] == "needs the ID chased" and ev[1]["actor"] == a.id
    assert (
        cb.post(
            f"/api/inbox/{item['id']}/reassign",
            headers=hb,
            json={"role": "manager", "note": "needs approval"},
        ).status_code
        == 200
    )


def test_reassign_to_an_unknown_role_or_user_is_rejected(env: Env) -> None:
    c, h, _ = env.user("agent")
    item = new(c, h, "agent")
    for body in (
        {"role": "god", "note": "because"},
        {"role": "agent", "note": "because", "assignee_id": str(uuid.uuid4())},
    ):
        assert c.post(f"/api/inbox/{item['id']}/reassign", headers=h, json=body).status_code == 422


def test_done_records_resolution_and_counts(env: Env) -> None:
    c, h, _ = env.user("agent")
    a, b = new(c, h, "agent"), new(c, h, "agent", urgency="urgent")
    assert c.get("/api/inbox/counts").json() == {
        "open": 2,
        "urgent": 1,
        "done_today": 0,
        "read_only": False,
    }
    assert c.post(f"/api/inbox/{a['id']}/done", headers=h, json={"note": "sent"}).status_code == 200
    counts = c.get("/api/inbox/counts").json()
    assert counts["open"] == 1 and counts["done_today"] == 1 and counts["urgent"] == 1
    assert b["id"] in ids(c)


def test_create_validates_and_is_audited(env: Env) -> None:
    c, h, _ = env.user("agent")
    for bad in ({"type": "nope"}, {"role": "god"}, {"urgency": "meh"}):
        body = {"type": "other", "role": "agent", "title": "t1", **bad}
        assert c.post("/api/inbox", headers=h, json=body).status_code == 422
    new(c, h, "agent")
    n = env.db.execute(text("SELECT count(*) FROM audit_entry WHERE action='inbox.create'"))
    assert n.scalar_one() == 1


# urgency and ordering -----------------------------------------------------------------------
def test_urgency_rises_when_the_deadline_passes_and_orders_first(env: Env) -> None:
    c, h, _ = env.user("agent")
    normal = new(c, h, "agent", "normal one")
    soon = new(
        c,
        h,
        "agent",
        "turns urgent",
        urgent_from=(datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
    )
    later = new(
        c, h, "agent", "not yet", urgent_from=(datetime.now(UTC) + timedelta(days=3)).isoformat()
    )
    important = new(c, h, "agent", "important one", urgency="important")
    items = c.get("/api/inbox").json()["items"]
    assert [i["id"] for i in items] == [soon["id"], important["id"], normal["id"], later["id"]]
    assert [i["urgency"] for i in items] == ["urgent", "important", "normal", "normal"]
    assert ids(c, urgency="urgent") == [soon["id"]]


def test_filter_by_type_and_status(env: Env) -> None:
    c, h, _ = env.user("backoffice")
    a = new(c, h, "backoffice", type="id_missing")
    new(c, h, "backoffice", type="tax_certificate")
    assert ids(c, type="id_missing") == [a["id"]]
    assert ids(c, status="done") == []


# pagination ---------------------------------------------------------------------------------
def test_keyset_pagination_visits_every_item_once_in_order(env: Env) -> None:
    c, h, _ = env.user("agent")
    for i in range(23):
        new(c, h, "agent", f"item {i}", urgency=("urgent", "important", "normal")[i % 3])
    seen: list[dict[str, Any]] = []
    cursor = None
    while True:
        r = c.get(
            "/api/inbox", params={"limit": 5, **({"cursor": cursor} if cursor else {})}
        ).json()
        seen += r["items"]
        cursor = r["next"]
        if not cursor:
            break
    assert len(seen) == 23 and len({i["id"] for i in seen}) == 23
    ranks = [{"urgent": 0, "important": 1, "normal": 2}[i["urgency"]] for i in seen]
    assert ranks == sorted(ranks)
    assert c.get("/api/inbox", params={"cursor": "garbage"}).status_code == 422


# T-M3-2: 10,000 items open in under one second --------------------------------------------------
def test_inbox_with_10000_items_opens_under_one_second(env: Env) -> None:
    c, h, a = env.user("agent")
    env.db.execute(
        text("""
        INSERT INTO inbox_item (id, type, role, title, detail, urgency, status, created_at)
        SELECT gen_random_uuid(), 'other', 'agent', 'bulk ' || g, '',
               (ARRAY['urgent','important','normal'])[1 + g % 3], 'open',
               now() - (g || ' seconds')::interval
        FROM generate_series(1, 10000) g
    """)
    )
    env.db.commit()
    timings = []
    for _ in range(20):
        t = time.perf_counter()
        r = c.get("/api/inbox", params={"limit": 50})
        timings.append(time.perf_counter() - t)
        assert r.status_code == 200 and len(r.json()["items"]) == 50
    timings.sort()
    p95 = timings[int(len(timings) * 0.95) - 1]
    assert p95 < 1.0, f"p95 {p95:.3f}s"
    t = time.perf_counter()
    counts = c.get("/api/inbox/counts").json()
    assert time.perf_counter() - t < 1.0 and counts["open"] == 10000
    t = time.perf_counter()
    deep = c.get("/api/inbox", params={"urgency": "normal", "type": "other", "limit": 200})
    assert deep.status_code == 200 and time.perf_counter() - t < 1.0
