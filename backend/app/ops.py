"""Daily operations run by the worker: seal the audit log, verify it, check the backups."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import archive, chain
from app.blob.base import BlobStore
from app.config import Settings
from app.models import InboxItem, PlatformInfo

log = logging.getLogger("ansurance.ops")


def get_info(db: Session, key: str) -> str | None:
    row = db.get(PlatformInfo, key)
    return row.value if row else None


def set_info(db: Session, key: str, value: str) -> None:
    row = db.get(PlatformInfo, key)
    if row is None:
        db.add(PlatformInfo(key=key, value=value))
    else:
        row.value = value
        row.updated_at = datetime.now(UTC)
    db.flush()


def _alert_manager(db: Session, title: str, detail: str) -> bool:
    """One open urgent Inbox item per problem; no duplicates while it is still open."""
    exists = db.scalar(
        select(InboxItem.id).where(
            InboxItem.role == "manager", InboxItem.status == "open", InboxItem.title == title
        )
    )
    if exists is not None:
        return False
    db.add(
        InboxItem(type="other", role="manager", urgency="urgent", title=title, detail=detail[:2000])
    )
    db.flush()
    return True


def backup_age(db: Session, now: datetime | None = None) -> timedelta | None:
    raw = get_info(db, "backup.last_success")
    if raw is None:
        return None
    return (now or datetime.now(UTC)) - datetime.fromisoformat(raw)


def run_daily(
    db: Session, settings: Settings, archive_store: BlobStore, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    report: dict[str, Any] = {"sealed": archive.seal_due(db, archive_store, now)}
    res = archive.verify_all(db, archive_store)
    set_info(db, "audit.last_verify", f"{now.isoformat()}|{'ok' if res.ok else 'FAILED'}")
    report["audit_ok"], report["audit_problems"] = res.ok, res.problems
    if not res.ok:
        chain.append(db, "system", "audit.verify_failed", "audit", {"problems": res.problems[:10]})
        _alert_manager(
            db,
            "Audit chain verification FAILED",
            "The nightly check found: " + "; ".join(res.problems[:5]),
        )
    age = backup_age(db, now)
    limit = timedelta(hours=settings.backup_max_age_hours)
    report["backup_ok"] = age is not None and age <= limit
    if not report["backup_ok"]:
        _alert_manager(
            db,
            "Backup is overdue",
            "No successful backup recorded."
            if age is None
            else f"The last successful backup is {age.total_seconds() / 3600:.0f} hours old "
            f"(limit {settings.backup_max_age_hours}).",
        )
    from app.flows import service as flows

    report["renewal_items"] = flows.renewal_tick(db, now.date())
    report["claim_reminders"] = flows.late_claims(db, now)
    set_info(db, "ops.daily_last", now.isoformat())
    return report


def daily_due(db: Session, settings: Settings, now: datetime | None = None) -> bool:
    raw = get_info(db, "ops.daily_last")
    if raw is None:
        return True
    return (now or datetime.now(UTC)) - datetime.fromisoformat(raw) >= timedelta(
        seconds=settings.daily_seconds
    )
