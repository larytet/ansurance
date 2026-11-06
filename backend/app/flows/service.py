"""Business rules for flows F1 to F5 (REQUIREMENTS section 3). The routers only translate."""

import calendar
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session as Db

from app.audit import chain
from app.config import Settings
from app.models import (
    Application,
    Claim,
    ClaimEvent,
    ClaimItem,
    Client,
    InboxItem,
    KycForm,
    Policy,
    RenewalOutcome,
)
from app.models import ESignRequest as ESign


class FlowError(Exception):
    def __init__(self, code: str, status: int = 409, detail: Any = None) -> None:
        super().__init__(code)
        self.code, self.status, self.detail = code, status, detail


MANDATE = "mandate"
LOOKUP_KINDS = ("lookup_insurers", "lookup_har", "lookup_clearing")  # F5 step 1
SIGN_KINDS = (MANDATE, *LOOKUP_KINDS, "application", "other")
DOC_TYPE_FOR_KIND = {"application": "application"}  # the rest are powers of attorney


def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def _inbox(
    db: Db,
    *,
    type: str,
    role: str,
    title: str,
    client: Client | None,
    urgency: str,
    assignee: uuid.UUID | None = None,
    detail: str = "",
    by: uuid.UUID | None = None,
) -> None:
    db.add(
        InboxItem(
            type=type,
            role=role,
            assignee_id=assignee,
            client_id=client.id if client else None,
            urgency=urgency,
            title=title,
            detail=detail,
            created_by=by,
        )
    )


# ---- F1: KYC and activation ---------------------------------------------------------------
def save_kyc(db: Db, actor: uuid.UUID, client: Client, data: dict[str, Any]) -> KycForm:
    missing: list[str] = []
    if not data.get("identity_verified"):
        missing.append("identity_verified")
    if not str(data.get("beneficiary", "")).strip():
        missing.append("beneficiary")
    if data.get("corporate") and not str(data.get("controlling_shareholder", "")).strip():
        missing.append("controlling_shareholder")
    if (data.get("large_single_premium") or data.get("unusual")) and not str(
        data.get("source_of_funds", "")
    ).strip():
        missing.append("source_of_funds")
    if missing:
        raise FlowError("kyc_incomplete", 422, missing)
    flags = [f for f in ("high_risk", "pep", "large_single_premium", "unusual") if data.get(f)]
    form = db.get(KycForm, client.id)
    if form is None:
        form = KycForm(client_id=client.id)
        db.add(form)
    form.data, form.flags = data, flags
    form.risk = "flagged" if flags else "normal"
    form.completed_by, form.completed_at = actor, datetime.now(UTC)
    form.approved_by = form.approved_at = None  # a changed form needs a fresh decision
    chain.append(
        db, str(actor), "kyc.save", f"client:{client.id}", {"risk": form.risk, "flags": flags}
    )
    if flags:  # an unusual transaction raises a manager task; a report is only ever drafted
        _inbox(
            db,
            type="kyc_review",
            role="manager",
            client=client,
            urgency="urgent",
            by=actor,
            title=f"KYC needs approval: {client.name}",
            detail="Flags: "
            + ", ".join(flags)
            + ". A suspicious-activity report is only a draft; it is never sent.",
        )
    db.flush()
    return form


def approve_kyc(db: Db, actor: uuid.UUID, client: Client) -> KycForm:
    form = db.get(KycForm, client.id)
    if form is None:
        raise FlowError("kyc_missing", 409)
    form.approved_by, form.approved_at = actor, datetime.now(UTC)
    for it in db.scalars(
        select(InboxItem).where(
            InboxItem.client_id == client.id,
            InboxItem.type == "kyc_review",
            InboxItem.status == "open",
        )
    ):
        it.status, it.resolved_at, it.resolved_by, it.resolution = (
            "done",
            datetime.now(UTC),
            actor,
            "approved",
        )
    chain.append(db, str(actor), "kyc.approve", f"client:{client.id}", {})
    return form


