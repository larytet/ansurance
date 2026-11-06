"""Process engine: definitions, instances, locked and parallel stages, finalization."""

import json
import uuid
from datetime import UTC, datetime
from importlib import resources
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session as Db

from app.audit import chain
from app.models import CheckInstance, ProcessDef, ProcessInstance, StageInstance
from app.processes.definition import ProcessDefinition


class ProcessError(Exception):
    def __init__(self, code: str, status: int = 409) -> None:
        super().__init__(code)
        self.code, self.status = code, status


def _now() -> datetime:
    return datetime.now(UTC)


# ---- definitions ----------------------------------------------------------------------
def latest(db: Db, key: str) -> ProcessDef | None:
    return db.scalar(
        select(ProcessDef).where(ProcessDef.key == key).order_by(ProcessDef.version.desc()).limit(1)
    )


def add_definition(db: Db, definition: ProcessDefinition, actor: uuid.UUID | None) -> ProcessDef:
    cur = latest(db, definition.id)
    row = ProcessDef(
        key=definition.id,
        version=(cur.version + 1) if cur else 1,
        definition=definition.model_dump(mode="json"),
        created_by=actor,
    )
    db.add(row)
    db.flush()
    return row


def seed_definitions(db: Db) -> int:
    """Load the 18 processes from the mock. Idempotent: only keys with no definition yet."""
    raw = resources.files("app.processes").joinpath("seed/processes.json").read_text("utf-8")
    added = 0
    for item in json.loads(raw):
        if latest(db, item["id"]) is None:
            add_definition(db, ProcessDefinition.model_validate(item), None)
            added += 1
    return added


def parse(row: ProcessDef) -> ProcessDefinition:
    return ProcessDefinition.model_validate(row.definition)


# ---- instances --------------------------------------------------------------------------
def start_instance(
    db: Db, actor: uuid.UUID, key: str, client_id: uuid.UUID | None, title: str | None
) -> ProcessInstance:
    row = latest(db, key)
    if row is None:
        raise ProcessError("unknown_process", 404)
    d = parse(row)
    inst = ProcessInstance(
        def_id=row.id,
        key=key,
        version=row.version,
        client_id=client_id,
        title=title or d.name.en,
        started_by=actor,
    )
    db.add(inst)
    db.flush()
    for pos, s in enumerate(d.stages):
        st = StageInstance(
            instance_id=inst.id,
            stage_key=s.id,
            position=pos,
            role=s.role,
            status="ready" if not s.depends_on else "locked",
        )
        db.add(st)
        db.flush()
        for idx, _ in s.real_checks:
            db.add(CheckInstance(stage_id=st.id, idx=idx))
    chain.append(
        db,
        str(actor),
        "process.start",
        f"process:{inst.id}",
        {"key": key, "version": row.version, "client": str(client_id or "")},
    )
    return inst


def load_definition(db: Db, inst: ProcessInstance) -> ProcessDefinition:
    """The definition version the instance started with, never a newer one."""
    row = db.get(ProcessDef, inst.def_id)
    assert row is not None  # noqa: S101  (foreign key guarantees it)
    return parse(row)


def lock_instance(db: Db, instance_id: uuid.UUID) -> ProcessInstance:
    inst = db.scalar(
        select(ProcessInstance).where(ProcessInstance.id == instance_id).with_for_update()
    )
    if inst is None:
        raise ProcessError("not_found", 404)
    return inst


def stages_of(db: Db, inst: ProcessInstance) -> dict[str, StageInstance]:
    rows = db.scalars(
        select(StageInstance)
        .where(StageInstance.instance_id == inst.id)
        .order_by(StageInstance.position)
    )
    return {s.stage_key: s for s in rows}


def checks_of(db: Db, stage: StageInstance) -> dict[int, CheckInstance]:
    rows = db.scalars(select(CheckInstance).where(CheckInstance.stage_id == stage.id))
    return {c.idx: c for c in rows}


def _may_act(roles: list[str], stage: StageInstance) -> bool:
    return stage.role in roles or "manager" in roles


def _writable(inst: ProcessInstance, stage: StageInstance) -> None:
    if inst.status != "running":
        raise ProcessError(f"process_{inst.status}")
    if stage.status == "locked":
        raise ProcessError("stage_locked")
    if stage.status == "final":
        raise ProcessError("stage_final")


def start_stage(
    db: Db, actor: uuid.UUID, roles: list[str], inst: ProcessInstance, stage: StageInstance
) -> None:
    _writable(inst, stage)
    if not _may_act(roles, stage):
        raise ProcessError("wrong_role", 403)
    if stage.status == "ready":
        stage.status, stage.started_at, stage.started_by = "in_progress", _now(), actor
        chain.append(
            db, str(actor), "stage.start", f"process:{inst.id}", {"stage": stage.stage_key}
        )


