"""Append-only hash-chained audit log (REQUIREMENTS section 7, DESIGN section 6).

hash = SHA-256(prev_hash || canonical_json(at, actor, action, object, detail)).
Inserts are serialised with a transaction-scoped advisory lock. UPDATE, DELETE and
TRUNCATE are refused by database triggers (see migration 0002).
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import AuditEntry

GENESIS = "0" * 64
_LOCK_KEY = 7_303_001  # arbitrary constant for pg_advisory_xact_lock


def _canonical(at: datetime, actor: str, action: str, obj: str, detail: dict[str, Any]) -> str:
    return json.dumps(
        {
            "at": at.astimezone(UTC).isoformat(),
            "actor": actor,
            "action": action,
            "object": obj,
            "detail": detail,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _hash(prev: str, payload: str) -> str:
    return hashlib.sha256((prev + payload).encode()).hexdigest()


def append(
    db: Session, actor: str, action: str, obj: str, detail: dict[str, Any] | None = None
) -> AuditEntry:
    detail = detail or {}
    db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOCK_KEY})
    prev = db.scalar(select(AuditEntry.hash).order_by(AuditEntry.seq.desc()).limit(1)) or GENESIS
    at = datetime.now(UTC)
    entry = AuditEntry(
        at=at,
        actor=actor,
        action=action,
        object=obj,
        detail=detail,
        prev_hash=prev,
        hash=_hash(prev, _canonical(at, actor, action, obj, detail)),
    )
    db.add(entry)
    db.flush()
    return entry


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    entries: int
    first_bad_seq: int | None = None
    reason: str = ""


def verify(db: Session) -> VerifyResult:
    prev, count = GENESIS, 0
    for e in db.scalars(select(AuditEntry).order_by(AuditEntry.seq)):
        count += 1
        if e.prev_hash != prev:
            return VerifyResult(False, count, e.seq, "broken link")
        if e.hash != _hash(prev, _canonical(e.at, e.actor, e.action, e.object, e.detail)):
            return VerifyResult(False, count, e.seq, "hash mismatch")
        prev = e.hash
    return VerifyResult(True, count)


@dataclass(frozen=True)
class Pending:
    """An entry captured at the time of the action, written later in a batch."""

    at: datetime
    actor: str
    action: str
    obj: str
    detail: dict[str, Any]


def append_many(db: Session, entries: list[Pending]) -> int:
    """Append several entries under one lock acquisition and one transaction."""
    if not entries:
        return 0
    db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOCK_KEY})
    prev = db.scalar(select(AuditEntry.hash).order_by(AuditEntry.seq.desc()).limit(1)) or GENESIS
    for e in entries:
        h = _hash(prev, _canonical(e.at, e.actor, e.action, e.obj, e.detail))
        db.add(
            AuditEntry(
                at=e.at,
                actor=e.actor,
                action=e.action,
                object=e.obj,
                detail=e.detail,
                prev_hash=prev,
                hash=h,
            )
        )
        prev = h
    db.flush()
    return len(entries)
