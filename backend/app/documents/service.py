"""Document intake: sniff, limit, scan, store, record."""

import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session as Db

from app.adapters.antivirus import Antivirus, ScannerUnavailable
from app.audit import chain
from app.blob.base import BlobStore
from app.config import Settings
from app.models import Blob, Client, Document

DOC_TYPES = {
    "id_card",
    "id_addendum",
    "driving_licence",
    "policy",
    "application",
    "claim_form",
    "medical_report",
    "invoice",
    "bank_form",
    "power_of_attorney",
    "insurer_letter",
    "commission_statement",
    "complaint",
    "other",
}
ALWAYS_RESTRICTED = {"medical_report"}
CHANNELS = {"upload", "email", "fax", "whatsapp", "scan"}


class RejectedUpload(Exception):
    def __init__(self, code: str, status: int) -> None:
        super().__init__(code)
        self.code, self.status = code, status


def sniff_media_type(data: bytes, filename: str) -> str | None:
    """Trust the bytes, not the declared type or extension."""
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    head = data[:2048].decode("latin-1", errors="replace")
    if filename.lower().endswith(".eml") and re.match(
        r"(?im)^(received|from|to|subject|date|message-id|mime-version):", head
    ):
        return "message/rfc822"
    return None


def safe_filename(raw: str) -> str:
    """Drop any path, control characters and leading dots; keep it short."""
    name = re.split(r"[\\/]", raw)[-1]
    name = re.sub(r"[\x00-\x1f\x7f\"<>|:*?]", "", name).lstrip(".").strip()
    return (name or "file")[:200]


def retention_until(settings: Settings, doc_type: str, received: datetime) -> datetime:
    years = settings.retention_years_by_type.get(doc_type, settings.retention_years_default)
    return received + timedelta(days=round(365.25 * years))


def ingest(
    db: Db,
    settings: Settings,
    store: BlobStore,
    av: Antivirus,
    *,
    client: Client,
    actor: str,
    data: bytes,
    filename: str,
    doc_type: str,
    restricted: bool,
    channel: str,
) -> Document:
    if doc_type not in DOC_TYPES:
        raise RejectedUpload("invalid_doc_type", 422)
    if channel not in CHANNELS:
        raise RejectedUpload("invalid_channel", 422)
    if not data:
        raise RejectedUpload("empty_file", 422)
    if len(data) > settings.max_upload_bytes:
        raise RejectedUpload("file_too_large", 413)
    media = sniff_media_type(data, filename)
    if media is None:
        raise RejectedUpload("unsupported_media_type", 415)

    ref = store.put(data)  # originals are kept even when infected (flagged, never served)
    blob = db.scalar(select(Blob).where(Blob.sha256 == ref.sha256))
    if blob is None:
        blob = Blob(
            sha256=ref.sha256,
            size=ref.size,
            media_type=media,
            bucket=ref.bucket,
            key=ref.key,
            scan_status="pending",
        )
        db.add(blob)
        db.flush()
    if blob.scan_status == "pending":
        try:
            result = av.scan(data)
            blob.scan_status = "clean" if result.clean else "infected"
            blob.scan_detail = result.signature
        except ScannerUnavailable:
            blob.scan_status = "pending"  # fail closed: not served until scanned
    now = datetime.now(UTC)
    doc = Document(
        client_id=client.id,
        blob_id=blob.id,
        doc_type=doc_type,
        original_filename=safe_filename(filename),
        restricted=restricted or doc_type in ALWAYS_RESTRICTED,
        source_channel=channel,
        uploaded_by=_uuid(actor),
        received_at=now,
        retention_until=retention_until(settings, doc_type, now).date(),
    )
    db.add(doc)
    db.flush()
    chain.append(
        db,
        actor,
        "document.upload",
        f"document:{doc.id}",
        {"client": str(client.id), "sha256": blob.sha256, "scan": blob.scan_status},
    )
    if blob.scan_status == "infected":
        chain.append(
            db,
            actor,
            "document.quarantine",
            f"document:{doc.id}",
            {"signature": blob.scan_detail or ""},
        )
    return doc


def _uuid(value: str):  # type: ignore[no-untyped-def]
    import uuid

    return uuid.UUID(value)
