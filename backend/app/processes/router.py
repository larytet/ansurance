import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session as Db

from app.audit import chain
from app.clients.access import get_visible
from app.deps import Principal, get_db, require
from app.models import ProcessDef, ProcessInstance, StageInstance
from app.processes import service
from app.processes.definition import ProcessDefinition, mermaid, validate_document

router = APIRouter(prefix="/api")


class StartIn(BaseModel):
    key: str
    client_id: uuid.UUID | None = None
    title: str | None = Field(default=None, max_length=200)


class CheckIn(BaseModel):
    done: bool


class CancelIn(BaseModel):
    reason: str = Field(min_length=3)


def _err(e: service.ProcessError) -> HTTPException:
    return HTTPException(e.status, e.code)


def _visible_instance(db: Db, p: Principal, instance_id: uuid.UUID) -> ProcessInstance:
    inst = db.get(ProcessInstance, instance_id)
    if inst is None:
        raise HTTPException(404, "not_found")
    if "manager" in p.roles or inst.started_by == p.user.id:
        return inst
    has_stage = db.scalar(
        select(StageInstance.id)
        .where(StageInstance.instance_id == inst.id, StageInstance.role.in_(p.roles))
        .limit(1)
    )
    if has_stage is None:
        raise HTTPException(404, "not_found")
    return inst


# ---- definitions ----------------------------------------------------------------------------
@router.get("/process-defs")
def list_defs(
    p: Principal = Depends(require("process.read")), db: Db = Depends(get_db, scope="function")
) -> list[dict[str, Any]]:
    rows = db.scalars(select(ProcessDef).order_by(ProcessDef.key, ProcessDef.version.desc()))
    seen: set[str] = set()
    out = []
    for r in rows:
        if r.key in seen:
            continue
        seen.add(r.key)
        d = service.parse(r)
        out.append(
            {
                "key": r.key,
                "version": r.version,
                "source_flow": d.source_flow,
                "name": d.name.model_dump(),
                "owner_role": d.owner_role,
                "stages": len(d.stages),
                "checks": sum(len(s.real_checks) for s in d.stages),
            }
        )
    return out


