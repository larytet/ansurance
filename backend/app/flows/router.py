import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as Db

from app.adapters.esign import ESign as ESignAdapter
from app.adapters.esign import verify_payload
from app.audit import chain
from app.blob.base import BlobRef
from app.clients.access import get_visible
from app.config import Settings
from app.deps import Principal, get_db, get_settings_dep, require
from app.documents import service as docs
from app.flows import service
from app.integrations.calls import logged_call, require_approved
from app.models import (
    AdviceRecord,
    Application,
    Blob,
    Claim,
    ClaimItem,
    Client,
    Document,
    Insurer,
    KycForm,
    Policy,
)
from app.models import ESignRequest as ESign

router = APIRouter(prefix="/api")
hook = APIRouter(prefix="/api/webhooks")


def _fail(e: service.FlowError) -> HTTPException:
    return HTTPException(e.status, {"error": e.code, "detail": e.detail} if e.detail else e.code)


def _esign(request: Request) -> ESignAdapter:
    a = getattr(request.app.state, "esign", None)
    if a is None:
        raise HTTPException(503, "esign_not_configured")
    return a  # type: ignore[no-any-return]


def _policy(db: Db, p: Principal, policy_id: uuid.UUID) -> tuple[Policy, Client]:
    pol = db.get(Policy, policy_id)
    if pol is None:
        raise HTTPException(404, "not_found")
    return pol, get_visible(db, p, pol.client_id)


def _document(db: Db, client: Client, document_id: uuid.UUID) -> Document:
    d = db.get(Document, document_id)
    if d is None or d.client_id != client.id:
        raise HTTPException(422, "document_not_in_client_file")
    return d


# ---- F1: KYC, signatures, activation ------------------------------------------------------
class KycIn(BaseModel):
    data: dict[str, Any]


@router.put("/clients/{client_id}/kyc")
def put_kyc(
    client_id: uuid.UUID,
    body: KycIn,
    p: Principal = Depends(require("flow.write")),
    db: Db = Depends(get_db),
) -> dict[str, Any]:
    c = get_visible(db, p, client_id)
    try:
        f = service.save_kyc(db, p.user.id, c, body.data)
    except service.FlowError as e:
        raise _fail(e) from e
    return {"risk": f.risk, "flags": f.flags}


@router.post("/clients/{client_id}/kyc/approve")
def approve_kyc(
    client_id: uuid.UUID, p: Principal = Depends(require("flow.approve")), db: Db = Depends(get_db)
) -> dict[str, Any]:
    c = get_visible(db, p, client_id)
    try:
        service.approve_kyc(db, p.user.id, c)
    except service.FlowError as e:
        raise _fail(e) from e
    return {"status": "approved"}


class SignIn(BaseModel):
    kind: str
    title: str = Field(min_length=2, max_length=200)
    document_id: uuid.UUID
    signer_contact: str = Field(min_length=3, max_length=254)  # the client, or the spouse
    signer_name: str = ""


