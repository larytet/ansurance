"""T-M7-3, T-M7-5 (daily ops): sealing the audit chain into Parquet and verifying both."""

import io
import json
from datetime import UTC, datetime, timedelta

import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from app import ops
from app.audit import archive, chain
from app.blob.base import BlobRef
from app.blob.fake import FakeBlobStore
from app.config import Settings
from app.main import create_app
from app.models import AuditSeal, InboxItem
from tests.conftest import login, make_account


def at(year: int, month: int, day: int = 10, hour: int = 12) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


def add(
    db: Session, when: datetime, n: int = 1, actor: str = "u1", action: str = "client.read"
) -> None:
    chain.append_many(
        db,
        [chain.Pending(when, actor, action, f"client:{i}", {"i": i, "ü": "קוד"}) for i in range(n)],
    )
    db.commit()


@pytest.fixture
def store() -> FakeBlobStore:
    return FakeBlobStore("audit-archive")


def seal_row(db: Session, period: str) -> AuditSeal:
    return db.scalars(select(AuditSeal).where(AuditSeal.period == period)).one()


# ---- sealing ------------------------------------------------------------------
def test_a_seal_holds_the_range_in_parquet_and_records_its_head_and_hash(
    db: Session, store: FakeBlobStore
) -> None:
    add(db, at(2026, 1), 5)
    seal = archive.seal_period(db, store, "2026-01")
    db.commit()
    assert seal is not None and (seal.first_seq, seal.last_seq, seal.entries) == (1, 5, 5)
    rows = db.execute(text("SELECT hash FROM audit_entry ORDER BY seq")).all()
    assert seal.head_hash == rows[4][0] and store.exists(seal.file_sha256)
    table = pq.read_table(
        io.BytesIO(store.get(BlobRef(seal.file_sha256, 0, seal.bucket, seal.key)))
    )
    assert table.num_rows == 5 and table.column_names == [
        "seq",
        "at",
        "actor",
        "action",
        "object",
        "detail",
        "prev_hash",
        "hash",
    ]
    assert json.loads(table.column("detail")[0].as_py()) == {"i": 0, "ü": "קוד"}
    assert (
        db.execute(text("SELECT count(*) FROM audit_entry WHERE action='audit.seal'")).scalar_one()
        == 1
    )  # the act of sealing is itself audited


def test_sealing_is_idempotent_and_the_next_seal_continues_where_the_last_ended(
    db: Session, store: FakeBlobStore
) -> None:
    add(db, at(2026, 1), 3)
    assert archive.seal_period(db, store, "2026-01") is not None
    assert archive.seal_period(db, store, "2026-01") is None  # same period again: nothing
    add(db, at(2026, 2), 4)
    s2 = archive.seal_period(db, store, "2026-02")
    db.commit()
    s1 = seal_row(db, "2026-01")
    assert s2 is not None
    # contiguous: the first seal's own audit.seal entry is the first entry of the next range
    assert s2.first_seq == s1.last_seq + 1
    assert archive.seal_period(db, store, "2026-03") is None  # nothing new to seal


def test_seal_due_seals_only_finished_months_after_the_grace_period(
    db: Session, store: FakeBlobStore
) -> None:
    add(db, at(2026, 1), 2)
    add(db, at(2026, 2), 2)
    add(db, at(2026, 3), 2)
    assert archive.seal_due(db, store, datetime(2026, 2, 1, 0, 30, tzinfo=UTC)) == []  # grace
    assert archive.seal_due(db, store, datetime(2026, 2, 15, tzinfo=UTC)) == ["2026-01"]
    assert archive.seal_due(db, store, datetime(2026, 3, 20, tzinfo=UTC)) == ["2026-02"]
    assert archive.seal_due(db, store, datetime(2026, 3, 20, tzinfo=UTC)) == []  # idempotent
    db.commit()
    assert archive.seal_due(db, store, datetime(2026, 4, 2, tzinfo=UTC)) == ["2026-03"]


def test_a_late_flushed_entry_of_the_old_month_is_still_sealed_with_its_month(
    db: Session, store: FakeBlobStore
) -> None:
    """Batched read entries can reach the chain after newer entries: the seal covers a
    contiguous range, so the chain order is preserved and nothing is skipped."""
    add(db, at(2026, 1, 31, 23))
    add(db, at(2026, 2, 1, 0))  # newer entry first ...
    add(db, at(2026, 1, 31, 23))  # ... then a January read that was still buffered
    archive.seal_due(db, store, datetime(2026, 2, 10, tzinfo=UTC))
    db.commit()
    s = seal_row(db, "2026-01")
    assert (s.first_seq, s.last_seq, s.entries) == (1, 3, 3)
    assert archive.verify_all(db, store).ok


def test_empty_database_and_months_without_entries(db: Session, store: FakeBlobStore) -> None:
    assert archive.seal_due(db, store, datetime(2027, 1, 1, tzinfo=UTC)) == []
    add(db, at(2026, 1), 1)
    add(db, at(2026, 5), 1)  # nothing in February to April
    assert archive.seal_due(db, store, datetime(2026, 9, 1, tzinfo=UTC)) == ["2026-01", "2026-05"]