def signed(db: Db, client_id: uuid.UUID, kind: str, today: date | None = None) -> ESign | None:
    """The newest signed request of this kind that is still valid."""
    today = today or datetime.now(UTC).date()
    rows = db.scalars(
        select(ESign)
        .where(ESign.client_id == client_id, ESign.kind == kind, ESign.status == "signed")
        .order_by(ESign.signed_at.desc())
    )
    for r in rows:
        if r.expires_at is None or r.expires_at >= today:
            return r
    return None


def activation_blockers(db: Db, client: Client) -> list[str]:
    out: list[str] = []
    if client.status not in ("registered", "active"):
        out.append("client_not_registered")  # validated ID and a clean ID card come first
    form = db.get(KycForm, client.id)
    if form is None:
        out.append("kyc_form_missing")
    elif form.risk == "flagged" and form.approved_at is None:
        out.append("manager_approval_missing")
    if signed(db, client.id, MANDATE) is None:
        out.append("signed_mandate_missing")
    if signed(db, client.id, "lookup_insurers") is None:
        out.append("signed_data_lookup_consent_missing")
    return out


def activate(db: Db, actor: uuid.UUID, client: Client) -> None:
    blockers = activation_blockers(db, client)
    if blockers:
        raise FlowError("cannot_activate", 409, blockers)
    client.status = "active"
    chain.append(db, str(actor), "client.activate", f"client:{client.id}", {})


# ---- F5: the three authorizations ---------------------------------------------------------
def authorizations(
    db: Db, client_id: uuid.UUID, settings: Settings, today: date | None = None
) -> dict[str, Any]:
    today = today or datetime.now(UTC).date()
    out: dict[str, Any] = {}
    for kind in LOOKUP_KINDS:
        r = signed(db, client_id, kind, today)
        if r is None:
            out[kind] = {"state": "missing"}
            continue
        warn = r.expires_at is not None and r.expires_at - today <= timedelta(
            days=settings.esign_warn_days
        )
        out[kind] = {
            "state": "expiring" if warn else "valid",
            "signed_at": r.signed_at.isoformat() if r.signed_at else None,
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
            "resign_offered": warn,
        }
    return out


def request_lookup(db: Db, actor: uuid.UUID, client: Client, settings: Settings) -> dict[str, Any]:
    auth = authorizations(db, client.id, settings)
    missing = [k for k, v in auth.items() if v["state"] == "missing"]
    if missing:  # no lookup runs without all three valid signed authorizations
        raise FlowError("authorizations_missing", 409, missing)
    chain.append(
        db,
        str(actor),
        "lookup.request",
        f"client:{client.id}",
        {"authorizations": {k: v["state"] for k, v in auth.items()}},
    )
    return {"status": "request_recorded", "authorizations": auth}


# ---- F2: advice, application, issuance ----------------------------------------------------
def issue_policy(
    db: Db,
    actor: uuid.UUID,
    app: Application,
    *,
    policy_number: str,
    premium: Any,
    renewal_date: date | None,
    confirmation_doc: uuid.UUID | None,
) -> Policy:
    if app.status == "issued":
        raise FlowError("already_issued", 409)
    if app.advice_id is None:
        raise FlowError("advice_record_missing", 409)
    es = db.get(ESign, app.esign_id) if app.esign_id else None
    if es is None or es.status != "signed":
        raise FlowError("signed_application_missing", 409)
    if renewal_date is None:
        raise FlowError("renewal_date_required", 422)
    pol = Policy(
        client_id=app.client_id,
        insurer_id=app.insurer_id,
        policy_number=policy_number,
        product=app.product,
        premium_ils=premium,
        renewal_date=renewal_date,
    )
    db.add(pol)
    db.flush()
    app.policy_id, app.confirmation_doc_id, app.status = pol.id, confirmation_doc, "issued"
    chain.append(
        db, str(actor), "application.issue", f"application:{app.id}", {"policy": str(pol.id)}
    )
    return pol


