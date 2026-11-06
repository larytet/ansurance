"""Manager side: complaints and compliance (F8), commission reconciliation (F7), Overview."""

import csv
import io
import uuid
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session as Db

from app.audit import chain
from app.config import Settings
from app.models import (
    AgentLicence,
    Campaign,
    Claim,
    Client,
    CommissionLine,
    CommissionStatement,
    CommissionTerm,
    Complaint,
    InboxItem,
    Insurer,
    IntakeItem,
    KycForm,
    Policy,
    User,
)

CENT = Decimal("0.01")


class ManagerError(Exception):
    def __init__(self, code: str, status: int = 409, detail: Any = None) -> None:
        super().__init__(code)
        self.code, self.status, self.detail = code, status, detail


def _inbox(
    db: Db,
    *,
    type: str,
    role: str,
    title: str,
    urgency: str = "important",
    client_id: uuid.UUID | None = None,
    detail: str = "",
) -> None:
    db.add(
        InboxItem(
            type=type, role=role, title=title, urgency=urgency, client_id=client_id, detail=detail
        )
    )


# ---- F8 complaints ---------------------------------------------------------------------------
def open_complaint(
    db: Db,
    settings: Settings,
    actor: uuid.UUID,
    *,
    client_id: uuid.UUID | None,
    policy_id: uuid.UUID | None,
    kind: str,
    channel: str,
    text: str,
    now: datetime | None = None,
) -> Complaint:
    if kind not in ("complaint", "privacy", "regulator"):
        raise ManagerError("invalid_kind", 422)
    now = now or datetime.now(UTC)
    c = Complaint(
        client_id=client_id,
        policy_id=policy_id,
        kind=kind,
        channel=channel,
        text=text,
        opened_by=actor,
        opened_at=now,
        due_at=now + timedelta(days=settings.complaint_sla_days),
    )
    db.add(c)
    db.flush()
    chain.append(db, str(actor), "complaint.open", f"complaint:{c.id}", {"kind": kind})
    _inbox(
        db,
        type="complaint",
        role="manager",
        client_id=client_id,
        urgency="important",
        title=f"New {kind}: assign an owner",
        detail=text[:200],
    )
    return c


def respond(
    db: Db, actor: uuid.UUID, c: Complaint, response: str, now: datetime | None = None
) -> None:
    if c.status == "answered":
        raise ManagerError("already_answered", 409)
    c.status, c.response, c.responded_at = "answered", response, now or datetime.now(UTC)
    chain.append(db, str(actor), "complaint.respond", f"complaint:{c.id}", {})


def is_overdue(c: Complaint, now: datetime) -> bool:
    return c.status != "answered" and c.due_at < now


def complaint_dict(c: Complaint, now: datetime) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "kind": c.kind,
        "channel": c.channel,
        "text": c.text,
        "status": c.status,
        "owner_id": str(c.owner_id) if c.owner_id else None,
        "opened_at": c.opened_at.isoformat(),
        "due_at": c.due_at.isoformat(),
        "overdue": is_overdue(c, now),  # shown red on the manager Inbox
        "client_id": str(c.client_id) if c.client_id else None,
        "response": c.response,
    }


def complaint_tick(db: Db, now: datetime | None = None) -> int:
    """Daily: a complaint past its deadline becomes an urgent manager item, once."""
    now = now or datetime.now(UTC)
    made = 0
    for c in db.scalars(
        select(Complaint).where(Complaint.status != "answered", Complaint.due_at < now)
    ):
        title = f"Complaint past its deadline ({c.id})"
        if (
            db.scalar(
                select(InboxItem.id).where(
                    InboxItem.type == "complaint_overdue", InboxItem.title == title
                )
            )
            is None
        ):
            _inbox(
                db,
                type="complaint_overdue",
                role="manager",
                urgency="urgent",
                title=title,
                client_id=c.client_id,
                detail=c.text[:200],
            )
            made += 1
    return made


def complaints_csv(db: Db, now: datetime) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(
        [
            "id",
            "kind",
            "channel",
            "opened_at",
            "due_at",
            "status",
            "responded_at",
            "overdue",
            "text",
            "response",
        ]
    )
    for c in db.scalars(select(Complaint).order_by(Complaint.opened_at)):
        w.writerow(
            [
                c.id,
                c.kind,
                c.channel,
                c.opened_at.isoformat(),
                c.due_at.isoformat(),
                c.status,
                c.responded_at.isoformat() if c.responded_at else "",
                is_overdue(c, now),
                c.text,
                c.response,
            ]
        )
    return out.getvalue()


