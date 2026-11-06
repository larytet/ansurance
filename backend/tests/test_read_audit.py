"""Asynchronous, batched audit of reads (owner decision 2026-11-06)."""

import threading
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.audit import chain
from app.audit.readbuffer import ReadAuditBuffer
from app.config import Settings
from app.main import create_app
from tests.conftest import login, make_account


def count(db: Session, action: str | None = None) -> int:
    db.rollback()  # see rows committed by other sessions
    if action is None:
        return int(db.execute(text("SELECT count(*) FROM audit_entry")).scalar_one())
    return int(
        db.execute(
            text("SELECT count(*) FROM audit_entry WHERE action = :a"), {"a": action}
        ).scalar_one()
    )


@pytest.fixture
def buf(fresh_db: Engine) -> ReadAuditBuffer:
    return ReadAuditBuffer(sessionmaker(fresh_db), flush_seconds=60, batch_size=200)


def test_record_returns_immediately_and_nothing_is_written_until_a_flush(
    buf: ReadAuditBuffer, db: Session
) -> None:
    for i in range(10):
        buf.record("u1", "client.read", f"client:{i}")
    assert buf.pending() == 10 and count(db) == 0  # no database work in the request path
    assert buf.flush() == 10 and buf.pending() == 0 and count(db) == 10


def test_flush_writes_batches_in_one_valid_chain_and_keeps_the_original_times(
    buf: ReadAuditBuffer, db: Session
) -> None:
    buf.record("u1", "client.read", "client:a", {"n": 1})
    before = db.execute(text("SELECT now()")).scalar_one()
    time.sleep(0.05)
    for i in range(449):  # 450 entries: batches of 200, 200 and 50
        buf.record("u2", "client.search", "clients", {"i": i})
    buf.stop()  # the thread may already have taken the first batch: wait for everything
    assert buf.pending() == 0 and buf.flushed == 450
    assert chain.verify(db).ok and count(db) == 450
    first = db.execute(text("SELECT at FROM audit_entry ORDER BY seq LIMIT 1")).scalar_one()
    assert first <= before  # the entry carries the time of the read, not of the flush
    assert db.execute(text("SELECT detail FROM audit_entry ORDER BY seq LIMIT 1")).scalar_one() == {
        "n": 1
    }


def test_synchronous_and_batched_entries_interleave_into_one_valid_chain(
    buf: ReadAuditBuffer, db: Session
) -> None:
    chain.append(db, "u1", "client.create", "client:x")
    db.commit()
    buf.record("u1", "client.read", "client:x")
    buf.flush()
    chain.append(db, "u1", "client.update", "client:x")
    db.commit()
    res = chain.verify(db)
    assert res.ok and res.entries == 3


def test_a_crash_loses_only_the_unflushed_reads_and_the_chain_stays_valid(
    buf: ReadAuditBuffer, db: Session
) -> None:
    chain.append(db, "u1", "client.create", "client:x")  # a write: synchronous
    db.commit()
    buf.record("u1", "client.read", "client:x")
    buf.flush()
    for _ in range(25):
        buf.record("u1", "client.read", "client:x")
    assert buf.discard() == 25  # the process dies with 25 reads still in memory
    res = chain.verify(db)
    assert res.ok and res.entries == 2  # the write and the flushed read survive, chain intact
    assert count(db, "client.create") == 1


def test_the_background_thread_flushes_on_its_own_and_stop_flushes_the_rest(
    fresh_db: Engine, db: Session
) -> None:
    b = ReadAuditBuffer(sessionmaker(fresh_db), flush_seconds=0.1, batch_size=1000)
    b.record("u1", "client.read", "client:1")
    deadline = time.time() + 5
    while count(db) < 1 and time.time() < deadline:
        time.sleep(0.05)
    assert count(db) == 1  # written without anybody calling flush
    b.record("u1", "client.read", "client:2")
    b.stop()  # orderly shutdown writes what is left
    assert count(db) == 2 and b.pending() == 0


def test_reaching_the_batch_size_wakes_the_flusher_early(fresh_db: Engine, db: Session) -> None:
    b = ReadAuditBuffer(sessionmaker(fresh_db), flush_seconds=60, batch_size=20)
    for i in range(20):
        b.record("u1", "client.read", f"client:{i}")
    deadline = time.time() + 5
    while count(db) < 20 and time.time() < deadline:
        time.sleep(0.05)
    assert count(db) == 20  # did not wait for the 60 s interval
    b.stop()


