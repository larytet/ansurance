import io
import uuid
from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from openpyxl import Workbook
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session as Db

from app.audit import chain
from app.clients.access import get_visible
from app.deps import Principal, get_db, require
from app.models import TaxCertificate, TaxDeadline
from app.tax import service

router = APIRouter(prefix="/api/tax")


def _fail(e: service.TaxError) -> HTTPException:
    return HTTPException(e.status, {"error": e.code, "detail": e.detail} if e.detail else e.code)


class YearIn(BaseModel):
    tax_year: int


@router.post("/certificates/generate", status_code=201)
def generate(
    body: YearIn,
    p: Principal = Depends(require("tax.write")),
    db: Db = Depends(get_db, scope="function"),
) -> dict[str, int]:
    return {"created": service.generate(db, p.user.id, body.tax_year)}


@router.post("/certificates/request-all")
def request_all(
    body: YearIn,
    p: Principal = Depends(require("tax.write")),
    db: Db = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    per = service.request_all(db, p.user.id, body.tax_year)
    return {"requested": sum(per.values()), "letters_per_insurer": per}


@router.post("/certificates/send-all")
def send_all(
    body: YearIn,
    p: Principal = Depends(require("tax.write")),
    db: Db = Depends(get_db, scope="function"),
) -> dict[str, int]:
    return service.send_all(db, p.user.id, body.tax_year)


@router.get("/certificates")
def matrix(
    tax_year: int,
    p: Principal = Depends(require("tax.read")),
    db: Db = Depends(get_db, scope="function"),
) -> list[dict[str, Any]]:
    return service.status_matrix(db, tax_year)


class ReceivedIn(BaseModel):
    document_id: uuid.UUID | None = None


@router.put("/certificates/{cert_id}/received")
def received(
    cert_id: uuid.UUID,
    body: ReceivedIn,
    p: Principal = Depends(require("tax.write")),
    db: Db = Depends(get_db, scope="function"),
) -> dict[str, str]:
    cert = db.get(TaxCertificate, cert_id)
    if cert is None:
        raise HTTPException(404, "not_found")
    try:
        service.mark_received(db, p.user.id, cert, body.document_id)
    except service.TaxError as e:
        raise _fail(e) from e
    return {"status": cert.status}


class ResidencyIn(BaseModel):
    residency: str
    form_received: bool = False


@router.put("/residency/{client_id}")
def residency(
    client_id: uuid.UUID,
    body: ResidencyIn,
    p: Principal = Depends(require("tax.write")),
    db: Db = Depends(get_db, scope="function"),
) -> dict[str, str]:
    c = get_visible(db, p, client_id)
    try:
        service.set_residency(db, p.user.id, c, body.residency, body.form_received)
    except service.TaxError as e:
        raise _fail(e) from e
    return {"status": "saved"}


@router.get("/residency/gaps")
def gaps(
    p: Principal = Depends(require("tax.write")), db: Db = Depends(get_db, scope="function")
) -> list[dict[str, Any]]:
    return service.residency_gaps(db)


class DeadlineIn(BaseModel):
    kind: str
    title: str
    due_on: date


@router.post("/deadlines", status_code=201)
def add_deadline(
    body: DeadlineIn,
    p: Principal = Depends(require("tax.write")),
    db: Db = Depends(get_db, scope="function"),
) -> dict[str, str]:
    try:
        d = service.add_deadline(db, p.user.id, body.kind, body.title, body.due_on)
    except service.TaxError as e:
        raise _fail(e) from e
    return {"id": str(d.id)}


@router.get("/deadlines")
def deadlines(
    p: Principal = Depends(require("tax.read")), db: Db = Depends(get_db, scope="function")
) -> list[dict[str, Any]]:
    return [
        {
            "id": str(d.id),
            "kind": d.kind,
            "title": d.title,
            "due_on": d.due_on.isoformat(),
            "done": d.done_at is not None,
        }
        for d in db.scalars(select(TaxDeadline).order_by(TaxDeadline.due_on))
    ]


@router.put("/deadlines/{did}/done")
def done(
    did: uuid.UUID,
    p: Principal = Depends(require("tax.write")),
    db: Db = Depends(get_db, scope="function"),
) -> dict[str, str]:
    d = db.get(TaxDeadline, did)
    if d is None:
        raise HTTPException(404, "not_found")
    d.done_at, d.done_by = datetime.now(UTC), p.user.id
    chain.append(db, str(p.user.id), "tax.deadline_done", f"deadline:{d.id}", {})
    return {"status": "done"}


@router.get("/package.xlsx")
def package(
    tax_year: int,
    p: Principal = Depends(require("tax.read")),
    db: Db = Depends(get_db, scope="function"),
) -> Response:
    """The read-only yearly package for the accountant: no personal contact data, no documents."""
    wb = Workbook()
    ws = wb.active
    if ws is None:
        raise HTTPException(500, "workbook")
    ws.title = "Certificates"
    ws.append(["client", "product", "policy_number", "status", "requested", "received", "sent"])
    for r in service.status_matrix(db, tax_year):
        ws.append(
            [
                r["client"],
                r["product"],
                r["policy_number"],
                r["status"],
                r["requested"],
                r["received"],
                r["sent"],
            ]
        )
    d = wb.create_sheet("Deadlines")
    d.append(["kind", "title", "due_on", "done"])
    for x in db.scalars(select(TaxDeadline).order_by(TaxDeadline.due_on)):
        d.append([x.kind, x.title, x.due_on.isoformat(), x.done_at is not None])
    buf = io.BytesIO()
    wb.save(buf)
    chain.append(db, str(p.user.id), "export.tax_package", f"year:{tax_year}", {})
    return Response(
        buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
