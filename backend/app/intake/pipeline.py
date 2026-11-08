"""The intake pipeline.

received -> scanned -> digitized -> classified -> extracted -> attributed -> one of
filed | review | unassigned (or quarantined, split, failed).

Every stage persists its result and the item's state before the next one runs, so a crash
or a retry resumes where it stopped and never repeats finished work or duplicates a
document. The original is stored once, content-addressed, and is never deleted.
"""

import email
import email.policy
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as Db

from app.adapters.antivirus import ScannerUnavailable
from app.audit import chain
from app.blob.base import BlobRef
from app.documents import render
from app.documents.service import (
    CHANNELS,
    DOC_TYPES,
    RejectedUpload,
    retention_until,
    safe_filename,
    sniff_media_type,
)
from app.intake.attribute import Attribution, attribute
from app.intake.classify import classify
from app.intake.context import IntakeContext
from app.intake.extract import IdHit, find_ids, policy_tokens
from app.jobs import queue
from app.models import (
    Blob,
    Client,
    Document,
    FamilyLink,
    InboxItem,
    IntakeFeedback,
    IntakeItem,
    User,
)
from app.sandbox.client import SandboxRejected, split_on_blank_pages

log = logging.getLogger("ansurance.intake")

ORDER = ["received", "scanned", "digitized", "classified", "extracted", "attributed"]
TERMINAL = {"filed", "review", "unassigned", "quarantined", "split", "failed"}
SYSTEM_EMAIL = "system@ansurance.local"
INTAKE_MEDIA = {"application/pdf", "image/png", "image/jpeg", "image/tiff"}


def system_user(db: Db) -> User:
    u = db.scalar(select(User).where(User.email == SYSTEM_EMAIL))
    if u is None:
        u = User(email=SYSTEM_EMAIL, display_name="System", auth_mode="sso", active=False)
        db.add(u)
        db.flush()
    return u


# ---- submit -----------------------------------------------------------------------------
def _store(db: Db, ctx: IntakeContext, data: bytes, media: str) -> Blob:
    ref = ctx.blobs.put(data)
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
    return blob


def submit(
    db: Db,
    ctx: IntakeContext,
    *,
    data: bytes,
    filename: str,
    channel: str,
    sender: str,
    actor: uuid.UUID | None,
) -> list[IntakeItem]:
    """Store the original and queue it. An e-mail becomes one item per attachment."""
    if channel not in CHANNELS:
        raise RejectedUpload("invalid_channel", 422)
    if not data:
        raise RejectedUpload("empty_file", 422)
    if len(data) > ctx.settings.max_upload_bytes:
        raise RejectedUpload("file_too_large", 413)
    media = sniff_media_type(data, filename)
    if media is None:
        raise RejectedUpload("unsupported_media_type", 415)
    if media == "message/rfc822":
        _store(db, ctx, data, media)  # keep the whole message as received
        return _submit_email(db, ctx, data, channel, actor)
    return [_new_item(db, ctx, data, media, filename, channel, sender, actor)]


def _new_item(
    db: Db,
    ctx: IntakeContext,
    data: bytes,
    media: str,
    filename: str,
    channel: str,
    sender: str,
    actor: uuid.UUID | None,
) -> IntakeItem:
    blob = _store(db, ctx, data, media)
    item = IntakeItem(
        blob_id=blob.id,
        channel=channel,
        sender=sender[:254],
        filename=safe_filename(filename),
        submitted_by=actor,
    )
    db.add(item)
    db.flush()
    chain.append(
        db,
        str(actor or "system"),
        "intake.receive",
        f"intake:{item.id}",
        {"channel": channel, "sha256": blob.sha256},
    )
    queue.enqueue(db, "intake.run", {"item_id": str(item.id)}, dedupe_key=f"intake:{item.id}")
    return item


def _submit_email(
    db: Db, ctx: IntakeContext, data: bytes, channel: str, actor: uuid.UUID | None
) -> list[IntakeItem]:
    msg = email.message_from_bytes(data, policy=email.policy.default)
    sender = email.utils.parseaddr(str(msg.get("From", "")))[1].lower()
    items = []
    for part in msg.iter_attachments():
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes) or not payload:
            continue
        media = sniff_media_type(payload, part.get_filename() or "")
        if media in INTAKE_MEDIA:
            items.append(
                _new_item(
                    db,
                    ctx,
                    payload,
                    media,
                    part.get_filename() or "attachment",
                    channel,
                    sender,
                    actor,
                )
            )
    return items


