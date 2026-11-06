"""The Postgres job queue: idempotent enqueue, exclusive claims, retry, dead letters."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.jobs import queue
from app.models import Job


def test_enqueue_is_idempotent_with_a_dedupe_key(db: Session) -> None:
    a = queue.enqueue(db, "t", {"n": 1}, dedupe_key="k1")
    b = queue.enqueue(db, "t", {"n": 2}, dedupe_key="k1")
    c = queue.enqueue(db, "t", {"n": 3})  # no key: always a new job
    d = queue.enqueue(db, "t", {"n": 4})
    db.commit()
    assert a is not None and b is None and c is not None and d is not None
    assert db.execute(text("SELECT count(*) FROM job")).scalar_one() == 3


def test_success_marks_done_and_passes_the_payload(db: Session) -> None:
    seen: list[dict[str, Any]] = []
    queue.enqueue(db, "t", {"n": 7})
    db.commit()
    assert queue.run_one(db, {"t": lambda _db, p: seen.append(p)}) is True
    assert seen == [{"n": 7}]
    job = db.scalars(select(Job)).one()
    assert job.status == "done" and job.finished_at is not None and job.attempts == 1
    assert queue.run_one(db, {"t": lambda _db, p: None}) is False  # queue is empty


def test_only_jobs_of_known_kinds_are_claimed(db: Session) -> None:
    queue.enqueue(db, "other", {})
    db.commit()
    assert queue.run_one(db, {"t": lambda _db, p: None}) is False
    assert db.scalars(select(Job)).one().status == "queued"


def test_jobs_run_in_order_and_future_jobs_wait(db: Session) -> None:
    order: list[int] = []
    now = datetime.now(UTC)
    queue.enqueue(db, "t", {"n": 2}, run_at=now - timedelta(seconds=1))
    queue.enqueue(db, "t", {"n": 1}, run_at=now - timedelta(seconds=5))
    queue.enqueue(db, "t", {"n": 3}, run_at=now + timedelta(hours=1))
    db.commit()
    assert queue.drain(db, {"t": lambda _db, p: order.append(p["n"])}) == 2
    assert order == [1, 2]


def test_failure_retries_with_backoff_then_dies(db: Session) -> None:
    def boom(_db: Session, _p: dict[str, Any]) -> None:
        raise RuntimeError("nope")

    queue.enqueue(db, "t", {}, max_attempts=3)
    db.commit()
    for attempt in (1, 2):
        assert queue.run_one(db, {"t": boom}) is True
        job = db.scalars(select(Job)).one()
        db.refresh(job)
        assert job.status == "queued" and job.attempts == attempt
        assert "nope" in (job.last_error or "") and job.run_at > datetime.now(UTC)
        db.execute(text("UPDATE job SET run_at = now() - interval '1 second'"))
        db.commit()
    assert queue.run_one(db, {"t": boom}) is True
    job = db.scalars(select(Job)).one()
    db.refresh(job)
    assert job.status == "dead" and job.attempts == 3 and job.finished_at is not None
    assert queue.run_one(db, {"t": boom}) is False  # dead jobs are not retried


def test_backoff_grows_and_is_capped() -> None:
    assert [queue.backoff(n).total_seconds() for n in (1, 2, 3, 4)] == [10, 20, 40, 80]
    assert queue.backoff(30).total_seconds() == 3600


def test_a_crashed_workers_job_is_reclaimed(db: Session) -> None:
    queue.enqueue(db, "t", {})
    db.commit()
    job = queue.claim(db, "worker-dead")
    assert job is not None
    db.commit()
    assert queue.reclaim_stale(db) == 0  # still fresh
    assert queue.reclaim_stale(db, datetime.now(UTC) + timedelta(hours=1)) == 1
    db.commit()
    again = queue.claim(db, "worker-2")
    assert again is not None and again.id == job.id and again.attempts == 2


def test_concurrent_workers_never_run_a_job_twice(fresh_db: Engine) -> None:
    maker = sessionmaker(fresh_db, expire_on_commit=False)
    with maker() as s:
        for n in range(60):
            queue.enqueue(s, "t", {"n": n})
        s.commit()
    done: list[int] = []

    def worker(name: str) -> None:
        with maker() as s:
            queue.drain(s, {"t": lambda _db, p: done.append(p["n"])}, limit=100)

    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(worker, [f"w{i}" for i in range(6)]))
    assert sorted(done) == list(range(60))  # each exactly once
    with maker() as s:
        assert s.execute(text("SELECT count(*) FROM job WHERE status='done'")).scalar_one() == 60