# ---- verification -------------------------------------------------------------
def test_a_clean_chain_verifies_across_the_hot_table_and_the_archive(
    db: Session, store: FakeBlobStore
) -> None:
    add(db, at(2026, 1), 20)
    add(db, at(2026, 2), 20)
    archive.seal_due(db, store, datetime(2026, 3, 5, tzinfo=UTC))
    add(db, at(2026, 3), 7)  # unsealed tail
    db.commit()
    v = archive.verify_all(db, store)
    assert v.ok and v.hot_ok and v.seals == 2 and v.sealed_entries >= 40 and v.problems == []
    assert v.hot_entries == db.execute(text("SELECT count(*) FROM audit_entry")).scalar_one()


def _rewrite_consistently(db: Session, bind: Engine, seq: int, new_detail: dict[str, int]) -> None:
    """An attacker with database superuser who edits an entry AND recomputes every later
    hash, so the hot chain alone still looks valid."""
    rows = list(db.scalars(select(chain.AuditEntry).order_by(chain.AuditEntry.seq)))
    prev = chain.GENESIS
    with bind.begin() as c:
        c.execute(text("SET session_replication_role = replica"))
        for r in rows:
            detail = new_detail if r.seq == seq else r.detail
            h = chain._hash(prev, chain._canonical(r.at, r.actor, r.action, r.object, detail))  # noqa: SLF001
            c.execute(
                text(
                    "UPDATE audit_entry SET detail = CAST(:d AS jsonb), prev_hash = :p, "
                    "hash = :h WHERE seq = :s"
                ),
                {"d": json.dumps(detail), "p": prev, "h": h, "s": r.seq},
            )
            prev = h
        c.execute(text("SET session_replication_role = DEFAULT"))


def test_a_consistent_rewrite_of_history_passes_the_hot_check_but_not_the_archive(
    db: Session, fresh_db: Engine, store: FakeBlobStore
) -> None:
    add(db, at(2026, 1), 10)
    archive.seal_due(db, store, datetime(2026, 2, 5, tzinfo=UTC))
    db.commit()
    _rewrite_consistently(db, fresh_db, 4, {"i": 999})
    db.expire_all()
    assert chain.verify(db).ok  # the hot table alone is fooled
    v = archive.verify_all(db, store)
    assert not v.ok and v.hot_ok
    assert any("differ from the archive" in p for p in v.problems), v.problems


def test_deleting_sealed_entries_from_the_tail_is_caught_by_the_archive(
    db: Session, fresh_db: Engine, store: FakeBlobStore
) -> None:
    add(db, at(2026, 1), 10)
    archive.seal_due(db, store, datetime(2026, 2, 5, tzinfo=UTC))
    db.commit()
    with fresh_db.begin() as c:
        c.execute(text("SET session_replication_role = replica"))
        c.execute(text("DELETE FROM audit_entry WHERE seq > 6"))  # incl. the audit.seal entry
        c.execute(text("SET session_replication_role = DEFAULT"))
    db.expire_all()
    assert chain.verify(db).ok  # a shorter chain is still a valid chain
    v = archive.verify_all(db, store)
    assert not v.ok and any("missing from the database" in p for p in v.problems), v.problems


def test_the_plain_hot_tamper_is_reported_too(
    db: Session, fresh_db: Engine, store: FakeBlobStore
) -> None:
    add(db, at(2026, 1), 5)
    with fresh_db.begin() as c:
        c.execute(text("SET session_replication_role = replica"))
        c.execute(text("UPDATE audit_entry SET actor = 'mallory' WHERE seq = 3"))
        c.execute(text("SET session_replication_role = DEFAULT"))
    db.expire_all()
    v = archive.verify_all(db, store)
    assert not v.ok and not v.hot_ok and "hot chain" in v.problems[0]


def test_an_altered_or_missing_archive_file_is_detected(db: Session, store: FakeBlobStore) -> None:
    add(db, at(2026, 1), 5)
    archive.seal_due(db, store, datetime(2026, 2, 5, tzinfo=UTC))
    db.commit()
    s = seal_row(db, "2026-01")
    store._objects[s.key] = b"PAR1 not the real file"  # noqa: SLF001
    v = archive.verify_all(db, store)
    assert not v.ok and any("unreadable or altered" in p for p in v.problems), v.problems
    del store._objects[s.key]  # noqa: SLF001
    v = archive.verify_all(db, store)
    assert not v.ok and any("unreadable or altered" in p for p in v.problems), v.problems


def test_a_missing_seal_leaves_a_visible_gap(
    db: Session, fresh_db: Engine, store: FakeBlobStore
) -> None:
    add(db, at(2026, 1), 3)
    add(db, at(2026, 2), 3)
    add(db, at(2026, 3), 3)
    archive.seal_due(db, store, datetime(2026, 4, 5, tzinfo=UTC))
    db.commit()
    with fresh_db.begin() as c:  # the database refuses this; a superuser can force it
        c.execute(text("SET session_replication_role = replica"))
        c.execute(text("DELETE FROM audit_seal WHERE period = '2026-02'"))
        c.execute(text("SET session_replication_role = DEFAULT"))
    db.expire_all()
    v = archive.verify_all(db, store)
    assert not v.ok and any("gap before it" in p for p in v.problems), v.problems