# ---- stages -----------------------------------------------------------------------------
def _blob_ref(blob: Blob) -> BlobRef:
    return BlobRef(blob.sha256, blob.size, blob.bucket, blob.key)


def _touch(item: IntakeItem, state: str) -> None:
    item.state, item.updated_at = state, datetime.now(UTC)


def _stage_scan(db: Db, ctx: IntakeContext, item: IntakeItem) -> None:
    blob = db.get(Blob, item.blob_id)
    assert blob is not None  # noqa: S101
    if blob.scan_status == "pending":
        try:
            res = ctx.antivirus.scan(ctx.blobs.get(_blob_ref(blob)))
        except ScannerUnavailable:
            raise  # fail closed: the job is retried, nothing proceeds unscanned
        blob.scan_status = "clean" if res.clean else "infected"
        blob.scan_detail = res.signature
    if blob.scan_status == "infected":
        _touch(item, "quarantined")
        chain.append(
            db,
            "system",
            "intake.quarantine",
            f"intake:{item.id}",
            {"signature": blob.scan_detail or ""},
        )
        return
    _touch(item, "scanned")


def _to_manual_review(db: Db, item: IntakeItem, reason: str) -> None:
    """The sandbox refused the file or broke a rule: a person decides, nothing is shown."""
    inbox = InboxItem(
        type="review",
        role="backoffice",
        urgency="important",
        title="Review: file could not be processed safely: " + (item.filename or "file"),
        detail=f"Refused by the sandbox ({reason}) via {item.channel}. Nothing is shown.",
    )
    db.add(inbox)
    db.flush()
    item.inbox_item_id, item.route, item.route_reason = inbox.id, "review", f"sandbox: {reason}"
    _touch(item, "review")
    chain.append(db, "system", "intake.sandbox_refused", f"intake:{item.id}", {"reason": reason})


def _stage_digitize(db: Db, ctx: IntakeContext, item: IntakeItem) -> None:
    blob = db.get(Blob, item.blob_id)
    assert blob is not None  # noqa: S101
    try:
        rendered = ctx.sandbox.render(
            ctx.blobs.get(_blob_ref(blob)), blob.media_type, ctx.settings.render_dpi
        )
    except SandboxRejected as e:
        _to_manual_review(db, item, e.reason)
        return
    pages = rendered.pages
    if item.page_from is not None and item.page_to is not None:
        pages = pages[item.page_from - 1 : item.page_to]
    elif len(pages) > 1:
        groups = split_on_blank_pages(rendered.blank)
        if len(groups) > 1:  # several documents in one fax: one item each
            for g in groups:
                child = IntakeItem(
                    blob_id=item.blob_id,
                    parent_id=item.id,
                    page_from=g.first,
                    page_to=g.last,
                    channel=item.channel,
                    sender=item.sender,
                    filename=item.filename,
                    submitted_by=item.submitted_by,
                )
                db.add(child)
                db.flush()
                queue.enqueue(
                    db, "intake.run", {"item_id": str(child.id)}, dedupe_key=f"intake:{child.id}"
                )
            _touch(item, "split")
            chain.append(
                db, "system", "intake.split", f"intake:{item.id}", {"documents": len(groups)}
            )
            return
        pages = pages[groups[0].first - 1 : groups[0].last]
    if not pages:
        item.error = "no pages could be rendered"
        _touch(item, "failed")
        return
    if ctx.ocr is None:
        raise RuntimeError("this process has no OCR engine configured")
    ocr = ctx.ocr.recognize(pages)
    ids = {h.id_number: h.conf for h in find_ids(ocr.words)}
    tokens = policy_tokens(ocr.words)
    second = False
    if _identifiers_are_shaky(ids, ctx.settings.second_pass_below):
        latin = getattr(ctx.ocr, "recognize_latin", None)
        extra = latin(pages) if callable(latin) else None
        if extra is not None:
            second = True
            # two independent readings that agree on a check-digit-valid number are stronger
            # than either alone, so keep the better confidence for every identifier seen
            for h in find_ids(extra.words):
                ids[h.id_number] = max(ids.get(h.id_number, 0.0), h.conf)
            for tok, conf in policy_tokens(extra.words).items():
                tokens[tok] = max(tokens.get(tok, 0.0), conf)
    item.ocr_text, item.ocr_conf = ocr.text, round(ocr.mean_conf, 4)
    item.entities = {
        "ids": [{"id_number": k, "conf": round(v, 4)} for k, v in ids.items()],
        "policy_tokens": {k: round(v, 4) for k, v in tokens.items()},
        "pages": len(pages),
        "second_pass": second,
    }
    _touch(item, "digitized")


