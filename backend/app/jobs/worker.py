"""The worker process: runs queued jobs and polls the fax drop folder.

python -m app.jobs.worker
"""

import logging
import os
import signal
import time

from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.db import make_engine
from app.intake.context import make_context
from app.intake.handlers import make_handlers, poll_fax_folder
from app.jobs import queue
from app.logging import configure_logging
from app.main import make_archive_store
from app.ops import daily_due, run_daily

log = logging.getLogger("ansurance.worker")


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    maker = sessionmaker(make_engine(settings), expire_on_commit=False)
    ctx = make_context(settings)
    archive_store = make_archive_store(settings)
    handlers = make_handlers(ctx)
    worker_id = f"{os.uname().nodename}:{os.getpid()}"
    stop = False

    def _stop(*_: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    last_fax = 0.0
    log.info("worker %s started", worker_id)
    while not stop:
        with maker() as db:
            if settings.fax_dir and time.time() - last_fax >= settings.fax_poll_seconds:
                last_fax = time.time()
                poll_fax_folder(db, ctx)
            if daily_due(db, settings):
                try:
                    log.info("daily operations: %s", run_daily(db, settings, archive_store))
                    db.commit()
                except Exception:  # noqa: BLE001  (never stops the job loop)
                    db.rollback()
                    log.exception("daily operations failed")
            queue.reclaim_stale(db)
            db.commit()
            busy = queue.run_one(db, handlers, worker_id)
        if not busy:
            time.sleep(1.0)
    log.info("worker stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
