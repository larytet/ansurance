"""Every call to an outside system is logged (REQUIREMENTS section 9). The log row is written
in its own transaction so a failed call is recorded even when the caller rolls back."""

import time
from collections.abc import Callable

from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models import AdapterCall


class AdapterNotApproved(Exception):
    pass


def require_approved(settings: Settings, adapter: str) -> None:
    """An adapter outside the approved list never runs (data residency, REQUIREMENTS 7)."""
    if adapter not in settings.approved_adapters:
        raise AdapterNotApproved(f"adapter {adapter} is not on the approved list")


def logged_call[T](
    maker: "sessionmaker[Session]",
    adapter: str,
    operation: str,
    target: str,
    fn: Callable[[], T],
) -> T:
    start = time.perf_counter()
    error: str | None = None
    try:
        return fn()
    except Exception as e:
        error = f"{type(e).__name__}: {e}"[:500]
        raise
    finally:
        elapsed = int((time.perf_counter() - start) * 1000)
        with maker() as s:
            s.add(
                AdapterCall(
                    adapter=adapter,
                    operation=operation,
                    target=target[:128],
                    ok=error is None,
                    duration_ms=elapsed,
                    error=error,
                )
            )
            s.commit()
