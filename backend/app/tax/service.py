"""F15: the agency supplies documents and keeps dates; tax advice and filing stay with the
client or their accountant."""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as Db

from app.audit import chain
from app.config import Settings
from app.jobs import queue
from app.messaging import gate
from app.models import (
    Client,
    Document,
    InboxItem,
    Insurer,
    OutboxMessage,
    Policy,
    TaxCertificate,
    TaxDeadline,
    TaxResidency,
)

CERT_PRODUCTS = ("pension", "life", "provident", "gemel", "study_fund")  # deposits earn a credit
DEADLINE_KINDS = (
    "commission_invoice",
    "withholding_cert",
    "vat_report",
    "accountant_package",
    "other",
)
RESIDENCY = ("israel", "us", "foreign")


class TaxError(Exception):
    def __init__(self, code: str, status: int = 409, detail: Any = None) -> None:
        super().__init__(code)
        self.code, self.status, self.detail = code, status, detail


def generate(db: Db, actor: uuid.UUID, year: int) -> int:
    """One row per active policy that earns deposit credit. Safe to repeat."""
    made = 0
    for pol in db.scalars(
        select(Policy).where(Policy.status == "active", Policy.product.in_(CERT_PRODUCTS))
    ):
        if (
            db.scalar(
                select(TaxCertificate.id).where(
                    TaxCertificate.policy_id == pol.id, TaxCertificate.tax_year == year
                )
            )
            is None
        ):
            db.add(TaxCertificate(client_id=pol.client_id, policy_id=pol.id, tax_year=year))
            made += 1
    chain.append(db, str(actor), "tax.generate", f"year:{year}", {"created": made})
    return made


def request_all(db: Db, actor: uuid.UUID, year: int, now: datetime | None = None) -> dict[str, int]:
    """Request every missing certificate; one dispatch item per insurer holds the letters (F13)."""
    now = now or datetime.now(UTC)
    per: dict[str, int] = {}
    for cert in db.scalars(
        select(TaxCertificate).where(
            TaxCertificate.tax_year == year, TaxCertificate.status == "to_request"
        )
    ):
        cert.status, cert.requested_at = "requested", now
        pol = db.get(Policy, cert.policy_id)
        ins = db.get(Insurer, pol.insurer_id) if pol else None
        per[ins.name if ins else "?"] = per.get(ins.name if ins else "?", 0) + 1
    for name, n in per.items():
        db.add(
            InboxItem(
                type="dispatch",
                role="backoffice",
                urgency="normal",
                created_by=actor,
                title=f"Send {n} tax-certificate request letters to {name} ({year})",
            )
        )
    chain.append(
        db, str(actor), "tax.request_all", f"year:{year}", {"requested": sum(per.values())}
    )
    return per


def mark_received(
    db: Db,
    actor: uuid.UUID,
    cert: TaxCertificate,
    document_id: uuid.UUID | None,
    now: datetime | None = None,
) -> None:
    if cert.status not in ("requested", "to_request"):
        raise TaxError(f"certificate_is_{cert.status}", 409)
    if document_id is not None:
        d = db.get(Document, document_id)
        if d is None or d.client_id != cert.client_id:
            raise TaxError("document_not_in_client_file", 422)
    cert.status, cert.received_at, cert.document_id = (
        "received",
        now or datetime.now(UTC),
        document_id,
    )
    chain.append(db, str(actor), "tax.received", f"certificate:{cert.id}", {})


def send_all(db: Db, actor: uuid.UUID, year: int, now: datetime | None = None) -> dict[str, int]:
    """Queue a service message per client whose certificates arrived; the consent gate decides."""
    now = now or datetime.now(UTC)
    done = blocked = 0
    by_client: dict[uuid.UUID, list[TaxCertificate]] = {}
    for cert in db.scalars(
        select(TaxCertificate).where(
            TaxCertificate.tax_year == year, TaxCertificate.status == "received"
        )
    ):
        by_client.setdefault(cert.client_id, []).append(cert)
    for cid, certs in by_client.items():
        cl = db.get(Client, cid)
        if cl is None:
            continue
        sent = False
        for ch in ("whatsapp", "email"):
            d = gate.decide(db, cid, ch, "service")
            if d.allowed and (cl.phone if ch == "whatsapp" else cl.email):
                out = OutboxMessage(
                    client_id=cid,
                    channel=ch,
                    purpose="service",
                    language=cl.preferred_language,
                    status="queued",
                    created_by=actor,
                    body=f"Your {year} tax certificates are ready ({len(certs)}).",
                )
                db.add(out)
                db.flush()
                queue.enqueue(
                    db,
                    "message.deliver",
                    {"message_id": str(out.id)},
                    dedupe_key=f"deliver:{out.id}",
                )
                sent = True
                break
        if not sent:
            blocked += 1
            continue
        for c in certs:
            c.status, c.sent_at = "sent", now
        done += len(certs)
    chain.append(
        db, str(actor), "tax.send_all", f"year:{year}", {"sent": done, "blocked_clients": blocked}
    )
    return {"sent": done, "clients_blocked": blocked}