def test_a_full_queue_drops_the_oldest_and_counts_it(fresh_db: Engine, db: Session) -> None:
    b = ReadAuditBuffer(sessionmaker(fresh_db), flush_seconds=60, batch_size=10_000, max_queue=5)
    for i in range(8):
        b.record("u1", "client.read", f"client:{i}")
    assert b.pending() == 5 and b.dropped == 3  # bounded memory beats completeness
    b.flush()
    objs = [r[0] for r in db.execute(text("SELECT object FROM audit_entry ORDER BY seq"))]
    assert objs == [f"client:{i}" for i in range(3, 8)]  # the newest survive


def test_a_database_failure_keeps_the_entries_for_the_next_attempt(
    fresh_db: Engine, db: Session
) -> None:
    ok = sessionmaker(fresh_db)
    calls = {"n": 0}

    def flaky() -> Session:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database unavailable")
        return ok()

    b = ReadAuditBuffer(flaky, flush_seconds=60)  # type: ignore[arg-type]
    for i in range(3):
        b.record("u1", "client.read", f"client:{i}")
    assert b.flush() == 0 and b.pending() == 3  # nothing lost, nothing raised
    assert b.flush() == 3 and count(db) == 3
    assert [r[0] for r in db.execute(text("SELECT object FROM audit_entry ORDER BY seq"))] == [
        "client:0",
        "client:1",
        "client:2",
    ]  # original order kept


def test_concurrent_requests_and_the_flusher_never_corrupt_the_chain(
    fresh_db: Engine, db: Session
) -> None:
    b = ReadAuditBuffer(sessionmaker(fresh_db), flush_seconds=0.05, batch_size=50)
    maker = sessionmaker(fresh_db)

    def readers(n: int) -> None:
        for i in range(100):
            b.record(f"r{n}", "client.read", f"client:{i}")

    def writers(n: int) -> None:
        for i in range(10):
            with maker() as s:
                chain.append(s, f"w{n}", "client.update", f"client:{i}")
                s.commit()

    threads = [threading.Thread(target=readers, args=(i,)) for i in range(4)] + [
        threading.Thread(target=writers, args=(i,)) for i in range(3)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    b.stop()
    res = chain.verify(db)
    assert res.ok and res.entries == 4 * 100 + 3 * 10


# ---- through the API ---------------------------------------------------------------------------
def test_api_reads_are_buffered_and_writes_are_not(
    settings: Settings, fresh_db: Engine, db: Session
) -> None:
    s = settings.model_copy(update={"read_audit_flush_seconds": 3600})
    app = create_app(s, fresh_db)
    c = TestClient(app, base_url="https://testserver")
    acct = make_account(db, "ann@agency.il", ["agent"])
    hdr = login(c, db, acct)
    cid = c.post(
        "/api/clients", headers=hdr, json={"id_number": "123456782", "name": "David Cohen"}
    ).json()["id"]
    c.get(f"/api/clients/{cid}")
    c.get("/api/clients", params={"q": "david"})
    c.get(f"/api/clients/{cid}/policies")
    assert count(db, "client.create") == 1  # the write is in the audit log already
    assert count(db, "client.read") == 0 and app.state.read_audit.pending() == 3
    app.state.read_audit.flush()
    assert count(db, "client.read") == 1 and count(db, "client.search") == 1
    assert count(db, "policy.read") == 1 and chain.verify(db).ok


def test_denials_logins_and_downloads_stay_synchronous(
    settings: Settings, fresh_db: Engine, db: Session
) -> None:
    s = settings.model_copy(update={"read_audit_flush_seconds": 3600})
    app = create_app(s, fresh_db)
    c = TestClient(app, base_url="https://testserver")
    acct = make_account(db, "mkt@agency.il", ["marketing"])
    hdr = login(c, db, acct)
    assert c.get("/api/audit/verify").status_code == 403  # a denial
    assert count(db, "login.success") == 1 and count(db, "authz.deny") == 1
    assert app.state.read_audit.pending() == 0 and hdr  # none of them went through the buffer


def test_orderly_shutdown_of_the_app_flushes_pending_reads(
    settings: Settings, fresh_db: Engine, db: Session
) -> None:
    s = settings.model_copy(update={"read_audit_flush_seconds": 3600})
    app = create_app(s, fresh_db)
    with TestClient(app, base_url="https://testserver") as c:  # runs startup and shutdown
        acct = make_account(db, "ann@agency.il", ["agent"])
        hdr = login(c, db, acct)
        cid = c.post(
            "/api/clients", headers=hdr, json={"id_number": "123456782", "name": "David Cohen"}
        ).json()["id"]
        c.get(f"/api/clients/{cid}")
        assert count(db, "client.read") == 0
    assert count(db, "client.read") == 1  # written when the application stopped
