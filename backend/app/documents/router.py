import uuid
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session as Db

from app.audit import chain
from app.blob.base import BlobRef, BlobStore, IntegrityError
from app.clients.access import can_see_restricted, get_visible
from app.config import Settings
from app.deps import Principal, get_db, get_settings_dep, require
from app.documents import render, service
from app.models import Blob, Document

router = APIRouter(prefix="/api")


class HoldIn(BaseModel):
    legal_hold: bool


def _doc_dict(d: Document, b: Blob) -> dict[str, Any]:
    return {
        "id": str(d.id),
        "client_id": str(d.client_id),
        "doc_type": d.doc_type,
        "filename": d.original_filename,
        "media_type": b.media_type,
        "size": b.size,
        "sha256": b.sha256,
        "restricted": d.restricted,
        "scan_status": b.scan_status,
        "channel": d.source_channel,
        "received_at": d.received_at.isoformat(),
        "retention_until": d.retention_until.isoformat(),
        "legal_hold": d.legal_hold,
    }


def _load(db: Db, p: Principal, doc_id: uuid.UUID) -> tuple[Document, Blob]:
    row = db.execute(
        select(Document, Blob).join(Blob, Blob.id == Document.blob_id).where(Document.id == doc_id)
    ).first()
    if row is None:
        raise HTTPException(404, "not_found")
    doc, blob = row
    client = get_visible(db, p, doc.client_id)  # 404 when the client is not theirs
    if doc.restricted and not can_see_restricted(p, client):
        raise HTTPException(404, "not_found")
    return doc, blob


@router.post("/clients/{client_id}/documents", status_code=201)
def upload(
    client_id: uuid.UUID,
    request: Request,
    file: UploadFile = File(...),
    doc_type: str = Form("other"),
    restricted: bool = Form(False),
    channel: str = Form("upload"),
    p: Principal = Depends(require("document.write")),
    db: Db = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings_dep),
) -> dict[str, Any]:
    client = get_visible(db, p, client_id)
    data = file.file.read(settings.max_upload_bytes + 1)
    try:
        doc = service.ingest(
            db,
            settings,
            request.app.state.blobs,
            request.app.state.antivirus,
            client=client,
            actor=str(p.user.id),
            data=data,
            filename=file.filename or "file",
            doc_type=doc_type,
            restricted=restricted,
            channel=channel,
        )
    except service.RejectedUpload as e:
        raise HTTPException(e.status, e.code) from e
    blob = db.get(Blob, doc.blob_id)
    if blob is None:
        raise HTTPException(500, "blob_missing")
    return _doc_dict(doc, blob)


@router.get("/clients/{client_id}/documents")
def list_documents(
    request: Request,
    client_id: uuid.UUID,
    p: Principal = Depends(require("document.read")),
    db: Db = Depends(get_db, scope="function"),
) -> list[dict[str, Any]]:
    client = get_visible(db, p, client_id)
    rows = db.execute(
        select(Document, Blob)
        .join(Blob, Blob.id == Document.blob_id)
        .where(Document.client_id == client.id)
        .order_by(Document.received_at.desc())
    ).all()
    allowed = can_see_restricted(p, client)
    out = [_doc_dict(d, b) for d, b in rows if allowed or not d.restricted]
    request.app.state.read_audit.record(
        str(p.user.id), "document.list", f"client:{client.id}", {"n": len(out)}
    )
    return out


@router.get("/documents/{doc_id}")
def get_document(
    doc_id: uuid.UUID,
    p: Principal = Depends(require("document.read")),
    db: Db = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    doc, blob = _load(db, p, doc_id)
    return _doc_dict(doc, blob)


@router.get("/documents/{doc_id}/pages")
def pages(
    doc_id: uuid.UUID,
    request: Request,
    p: Principal = Depends(require("document.read")),
    db: Db = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    """The page images made in the sandbox: what a user sees instead of the file itself."""
    doc, blob = _load(db, p, doc_id)
    if blob.scan_status != "clean":
        raise HTTPException(409, f"scan_{blob.scan_status}")
    row = render.latest(db, blob.id)
    if row is None:
        render.enqueue(db, blob.id)
        return {"status": "pending", "pages": 0, "reason": None}
    return {"status": row.status, "pages": row.pages, "reason": row.reason}


@router.get("/documents/{doc_id}/pages/{page_no}")
def page_image(
    doc_id: uuid.UUID,
    page_no: int,
    request: Request,
    p: Principal = Depends(require("document.read")),
    db: Db = Depends(get_db, scope="function"),
) -> Response:
    doc, blob = _load(db, p, doc_id)
    row = render.latest(db, blob.id)
    img = render.page_blob(db, row, page_no) if row is not None and row.status == "ready" else None
    if img is None:
        raise HTTPException(404, "not_found")
    store: BlobStore = request.app.state.blobs
    try:
        data = store.get(BlobRef(img.sha256, img.size, img.bucket, img.key))
    except IntegrityError as e:
        raise HTTPException(500, "integrity_check_failed") from e
    request.app.state.read_audit.record(
        str(p.user.id), "document.view", f"document:{doc.id}", {"page": page_no}
    )
    return Response(
        data,
        media_type="image/png",
        headers={
            "x-content-type-options": "nosniff",
            "content-security-policy": "default-src 'none'; img-src 'self'; sandbox",
            "cache-control": "private, no-store",
        },
    )


@router.get("/documents/{doc_id}/content")
def download(
    doc_id: uuid.UUID,
    request: Request,
    acknowledge: bool = False,
    p: Principal = Depends(require("document.download_original")),
    db: Db = Depends(get_db, scope="function"),
) -> Response:
    """The original file exactly as it arrived. Never opened in the browser, only downloaded,
    and only after the person has been told it is untrusted (acknowledge=true)."""
    doc, blob = _load(db, p, doc_id)
    if blob.scan_status != "clean":
        chain.append(
            db,
            str(p.user.id),
            "document.download_refused",
            f"document:{doc.id}",
            {"scan": blob.scan_status},
        )
        db.commit()
        raise HTTPException(409, f"scan_{blob.scan_status}")
    if not acknowledge:
        raise HTTPException(409, "warning_required")
    store: BlobStore = request.app.state.blobs
    try:
        data = store.get(BlobRef(blob.sha256, blob.size, blob.bucket, blob.key))
    except IntegrityError as e:
        chain.append(
            db, str(p.user.id), "integrity.fail", f"document:{doc.id}", {"sha256": blob.sha256}
        )
        db.commit()
        raise HTTPException(500, "integrity_check_failed") from e
    chain.append(
        db,
        str(p.user.id),
        "document.download",
        f"document:{doc.id}",
        {"sha256": blob.sha256, "warning_acknowledged": True},
    )
    return Response(
        data,
        media_type="application/octet-stream",  # never a type the browser would open itself
        headers={
            "content-disposition": f"attachment; filename*=UTF-8''{quote(doc.original_filename)}",
            "x-content-type-options": "nosniff",
            "content-security-policy": "sandbox; default-src 'none'",
            "cache-control": "no-store",
        },
    )


@router.patch("/documents/{doc_id}/hold")
def set_hold(
    doc_id: uuid.UUID,
    body: HoldIn,
    p: Principal = Depends(require("document.hold")),
    db: Db = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    doc, blob = _load(db, p, doc_id)
    doc.legal_hold = body.legal_hold
    chain.append(
        db, str(p.user.id), "document.hold", f"document:{doc.id}", {"legal_hold": body.legal_hold}
    )
    return _doc_dict(doc, blob)