def status_matrix(db: Db, year: int) -> list[dict[str, Any]]:
    rows = db.execute(
        select(TaxCertificate, Client.name, Policy.product, Policy.policy_number)
        .join(Client, Client.id == TaxCertificate.client_id)
        .join(Policy, Policy.id == TaxCertificate.policy_id)
        .where(TaxCertificate.tax_year == year)
        .order_by(Client.name, Policy.product)
    )
    return [
        {
            "id": str(c.id),
            "client": n,
            "product": pr,
            "policy_number": pn,
            "status": c.status,
            "requested": c.requested_at is not None,
            "received": c.received_at is not None,
            "sent": c.sent_at is not None,
        }
        for c, n, pr, pn in rows
    ]


def set_residency(
    db: Db, actor: uuid.UUID, client: Client, residency: str, form_received: bool
) -> None:
    if residency not in RESIDENCY:
        raise TaxError("invalid_residency", 422)
    row = db.get(TaxResidency, client.id)
    if row is None:
        db.add(TaxResidency(client_id=client.id, residency=residency, form_received=form_received))
    else:
        row.residency, row.form_received = residency, form_received
    chain.append(
        db,
        str(actor),
        "tax.residency",
        f"client:{client.id}",
        {"residency": residency, "form": form_received},
    )


def residency_gaps(db: Db) -> list[dict[str, Any]]:
    """No declaration at all, or a foreign residency without the extra form: 'send form'."""
    out = []
    for cl in db.scalars(
        select(Client).where(Client.status.in_(("registered", "active"))).order_by(Client.name)
    ):
        r = db.get(TaxResidency, cl.id)
        if r is None:
            out.append({"client_id": str(cl.id), "client": cl.name, "gap": "no_declaration"})
        elif r.residency != "israel" and not r.form_received:
            out.append(
                {
                    "client_id": str(cl.id),
                    "client": cl.name,
                    "gap": "foreign_residency_without_form",
                }
            )
    return out


def add_deadline(db: Db, actor: uuid.UUID, kind: str, title: str, due_on: date) -> TaxDeadline:
    if kind not in DEADLINE_KINDS:
        raise TaxError("invalid_kind", 422)
    d = TaxDeadline(kind=kind, title=title, due_on=due_on)
    db.add(d)
    db.flush()
    chain.append(db, str(actor), "tax.deadline", f"deadline:{d.id}", {"due_on": due_on.isoformat()})
    return d


def tick(db: Db, settings: Settings, now: datetime | None = None) -> dict[str, int]:
    """Daily: agency deadlines within the warning window or overdue become a manager item (once);
    certificates requested long ago and still missing become a back-office item (once)."""
    now = now or datetime.now(UTC)
    today = now.date()
    dl = cert = 0
    for d in db.scalars(
        select(TaxDeadline).where(
            TaxDeadline.done_at.is_(None),
            TaxDeadline.due_on <= today + timedelta(days=settings.tax_deadline_warn_days),
        )
    ):
        title = (
            f"Tax deadline {'PASSED' if d.due_on < today else 'due'} {d.due_on}: {d.title} [{d.id}]"
        )
        if (
            db.scalar(
                select(InboxItem.id).where(
                    InboxItem.type == "tax_deadline", InboxItem.title == title
                )
            )
            is None
        ):
            db.add(
                InboxItem(
                    type="tax_deadline",
                    role="manager",
                    urgency="urgent" if d.due_on <= today else "important",
                    title=title,
                )
            )
            dl += 1
    limit = now - timedelta(days=settings.tax_cert_wait_days)
    for c in db.scalars(
        select(TaxCertificate).where(
            TaxCertificate.status == "requested", TaxCertificate.requested_at < limit
        )
    ):
        title = f"Tax certificate not received after {settings.tax_cert_wait_days} days [{c.id}]"
        if (
            db.scalar(
                select(InboxItem.id).where(
                    InboxItem.type == "tax_missing", InboxItem.title == title
                )
            )
            is None
        ):
            db.add(
                InboxItem(
                    type="tax_missing",
                    role="backoffice",
                    client_id=c.client_id,
                    urgency="important",
                    title=title,
                )
            )
            cert += 1
    return {"deadline_items": dl, "certificate_items": cert}