def compliance(db: Db, settings: Settings, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    today = now.date()
    lic = []
    for lc, u in db.execute(select(AgentLicence, User).join(User, User.id == AgentLicence.user_id)):
        days = (lc.expires_on - today).days
        state = (
            "expired" if days < 0 else "expiring" if days <= settings.licence_warn_days else "valid"
        )
        lic.append(
            {
                "user": u.display_name,
                "licence_no": lc.licence_no,
                "expires_on": lc.expires_on.isoformat(),
                "state": state,
                "cpd_missing_hours": max(0, lc.cpd_required_hours - lc.cpd_done_hours),
            }
        )
    limit = now - timedelta(
        days=settings.kyc_valid_days - 30
    )  # valid for a year, flagged a month ahead
    kyc = (
        db.scalar(select(func.count()).select_from(KycForm).where(KycForm.completed_at < limit))
        or 0
    )
    open_c = list(db.scalars(select(Complaint).where(Complaint.status != "answered")))
    return {
        "licences": lic,
        "kyc_expiring": kyc,
        "complaints_unanswered": len(open_c),
        "complaints_overdue": sum(1 for c in open_c if is_overdue(c, now)),
        "privacy_requests_open": sum(1 for c in open_c if c.kind == "privacy"),
    }


def overview(db: Db, settings: Settings, now: datetime | None = None) -> dict[str, Any]:
    from app.flows import service as flows

    now = now or datetime.now(UTC)
    comp = compliance(db, settings, now)
    return {
        "inbox_urgent": db.scalar(
            select(func.count())
            .select_from(InboxItem)
            .where(InboxItem.status == "open", InboxItem.urgency == "urgent")
        )
        or 0,
        "renewals_overdue": len(flows.leaks(db, now.date())),
        "renewals_next_30": len(flows.renewal_queue(db, now.date(), 30)),
        "claims_open": db.scalar(
            select(func.count()).select_from(Claim).where(Claim.status.in_(("open", "submitted")))
        )
        or 0,
        "intake_waiting_review": db.scalar(
            select(func.count())
            .select_from(IntakeItem)
            .where(IntakeItem.route == "review", IntakeItem.document_id.is_(None))
        )
        or 0,
        "kyc_waiting_approval": db.scalar(
            select(func.count())
            .select_from(KycForm)
            .where(KycForm.risk == "flagged", KycForm.approved_at.is_(None))
        )
        or 0,
        "campaigns_sending": db.scalar(
            select(func.count()).select_from(Campaign).where(Campaign.status == "sending")
        )
        or 0,
        "clients": db.scalar(select(func.count()).select_from(Client)) or 0,
        "complaints_overdue": comp["complaints_overdue"],
        "licences_attention": sum(1 for lc in comp["licences"] if lc["state"] != "valid"),
    }


# ---- F7 commissions ------------------------------------------------------------------------------
def money(v: Any) -> Decimal:
    return Decimal(str(v)).quantize(CENT, rounding=ROUND_HALF_UP)


def set_term(db: Db, actor: uuid.UUID, insurer: str, product: str, rate_pct: Decimal) -> None:
    ins = _insurer(db, insurer)
    t = db.scalar(
        select(CommissionTerm).where(
            CommissionTerm.insurer_id == ins.id, CommissionTerm.product == product
        )
    )
    if t is None:
        db.add(CommissionTerm(insurer_id=ins.id, product=product, rate_pct=rate_pct))
    else:
        t.rate_pct = rate_pct
    chain.append(
        db,
        str(actor),
        "commission.term",
        f"insurer:{ins.id}",
        {"product": product, "rate_pct": str(rate_pct)},
    )


def _insurer(db: Db, name: str) -> Insurer:
    ins = db.scalar(select(Insurer).where(Insurer.name == name))
    if ins is None:
        ins = Insurer(name=name)
        db.add(ins)
        db.flush()
    return ins


def import_statement(
    db: Db,
    settings: Settings,
    actor: uuid.UUID,
    *,
    insurer: str,
    period: str,
    declared_total: Any,
    lines: list[tuple[str, Any]],
) -> CommissionStatement:
    if len(period) != 7 or period[4] != "-":
        raise ManagerError("invalid_period", 422)
    amounts = [(pn.strip(), money(a)) for pn, a in lines]
    if not amounts:
        raise ManagerError("no_lines", 422)
    total = sum((a for _, a in amounts), Decimal(0))
    if total != money(declared_total):  # totals must tie to the statement to the agora
        raise ManagerError(
            "totals_do_not_tie",
            422,
            {
                "declared": str(money(declared_total)),
                "lines": str(total),
                "difference": str(total - money(declared_total)),
            },
        )
    ins = _insurer(db, insurer)
    if db.scalar(
        select(CommissionStatement.id).where(
            CommissionStatement.insurer_id == ins.id, CommissionStatement.period == period
        )
    ):
        raise ManagerError("statement_already_imported", 409)
    st = CommissionStatement(
        insurer_id=ins.id,
        period=period,
        declared_total_ils=money(declared_total),
        imported_by=actor,
    )
    db.add(st)
    db.flush()
    terms = {
        t.product: t.rate_pct
        for t in db.scalars(select(CommissionTerm).where(CommissionTerm.insurer_id == ins.id))
    }
    seen: set[uuid.UUID] = set()
    tol = Decimal(str(settings.commission_tolerance_ils))
    for pn, amt in amounts:
        pol = db.scalar(
            select(Policy).where(Policy.insurer_id == ins.id, Policy.policy_number == pn)
        )
        expected = None
        if pol is None:
            result = "unknown_policy"
        else:
            seen.add(pol.id)
            rate = terms.get(pol.product)
            if rate is None:
                result = "no_terms"
            else:
                expected = money(pol.premium_ils * rate / 100)
                result = (
                    "clawback"
                    if amt < 0
                    else "matched"
                    if abs(amt - expected) <= tol
                    else "wrong_amount"
                )
        db.add(
            CommissionLine(
                statement_id=st.id,
                policy_number=pn,
                amount_ils=amt,
                policy_id=pol.id if pol else None,
                expected_ils=expected,
                result=result,
            )
        )
    # commissions we expected but the statement does not contain
    for pol in db.scalars(
        select(Policy).where(Policy.insurer_id == ins.id, Policy.status == "active")
    ):
        rate = terms.get(pol.product)
        if pol.id in seen or rate is None:
            continue
        db.add(
            CommissionLine(
                statement_id=st.id,
                policy_number=pol.policy_number,
                amount_ils=Decimal(0),
                policy_id=pol.id,
                expected_ils=money(pol.premium_ils * rate / 100),
                result="missing",
            )
        )
    db.flush()
    bad = list(
        db.scalars(
            select(CommissionLine).where(
                CommissionLine.statement_id == st.id, CommissionLine.result != "matched"
            )
        )
    )
    for ln in bad:  # every difference becomes a task
        _inbox(
            db,
            type="commission_diff",
            role="backoffice",
            title=f"Commission {ln.result}: {insurer} {period} {ln.policy_number}",
            detail=f"statement {ln.amount_ils}, expected {ln.expected_ils}",
        )
    chain.append(
        db,
        str(actor),
        "commission.import",
        f"statement:{st.id}",
        {"lines": len(amounts), "differences": len(bad)},
    )
    return st


def statement_view(db: Db, st: CommissionStatement) -> dict[str, Any]:
    rows = list(
        db.scalars(
            select(CommissionLine)
            .where(CommissionLine.statement_id == st.id)
            .order_by(CommissionLine.policy_number)
        )
    )
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.result] = counts.get(r.result, 0) + 1
    on_statement = sum((r.amount_ils for r in rows), Decimal(0))
    return {
        "id": str(st.id),
        "period": st.period,
        "status": st.status,
        "declared_total": str(st.declared_total_ils),
        "lines_total": str(on_statement),
        "ties": on_statement == st.declared_total_ils,
        "counts": counts,
        "lines": [
            {
                "policy_number": r.policy_number,
                "amount": str(r.amount_ils),
                "expected": str(r.expected_ils) if r.expected_ils is not None else None,
                "result": r.result,
            }
            for r in rows
        ],
    }