def _identifiers_are_shaky(ids: dict[str, float], below: float) -> bool:
    """No ID at all, or none read confidently: worth a second opinion."""
    return not ids or max(ids.values()) < below


def _stage_classify(item: IntakeItem) -> None:
    c = classify(item.ocr_text)
    item.doc_type, item.type_conf, item.type_reason = c.doc_type, c.confidence, c.reason
    _touch(item, "classified")


def _stage_extract(item: IntakeItem) -> None:
    # entities were read while the words were at hand; this stage validates what is stored
    item.entities = {
        **item.entities,
        "ids": [e for e in item.entities.get("ids", []) if len(str(e["id_number"])) == 9],
    }
    _touch(item, "extracted")


def _stage_attribute(db: Db, ctx: IntakeContext, item: IntakeItem) -> Attribution:
    ids = [IdHit(e["id_number"], float(e["conf"])) for e in item.entities.get("ids", [])]
    tokens = {k: float(v) for k, v in item.entities.get("policy_tokens", {}).items()}
    att = attribute(
        db,
        ids=ids,
        policy_tokens=tokens,
        doc_type=item.doc_type,
        sender=item.sender,
        threshold=ctx.settings.intake_auto_threshold,
    )
    item.candidates = [c.as_json() for c in att.candidates]
    item.chosen_client_id, item.attribution_conf = att.chosen, round(att.confidence, 4)
    item.route, item.route_reason = att.route, att.reason[:200]
    item.entities = {**item.entities, "family": [str(m) for m in att.family_members]}
    _touch(item, "attributed")
    return att


def _route(db: Db, ctx: IntakeContext, item: IntakeItem) -> None:
    if item.route == "filed" and item.chosen_client_id:
        client = db.get(Client, item.chosen_client_id)
        assert client is not None  # noqa: S101
        file_document(db, ctx, item, client, actor=None, how="auto")
        return
    kind = "review" if item.route == "review" else "unassigned"
    suggestion = db.get(Client, item.chosen_client_id) if item.chosen_client_id else None
    inbox = InboxItem(
        type=kind,
        role="backoffice",
        client_id=suggestion.id if suggestion else None,
        urgency="important" if kind == "review" else "normal",
        title=("Review: " if kind == "review" else "Unassigned: ")
        + (item.filename or item.doc_type),
        detail=f"{item.doc_type} via {item.channel}. {item.route_reason}"
        + (f" Suggested: {suggestion.name}." if suggestion else ""),
    )
    db.add(inbox)
    db.flush()
    item.inbox_item_id = inbox.id
    _touch(item, kind)
    chain.append(
        db,
        "system",
        f"intake.{kind}",
        f"intake:{item.id}",
        {"reason": item.route_reason, "type": item.doc_type},
    )


def run(db: Db, ctx: IntakeContext, item_id: uuid.UUID) -> IntakeItem:
    """Advance one item as far as it goes. Safe to call again after a crash."""
    item = db.scalar(select(IntakeItem).where(IntakeItem.id == item_id).with_for_update())
    if item is None:
        raise LookupError(f"intake item {item_id} not found")
    while item.state not in TERMINAL:
        before = item.state
        if before == "received":
            _stage_scan(db, ctx, item)
        elif before == "scanned":
            _stage_digitize(db, ctx, item)
        elif before == "digitized":
            _stage_classify(item)
        elif before == "classified":
            _stage_extract(item)
        elif before == "extracted":
            _stage_attribute(db, ctx, item)
        elif before == "attributed":
            _route(db, ctx, item)
        db.commit()  # persist the stage before starting the next one
    return item


