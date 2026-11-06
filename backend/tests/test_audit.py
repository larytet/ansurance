"""T-M1-10. Also P-13 (audit tamper)."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session

from app.audit import chain
from tests.conftest import login, make_account


def test_entries_are_linked_and_verify(db: Session) -> None:
    for i in range(5):
        chain.append(db, "u1", "test.action", f"obj:{i}", {"i": i, "ünï": "קוד"})
    db.commit()
    rows = db.execute(text("SELECT seq, prev_hash, hash FROM audit_entry ORDER BY seq")).all()
    assert rows[0][1] == chain.GENESIS
    assert all(rows[i][1] == rows[i - 1][2] for i in range(1, 5))
    res = chain.verify(db)
    assert res.ok and res.entries == 5


def test_empty_chain_verifies(db: Session) -> None:
    assert chain.verify(db).ok


def _tamper(engine: Engine, sql: str) -> None:
    """Change a row bypassing the trigger, as an attacker with database superuser could."""
    with engine.begin() as c:
        c.execute(text("SET session_replication_role = replica"))
        c.execute(text(sql))
        c.execute(text("SET session_replication_role = DEFAULT"))


@pytest.mark.parametrize(
    "sql,reason",
    [
        ("UPDATE audit_entry SET actor = 'mallory' WHERE seq = 3", "hash mismatch"),
        ("UPDATE audit_entry SET detail = '{\"i\": 99}' WHERE seq = 2", "hash mismatch"),
        ("DELETE FROM audit_entry WHERE seq = 3", "broken link"),
        ("UPDATE audit_entry SET prev_hash = repeat('a', 64) WHERE seq = 4", "broken link"),
    ],
)
def test_tamper_is_detected(db: Session, fresh_db: Engine, sql: str, reason: str) -> None:
    for i in range(5):
        chain.append(db, "u1", "test.action", f"obj:{i}", {"i": i})
    db.commit()
    _tamper(fresh_db, sql)
    db.expire_all()
    res = chain.verify(db)
    assert not res.ok and res.reason == reason and res.first_bad_seq is not None


def test_truncating_the_tail_is_not_silent_when_head_is_recorded(db: Session) -> None:
    """Deleting the newest entries leaves a valid prefix: the head hash must be anchored
    externally (sealed archive, M7). Here we only assert the head is observable."""
    for i in range(3):
        chain.append(db, "u1", "a", f"o:{i}")
    db.commit()
    head = db.execute(text("SELECT hash FROM audit_entry ORDER BY seq DESC LIMIT 1")).scalar_one()
    assert len(head) == 64


def test_update_delete_truncate_refused_by_database(db: Session) -> None:
    chain.append(db, "u1", "a", "o")
    db.commit()
    for sql in (
        "UPDATE audit_entry SET actor = 'x'",
        "DELETE FROM audit_entry",
        "TRUNCATE audit_entry",
    ):
        with pytest.raises(DatabaseError, match="append-only"):
            db.execute(text(sql))
        db.rollback()
    assert db.execute(text("SELECT count(*) FROM audit_entry")).scalar_one() == 1


def test_manager_verify_endpoint(api: TestClient, db: Session) -> None:
    m = make_account(db, "mgr@agency.il", ["manager"])
    login(api, db, m)
    r = api.get("/api/audit/verify")
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["entries"] >= 1


def test_concurrent_appends_keep_one_linear_chain(fresh_db: Engine) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy.orm import sessionmaker

    maker = sessionmaker(fresh_db)

    def work(n: int) -> None:
        for i in range(10):
            with maker() as s:
                chain.append(s, f"w{n}", "concurrent", f"o:{i}")
                s.commit()

    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(work, range(6)))
    with maker() as s:
        res = chain.verify(s)
    assert res.ok and res.entries == 60