def policy_checklist(db: Db, policy: Policy) -> dict[str, Any]:
    app = db.scalar(select(Application).where(Application.policy_id == policy.id))
    es = db.get(ESign, app.esign_id) if app and app.esign_id else None
    items = {
        "advice_record": bool(app and app.advice_id),
        "signed_application": bool(es and es.status == "signed"),
        "insurer_confirmation": bool(app and app.confirmation_doc_id),
        "renewal_date": policy.renewal_date is not None,
    }
    return {
        "items": items,
        "complete": all(items.values()),
        "missing": [k for k, v in items.items() if not v],
    }


# ---- F3: renewals ---------------------------------------------------------------------------
STAGES = (10, 30, 60)


def renewal_stage(days: int) -> str:
    if days < 0:
        return "overdue"
    for s in STAGES:
        if days <= s:
            return f"t{s}"
    return "later"


def _open_renewals(db: Db, horizon: date, today: date):  # type: ignore[no-untyped-def]
    done = select(RenewalOutcome.policy_id).where(RenewalOutcome.cycle_date == Policy.renewal_date)
    return db.execute(
        select(Policy, Client)
        .join(Client, Client.id == Policy.client_id)
        .where(
            Policy.status == "active",
            Policy.renewal_date.is_not(None),
            Policy.renewal_date <= horizon,
            Policy.id.not_in(done),
        )
        .order_by(Policy.renewal_date)
    )


def renewal_queue(db: Db, today: date | None = None, within: int = 60) -> list[dict[str, Any]]:
    today = today or datetime.now(UTC).date()
    out = []
    for pol, cl in _open_renewals(db, today + timedelta(days=within), today):
        if pol.renewal_date is None:
            continue
        days = (pol.renewal_date - today).days
        out.append(
            {
                "policy_id": str(pol.id),
                "client_id": str(cl.id),
                "client": cl.name,
                "agent_id": str(cl.agent_id) if cl.agent_id else None,
                "product": pol.product,
                "renewal_date": pol.renewal_date.isoformat(),
                "days": days,
                "stage": renewal_stage(days),
            }
        )
    return out


def record_renewal(
    db: Db, actor: uuid.UUID, policy: Policy, outcome: str, reason: str, new_date: date | None
) -> RenewalOutcome:
    if outcome not in ("renewed", "changed", "cancelled", "lapsed"):
        raise FlowError("invalid_outcome", 422)
    if outcome in ("cancelled", "lapsed") and len(reason.strip()) < 3:
        raise FlowError("reason_required", 422)
    if outcome in ("renewed", "changed") and new_date is None:
        raise FlowError("new_renewal_date_required", 422)
    if policy.renewal_date is None:
        raise FlowError("policy_has_no_renewal_date", 409)
    row = RenewalOutcome(
        policy_id=policy.id,
        cycle_date=policy.renewal_date,
        outcome=outcome,
        reason=reason,
        new_renewal_date=new_date,
        recorded_by=actor,
    )
    db.add(row)
    try:
        db.flush()
    except Exception as e:
        db.rollback()
        raise FlowError("outcome_already_recorded", 409) from e
    cl = db.get(Client, policy.client_id)
    if outcome in ("renewed", "changed"):
        policy.renewal_date = new_date
    else:
        policy.status = outcome
        if outcome == "cancelled":  # cancellations go to the manager for retention follow-up
            _inbox(
                db,
                type="retention",
                role="manager",
                client=cl,
                urgency="important",
                by=actor,
                title=f"Cancellation: {cl.name if cl else ''}, {policy.product}",
                detail=reason,
            )
    chain.append(db, str(actor), "renewal.outcome", f"policy:{policy.id}", {"outcome": outcome})
    return row


def leaks(db: Db, today: date | None = None) -> list[dict[str, Any]]:
    today = today or datetime.now(UTC).date()
    return [r for r in renewal_queue(db, today, within=-1)]


def renewal_tick(db: Db, today: date | None = None) -> int:
    """Daily: one Inbox item for the agent of record when a policy first enters T-60, T-30,
    T-10 or is overdue. Safe to run repeatedly."""
    today = today or datetime.now(UTC).date()
    made = 0
    for r in renewal_queue(db, today, 60):
        title = f"Renewal {r['stage'].upper()}: {r['client']}, {r['product']} ({r['renewal_date']})"
        exists = db.scalar(
            select(InboxItem.id).where(InboxItem.type == "renewal", InboxItem.title == title)
        )
        if exists is not None:
            continue
        cl = db.get(Client, uuid.UUID(r["client_id"]))
        _inbox(
            db,
            type="renewal",
            role="agent",
            client=cl,
            assignee=cl.agent_id if cl else None,
            title=title,
            urgency="urgent" if r["stage"] in ("t10", "overdue") else "important",
        )
        made += 1
    return made