@router.post("/clients/{client_id}/esign", status_code=201)
def send_for_signature(
    request: Request,
    client_id: uuid.UUID,
    body: SignIn,
    p: Principal = Depends(require("flow.write")),
    db: Db = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> dict[str, Any]:
    if body.kind not in service.SIGN_KINDS:
        raise HTTPException(422, "invalid_kind")
    c = get_visible(db, p, client_id)
    d = _document(db, c, body.document_id)
    blob = db.get(Blob, d.blob_id)
    if blob is None:
        raise HTTPException(500, "blob_missing")
    if blob.scan_status != "clean":
        raise HTTPException(409, "document_not_scanned_clean")
    require_approved(settings, "esign")
    adapter = _esign(request)
    pdf = request.app.state.blobs.get(BlobRef(blob.sha256, blob.size, blob.bucket, blob.key))
    name = body.signer_name or c.name
    env = logged_call(
        request.app.state.sessionmaker,
        "esign",
        "send",
        f"client:{c.id}",
        lambda: adapter.send(
            pdf=pdf, signer_name=name, signer_contact=body.signer_contact, title=body.title
        ),
    )
    r = ESign(
        client_id=c.id,
        kind=body.kind,
        title=body.title,
        document_id=d.id,
        provider_ref=env.provider_ref,
        signer_name=name,
        created_by=p.user.id,
    )
    db.add(r)
    db.flush()
    chain.append(
        db, str(p.user.id), "esign.send", f"esign:{r.id}", {"client": str(c.id), "kind": body.kind}
    )
    return {"id": str(r.id), "status": r.status}


@router.get("/clients/{client_id}/esign")
def list_esign(
    client_id: uuid.UUID, p: Principal = Depends(require("client.read")), db: Db = Depends(get_db)
) -> list[dict[str, Any]]:
    c = get_visible(db, p, client_id)
    rows = db.scalars(select(ESign).where(ESign.client_id == c.id).order_by(ESign.sent_at.desc()))
    return [
        {
            "id": str(r.id),
            "kind": r.kind,
            "title": r.title,
            "status": r.status,
            "signed_at": r.signed_at.isoformat() if r.signed_at else None,
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
            "signed_document_id": str(r.signed_document_id) if r.signed_document_id else None,
        }
        for r in rows
    ]


@hook.post("/esign")
async def esign_webhook(
    request: Request, settings: Settings = Depends(get_settings_dep)
) -> dict[str, str]:
    body = await request.body()
    secret = (
        settings.esign_webhook_secret.get_secret_value() if settings.esign_webhook_secret else ""
    )
    if not verify_payload(secret, body, request.headers.get("x-signature", "")):
        raise HTTPException(403, "bad_signature")
    try:
        msg = json.loads(body)
        ref, status = str(msg["provider_ref"]), str(msg["status"])
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(400, "bad_payload") from e
    if status not in ("signed", "declined", "expired"):
        raise HTTPException(400, "bad_status")
    adapter = _esign(request)
    with request.app.state.sessionmaker() as db:
        r = db.scalar(select(ESign).where(ESign.provider_ref == ref).with_for_update())
        if r is None:
            raise HTTPException(404, "unknown_envelope")
        if r.status != "sent":
            return {"status": "already_" + r.status}  # providers retry: handle each result once
        if status == "signed":
            data = logged_call(
                request.app.state.sessionmaker,
                "esign",
                "fetch_signed",
                ref,
                lambda: adapter.fetch_signed(ref),
            )
            c = db.get(Client, r.client_id)
            if c is None:
                raise HTTPException(500, "client_missing")
            doc = docs.ingest(
                db,
                settings,
                request.app.state.blobs,
                request.app.state.antivirus,
                client=c,
                actor=str(r.created_by),
                data=data,
                filename=f"{r.kind}-signed.pdf",
                doc_type=service.DOC_TYPE_FOR_KIND.get(r.kind, "power_of_attorney"),
                restricted=False,
                channel="upload",
            )
            r.signed_document_id, r.status, r.signed_at = doc.id, "signed", datetime.now(UTC)
            if r.kind in service.LOOKUP_KINDS:
                r.expires_at = service.add_months(
                    r.signed_at.date(), settings.esign_validity_months
                )
        else:
            r.status = status
        chain.append(
            db,
            "system:esign",
            f"esign.{r.status}",
            f"esign:{r.id}",
            {"client": str(r.client_id), "kind": r.kind},
        )
        db.commit()
    return {"status": status}


@router.get("/clients/{client_id}/flow-status")
def flow_status(
    client_id: uuid.UUID,
    p: Principal = Depends(require("client.read")),
    db: Db = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> dict[str, Any]:
    c = get_visible(db, p, client_id)
    kyc = db.get(KycForm, c.id)
    return {
        "status": c.status,
        "kyc": None
        if kyc is None
        else {"risk": kyc.risk, "flags": kyc.flags, "approved": kyc.approved_at is not None},
        "mandate_signed": service.signed(db, c.id, service.MANDATE) is not None,
        "authorizations": service.authorizations(db, c.id, settings),
        "activation_blockers": service.activation_blockers(db, c),
    }


@router.post("/clients/{client_id}/activate")
def activate(
    client_id: uuid.UUID, p: Principal = Depends(require("flow.write")), db: Db = Depends(get_db)
) -> dict[str, Any]:
    c = get_visible(db, p, client_id)
    try:
        service.activate(db, p.user.id, c)
    except service.FlowError as e:
        raise _fail(e) from e
    return {"status": c.status}


# ---- F5 ---------------------------------------------------------------------------------------
@router.post("/clients/{client_id}/lookup-requests", status_code=201)
def lookup_request(
    client_id: uuid.UUID,
    p: Principal = Depends(require("flow.write")),
    db: Db = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> dict[str, Any]:
    c = get_visible(db, p, client_id)
    try:
        return service.request_lookup(db, p.user.id, c, settings)
    except service.FlowError as e:
        raise _fail(e) from e


# ---- F2 ---------------------------------------------------------------------------------------
class AdviceIn(BaseModel):
    product: str = Field(min_length=2, max_length=32)
    needs: str = Field(min_length=3)
    advice: str = Field(min_length=3)


@router.post("/clients/{client_id}/advice", status_code=201)
def add_advice(
    client_id: uuid.UUID,
    body: AdviceIn,
    p: Principal = Depends(require("flow.write")),
    db: Db = Depends(get_db),
) -> dict[str, Any]:
    c = get_visible(db, p, client_id)
    a = AdviceRecord(
        client_id=c.id,
        product=body.product,
        needs=body.needs,
        advice=body.advice,
        created_by=p.user.id,
    )
    db.add(a)
    db.flush()
    chain.append(db, str(p.user.id), "advice.record", f"advice:{a.id}", {"client": str(c.id)})
    return {"id": str(a.id)}


class AppIn(BaseModel):
    insurer: str = Field(min_length=2, max_length=120)
    product: str = Field(min_length=2, max_length=32)
    advice_id: uuid.UUID | None = None


@router.post("/clients/{client_id}/applications", status_code=201)
def new_application(
    client_id: uuid.UUID,
    body: AppIn,
    p: Principal = Depends(require("flow.write")),
    db: Db = Depends(get_db),
) -> dict[str, Any]:
    c = get_visible(db, p, client_id)
    if body.advice_id is not None:
        adv = db.get(AdviceRecord, body.advice_id)
        if adv is None or adv.client_id != c.id:
            raise HTTPException(422, "advice_not_of_client")
    ins = db.scalar(select(Insurer).where(Insurer.name == body.insurer))
    if ins is None:
        ins = Insurer(name=body.insurer)
        db.add(ins)
        db.flush()
    a = Application(
        client_id=c.id,
        insurer_id=ins.id,
        product=body.product,
        advice_id=body.advice_id,
        created_by=p.user.id,
    )
    db.add(a)
    db.flush()
    chain.append(
        db, str(p.user.id), "application.create", f"application:{a.id}", {"client": str(c.id)}
    )
    return {"id": str(a.id), "status": a.status}


class AppSignIn(BaseModel):
    document_id: uuid.UUID
    signer_contact: str = Field(min_length=3, max_length=254)


def _application(db: Db, p: Principal, app_id: uuid.UUID) -> tuple[Application, Client]:
    a = db.get(Application, app_id)
    if a is None:
        raise HTTPException(404, "not_found")
    return a, get_visible(db, p, a.client_id)


@router.post("/applications/{app_id}/send-for-signature", status_code=201)
def application_sign(
    request: Request,
    app_id: uuid.UUID,
    body: AppSignIn,
    p: Principal = Depends(require("flow.write")),
    db: Db = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> dict[str, Any]:
    a, c = _application(db, p, app_id)
    if a.advice_id is None:
        raise HTTPException(409, "advice_record_missing")
    out = send_for_signature(
        request,
        c.id,
        SignIn(
            kind="application",
            title=f"Application: {a.product}",
            document_id=body.document_id,
            signer_contact=body.signer_contact,
        ),
        p,
        db,
        settings,
    )
    a.esign_id, a.status = uuid.UUID(out["id"]), "sent"
    return out


class IssueIn(BaseModel):
    policy_number: str = Field(min_length=1, max_length=64)
    premium_ils: Decimal = Field(ge=0)
    renewal_date: date | None = None
    confirmation_document_id: uuid.UUID | None = None


@router.post("/applications/{app_id}/issue", status_code=201)
def issue(
    app_id: uuid.UUID,
    body: IssueIn,
    p: Principal = Depends(require("flow.write")),
    db: Db = Depends(get_db),
) -> dict[str, Any]:
    a, c = _application(db, p, app_id)
    if body.confirmation_document_id is not None:
        _document(db, c, body.confirmation_document_id)
    try:
        pol = service.issue_policy(
            db,
            p.user.id,
            a,
            policy_number=body.policy_number,
            premium=body.premium_ils,
            renewal_date=body.renewal_date,
            confirmation_doc=body.confirmation_document_id,
        )
    except service.FlowError as e:
        raise _fail(e) from e
    return {"policy_id": str(pol.id), "checklist": service.policy_checklist(db, pol)}


@router.get("/policies/{policy_id}/checklist")
def checklist(
    policy_id: uuid.UUID, p: Principal = Depends(require("policy.read")), db: Db = Depends(get_db)
) -> dict[str, Any]:
    pol, _ = _policy(db, p, policy_id)
    return service.policy_checklist(db, pol)


class ConfirmIn(BaseModel):
    document_id: uuid.UUID


@router.put("/applications/{app_id}/confirmation")
def attach_confirmation(
    app_id: uuid.UUID,
    body: ConfirmIn,
    p: Principal = Depends(require("flow.write")),
    db: Db = Depends(get_db),
) -> dict[str, Any]:
    a, c = _application(db, p, app_id)
    _document(db, c, body.document_id)
    a.confirmation_doc_id = body.document_id
    pol = db.get(Policy, a.policy_id) if a.policy_id else None
    return {"policy_checklist": service.policy_checklist(db, pol) if pol else None}


# ---- F3 ---------------------------------------------------------------------------------------
@router.get("/renewals")
def renewals(
    within: int = 60, p: Principal = Depends(require("policy.read")), db: Db = Depends(get_db)
) -> list[dict[str, Any]]:
    q = service.renewal_queue(db, within=max(0, min(within, 365)))
    if "client.read_all" in p.capabilities:
        return q
    return [
        r for r in q if r["agent_id"] == str(p.user.id)
    ]  # each item belongs to the agent of record


@router.get("/renewals/leaks")
def renewal_leaks(
    p: Principal = Depends(require("flow.approve")), db: Db = Depends(get_db)
) -> list[dict[str, Any]]:
    return service.leaks(db)


class OutcomeIn(BaseModel):
    outcome: str
    reason: str = ""
    new_renewal_date: date | None = None


@router.post("/policies/{policy_id}/renewal", status_code=201)
def renewal_outcome(
    policy_id: uuid.UUID,
    body: OutcomeIn,
    p: Principal = Depends(require("flow.write")),
    db: Db = Depends(get_db),
) -> dict[str, Any]:
    pol, _ = _policy(db, p, policy_id)
    try:
        service.record_renewal(db, p.user.id, pol, body.outcome, body.reason, body.new_renewal_date)
    except service.FlowError as e:
        raise _fail(e) from e
    return {
        "status": pol.status,
        "renewal_date": pol.renewal_date.isoformat() if pol.renewal_date else None,
    }


# ---- F4 ---------------------------------------------------------------------------------------
class ClaimIn(BaseModel):
    policy_id: uuid.UUID
    event_date: date
    place: str = ""
    description: str = Field(min_length=3)


@router.post("/clients/{client_id}/claims", status_code=201)
def open_claim(
    client_id: uuid.UUID,
    body: ClaimIn,
    p: Principal = Depends(require("flow.write")),
    db: Db = Depends(get_db),
) -> dict[str, Any]:
    c = get_visible(db, p, client_id)
    pol = db.get(Policy, body.policy_id)
    if pol is None:
        raise HTTPException(404, "not_found")
    try:
        cl = service.open_claim(
            db, p.user.id, c, pol, body.event_date, body.place, body.description
        )
    except service.FlowError as e:
        raise _fail(e) from e
    return service.claim_view(db, cl)


def _claim(db: Db, p: Principal, claim_id: uuid.UUID) -> tuple[Claim, Client]:
    cl = db.get(Claim, claim_id)
    if cl is None:
        raise HTTPException(404, "not_found")
    return cl, get_visible(db, p, cl.client_id)


@router.get("/claims/{claim_id}")
def get_claim(
    claim_id: uuid.UUID, p: Principal = Depends(require("client.read")), db: Db = Depends(get_db)
) -> dict[str, Any]:
    cl, _ = _claim(db, p, claim_id)
    return service.claim_view(db, cl)


class AttachIn(BaseModel):
    document_id: uuid.UUID


@router.put("/claims/{claim_id}/checklist/{item_id}")
def attach(
    claim_id: uuid.UUID,
    item_id: uuid.UUID,
    body: AttachIn,
    p: Principal = Depends(require("flow.write")),
    db: Db = Depends(get_db),
) -> dict[str, Any]:
    cl, c = _claim(db, p, claim_id)
    item = db.get(ClaimItem, item_id)
    if item is None or item.claim_id != cl.id:
        raise HTTPException(404, "not_found")
    _document(db, c, body.document_id)
    item.document_id = body.document_id
    service.event(db, cl, "document", item.label, p.user.id)
    return service.claim_view(db, cl)


class SubmitIn(BaseModel):
    insurer_ref: str = Field(min_length=1, max_length=64)


@router.post("/claims/{claim_id}/submit")
def submit(
    claim_id: uuid.UUID,
    body: SubmitIn,
    p: Principal = Depends(require("flow.write")),
    db: Db = Depends(get_db),
) -> dict[str, Any]:
    cl, _ = _claim(db, p, claim_id)
    if cl.status != "open":
        raise HTTPException(409, f"claim_is_{cl.status}")
    cl.status, cl.insurer_ref, cl.submitted_at = "submitted", body.insurer_ref, datetime.now(UTC)
    service.event(db, cl, "submitted", body.insurer_ref, p.user.id)
    chain.append(db, str(p.user.id), "claim.submit", f"claim:{cl.id}", {})
    return service.claim_view(db, cl)


class OwnerIn(BaseModel):
    owner_id: uuid.UUID


@router.put("/claims/{claim_id}/owner")
def set_owner(
    claim_id: uuid.UUID,
    body: OwnerIn,
    p: Principal = Depends(require("flow.write")),
    db: Db = Depends(get_db),
) -> dict[str, Any]:
    cl, _ = _claim(db, p, claim_id)  # always an owner: it can be changed, never emptied
    cl.owner_id = body.owner_id
    service.event(db, cl, "owner", str(body.owner_id), p.user.id)
    return service.claim_view(db, cl)


class ClaimOutcomeIn(BaseModel):
    outcome: str
    paid_ils: Decimal | None = Field(default=None, ge=0)
    note: str = ""
    client_unhappy: bool = False


@router.post("/claims/{claim_id}/outcome")
def claim_outcome(
    claim_id: uuid.UUID,
    body: ClaimOutcomeIn,
    p: Principal = Depends(require("flow.write")),
    db: Db = Depends(get_db),
) -> dict[str, Any]:
    cl, c = _claim(db, p, claim_id)
    if cl.status != "submitted":
        raise HTTPException(409, f"claim_is_{cl.status}")
    if body.outcome not in ("paid", "rejected"):
        raise HTTPException(422, "invalid_outcome")
    if body.outcome == "paid" and body.paid_ils is None:
        raise HTTPException(422, "paid_amount_required")
    cl.status, cl.paid_ils, cl.outcome_note = body.outcome, body.paid_ils, body.note
    service.event(db, cl, body.outcome, body.note, p.user.id)
    if body.outcome == "rejected" and body.client_unhappy:  # F8: a complaint is opened
        service._inbox(
            db,
            type="complaint",
            role="manager",
            client=c,
            urgency="urgent",
            by=p.user.id,
            title=f"Complaint: claim rejected, {c.name}",
            detail=body.note,
        )
    chain.append(db, str(p.user.id), "claim.outcome", f"claim:{cl.id}", {"outcome": body.outcome})
    return service.claim_view(db, cl)
