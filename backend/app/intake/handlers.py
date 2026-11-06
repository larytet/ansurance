"""Job handlers and the fax drop-folder adapter."""

import logging
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session as Db

from app.documents.service import RejectedUpload
from app.intake import pipeline
from app.intake.context import IntakeContext
from app.jobs.queue import Handler

log = logging.getLogger("ansurance.intake")
FAX_SUFFIXES = {".pdf", ".tif", ".tiff", ".png", ".jpg", ".jpeg"}


def make_handlers(ctx: IntakeContext) -> dict[str, Handler]:
    def run(db: Db, payload: dict[str, Any]) -> None:
        pipeline.run(db, ctx, uuid.UUID(payload["item_id"]))

    return {"intake.run": run}


def poll_fax_folder(db: Db, ctx: IntakeContext) -> int:
    """Ingest every file dropped by the cloud-fax provider. Files move to `processed/`
    only after the original is stored, so a crash never loses a fax."""
    root = Path(ctx.settings.fax_dir)
    done, failed = root / "processed", root / "rejected"
    n = 0
    for path in sorted(root.glob("*")):
        if not path.is_file() or path.suffix.lower() not in FAX_SUFFIXES:
            continue
        try:
            pipeline.submit(
                db,
                ctx,
                data=path.read_bytes(),
                filename=path.name,
                channel="fax",
                sender="",
                actor=None,
            )
            db.commit()
            target = done
        except RejectedUpload as e:
            log.warning("fax %s rejected: %s", path.name, e.code)
            db.rollback()
            target = failed
        target.mkdir(exist_ok=True)
        path.rename(target / path.name)
        n += 1
    return n
