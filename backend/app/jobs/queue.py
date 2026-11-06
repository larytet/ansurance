"""A job queue inside PostgreSQL (SELECT ... FOR UPDATE SKIP LOCKED). No broker.

Jobs are created in the same transaction as the business change that needs them, so
nothing is lost or duplicated. A `dedupe_key` makes enqueueing idempotent.
"""

import logging
import traceback
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session as Db

from app.models import Job

log = logging.getLogger("ansurance.jobs")
Handler = Callable[[Db, dict[str, Any]], None]
STALE_AFTER = timedelta(minutes=15)


def _now() -> datetime:
    return datetime.now(UTC)


def enqueue(
    db: Db,
    kind: str,
    payload: dict[str, Any],
    dedupe_key: str | None = None,
    run_at: datetime | None = None,
    max_attempts: int = 5,
) -> uuid.UUID | None:
    """Queue a job. Returns None when a job with the same dedupe key already exists."""
    job_id = uuid.uuid4()
    stmt = (
        insert(Job)
        .values(
            id=job_id,
            kind=kind,
            payload=payload,
            dedupe_key=dedupe_key,
            run_at=run_at or _now(),
            max_attempts=max_attempts,
            status="queued",
            attempts=0,
        )
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
        .returning(Job.id)
    )
    return db.execute(stmt).scalar()


def reclaim_stale(db: Db, now: datetime | None = None) -> int:
    """Jobs whose worker died while running go back to the queue."""
    now = now or _now()
    res = db.execute(
        update(Job)
        .where(Job.status == "running", Job.locked_at < now - STALE_AFTER)
        .values(status="queued", locked_at=None, locked_by=None)
    )
    return int(res.rowcount or 0)  # type: ignore[attr-defined]


def claim(db: Db, worker: str, kinds: list[str] | None = None) -> Job | None:
    stmt = select(Job).where(Job.status == "queued", Job.run_at <= _now())
    if kinds:
        stmt = stmt.where(Job.kind.in_(kinds))
    job = db.scalar(stmt.order_by(Job.run_at, Job.id).limit(1).with_for_update(skip_locked=True))
    if job is None:
        return None
    job.status, job.locked_at, job.locked_by = "running", _now(), worker
    job.attempts += 1
    db.flush()
    return job


def backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(10 * 2 ** (attempts - 1), 3600))


def run_one(db: Db, handlers: dict[str, Handler], worker: str = "worker") -> bool:
    """Claim and run one job. Returns False when the queue is empty."""
    job = claim(db, worker, list(handlers))
    if job is None:
        return False
    job_id, kind, payload = job.id, job.kind, dict(job.payload)
    db.commit()  # the claim is visible to others before the work starts
    try:
        handlers[kind](db, payload)
        db.commit()
    except Exception as e:  # noqa: BLE001  (any handler failure is recorded and retried)
        db.rollback()
        log.warning("job %s (%s) failed: %s", job_id, kind, e)
        row = db.get(Job, job_id)
        if row is not None:
            row.last_error = "".join(traceback.format_exception_only(type(e), e)).strip()[:2000]
            if row.attempts >= row.max_attempts:
                row.status, row.finished_at = "dead", _now()
            else:
                row.status, row.locked_at, row.locked_by = "queued", None, None
                row.run_at = _now() + backoff(row.attempts)
            db.commit()
        return True
    row = db.get(Job, job_id)
    if row is not None:
        row.status, row.finished_at, row.last_error = "done", _now(), None
        db.commit()
    return True


def drain(db: Db, handlers: dict[str, Handler], limit: int = 1000) -> int:
    n = 0
    while n < limit and run_one(db, handlers):
        n += 1
    return n