# ---- filing and corrections ---------------------------------------------------------------
def file_document(
    db: Db, ctx: IntakeContext, item: IntakeItem, client: Client, actor: uuid.UUID | None, how: str
) -> Document:
    """Create the document in the client's folder. Idempotent: one document per item."""
    if item.document_id is not None:
        doc = db.get(Document, item.document_id)
        assert doc is not None  # noqa: S101
        return doc
    who = actor or system_user(db).id
    now = datetime.now(UTC)
    doc_type = item.doc_type if item.doc_type in DOC_TYPES else "other"
    doc = Document(
        client_id=client.id,
        blob_id=item.blob_id,
        doc_type=doc_type,
        original_filename=item.filename or "scan",
        restricted=doc_type == "medical_report",
        source_channel=item.channel if item.channel in CHANNELS else "upload",
        uploaded_by=who,
        received_at=item.received_at,
        retention_until=retention_until(ctx.settings, doc_type, now).date(),
    )
    db.add(doc)
    db.flush()
    item.document_id, item.chosen_client_id = doc.id, client.id
    _touch(item, "filed")
    render.enqueue(db, item.blob_id)
    for member_id in item.entities.get("family", []):
        mid = uuid.UUID(str(member_id))
        exists = db.scalar(
            select(FamilyLink.id).where(
                FamilyLink.head_client_id == client.id, FamilyLink.member_client_id == mid
            )
        )
        if exists is None and mid != client.id:
            db.add(FamilyLink(head_client_id=client.id, member_client_id=mid, relation="member"))
    chain.append(
        db,
        str(actor or "system"),
        "intake.file",
        f"intake:{item.id}",
        {
            "client": str(client.id),
            "document": str(doc.id),
            "how": how,
            "confidence": float(item.attribution_conf),
        },
    )
    if client.agent_id and how == "auto":
        db.add(
            InboxItem(
                type="auto_filed",
                role="agent",
                assignee_id=client.agent_id,
                client_id=client.id,
                title=f"Filed: {doc_type} for {client.name}",
                detail=f"via {item.channel}, confidence {float(item.attribution_conf):.2f}",
            )
        )
    return doc


def _close_inbox(db: Db, item: IntakeItem, actor: uuid.UUID, note: str) -> None:
    if item.inbox_item_id:
        ib = db.get(InboxItem, item.inbox_item_id)
        if ib is not None and ib.status == "open":
            ib.status, ib.resolved_at, ib.resolved_by, ib.resolution = (
                "done",
                datetime.now(UTC),
                actor,
                note,
            )


def confirm(
    db: Db, ctx: IntakeContext, item: IntakeItem, client: Client, actor: uuid.UUID
) -> Document:
    """Back-office files a review or unassigned item to a client (one click)."""
    if item.state not in ("review", "unassigned"):
        raise ValueError(f"item is {item.state}")
    suggested = item.chosen_client_id
    kind = "confirm" if suggested == client.id else "reassign" if suggested else "assign"
    doc = file_document(db, ctx, item, client, actor, how="manual")
    db.add(
        IntakeFeedback(
            item_id=item.id,
            actor=actor,
            kind=kind,
            from_client_id=None,
            to_client_id=client.id,
            suggested_client_id=suggested,
        )
    )
    _close_inbox(db, item, actor, f"filed to {client.name}")
    chain.append(
        db,
        str(actor),
        "intake.confirm",
        f"intake:{item.id}",
        {"kind": kind, "client": str(client.id)},
    )
    return doc


def wrong_client(
    db: Db, item: IntakeItem, new_client: Client, actor: uuid.UUID, reason: str
) -> None:
    """The document was filed to the wrong person: move it, log it, count it as a misfile."""
    if item.state != "filed" or item.document_id is None:
        raise ValueError("only filed items can be moved")
    doc = db.get(Document, item.document_id)
    assert doc is not None  # noqa: S101
    old = doc.client_id
    if old == new_client.id:
        raise ValueError("already filed to that client")
    doc.client_id, item.chosen_client_id = new_client.id, new_client.id
    db.add(
        IntakeFeedback(
            item_id=item.id,
            actor=actor,
            kind="wrong_client",
            from_client_id=old,
            to_client_id=new_client.id,
            note=reason,
        )
    )
    chain.append(
        db,
        str(actor),
        "intake.wrong_client",
        f"intake:{item.id}",
        {"from": str(old), "to": str(new_client.id), "reason": reason},
    )
    if new_client.agent_id:
        db.add(
            InboxItem(
                type="auto_filed",
                role="agent",
                assignee_id=new_client.agent_id,
                client_id=new_client.id,
                urgency="important",
                title=f"Document moved to {new_client.name}",
                detail=f"Corrected misfile: {reason}",
            )
        )


def result_summary(item: IntakeItem) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "state": item.state,
        "channel": item.channel,
        "sender": item.sender,
        "filename": item.filename,
        "doc_type": item.doc_type,
        "type_conf": float(item.type_conf),
        "type_reason": item.type_reason,
        "ocr_conf": float(item.ocr_conf),
        "route": item.route,
        "route_reason": item.route_reason,
        "client_id": str(item.chosen_client_id) if item.chosen_client_id else None,
        "attribution_conf": float(item.attribution_conf),
        "candidates": item.candidates,
        "document_id": str(item.document_id) if item.document_id else None,
        "parent_id": str(item.parent_id) if item.parent_id else None,
        "pages": [item.page_from, item.page_to],
        "received_at": item.received_at.isoformat(),
    }
