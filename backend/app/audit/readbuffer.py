"""Asynchronous, batched audit of reads (owner decision 2026-11-06).

A read hands its entry to an in-memory buffer and returns at once. A background thread
appends the buffered entries to the audit chain in batches, in one transaction, through the
same serialized writer as every other entry, so the chain stays valid. If the process
crashes, the entries not yet flushed are lost: that is accepted for reads. Writes, security
events, exports and downloads do not use this buffer.
"""

import logging
import threading
from collections import deque
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.audit import chain

log = logging.getLogger("ansurance.audit")


class ReadAuditBuffer:
    def __init__(
        self,
        maker: "sessionmaker[Session]",
        flush_seconds: float = 5.0,
        batch_size: int = 200,
        max_queue: int = 20_000,
    ) -> None:
        self._maker, self._interval, self._batch = maker, flush_seconds, batch_size
        self._q: deque[chain.Pending] = deque(maxlen=max_queue)
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.dropped = 0  # entries discarded because the queue was full
        self.flushed = 0

    # -- called by requests ---------------------------------------------------------------
    def record(
        self, actor: str, action: str, obj: str, detail: dict[str, Any] | None = None
    ) -> None:
        entry = chain.Pending(datetime.now(UTC), actor, action, obj, detail or {})
        with self._lock:
            if len(self._q) == self._q.maxlen:
                self.dropped += 1  # the oldest is discarded: bounded memory beats completeness
            self._q.append(entry)
            full = len(self._q) >= self._batch
        self._ensure_thread()
        if full:
            self._wake.set()

    # -- flushing -----------------------------------------------------------------------------
    def flush(self) -> int:
        """Write everything buffered, in batches. Returns the number of entries written."""
        written = 0
        while True:
            with self._lock:
                batch = [self._q.popleft() for _ in range(min(self._batch, len(self._q)))]
            if not batch:
                return written
            try:
                with self._maker() as db:
                    chain.append_many(db, batch)
                    db.commit()
            except Exception:  # noqa: BLE001  (the database is briefly unavailable)
                log.warning("read-audit flush failed; will retry", exc_info=True)
                with self._lock:
                    self._q.extendleft(reversed(batch))  # keep them for the next attempt
                return written
            written += len(batch)
            self.flushed += len(batch)

    def pending(self) -> int:
        with self._lock:
            return len(self._q)

    def discard(self) -> int:
        """Simulate a crash: forget what has not been written."""
        with self._lock:
            n = len(self._q)
            self._q.clear()
        return n

    # -- background thread --
    def _ensure_thread(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            with self._lock:
                if self._thread is None or not self._thread.is_alive():
                    self._stop.clear()
                    self._thread = threading.Thread(
                        target=self._run, name="read-audit", daemon=True
                    )
                    self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self._interval)
            self._wake.clear()
            self.flush()

    def stop(self) -> None:
        """Orderly shutdown: stop the thread and write what is left."""
        self._stop.set()
        self._wake.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=10)
        self.flush()