def approve_statement(db: Db, actor: uuid.UUID, st: CommissionStatement) -> None:
    if st.status == "approved":
        raise ManagerError("already_approved", 409)
    st.status, st.approved_by, st.approved_at = "approved", actor, datetime.now(UTC)
    chain.append(db, str(actor), "commission.approve", f"statement:{st.id}", {})


def payout(db: Db, st: CommissionStatement) -> dict[str, Any]:
    """Per agent of record. Lines with no policy are shown as unassigned, never dropped."""
    if st.status != "approved":
        raise ManagerError("not_approved", 409)
    per: dict[str, Decimal] = {}
    for ln in db.scalars(select(CommissionLine).where(CommissionLine.statement_id == st.id)):
        if ln.result == "missing":
            continue
        who = "unassigned"
        if ln.policy_id:
            pol = db.get(Policy, ln.policy_id)
            cl = db.get(Client, pol.client_id) if pol else None
            if cl and cl.agent_id:
                u = db.get(User, cl.agent_id)
                who = u.display_name if u else "unassigned"
        per[who] = per.get(who, Decimal(0)) + ln.amount_ils
    return {
        "period": st.period,
        "agents": {k: str(v) for k, v in sorted(per.items())},
        "total": str(sum(per.values(), Decimal(0))),
    }
