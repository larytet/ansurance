import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session as Db

from app.audit import chain
from app.deps import Principal, get_db, require
from app.excel import join as xl
from app.integrations import agency, agency_sync
from app.models import Client, Policy

router = APIRouter(prefix="/api")
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_BYTES = 20 * 1024 * 1024


def _read(f: UploadFile) -> bytes:
    data = f.file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "file_too_large")
    return data


@router.post("/import/agency")
def import_agency(
    clients: UploadFile = File(...),
    policies: UploadFile | None = File(None),
    dry_run: bool = Form(False),
    p: Principal = Depends(require("import.run")),
    db: Db = Depends(get_db),
) -> dict[str, Any]:
    """One-time or repeated import from the existing system's CSV export (REQUIREMENTS 9.1)."""
    try:
        ext_clients = agency.parse_clients_csv(_read(clients).decode("utf-8-sig"))
        ext_policies = (
            agency.parse_policies_csv(_read(policies).decode("utf-8-sig")) if policies else []
        )
    except (agency.CsvError, UnicodeDecodeError) as e:
        raise HTTPException(422, f"bad_csv: {e}") from e
    ad = agency.FakeAgencySystem()  # only supplies the system name "csv" for the links
    ad.name = "csv"
    rep = agency_sync.run_sync(db, ad, ext_clients, ext_policies, dry_run, actor=str(p.user.id))
    return {"dry_run": dry_run, **rep.as_json()}


@router.post("/excel/join")
def excel_join(
    agency_file: UploadFile = File(..., alias="agency"),
    insurer: UploadFile | None = File(None),
    clearing: UploadFile | None = File(None),
    p: Principal = Depends(require("excel.use")),
    db: Db = Depends(get_db),
) -> Response:
    others = []
    try:
        base = xl.read_sheet("agency", _read(agency_file))
        for label, f in (("insurer", insurer), ("clearing", clearing)):
            if f is not None:
                others.append(xl.read_sheet(label, _read(f)))
        if not others:
            raise xl.JoinError("give at least one more spreadsheet to join with")
        res = xl.join(base, others)
    except xl.JoinError as e:
        raise HTTPException(422, str(e)) from e
    chain.append(db, str(p.user.id), "excel.join", "excel", {"summary": res.summary})
    return Response(
        xl.to_xlsx(res),
        media_type=XLSX,
        headers={
            "content-disposition": 'attachment; filename="joined.xlsx"',
            "x-join-summary": json.dumps(res.summary, ensure_ascii=True),
        },
    )


@router.get("/export/clients.xlsx")
def export_clients(
    p: Principal = Depends(require("export.run")), db: Db = Depends(get_db)
) -> Response:
    """A client list for Excel. An export leaves the system, so it is audited at once."""
    counts = dict(
        db.execute(select(Policy.client_id, func.count()).group_by(Policy.client_id)).all()
    )
    rows = [
        [
            c.id_number,
            c.name,
            c.name_translit_lat,
            c.preferred_language,
            c.phone,
            c.email,
            c.status,
            counts.get(c.id, 0),
        ]
        for c in db.scalars(select(Client).order_by(Client.name))
    ]
    chain.append(db, str(p.user.id), "export.clients", "clients", {"rows": len(rows)})
    data = xl.rows_to_xlsx(
        "Clients",
        ["id_number", "name", "name_latin", "language", "phone", "email", "status", "policies"],
        rows,
    )
    return Response(
        data,
        media_type=XLSX,
        headers={"content-disposition": 'attachment; filename="clients.xlsx"'},
    )


@router.get("/clients/{client_id}/messages")
def client_messages(
    client_id: uuid.UUID,
    p: Principal = Depends(require("client.read")),
    db: Db = Depends(get_db),
) -> list[dict[str, Any]]:
    """The conversation with a client, newest first: what was sent (and whether the consent gate
    blocked it) and what the client sent."""
    from app.clients.access import get_visible
    from app.models import InboundMessage, OutboxMessage

    c = get_visible(db, p, client_id)
    out: list[dict[str, Any]] = [
        {
            "direction": "out",
            "channel": m.channel,
            "purpose": m.purpose,
            "status": m.status,
            "block_reason": m.block_reason,
            "delivery": m.delivery,
            "body": m.body,
            "at": m.created_at.isoformat(),
        }
        for m in db.scalars(select(OutboxMessage).where(OutboxMessage.client_id == c.id).limit(200))
    ]
    out += [
        {
            "direction": "in",
            "channel": m.channel,
            "kind": m.kind,
            "body": m.body,
            "at": m.received_at.isoformat(),
        }
        for m in db.scalars(
            select(InboundMessage).where(InboundMessage.client_id == c.id).limit(200)
        )
    ]
    out.sort(key=lambda r: str(r["at"]), reverse=True)
    return out[:200]


@router.get("/integrations/status")
def integrations_status(
    request: Request,
    p: Principal = Depends(require("import.run")),
    db: Db = Depends(get_db),
) -> dict[str, Any]:
    """Which outside systems are configured and approved, and how their calls went lately.
    No secrets and no message content."""
    from datetime import UTC, datetime, timedelta

    from app.config import Settings
    from app.models import AdapterCall, SyncState

    settings: Settings = request.app.state.settings
    since = datetime.now(UTC) - timedelta(hours=24)
    configured = {
        "whatsapp": request.app.state.whatsapp is not None,
        "imap": bool(settings.imap_host and settings.imap_user),
        "esign": request.app.state.esign is not None,
        "translate": request.app.state.translator is not None,
        "agency": True,  # file import needs no credentials
    }
    rows = db.execute(
        select(AdapterCall.adapter, AdapterCall.ok, func.count())
        .where(AdapterCall.at >= since)
        .group_by(AdapterCall.adapter, AdapterCall.ok)
    ).all()
    calls: dict[str, dict[str, int]] = {}
    for adapter, ok, n in rows:
        calls.setdefault(adapter, {"ok": 0, "failed": 0})["ok" if ok else "failed"] = n
    last_error = {
        a: e
        for a, e in db.execute(
            select(AdapterCall.adapter, AdapterCall.error)
            .where(AdapterCall.ok.is_(False))
            .order_by(AdapterCall.at.desc())
            .limit(50)
        ).all()
    }
    return {
        "adapters": [
            {
                "name": n,
                "configured": configured[n],
                "approved": n in settings.approved_adapters,
                "calls_24h": calls.get(n, {"ok": 0, "failed": 0}),
                "last_error": last_error.get(n),
            }
            for n in configured
        ],
        "syncs": [
            {"system": s.system, "last_run": s.last_run.isoformat() if s.last_run else None}
            for s in db.scalars(select(SyncState))
        ],
    }
