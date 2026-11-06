"""A read-only view for the client (REQUIREMENTS section 2: external, own data only, never
internal notes or the audit log). Access is by an unguessable link token; only its hash is kept."""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select, text
from sqlalchemy.orm import Session as Db

from app.audit import chain
from app.clients.access import get_visible
from app.config import Settings
from app.deps import Principal, get_db, get_settings_dep, require
from app.models import Blob, Claim, Client, Document, Insurer, Policy, PortalLink

router = APIRouter(prefix="/api")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@router.post("/clients/{client_id}/portal-link", status_code=201)
def issue(
    client_id: uuid.UUID,
    p: Principal = Depends(require("flow.write")),
    db: Db = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> dict[str, Any]:
    c = get_visible(db, p, client_id)
    token = secrets.token_urlsafe(32)
    link = PortalLink(
        client_id=c.id,
        token_hash=_hash(token),
        created_by=p.user.id,
        expires_at=datetime.now(UTC) + timedelta(days=settings.portal_link_days),
    )
    db.add(link)
    db.flush()
    chain.append(db, str(p.user.id), "portal.issue", f"portal:{link.id}", {"client": str(c.id)})
    return {
        "id": str(link.id),
        "token": token,
        "expires_at": link.expires_at.isoformat(),
    }  # shown once


@router.delete("/portal-links/{link_id}")
def revoke(
    link_id: uuid.UUID, p: Principal = Depends(require("flow.write")), db: Db = Depends(get_db)
) -> dict[str, str]:
    link = db.get(PortalLink, link_id)
    if link is None:
        raise HTTPException(404, "not_found")
    get_visible(db, p, link.client_id)
    link.revoked_at = datetime.now(UTC)
    chain.append(db, str(p.user.id), "portal.revoke", f"portal:{link.id}", {})
    return {"status": "revoked"}


@router.get("/portal/summary")
def summary(
    request: Request, x_portal_token: str = Header(""), db: Db = Depends(get_db)
) -> dict[str, Any]:
    link = (
        db.scalar(select(PortalLink).where(PortalLink.token_hash == _hash(x_portal_token)))
        if x_portal_token
        else None
    )
    now = datetime.now(UTC)
    if link is None or link.revoked_at is not None or link.expires_at < now:
        raise HTTPException(404, "not_found")  # unknown, expired and revoked look the same
    c = db.get(Client, link.client_id)
    if c is None:
        raise HTTPException(404, "not_found")
    link.last_used_at = now
    request.app.state.read_audit.record(f"portal:{link.id}", "portal.view", f"client:{c.id}")
    pols = db.execute(
        select(Policy, Insurer.name)
        .join(Insurer, Insurer.id == Policy.insurer_id)
        .where(Policy.client_id == c.id)
        .order_by(Policy.product)
    )
    docs = db.execute(
        select(Document, Blob)
        .join(Blob, Blob.id == Document.blob_id)
        .where(
            Document.client_id == c.id, Document.restricted.is_(False), Blob.scan_status == "clean"
        )
        .order_by(Document.received_at.desc())
    )
    claims = db.scalars(
        select(Claim).where(Claim.client_id == c.id).order_by(Claim.created_at.desc())
    )
    consent: dict[str, str] = dict(
        db.execute(
            text("SELECT channel, state FROM consent_current WHERE client_id = :c"), {"c": c.id}
        )
        .tuples()
        .all()
    )
    return {
        "name": c.name,
        "language": c.preferred_language,
        "policies": [
            {
                "insurer": ins,
                "product": p.product,
                "policy_number": p.policy_number,
                "status": p.status,
                "renewal_date": p.renewal_date.isoformat() if p.renewal_date else None,
            }
            for p, ins in pols
        ],
        "documents": [
            {
                "type": d.doc_type,
                "filename": d.original_filename,
                "received_at": d.received_at.isoformat(),
            }
            for d, _ in docs
        ],
        "claims": [
            {
                "status": cl.status,
                "event_date": cl.event_date.isoformat(),
                "insurer_ref": cl.insurer_ref,
            }
            for cl in claims
        ],
        "consent": consent,
    }