def test_seals_cannot_be_changed_or_removed_through_normal_access(
    db: Session, store: FakeBlobStore
) -> None:
    from sqlalchemy.exc import DatabaseError

    add(db, at(2026, 1), 2)
    archive.seal_due(db, store, datetime(2026, 2, 5, tzinfo=UTC))
    db.commit()
    for sql in ("UPDATE audit_seal SET head_hash = repeat('a', 64)", "DELETE FROM audit_seal"):
        with pytest.raises(DatabaseError, match="append-only"):
            db.execute(text(sql))
        db.rollback()


# ---- daily operations ---------------------------------------------------------
def test_daily_run_seals_verifies_and_records_the_result(
    db: Session, settings: Settings, store: FakeBlobStore
) -> None:
    add(db, at(2026, 1), 4)
    ops.set_info(db, "backup.last_success", datetime(2026, 2, 9, 1, tzinfo=UTC).isoformat())
    now = datetime(2026, 2, 10, tzinfo=UTC)
    rep = ops.run_daily(db, settings, store, now)
    db.commit()
    assert rep["sealed"] == ["2026-01"] and rep["audit_ok"] is True and rep["backup_ok"] is True
    assert ops.get_info(db, "audit.last_verify") == f"{now.isoformat()}|ok"
    assert db.scalars(select(InboxItem)).all() == []  # nothing to worry the manager with


def test_a_failed_verification_raises_one_urgent_manager_item_not_many(
    db: Session, fresh_db: Engine, settings: Settings, store: FakeBlobStore
) -> None:
    add(db, at(2026, 1), 6)
    ops.set_info(db, "backup.last_success", datetime.now(UTC).isoformat())
    with fresh_db.begin() as c:
        c.execute(text("SET session_replication_role = replica"))
        c.execute(text("UPDATE audit_entry SET actor = 'mallory' WHERE seq = 2"))
        c.execute(text("SET session_replication_role = DEFAULT"))
    db.expire_all()
    for _ in range(3):  # three nights in a row
        rep = ops.run_daily(db, settings, store, datetime(2026, 2, 10, tzinfo=UTC))
        db.commit()
    assert rep["audit_ok"] is False and rep["audit_problems"]
    (item,) = db.scalars(select(InboxItem)).all()
    assert item.role == "manager" and item.urgency == "urgent" and item.status == "open"
    assert "FAILED" in item.title and "hot chain" in item.detail
    assert ops.get_info(db, "audit.last_verify").endswith("|FAILED")  # type: ignore[union-attr]
    assert (
        db.execute(
            text("SELECT count(*) FROM audit_entry WHERE action='audit.verify_failed'")
        ).scalar_one()
        == 3
    )


def test_an_overdue_or_missing_backup_alerts_the_manager(
    db: Session, settings: Settings, store: FakeBlobStore
) -> None:
    now = datetime(2026, 2, 10, 12, tzinfo=UTC)
    rep = ops.run_daily(db, settings, store, now)
    assert rep["backup_ok"] is False
    (item,) = db.scalars(select(InboxItem)).all()
    assert item.title == "Backup is overdue" and "No successful backup" in item.detail
    ops.set_info(db, "backup.last_success", (now - timedelta(hours=40)).isoformat())
    ops.run_daily(db, settings, store, now)
    assert len(db.scalars(select(InboxItem)).all()) == 1  # still the one open item
    ops.set_info(db, "backup.last_success", (now - timedelta(hours=3)).isoformat())
    assert ops.run_daily(db, settings, store, now)["backup_ok"] is True


def test_daily_due_follows_the_interval(db: Session, settings: Settings) -> None:
    now = datetime(2026, 2, 10, tzinfo=UTC)
    assert ops.daily_due(db, settings, now)  # never run
    ops.set_info(db, "ops.daily_last", now.isoformat())
    assert not ops.daily_due(db, settings, now + timedelta(hours=23))
    assert ops.daily_due(db, settings, now + timedelta(hours=24))


# ---- API ----------------------------------------------------------------------
def test_manager_verify_endpoint_reports_seals_and_problems(
    settings: Settings, fresh_db: Engine, db: Session, store: FakeBlobStore
) -> None:
    app = create_app(settings, fresh_db, archive_blobs=store)
    c = TestClient(app, base_url="https://testserver")
    login(c, db, make_account(db, "mgr@agency.il", ["manager"]))
    add(db, at(2026, 1), 5)
    archive.seal_due(db, store, datetime(2026, 2, 5, tzinfo=UTC))
    db.commit()
    r = c.get("/api/audit/verify").json()
    # the range is contiguous, so it also holds the login entries recorded just before
    assert r["ok"] is True and r["seals"] == 1 and r["sealed_entries"] >= 5
    assert r["problems"] == []
    s = seal_row(db, "2026-01")
    store._objects[s.key] = b"tampered"  # noqa: SLF001
    r = c.get("/api/audit/verify").json()
    assert r["ok"] is False and "archive file" in r["reason"]
