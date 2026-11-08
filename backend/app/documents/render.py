"""Page images of a stored file, made in the sandbox (DESIGN 8.1).

The images are the only form in which a user sees a document. They are stored as separate
objects tied to the original by its hash and to the tool version that made them.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session as Db

from app.audit import chain
from app.blob.base import BlobRef, BlobStore
from app.jobs import queue
from app.models import Blob, BlobPage, BlobRender
from app.sandbox.client import Sandbox, SandboxRejected


def tool_name(tool: str, dpi: int) -> str:
    return f"{tool or 'sandbox'}@{dpi}"


def enqueue(db: Db, blob_id: uuid.UUID) -> None:
    queue.enqueue(db, "document.render", {"blob_id": str(blob_id)}, dedupe_key=f"render:{blob_id}")


def latest(db: Db, blob_id: uuid.UUID) -> BlobRender | None:
    return db.scalar(
        select(BlobRender)
        .where(BlobRender.blob_id == blob_id)
        .order_by(BlobRender.created_at.desc())
        .limit(1)
    )


def render_blob(db: Db, store: BlobStore, sandbox: Sandbox, blob: Blob, dpi: int) -> BlobRender:
    """Run the sandbox over the file and keep what it returns. Idempotent per (file, tool)."""
    if blob.scan_status != "clean":
        raise ValueError("only a file that passed the virus scan is rendered")
    data = store.get(BlobRef(blob.sha256, blob.size, blob.bucket, blob.key))
    try:
        rendered = sandbox.render(data, blob.media_type, dpi)
    except SandboxRejected as e:
        row = BlobRender(
            blob_id=blob.id, tool=tool_name("", dpi), status="refused", pages=0, reason=e.reason
        )
        existing = db.scalar(
            select(BlobRender).where(BlobRender.blob_id == blob.id, BlobRender.tool == row.tool)
        )
        if existing is not None:
            return existing
        db.add(row)
        db.flush()
        chain.append(
            db, "system", "document.render_refused", f"blob:{blob.id}", {"reason": e.reason}
        )
        return row
    tool = tool_name(rendered.tool, dpi)
    existing = db.scalar(
        select(BlobRender).where(BlobRender.blob_id == blob.id, BlobRender.tool == tool)
    )
    if existing is not None:
        return existing
    row = BlobRender(blob_id=blob.id, tool=tool, status="ready", pages=len(rendered.pages))
    db.add(row)
    db.flush()
    for n, png in enumerate(rendered.pages, start=1):
        ref = store.put(png)
        img = db.scalar(select(Blob).where(Blob.sha256 == ref.sha256))
        if img is None:  # made by the sandbox from a file that was scanned: not scanned again
            img = Blob(
                sha256=ref.sha256,
                size=ref.size,
                media_type="image/png",
                bucket=ref.bucket,
                key=ref.key,
                scan_status="clean",
                scan_detail="derived",
            )
            db.add(img)
            db.flush()
        db.add(BlobPage(render_id=row.id, page_no=n, image_blob_id=img.id))
    db.flush()
    return row


def page_blob(db: Db, render: BlobRender, page_no: int) -> Blob | None:
    return db.scalar(
        select(Blob)
        .join(BlobPage, BlobPage.image_blob_id == Blob.id)
        .where(BlobPage.render_id == render.id, BlobPage.page_no == page_no)
    )
