"""Seal the audit chain into Parquet files held outside the database, and verify both.

A seal covers a contiguous range of the chain, closed once a month has ended (plus a short
grace period for entries still in the read-audit buffer). The Parquet file goes to the
write-once audit-archive bucket and its SHA-256 and the head hash are recorded in
`audit_seal`. Verification checks the hot chain, then every sealed file: its own hashes,
that it links to the previous seal, and that it matches what the database holds. Someone who
rewrites hot rows and recomputes the hashes is caught by the archive; someone who truncates
the newest entries after the last seal is not (only the next seal anchors them).
"""

import io
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import chain
from app.blob.base import BlobRef, BlobStore, IntegrityError
from app.models import AuditEntry, AuditSeal

GRACE = timedelta(hours=1)  # entries of the last minutes of a month may still be buffered
SCHEMA = pa.schema(
    [
        ("seq", pa.int64()),
        ("at", pa.timestamp("us", tz="UTC")),
        ("actor", pa.string()),
        ("action", pa.string()),
        ("object", pa.string()),
        ("detail", pa.string()),
        ("prev_hash", pa.string()),
        ("hash", pa.string()),
    ]
)


def _month_end(period: str) -> datetime:
    y, m = int(period[:4]), int(period[5:7])
    return datetime(y + (m == 12), 1 if m == 12 else m + 1, 1, tzinfo=UTC)


def _period_of(dt: datetime) -> str:
    return f"{dt.year:04d}-{dt.month:02d}"


def _to_parquet(rows: list[AuditEntry]) -> bytes:
    table = pa.Table.from_pylist(
        [
            {
                "seq": r.seq,
                "at": r.at,
                "actor": r.actor,
                "action": r.action,
                "object": r.object,
                "detail": json.dumps(
                    r.detail, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ),
                "prev_hash": r.prev_hash,
                "hash": r.hash,
            }
            for r in rows
        ],
        schema=SCHEMA,
    )
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="zstd")
    return buf.getvalue()


def seal_period(db: Session, store: BlobStore, period: str) -> AuditSeal | None:
    """Seal the unsealed chain up to the end of `period`. None when there is nothing to seal."""
    if db.scalar(select(AuditSeal.id).where(AuditSeal.period == period)) is not None:
        return None
    last = db.scalar(select(func.max(AuditSeal.last_seq))) or 0
    end = _month_end(period)
    cut = db.scalar(select(func.max(AuditEntry.seq)).where(AuditEntry.at < end))
    if cut is None or cut <= last:
        return None
    rows = list(
        db.scalars(
            select(AuditEntry)
            .where(AuditEntry.seq > last, AuditEntry.seq <= cut)
            .order_by(AuditEntry.seq)
        )
    )
    ref = store.put(_to_parquet(rows))
    seal = AuditSeal(
        period=period,
        first_seq=rows[0].seq,
        last_seq=rows[-1].seq,
        entries=len(rows),
        head_hash=rows[-1].hash,
        file_sha256=ref.sha256,
        bucket=ref.bucket,
        key=ref.key,
    )
    db.add(seal)
    db.flush()
    chain.append(
        db,
        "system",
        "audit.seal",
        f"seal:{period}",
        {"entries": len(rows), "head": seal.head_hash, "file": ref.sha256},
    )
    return seal


def seal_due(db: Session, store: BlobStore, now: datetime | None = None) -> list[str]:
    """Seal every finished month that is not sealed yet. Returns the periods sealed."""
    now = now or datetime.now(UTC)
    first = db.scalar(select(func.min(AuditEntry.at)))
    if first is None:
        return []
    sealed: list[str] = []
    year, month = first.year, first.month
    while True:
        period = f"{year:04d}-{month:02d}"
        if _month_end(period) + GRACE > now:
            break  # this month has not finished (or is still in its grace period)
        if seal_period(db, store, period) is not None:
            sealed.append(period)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return sealed


@dataclass
class ArchiveVerify:
    ok: bool
    hot_ok: bool
    hot_entries: int
    seals: int = 0
    sealed_entries: int = 0
    problems: list[str] = field(default_factory=list)


def _read(store: BlobStore, seal: AuditSeal) -> list[dict[str, Any]]:
    data = store.get(BlobRef(seal.file_sha256, 0, seal.bucket, seal.key))
    return list(pq.read_table(io.BytesIO(data)).to_pylist())


def verify_all(db: Session, store: BlobStore) -> ArchiveVerify:
    hot = chain.verify(db)
    out = ArchiveVerify(ok=hot.ok, hot_ok=hot.ok, hot_entries=hot.entries)
    if not hot.ok:
        out.problems.append(f"hot chain: {hot.reason} at seq {hot.first_bad_seq}")
    prev_head = chain.GENESIS
    expected_next = None
    for seal in db.scalars(select(AuditSeal).order_by(AuditSeal.first_seq)):
        out.seals += 1
        tag = f"seal {seal.period}"
        if expected_next is not None and seal.first_seq != expected_next:
            out.problems.append(f"{tag}: gap before it (expected seq {expected_next})")
        expected_next = seal.last_seq + 1
        try:
            rows = _read(store, seal)
        except (IntegrityError, KeyError, OSError, ValueError, pa.ArrowException) as e:
            out.problems.append(f"{tag}: archive file unreadable or altered ({type(e).__name__})")
            continue
        out.sealed_entries += len(rows)
        if len(rows) != seal.entries or not rows:
            out.problems.append(f"{tag}: has {len(rows)} entries, expected {seal.entries}")
            continue
        h = prev_head
        for r in rows:
            at = r["at"].astimezone(UTC)
            want = chain._hash(
                h,
                chain._canonical(  # noqa: SLF001
                    at, r["actor"], r["action"], r["object"], json.loads(r["detail"])
                ),
            )
            if r["prev_hash"] != h or r["hash"] != want:
                out.problems.append(f"{tag}: archive chain breaks at seq {r['seq']}")
                break
            h = r["hash"]
        else:
            if h != seal.head_hash:
                out.problems.append(f"{tag}: head hash does not match the seal record")
        prev_head = seal.head_hash
        hot_rows = dict(
            db.execute(
                select(AuditEntry.seq, AuditEntry.hash).where(
                    AuditEntry.seq.between(seal.first_seq, seal.last_seq)
                )
            ).all()
        )
        sealed = {r["seq"]: r["hash"] for r in rows}
        missing = sorted(set(sealed) - set(hot_rows))
        differ = sorted(s for s in sealed if s in hot_rows and hot_rows[s] != sealed[s])
        if missing:
            out.problems.append(
                f"{tag}: {len(missing)} sealed entries missing from the database "
                f"(first seq {missing[0]})"
            )
        if differ:
            out.problems.append(
                f"{tag}: {len(differ)} database entries differ from the archive "
                f"(first seq {differ[0]})"
            )
    out.ok = not out.problems
    return out
