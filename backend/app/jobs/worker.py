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

log = logging.getLogger("ansurance.worker")


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    maker = sessionmaker(make_engine(settings), expire_on_commit=False)
    ctx = make_context(settings)
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
            queue.reclaim_stale(db)
            db.commit()
            busy = queue.run_one(db, handlers, worker_id)
        if not busy:
            time.sleep(1.0)
    log.info("worker stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
