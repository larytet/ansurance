"""T-M12-1 .. T-M12-5: tasks on the agent's Today and meetings on the client file."""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.flows.service import add_months
from app.tasks import service
from tests.conftest import Account, login, make_account

TODAY = datetime.now(UTC).date()


def _day(offset: int) -> str:
    return (TODAY + timedelta(days=offset)).isoformat()


def _browser(
    api: TestClient, db: Session, email: str, role: str
) -> tuple[TestClient, dict[str, str], Account]:
    acct = make_account(db, email, [role])
    c = TestClient(api.app, base_url="https://testserver")
    return c, login(c, db, acct), acct


def _task(
    c: TestClient, h: dict[str, str], text_: str, offset: int, client_id: str | None = None
) -> dict:  # type: ignore[type-arg]
    r = c.post(
        "/api/tasks", json={"text": text_, "due": _day(offset), "client_id": client_id}, headers=h
    )
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


def _view(c: TestClient, view: str, **params: str) -> list[str]:
    r = c.get("/api/tasks", params={"view": view, **params})
    assert r.status_code == 200, r.text
    return [t["text"] for t in r.json()]


def _client(c: TestClient, h: dict[str, str], idn: str = "123456782") -> str:
    r = c.post("/api/clients", json={"id_number": idn, "name": "David Cohen"}, headers=h)
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


# T-M12-1 ------------------------------------------------------------------------------
def test_today_chips_show_today_overdue_next_done_and_delegated(
    api: TestClient, db: Session
) -> None:
    ann, ha, _ = _browser(api, db, "ann@agency.il", "agent")
    _, _, dan = _browser(api, db, "dan@agency.il", "agent")
    _task(ann, ha, "call today", 0)
    _task(ann, ha, "late one", -3)
    _task(ann, ha, "later", 5)
    finished = _task(ann, ha, "finished", 0)
    handed = _task(ann, ha, "hand over", 1)

    assert _view(ann, "today") == ["call today", "finished"]
    assert _view(ann, "over") == ["late one"]
    assert _view(ann, "next") == ["hand over", "later"]  # by date

    assert (
        ann.patch(f"/api/tasks/{finished['id']}", json={"done": True}, headers=ha).status_code
        == 200
    )
    assert _view(ann, "today") == ["call today"] and _view(ann, "done") == ["finished"]
    assert (
        ann.patch(f"/api/tasks/{finished['id']}", json={"done": False}, headers=ha).status_code
        == 200
    )
    assert _view(ann, "done") == []

    r = ann.patch(f"/api/tasks/{handed['id']}", json={"owner_id": dan.id}, headers=ha)
    assert r.status_code == 200 and r.json()["owner_id"] == dan.id
    assert _view(ann, "deleg") == ["hand over"] and "hand over" not in _view(ann, "next")


def test_delegating_to_a_stranger_or_an_inactive_user_is_refused(
    api: TestClient, db: Session
) -> None:
    ann, ha, _ = _browser(api, db, "ann@agency.il", "agent")
    t = _task(ann, ha, "x", 1)
    nobody = "00000000-0000-0000-0000-000000000000"
    assert (
        ann.patch(f"/api/tasks/{t['id']}", json={"owner_id": nobody}, headers=ha).status_code == 422
    )
    _, _, gone = _browser(api, db, "gone@agency.il", "agent")
    db.execute(text("UPDATE user_account SET active = false WHERE email = 'gone@agency.il'"))
    db.commit()
    assert (
        ann.patch(f"/api/tasks/{t['id']}", json={"owner_id": gone.id}, headers=ha).status_code
        == 422
    )


def test_a_bad_view_or_empty_text_is_rejected(api: TestClient, db: Session) -> None:
    ann, ha, _ = _browser(api, db, "ann@agency.il", "agent")
    assert ann.get("/api/tasks", params={"view": "nope"}).status_code == 422
    assert ann.post("/api/tasks", json={"text": "", "due": _day(0)}, headers=ha).status_code == 422
    assert ann.post("/api/tasks", json={"text": "x", "due": _day(0)}).status_code == 403  # CSRF


# T-M12-3 ------------------------------------------------------------------------------
def test_a_meeting_is_recorded_with_a_summary_and_moves_the_service_contact_date(
    api: TestClient, db: Session
) -> None:
    ann, ha, _ = _browser(api, db, "ann@agency.il", "agent")
    cid = _client(ann, ha)
    assert ann.get(f"/api/clients/{cid}/meetings").json() == {
        "items": [],
        "service_contact_due": None,
    }
    old = ann.post(
        f"/api/clients/{cid}/meetings",
        json={"kind": "phone", "held_on": "2025-01-31", "subject": "Annual call"},
        headers=ha,
    )
    assert old.status_code == 201
    first = ann.get(f"/api/clients/{cid}/meetings").json()["service_contact_due"]
    assert first == add_months(datetime(2025, 1, 31).date(), 24).isoformat() == "2027-01-31"
    new = ann.post(
        f"/api/clients/{cid}/meetings",
        json={
            "kind": "in_person",
            "held_on": "2026-06-10",
            "subject": "Review",
            "summary": "Agreed to raise cover",
        },
        headers=ha,
    ).json()
    listing = ann.get(f"/api/clients/{cid}/meetings").json()
    assert listing["service_contact_due"] == "2028-06-10"  # moved by the newer meeting
    assert [m["subject"] for m in listing["items"]] == ["Review", "Annual call"]  # newest first
    r = ann.put(
        f"/api/meetings/{old.json()['id']}/summary", json={"summary": "Nothing new"}, headers=ha
    )
    assert r.status_code == 200 and r.json()["summary"] == "Nothing new"
    bad = ann.post(
        f"/api/clients/{cid}/meetings",
        json={"kind": "carrier-pigeon", "held_on": "2026-06-10", "subject": "x"},
        headers=ha,
    )
    assert bad.status_code == 422 and new["kind"] == "in_person"
    actions = db.execute(text("SELECT action FROM audit_entry")).scalars().all()
    assert "meeting.create" in actions and "meeting.summary" in actions