def set_check(
    db: Db,
    actor: uuid.UUID,
    roles: list[str],
    inst: ProcessInstance,
    stage: StageInstance,
    idx: int,
    done: bool,
) -> CheckInstance:
    _writable(inst, stage)
    if not _may_act(roles, stage):
        raise ProcessError("wrong_role", 403)
    check = checks_of(db, stage).get(idx)
    if check is None:
        raise ProcessError("no_such_check", 404)
    start_stage(db, actor, roles, inst, stage)  # ticking implies the stage is being worked
    if check.done != done:
        check.done = done
        check.done_at, check.done_by = (_now(), actor) if done else (None, None)
        chain.append(
            db,
            str(actor),
            "check.tick" if done else "check.untick",
            f"process:{inst.id}",
            {"stage": stage.stage_key, "check": idx},
        )
    return check


def finalize_stage(
    db: Db,
    actor: uuid.UUID,
    roles: list[str],
    inst: ProcessInstance,
    stage: StageInstance,
    defn: ProcessDefinition,
) -> None:
    _writable(inst, stage)
    if not _may_act(roles, stage):
        raise ProcessError("wrong_role", 403)
    checks = checks_of(db, stage)
    if not all(c.done for c in checks.values()):
        raise ProcessError("checks_incomplete")
    sdef = defn.stage(stage.stage_key)
    if sdef.four_eyes and any(c.done_by == actor for c in checks.values()):
        raise ProcessError("four_eyes_violation")  # maker and checker must differ
    stage.status, stage.finalized_at, stage.finalized_by = "final", _now(), actor
    chain.append(db, str(actor), "stage.final", f"process:{inst.id}", {"stage": stage.stage_key})
    stages = stages_of(db, inst)
    for s in defn.stages:  # unlock every stage whose dependencies are all final
        cur = stages[s.id]
        if cur.status == "locked" and all(stages[d].status == "final" for d in s.depends_on):
            cur.status = "ready"
    if all(s.status == "final" for s in stages.values()):
        inst.status, inst.completed_at = "complete", _now()
        chain.append(db, "system", "process.complete", f"process:{inst.id}", {"key": inst.key})


def cancel(db: Db, actor: uuid.UUID, inst: ProcessInstance, reason: str) -> None:
    if inst.status != "running":
        raise ProcessError(f"process_{inst.status}")
    inst.status, inst.cancel_reason = "cancelled", reason
    chain.append(db, str(actor), "process.cancel", f"process:{inst.id}", {"reason": reason})


# ---- views --------------------------------------------------------------------------------
def instance_view(db: Db, inst: ProcessInstance, defn: ProcessDefinition) -> dict[str, Any]:
    stages = stages_of(db, inst)
    out_stages: list[dict[str, Any]] = []
    done_checks = total_checks = 0
    for s in defn.stages:
        st = stages[s.id]
        marks = checks_of(db, st)
        checks = []
        for i, c in enumerate(s.checks):
            row: dict[str, Any] = {
                "idx": i,
                "text": c.text.model_dump(),
                "kind": c.kind,
                "verify_note": c.verify_note,
            }
            if c.kind != "heading":
                m = marks[i]
                row.update(done=m.done, done_by=str(m.done_by) if m.done_by else None)
                total_checks += 1
                done_checks += int(m.done)
            checks.append(row)
        out_stages.append(
            {
                "id": s.id,
                "name": s.name.model_dump(),
                "role": s.role,
                "status": st.status,
                "depends_on": s.depends_on,
                "checks": checks,
                "finalized_at": st.finalized_at.isoformat() if st.finalized_at else None,
            }
        )
    return {
        "id": str(inst.id),
        "key": inst.key,
        "version": inst.version,
        "name": defn.name.model_dump(),
        "title": inst.title,
        "status": inst.status,
        "client_id": str(inst.client_id) if inst.client_id else None,
        "started_at": inst.started_at.isoformat(),
        "stages": out_stages,
        "progress": {
            "checks_done": done_checks,
            "checks_total": total_checks,
            "stages_final": sum(1 for s in stages.values() if s.status == "final"),
            "stages_total": len(stages),
        },
    }


def my_work(db: Db, roles: list[str]) -> list[dict[str, Any]]:
    """Stages waiting for the user's roles: ready or in progress, in running processes."""
    rows = db.execute(
        select(StageInstance, ProcessInstance)
        .join(ProcessInstance, ProcessInstance.id == StageInstance.instance_id)
        .where(
            StageInstance.role.in_(roles),
            StageInstance.status.in_(("ready", "in_progress")),
            ProcessInstance.status == "running",
        )
        .order_by(ProcessInstance.started_at, StageInstance.position)
    ).all()
    return [
        {
            "instance_id": str(i.id),
            "key": i.key,
            "title": i.title,
            "stage": s.stage_key,
            "stage_status": s.status,
            "role": s.role,
            "client_id": str(i.client_id) if i.client_id else None,
        }
        for s, i in rows
    ]


def count_my_work(db: Db, roles: list[str]) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(StageInstance)
            .join(ProcessInstance, ProcessInstance.id == StageInstance.instance_id)
            .where(
                StageInstance.role.in_(roles),
                StageInstance.status.in_(("ready", "in_progress")),
                ProcessInstance.status == "running",
            )
        )
        or 0
    )