# ---- F4: claims -----------------------------------------------------------------------------
CHECKLISTS = {
    "car": ["police_report", "photos", "repair_invoice", "driving_licence"],
    "health": ["medical_forms", "invoices", "referral"],
    "home": ["photos", "invoices", "police_report"],
    "life": ["death_certificate", "medical_forms"],
}
DEFAULT_CHECKLIST = ["claim_form", "supporting_documents"]


def event(
    db: Db, claim: Claim, kind: str, note: str, actor: uuid.UUID | None, at: datetime | None = None
) -> None:
    db.add(
        ClaimEvent(
            claim_id=claim.id, kind=kind, note=note, actor_id=actor, at=at or datetime.now(UTC)
        )
    )


def open_claim(
    db: Db,
    actor: uuid.UUID,
    client: Client,
    policy: Policy,
    event_date: date,
    place: str,
    description: str,
) -> Claim:
    if policy.client_id != client.id:
        raise FlowError("policy_not_of_client", 422)
    c = Claim(
        client_id=client.id,
        policy_id=policy.id,
        event_date=event_date,
        place=place,
        description=description,
        owner_id=actor,
    )
    db.add(c)
    db.flush()
    for label in CHECKLISTS.get(policy.product, DEFAULT_CHECKLIST):
        db.add(ClaimItem(claim_id=c.id, label=label))
    event(db, c, "opened", description[:200], actor)
    chain.append(db, str(actor), "claim.open", f"claim:{c.id}", {"policy": str(policy.id)})
    return c


def claim_view(db: Db, c: Claim) -> dict[str, Any]:
    items = list(
        db.scalars(select(ClaimItem).where(ClaimItem.claim_id == c.id).order_by(ClaimItem.label))
    )
    done = sum(1 for i in items if i.document_id)
    tl = db.scalars(
        select(ClaimEvent).where(ClaimEvent.claim_id == c.id).order_by(ClaimEvent.at, ClaimEvent.id)
    )
    return {
        "id": str(c.id),
        "client_id": str(c.client_id),
        "policy_id": str(c.policy_id),
        "status": c.status,
        "owner_id": str(c.owner_id),
        "insurer_ref": c.insurer_ref,
        "event_date": c.event_date.isoformat(),
        "place": c.place,
        "description": c.description,
        "paid_ils": str(c.paid_ils) if c.paid_ils is not None else None,
        "checklist": [
            {
                "id": str(i.id),
                "label": i.label,
                "document_id": str(i.document_id) if i.document_id else None,
            }
            for i in items
        ],
        "completion_percent": round(100 * done / len(items)) if items else 100,
        "timeline": [{"kind": e.kind, "note": e.note, "at": e.at.isoformat()} for e in tl],
    }


def late_claims(db: Db, now: datetime | None = None) -> int:
    """Daily: remind the owner when the insurer has not answered a submitted claim in time."""
    now = now or datetime.now(UTC)
    made = 0
    for c in db.scalars(select(Claim).where(Claim.status == "submitted")):
        if c.submitted_at is None:
            continue
        last = (
            db.scalar(
                select(func.max(ClaimEvent.at)).where(
                    ClaimEvent.claim_id == c.id, ClaimEvent.kind == "reminder"
                )
            )
            or c.submitted_at
        )
        if now - last >= timedelta(days=c.reminder_days):
            cl = db.get(Client, c.client_id)
            _inbox(
                db,
                type="claim_late",
                role="agent",
                assignee=c.owner_id,
                client=cl,
                urgency="important",
                title=f"Insurer is late on claim {c.insurer_ref}: {cl.name if cl else ''}",
            )
            event(db, c, "reminder", "insurer late", None, now)
            made += 1
    return made
