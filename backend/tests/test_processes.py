"""T-M4-1 .. T-M4-6. Process engine: definitions as data, locked and parallel stages,
maker-checker, versions, the 18 seeded processes and their diagrams."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session

from app.config import Settings
from app.main import create_app
from app.processes.definition import ProcessDefinition, mermaid
from app.processes.service import seed_definitions
from tests.conftest import Account, login, make_account

DOCS = Path(__file__).resolve().parents[2] / "docs" / "processes"


class Env:
    def __init__(self, app: Any, db: Session) -> None:
        self.app, self.db = app, db
        self.users: dict[str, tuple[TestClient, dict[str, str], Account]] = {}

    def user(self, role: str, n: int = 1) -> tuple[TestClient, dict[str, str], Account]:
        key = f"{role}{n}"
        if key not in self.users:
            a = make_account(self.db, f"{key}@agency.il", [role])
            c = TestClient(self.app, base_url="https://testserver")
            self.users[key] = (c, login(c, self.db, a), a)
        return self.users[key]


@pytest.fixture
def env(settings: Settings, fresh_db: Engine, db: Session) -> Env:
    seed_definitions(db)
    db.commit()
    return Env(create_app(settings, fresh_db), db)


def start(c: TestClient, h: dict[str, str], key: str, **kw: Any) -> dict[str, Any]:
    r = c.post("/api/processes", headers=h, json={"key": key, **kw})
    assert r.status_code == 201, r.text
    return dict(r.json())


def stage(inst: dict[str, Any], sid: str) -> dict[str, Any]:
    return next(s for s in inst["stages"] if s["id"] == sid)


def tick_all(
    c: TestClient, h: dict[str, str], inst_id: str, inst: dict[str, Any], sid: str
) -> dict[str, Any]:
    out = inst
    for ch in stage(inst, sid)["checks"]:
        if ch["kind"] != "heading":
            r = c.put(
                f"/api/processes/{inst_id}/stages/{sid}/checks/{ch['idx']}",
                headers=h,
                json={"done": True},
            )
            assert r.status_code == 200, r.text
            out = r.json()
    return dict(out)


def fin(c: TestClient, h: dict[str, str], inst_id: str, sid: str) -> Any:
    return c.post(f"/api/processes/{inst_id}/stages/{sid}/finalize", headers=h)


# ---- T-M4-4: definitions -------------------------------------------------------------------
def test_all_18_processes_seeded_with_the_documented_counts(env: Env) -> None:
    c, _, _ = env.user("agent")
    defs = c.get("/api/process-defs").json()
    assert len(defs) == 18
    assert sum(d["stages"] for d in defs) == 72 and sum(d["checks"] for d in defs) == 221
    retire = next(d for d in defs if d["key"] == "retire")
    assert (retire["stages"], retire["checks"]) == (10, 54)
    assert seed_definitions(env.db) == 0  # idempotent


def test_every_diagram_matches_the_stored_mmd_file_byte_for_byte(env: Env) -> None:
    c, _, _ = env.user("agent")
    for d in c.get("/api/process-defs").json():
        for lang, suffix in (("en", ""), ("he", ".he")):
            want = (DOCS / f"{d['key']}{suffix}.mmd").read_text(encoding="utf-8")
            got = c.get(f"/api/process-defs/{d['key']}/mermaid", params={"lang": lang}).text
            assert got == want, (d["key"], lang)


def test_seed_matches_the_mock_and_is_valid_data() -> None:
    seed = Path(__file__).resolve().parents[1] / "app/processes/seed/processes.json"
    for raw in json.loads(seed.read_text(encoding="utf-8")):
        d = ProcessDefinition.model_validate(raw)
        assert mermaid(d, "en").startswith("%%{init:") and "flowchart LR" in mermaid(d, "he")


def _base() -> dict[str, Any]:
    t = {"en": "x", "he": "x"}
    chk = [{"text": t, "kind": "do"}]
    return {
        "id": "t1",
        "owner_role": "agent",
        "name": t,
        "stages": [
            {"id": "a", "name": t, "role": "agent", "checks": chk},
            {"id": "b", "name": t, "role": "backoffice", "depends_on": ["a"], "checks": chk},
        ],
    }


@pytest.mark.parametrize(
    "mutate,problem",
    [
        (lambda d: d["stages"][1].update(depends_on=["zzz"]), "unknown stage"),
        (lambda d: d["stages"][1].update(depends_on=["b"]), "itself"),
        (lambda d: d["stages"][0].update(depends_on=["b"]), "later stage"),
        (lambda d: d["stages"][1].update(id="a"), "duplicate"),
        (lambda d: d["stages"][0].update(checks=[]), "at least 1"),
        (
            lambda d: d["stages"][0].update(
                checks=[{"text": {"en": "h", "he": "h"}, "kind": "heading"}]
            ),
            "tickable",
        ),
        (lambda d: d["stages"][0].update(role="janitor"), "agent"),
        (lambda d: d.update(stages=[]), "at least 1"),
        (lambda d: d["stages"][0].update(id="Bad Id"), "pattern"),
    ],
)
def test_invalid_definitions_are_rejected(env: Env, mutate: Any, problem: str) -> None:
    c, h, _ = env.user("manager")
    d = copy.deepcopy(_base())
    mutate(d)
    r = c.post("/api/process-defs", headers=h, json=d)
    assert r.status_code == 422 and problem in json.dumps(r.json()), r.text


def test_only_a_manager_publishes_definitions(env: Env) -> None:
    for role, status in (("agent", 403), ("backoffice", 403), ("marketing", 403), ("manager", 201)):
        c, h, _ = env.user(role)
        assert c.post("/api/process-defs", headers=h, json=_base()).status_code == status, role


# ---- T-M4-3: versions ----------------------------------------------------------------------
def test_running_instances_keep_the_version_they_started_with(env: Env) -> None:
    c, h, _ = env.user("manager")
    assert c.post("/api/process-defs", headers=h, json=_base()).json()["version"] == 1
    old = start(c, h, "t1")
    v2 = _base()
    v2["stages"].append(
        {
            "id": "c",
            "name": {"en": "c", "he": "c"},
            "role": "manager",
            "depends_on": ["b"],
            "checks": [{"text": {"en": "c", "he": "c"}}],
        }
    )
    assert c.post("/api/process-defs", headers=h, json=v2).json()["version"] == 2
    new = start(c, h, "t1")
    assert (old["version"], len(old["stages"])) == (1, 2)
    assert (new["version"], len(new["stages"])) == (2, 3)
    again = c.get(f"/api/processes/{old['id']}").json()
    assert again["version"] == 1 and len(again["stages"]) == 2  # still the old shape
    assert c.get("/api/process-defs/t1", params={"version": 1}).json()["version"] == 1
    assert c.get("/api/process-defs/t1").json()["version"] == 2
    assert c.get("/api/process-defs/t1", params={"version": 9}).status_code == 404


# ---- T-M4-1: locked and parallel -----------------------------------------------------------
def test_parallel_stages_start_ready_and_dependents_are_locked(env: Env) -> None:
    c, h, _ = env.user("agent")
    inst = start(c, h, "retire")
    assert stage(inst, "int")["status"] == "ready" and stage(inst, "auth")["status"] == "ready"
    for sid in ("ana", "rec", "rev", "dec", "exe", "inq", "tx", "cls"):
        assert stage(inst, sid)["status"] == "locked", sid
    assert inst["progress"] == {
        "checks_done": 0,
        "checks_total": 54,
        "stages_final": 0,
        "stages_total": 10,
    }


def test_a_locked_stage_cannot_be_started_ticked_or_finalized(env: Env) -> None:
    c, h, _ = env.user("agent")
    inst = start(c, h, "retire")
    i = inst["id"]
    first = next(ch["idx"] for ch in stage(inst, "ana")["checks"] if ch["kind"] != "heading")
    assert c.post(f"/api/processes/{i}/stages/ana/start", headers=h).json()["detail"] == (
        "stage_locked"
    )
    assert (
        c.put(
            f"/api/processes/{i}/stages/ana/checks/{first}", headers=h, json={"done": True}
        ).status_code
        == 409
    )
    assert fin(c, h, i, "ana").status_code == 409


def test_a_stage_unlocks_only_when_all_its_dependencies_are_final(env: Env) -> None:
    ca, ha, _ = env.user("agent")
    cb, hb, _ = env.user("backoffice")
    inst = start(ca, ha, "retire")
    i = inst["id"]
    tick_all(ca, ha, i, inst, "int")
    after_int = fin(ca, ha, i, "int").json()
    assert stage(after_int, "int")["status"] == "final"
    assert stage(after_int, "ana")["status"] == "locked"  # still waits for "auth"
    inst2 = cb.get(f"/api/processes/{i}").json()
    tick_all(cb, hb, i, inst2, "auth")
    after_auth = fin(cb, hb, i, "auth").json()
    assert stage(after_auth, "ana")["status"] == "ready"


# ---- T-M4-2: checks, roles, maker-checker --------------------------------------------------
def test_cannot_finalize_until_every_check_is_ticked(env: Env) -> None:
    c, h, _ = env.user("agent")
    inst = start(c, h, "retire")
    i = inst["id"]
    assert fin(c, h, i, "int").json()["detail"] == "checks_incomplete"
    checks = [ch for ch in stage(inst, "int")["checks"] if ch["kind"] != "heading"]
    for ch in checks[:-1]:
        c.put(f"/api/processes/{i}/stages/int/checks/{ch['idx']}", headers=h, json={"done": True})
    assert fin(c, h, i, "int").status_code == 409
    c.put(
        f"/api/processes/{i}/stages/int/checks/{checks[-1]['idx']}", headers=h, json={"done": True}
    )
    assert fin(c, h, i, "int").status_code == 200


def test_untick_and_idempotent_tick(env: Env) -> None:
    c, h, _ = env.user("agent")
    inst = start(c, h, "retire")
    i = inst["id"]
    idx = next(ch["idx"] for ch in stage(inst, "int")["checks"] if ch["kind"] != "heading")
    url = f"/api/processes/{i}/stages/int/checks/{idx}"
    assert c.put(url, headers=h, json={"done": True}).json()["progress"]["checks_done"] == 1
    assert c.put(url, headers=h, json={"done": True}).json()["progress"]["checks_done"] == 1
    assert c.put(url, headers=h, json={"done": False}).json()["progress"]["checks_done"] == 0
    assert stage(c.get(f"/api/processes/{i}").json(), "int")["status"] == "in_progress"


def test_headings_are_not_tickable_and_unknown_things_404(env: Env) -> None:
    c, h, _ = env.user("agent")
    inst = start(c, h, "retire")
    i = inst["id"]
    heading = next(ch["idx"] for ch in stage(inst, "int")["checks"] if ch["kind"] == "heading")
    assert (
        c.put(
            f"/api/processes/{i}/stages/int/checks/{heading}", headers=h, json={"done": True}
        ).status_code
        == 404
    )
    assert (
        c.put(
            f"/api/processes/{i}/stages/int/checks/999", headers=h, json={"done": True}
        ).status_code
        == 404
    )
    assert c.post(f"/api/processes/{i}/stages/nope/start", headers=h).status_code == 404
    assert c.post("/api/processes", headers=h, json={"key": "nope"}).status_code == 404


def test_only_the_owner_role_or_a_manager_can_work_a_stage(env: Env) -> None:
    ca, ha, _ = env.user("agent")
    cb, hb, _ = env.user("backoffice")
    cm, hm, _ = env.user("manager")
    inst = start(ca, ha, "retire")
    i = inst["id"]
    idx = next(ch["idx"] for ch in stage(inst, "auth")["checks"] if ch["kind"] != "heading")
    # "auth" belongs to the back-office: the agent who started the process cannot tick it
    assert (
        ca.put(
            f"/api/processes/{i}/stages/auth/checks/{idx}", headers=ha, json={"done": True}
        ).status_code
        == 403
    )
    assert ca.post(f"/api/processes/{i}/stages/auth/start", headers=ha).status_code == 403
    assert fin(ca, ha, i, "auth").status_code == 403
    assert (
        cb.put(
            f"/api/processes/{i}/stages/auth/checks/{idx}", headers=hb, json={"done": True}
        ).status_code
        == 200
    )
    # the manager may act on any stage
    first = next(c["idx"] for c in stage(inst, "int")["checks"] if c["kind"] != "heading")
    assert (
        cm.put(
            f"/api/processes/{i}/stages/int/checks/{first}", headers=hm, json={"done": True}
        ).status_code
        == 200
    )


def test_four_eyes_stage_needs_a_different_finalizer(env: Env) -> None:
    cm, hm, _ = env.user("manager")
    d = _base()
    d["id"] = "fe"
    d["stages"][0]["four_eyes"] = True
    cm.post("/api/process-defs", headers=hm, json=d)
    ca, ha, _ = env.user("agent")
    inst = start(ca, ha, "fe")
    i = inst["id"]
    tick_all(ca, ha, i, inst, "a")
    assert fin(ca, ha, i, "a").json()["detail"] == "four_eyes_violation"  # the maker cannot check
    assert fin(cm, hm, i, "a").status_code == 200  # a different person can


def test_a_final_stage_is_frozen(env: Env) -> None:
    c, h, _ = env.user("manager")
    c.post("/api/process-defs", headers=h, json=_base())
    inst = start(c, h, "t1")
    i = inst["id"]
    tick_all(c, h, i, inst, "a")
    assert fin(c, h, i, "a").status_code == 200
    idx = next(ch["idx"] for ch in stage(inst, "a")["checks"] if ch["kind"] != "heading")
    assert (
        c.put(f"/api/processes/{i}/stages/a/checks/{idx}", headers=h, json={"done": False}).json()[
            "detail"
        ]
        == "stage_final"
    )
    assert fin(c, h, i, "a").status_code == 409
    with pytest.raises(DatabaseError, match="cannot be reopened"):  # and the database agrees
        env.db.execute(text("UPDATE stage_instance SET status = 'ready' WHERE status = 'final'"))
    env.db.rollback()


# ---- T-M4-5: the reference process end to end ----------------------------------------------
def test_exit_to_retirement_planning_runs_end_to_end(env: Env) -> None:
    users = {
        "agent": env.user("agent"),
        "backoffice": env.user("backoffice"),
        "manager": env.user("manager"),
    }
    c0, h0, _ = users["agent"]
    inst = start(c0, h0, "retire", title="Exit planning, test client")
    i = inst["id"]
    finalized: list[str] = []
    for _ in range(20):
        cur = c0.get(f"/api/processes/{i}").json() if inst["status"] != "complete" else inst
        if cur["status"] == "complete":
            inst = cur
            break
        actionable = [s for s in cur["stages"] if s["status"] in ("ready", "in_progress")]
        assert actionable, "deadlock: nothing is ready but the process is not complete"
        s = actionable[0]
        c, h, _ = users[s["role"]]
        tick_all(c, h, i, cur, s["id"])
        inst = fin(c, h, i, s["id"]).json()
        finalized.append(s["id"])
    assert inst["status"] == "complete" and len(finalized) == 10
    assert inst["progress"] == {
        "checks_done": 54,
        "checks_total": 54,
        "stages_final": 10,
        "stages_total": 10,
    }
    pos = {sid: n for n, sid in enumerate(finalized)}
    for before, after in [
        ("int", "ana"),
        ("auth", "ana"),
        ("ana", "rec"),
        ("rec", "rev"),
        ("rev", "dec"),
        ("dec", "exe"),
        ("exe", "inq"),
        ("exe", "tx"),
        ("inq", "cls"),
        ("tx", "cls"),
    ]:
        assert pos[before] < pos[after], (before, after)
    assert c0.get("/api/my-work").json()["count"] == 0
    assert c0.get("/api/processes", params={"status": "complete"}).json()[0]["id"] == i


# ---- T-M4-6: audit -------------------------------------------------------------------------
def test_start_stage_tick_finalize_and_completion_are_audited(env: Env) -> None:
    c, h, a = env.user("manager")
    c.post("/api/process-defs", headers=h, json=_base())
    inst = start(c, h, "t1")
    i = inst["id"]
    cur = tick_all(c, h, i, inst, "a")
    fin(c, h, i, "a")
    tick_all(c, h, i, cur, "b")
    fin(c, h, i, "b")
    rows = env.db.execute(
        text("SELECT actor, action FROM audit_entry WHERE object = :o ORDER BY seq"),
        {"o": f"process:{i}"},
    ).all()
    actions = [r[1] for r in rows]
    assert actions[0] == "process.start" and actions[-1] == "process.complete"
    assert actions.count("stage.start") == 2 and actions.count("check.tick") == 2
    assert actions.count("stage.final") == 2
    assert rows[0][0] == a.id and rows[-1][0] == "system"
    assert (
        env.db.execute(
            text("SELECT count(*) FROM audit_entry WHERE action = 'process_def.publish'")
        ).scalar_one()
        == 1
    )


# ---- My work, visibility, banner data, cancel ----------------------------------------------
def test_my_work_lists_stages_waiting_for_each_role(env: Env) -> None:
    ca, ha, _ = env.user("agent")
    cb, _, _ = env.user("backoffice")
    cmk, _, _ = env.user("marketing")
    start(ca, ha, "retire")
    agent = ca.get("/api/my-work").json()
    assert agent["count"] == 1 and agent["items"][0]["stage"] == "int"
    assert cb.get("/api/my-work").json()["items"][0]["stage"] == "auth"
    assert cmk.get("/api/my-work").json() == {"count": 0, "items": []}


def test_visibility_of_instances(env: Env) -> None:
    ca, ha, _ = env.user("agent")
    cmk, _, _ = env.user("marketing")
    cmg, _, _ = env.user("manager")
    inst = start(ca, ha, "retire")
    i = inst["id"]
    assert cmk.get(f"/api/processes/{i}").status_code == 404  # no stage for marketing
    assert cmk.get("/api/processes").json() == []
    assert cmg.get(f"/api/processes/{i}").status_code == 200  # the manager sees all
    other, _, _ = env.user("agent", 2)
    assert other.get(f"/api/processes/{i}").status_code == 200  # an agent stage exists for them
    cc, hc, _ = env.user("marketing", 2)
    assert cc.post("/api/processes", headers=hc, json={"key": "camp"}).status_code == 201


def test_processes_on_a_client_need_that_client_to_be_visible(env: Env) -> None:
    c1, h1, _ = env.user("agent", 1)
    c2, h2, _ = env.user("agent", 2)
    cid = c1.post(
        "/api/clients", headers=h1, json={"id_number": "123456782", "name": "David Cohen"}
    ).json()["id"]
    assert (
        c2.post("/api/processes", headers=h2, json={"key": "onb", "client_id": cid}).status_code
        == 404
    )
    inst = start(c1, h1, "onb", client_id=cid)
    banner = c1.get("/api/processes", params={"client_id": cid}).json()
    assert [b["id"] for b in banner] == [inst["id"]]
    assert {s["id"]: s["status"] for s in banner[0]["stages"]} == {
        "id": "ready",
        "kyc": "ready",
        "reg": "locked",
        "fin": "locked",
    }


def test_cancel_is_manager_only_needs_a_reason_and_freezes_the_process(env: Env) -> None:
    ca, ha, _ = env.user("agent")
    cm, hm, _ = env.user("manager")
    inst = start(ca, ha, "retire")
    i = inst["id"]
    assert (
        ca.post(
            f"/api/processes/{i}/cancel", headers=ha, json={"reason": "changed my mind"}
        ).status_code
        == 403
    )
    assert (
        cm.post(f"/api/processes/{i}/cancel", headers=hm, json={"reason": "x"}).status_code == 422
    )
    assert (
        cm.post(
            f"/api/processes/{i}/cancel", headers=hm, json={"reason": "client withdrew"}
        ).json()["status"]
        == "cancelled"
    )
    idx = next(ch["idx"] for ch in stage(inst, "int")["checks"] if ch["kind"] != "heading")
    assert (
        ca.put(
            f"/api/processes/{i}/stages/int/checks/{idx}", headers=ha, json={"done": True}
        ).json()["detail"]
        == "process_cancelled"
    )
    assert (
        cm.post(f"/api/processes/{i}/cancel", headers=hm, json={"reason": "again"}).status_code
        == 409
    )
    assert ca.get("/api/my-work").json()["count"] == 0  # a cancelled process leaves My work


# ---- M18: dry-run validation for the editor (T-M18-5 .. T-M18-7) --------------------------
def _codes(r: Any) -> list[str]:
    return [e["code"] for e in r.json()["errors"]]


def test_validate_accepts_a_good_draft_and_writes_nothing(env: Env) -> None:
    c, h, _ = env.user("manager")
    r = c.post("/api/process-defs/validate", headers=h, json=_base())
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["errors"] == []
    assert r.json()["current_version"] is None and r.json()["running_cases"] == 0
    assert c.get("/api/process-defs/t1").status_code == 404  # nothing was stored


@pytest.mark.parametrize(
    "mutate,code,stage,edge",
    [
        (lambda d: d["stages"][1].update(depends_on=["zzz"]), "unknown_stage", "b", ["zzz", "b"]),
        (lambda d: d["stages"][1].update(depends_on=["b"]), "self_dependency", "b", ["b", "b"]),
        (lambda d: d["stages"][0].update(depends_on=["b"]), "later_stage", "a", ["b", "a"]),
        (lambda d: d["stages"][1].update(depends_on=["a", "a"]), "duplicate_edge", "b", ["a", "b"]),
        (lambda d: d["stages"][1].update(id="a"), "duplicate_stage_id", "a", None),
        (
            lambda d: d["stages"][0].update(
                checks=[{"text": {"en": "h", "he": "h"}, "kind": "heading"}]
            ),
            "no_tickable_check",
            "a",
            None,
        ),
        (lambda d: d["stages"][0].update(role="janitor"), "invalid", "a", None),
    ],
)
def test_validate_says_what_is_wrong_and_where(
    env: Env, mutate: Any, code: str, stage: str, edge: list[str] | None
) -> None:
    c, h, _ = env.user("manager")
    d = copy.deepcopy(_base())
    mutate(d)
    r = c.post("/api/process-defs/validate", headers=h, json=d)
    assert r.status_code == 200 and r.json()["ok"] is False
    hit = [e for e in r.json()["errors"] if e["code"] == code]
    assert hit and hit[0]["stage"] == stage and hit[0]["edge"] == edge, r.json()


def test_validate_reports_every_problem_not_only_the_first(env: Env) -> None:
    c, h, _ = env.user("manager")
    d = copy.deepcopy(_base())
    d["stages"][1]["depends_on"] = ["zzz", "b"]
    d["stages"][0]["checks"][0]["text"]["en"] = ""  # bad text on the first item of stage a
    codes = _codes(c.post("/api/process-defs/validate", headers=h, json=d))
    assert "invalid" in codes  # a field error does not hide the others being reported next time
    d["stages"][0]["checks"][0]["text"]["en"] = "ok"
    codes = _codes(c.post("/api/process-defs/validate", headers=h, json=d))
    assert sorted(codes) == ["self_dependency", "unknown_stage"]


def test_a_field_error_names_the_check(env: Env) -> None:
    c, h, _ = env.user("manager")
    d = copy.deepcopy(_base())
    d["stages"][0]["checks"].append({"text": {"en": "", "he": "x"}})
    e = c.post("/api/process-defs/validate", headers=h, json=d).json()["errors"][0]
    assert (e["stage"], e["check"]) == ("a", 1)


def test_validate_and_publish_give_the_same_verdict(env: Env) -> None:
    c, h, _ = env.user("manager")
    bad: list[dict[str, Any]] = []
    for mut in (
        lambda d: d["stages"][1].update(depends_on=["zzz"]),
        lambda d: d["stages"][1].update(depends_on=["a", "a"]),
        lambda d: d["stages"][0].update(checks=[]),
        lambda d: d.update(id="Bad Id"),
        lambda d: d.update(stages=[]),
        lambda d: d.pop("name"),
    ):
        d = copy.deepcopy(_base())
        mut(d)
        bad.append(d)
    for d in [_base(), *bad]:
        ok = c.post("/api/process-defs/validate", headers=h, json=d).json()["ok"]
        published = c.post("/api/process-defs", headers=h, json=d)
        assert ok == (published.status_code == 201), d
        if not ok:
            assert published.json()["detail"], "the publish refusal carries the same list"


def test_validate_counts_cases_running_on_the_current_version(env: Env) -> None:
    c, h, _ = env.user("manager")
    assert c.post("/api/process-defs", headers=h, json=_base()).status_code == 201
    start(c, h, "t1")
    start(c, h, "t1")
    r = c.post("/api/process-defs/validate", headers=h, json=_base()).json()
    assert (r["current_version"], r["running_cases"]) == (1, 2)
    assert c.post("/api/process-defs", headers=h, json=_base()).json()["version"] == 2
    r = c.post("/api/process-defs/validate", headers=h, json=_base()).json()
    assert (r["current_version"], r["running_cases"]) == (2, 0)  # the two stay on version 1


def test_validate_needs_the_define_capability(env: Env) -> None:
    for role, status in (("agent", 403), ("backoffice", 403), ("marketing", 403), ("manager", 200)):
        c, h, _ = env.user(role)
        r = c.post("/api/process-defs/validate", headers=h, json=_base())
        assert r.status_code == status, role


def test_the_seeded_definitions_all_validate(env: Env) -> None:
    c, h, _ = env.user("manager")
    for d in c.get("/api/process-defs").json():
        doc = c.get(f"/api/process-defs/{d['key']}").json()["definition"]
        r = c.post("/api/process-defs/validate", headers=h, json=doc)
        assert r.json()["ok"], (d["key"], r.json()["errors"])