@router.get("/process-defs/{key}")
def get_def(
    key: str,
    version: int | None = None,
    p: Principal = Depends(require("process.read")),
    db: Db = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    row = (
        service.latest(db, key)
        if version is None
        else db.scalar(
            select(ProcessDef).where(ProcessDef.key == key, ProcessDef.version == version)
        )
    )
    if row is None:
        raise HTTPException(404, "not_found")
    return {"key": key, "version": row.version, "definition": row.definition}


@router.get("/process-defs/{key}/mermaid")
def get_mermaid(
    key: str,
    lang: Literal["en", "he"] = "en",
    p: Principal = Depends(require("process.read")),
    db: Db = Depends(get_db, scope="function"),
) -> Response:
    row = service.latest(db, key)
    if row is None:
        raise HTTPException(404, "not_found")
    return Response(mermaid(service.parse(row), lang) + "\n", media_type="text/plain")


@router.post("/process-defs/validate")
def validate_def(
    body: dict[str, Any],
    p: Principal = Depends(require("process.define")),
    db: Db = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    """Dry run for the editor: every problem with its place, and how many running cases a new
    version would leave on the old one. Writes nothing."""
    problems = validate_document(body)
    key = body.get("id") if isinstance(body.get("id"), str) else None
    cur = service.latest(db, key) if key else None
    running = 0
    if cur is not None:
        running = (
            db.scalar(
                select(func.count())
                .select_from(ProcessInstance)
                .where(
                    ProcessInstance.key == key,
                    ProcessInstance.version == cur.version,
                    ProcessInstance.status == "running",
                )
            )
            or 0
        )
    return {
        "ok": not problems,
        "errors": problems,
        "current_version": cur.version if cur else None,
        "running_cases": running,
    }


@router.post("/process-defs", status_code=201)
def new_def_version(
    body: dict[str, Any],
    p: Principal = Depends(require("process.define")),
    db: Db = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    """A manager publishes a new version. Running instances keep the version they started on."""
    problems = validate_document(body)
    if problems:
        raise HTTPException(422, problems)
    definition = ProcessDefinition.model_validate(body)
    row = service.add_definition(db, definition, p.user.id)
    chain.append(
        db,
        str(p.user.id),
        "process_def.publish",
        f"process_def:{row.key}",
        {"version": row.version},
    )
    return {"key": row.key, "version": row.version}


# ---- instances ------------------------------------------------------------------------------
@router.post("/processes", status_code=201)
def start(
    body: StartIn,
    p: Principal = Depends(require("process.start")),
    db: Db = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    if body.client_id is not None:
        get_visible(db, p, body.client_id)  # you can only run a process on a client you may see
    try:
        inst = service.start_instance(db, p.user.id, body.key, body.client_id, body.title)
    except service.ProcessError as e:
        raise _err(e) from e
    return service.instance_view(db, inst, service.load_definition(db, inst))


@router.get("/processes")
def list_instances(
    status: str = "running",
    client_id: uuid.UUID | None = None,
    p: Principal = Depends(require("process.read")),
    db: Db = Depends(get_db, scope="function"),
) -> list[dict[str, Any]]:
    stmt = select(ProcessInstance).where(ProcessInstance.status == status)
    if "manager" not in p.roles:
        mine = select(StageInstance.instance_id).where(StageInstance.role.in_(p.roles))
        stmt = stmt.where(
            or_(ProcessInstance.started_by == p.user.id, ProcessInstance.id.in_(mine))
        )
    if client_id:
        stmt = stmt.where(ProcessInstance.client_id == client_id)
    out = []
    for inst in db.scalars(stmt.order_by(ProcessInstance.started_at.desc()).limit(200)):
        v = service.instance_view(db, inst, service.load_definition(db, inst))
        out.append(
            {k: v[k] for k in ("id", "key", "title", "status", "client_id", "progress")}
            | {
                "stages": [
                    {"id": s["id"], "status": s["status"], "role": s["role"], "name": s["name"]}
                    for s in v["stages"]
                ]
            }
        )
    return out


@router.get("/processes/{instance_id}")
def get_instance(
    instance_id: uuid.UUID,
    p: Principal = Depends(require("process.read")),
    db: Db = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    inst = _visible_instance(db, p, instance_id)
    return service.instance_view(db, inst, service.load_definition(db, inst))


def _stage(db: Db, inst: ProcessInstance, key: str) -> StageInstance:
    st = service.stages_of(db, inst).get(key)
    if st is None:
        raise HTTPException(404, "no_such_stage")
    return st


@router.post("/processes/{instance_id}/stages/{stage}/start")
def start_stage(
    instance_id: uuid.UUID,
    stage: str,
    p: Principal = Depends(require("process.read")),
    db: Db = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    _visible_instance(db, p, instance_id)
    inst = service.lock_instance(db, instance_id)
    try:
        service.start_stage(db, p.user.id, p.roles, inst, _stage(db, inst, stage))
    except service.ProcessError as e:
        raise _err(e) from e
    return service.instance_view(db, inst, service.load_definition(db, inst))


@router.put("/processes/{instance_id}/stages/{stage}/checks/{idx}")
def tick(
    instance_id: uuid.UUID,
    stage: str,
    idx: int,
    body: CheckIn,
    p: Principal = Depends(require("process.read")),
    db: Db = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    _visible_instance(db, p, instance_id)
    inst = service.lock_instance(db, instance_id)
    try:
        service.set_check(db, p.user.id, p.roles, inst, _stage(db, inst, stage), idx, body.done)
    except service.ProcessError as e:
        db.commit()
        raise _err(e) from e
    return service.instance_view(db, inst, service.load_definition(db, inst))


@router.post("/processes/{instance_id}/stages/{stage}/finalize")
def finalize(
    instance_id: uuid.UUID,
    stage: str,
    p: Principal = Depends(require("process.read")),
    db: Db = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    _visible_instance(db, p, instance_id)
    inst = service.lock_instance(db, instance_id)
    defn = service.load_definition(db, inst)
    try:
        service.finalize_stage(db, p.user.id, p.roles, inst, _stage(db, inst, stage), defn)
    except service.ProcessError as e:
        raise _err(e) from e
    return service.instance_view(db, inst, defn)


@router.post("/processes/{instance_id}/cancel")
def cancel(
    instance_id: uuid.UUID,
    body: CancelIn,
    p: Principal = Depends(require("process.cancel")),
    db: Db = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    inst = service.lock_instance(db, instance_id)
    try:
        service.cancel(db, p.user.id, inst, body.reason)
    except service.ProcessError as e:
        raise _err(e) from e
    return service.instance_view(db, inst, service.load_definition(db, inst))


@router.get("/my-work")
def my_work(
    p: Principal = Depends(require("process.read")), db: Db = Depends(get_db, scope="function")
) -> dict[str, Any]:
    items = service.my_work(db, p.roles)
    return {"count": len(items), "items": items}