# T-M12-4 ------------------------------------------------------------------------------
def test_an_agent_sees_only_own_tasks_and_clients_the_manager_sees_all(
    api: TestClient, db: Session
) -> None:
    ann, ha, ann_acct = _browser(api, db, "ann@agency.il", "agent")
    bob, hb, _ = _browser(api, db, "bob@agency.il", "agent")
    mgr, hm, _ = _browser(api, db, "mgr@agency.il", "manager")
    cid = _client(ann, ha)
    _task(ann, ha, "ann's task", 0, cid)
    _task(bob, hb, "bob's task", 0)

    assert _view(ann, "today") == ["ann's task"] and _view(bob, "today") == ["bob's task"]
    assert _view(ann, "today", owner=str(ann_acct.id)) == ["ann's task"]
    assert bob.get("/api/tasks", params={"owner": str(ann_acct.id)}).status_code == 403
    assert _view(mgr, "today", owner=str(ann_acct.id)) == ["ann's task"]

    # bob cannot attach a task to ann's client, read her meetings, or change her task
    assert (
        bob.post(
            "/api/tasks", json={"text": "x", "due": _day(0), "client_id": cid}, headers=hb
        ).status_code
        == 404
    )
    assert bob.get(f"/api/clients/{cid}/meetings").status_code == 404
    tid = ann.get("/api/tasks").json()[0]["id"]
    assert bob.patch(f"/api/tasks/{tid}", json={"done": True}, headers=hb).status_code == 404
    assert mgr.patch(f"/api/tasks/{tid}", json={"done": True}, headers=hm).status_code == 200
    meeting = ann.post(
        f"/api/clients/{cid}/meetings",
        json={"kind": "online", "held_on": _day(0), "subject": "s"},
        headers=ha,
    ).json()
    assert (
        bob.put(
            f"/api/meetings/{meeting['id']}/summary", json={"summary": "x"}, headers=hb
        ).status_code
        == 404
    )
    assert mgr.get(f"/api/clients/{cid}/meetings").status_code == 200


def test_marketing_and_accountant_have_no_tasks(api: TestClient, db: Session) -> None:
    for role in ("marketing", "accountant"):
        c, h, _ = _browser(api, db, f"{role}@agency.il", role)
        assert c.get("/api/tasks").status_code == 403
        assert (
            c.post("/api/tasks", json={"text": "x", "due": _day(0)}, headers=h).status_code == 403
        )


# T-M12-5 ------------------------------------------------------------------------------
def test_an_overdue_task_raises_one_inbox_item_not_one_per_day(
    api: TestClient, db: Session
) -> None:
    ann, ha, ann_acct = _browser(api, db, "ann@agency.il", "agent")
    done = _task(ann, ha, "was late but finished", -4)
    _task(ann, ha, "still late", -2)
    _task(ann, ha, "not yet", 3)
    ann.patch(f"/api/tasks/{done['id']}", json={"done": True}, headers=ha)

    assert service.overdue_tick(db, TODAY) == 1
    assert service.overdue_tick(db, TODAY) == 0
    assert service.overdue_tick(db, TODAY + timedelta(days=1)) == 0  # the next day adds nothing
    db.commit()
    items = [i for i in ann.get("/api/inbox").json()["items"] if i["type"] == "task_overdue"]
    assert len(items) == 1 and "still late" in items[0]["title"]
    assert (
        service.overdue_tick(db, TODAY + timedelta(days=5)) == 1
    )  # the 'not yet' task is late now


def test_the_people_list_has_names_only_and_skips_inactive_users(
    api: TestClient, db: Session
) -> None:
    ann, _, _ = _browser(api, db, "ann@agency.il", "agent")
    _browser(api, db, "gone@agency.il", "agent")
    db.execute(text("UPDATE user_account SET active = false WHERE email = 'gone@agency.il'"))
    db.commit()
    rows = ann.get("/api/tasks/people").json()
    assert [set(r) for r in rows] == [{"id", "name"}]
    assert rows[0]["name"] == "ann"
    market, _, _ = _browser(api, db, "m@agency.il", "marketing")
    assert market.get("/api/tasks/people").status_code == 403
