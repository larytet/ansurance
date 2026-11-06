import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as Db

from app.audit import chain
from app.clients.access import get_visible
from app.config import Settings
from app.deps import Principal, get_db, get_settings_dep, require
from app.integrations.calls import logged_call, require_approved
from app.models import TranslationRecord

router = APIRouter(prefix="/api")
LANGS = ("he", "ar", "ru", "fr", "en")
MARK = "machine translation, may contain errors"
# amounts, dates and coverage words are where a wrong translation does harm: flagged for a person
PATTERNS = {
    "amount": re.compile(
        r"(?:₪|NIS|ILS|ש\"ח|\$|€)\s?\d[\d,.]*|\d[\d,.]*\s?(?:₪|NIS|ILS|ש\"ח|שקל|%)", re.IGNORECASE
    ),
    "date": re.compile(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b"),
    "coverage": re.compile(
        r"\b(coverage|exclusion|deductible|beneficiary|premium|liability)\b"
        r"|כיסוי|החרגה|השתתפות עצמית|מוטב|פרמיה|תביעה",
        re.IGNORECASE,
    ),
}


def flag_passages(text: str) -> list[str]:
    return sorted({k for k, rx in PATTERNS.items() if rx.search(text)})


class TranslateIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    source: str
    target: str
    client_id: uuid.UUID | None = None


@router.post("/translate", status_code=201)
def translate(
    request: Request,
    body: TranslateIn,
    p: Principal = Depends(require("inbox.read")),
    db: Db = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> dict[str, Any]:
    if body.source not in LANGS or body.target not in LANGS or body.source == body.target:
        raise HTTPException(422, "invalid_languages")
    client = get_visible(db, p, body.client_id) if body.client_id else None
    tr = getattr(request.app.state, "translator", None)
    if tr is None:
        raise HTTPException(503, "translation_not_configured")
    require_approved(settings, "translate")
    try:
        out = logged_call(
            request.app.state.sessionmaker,
            "translate",
            "translate",
            f"{body.source}>{body.target}",
            lambda: tr.translate(body.text, body.source, body.target),
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, "translation_provider_unavailable") from e
    flagged = flag_passages(body.text)
    rec = TranslationRecord(
        client_id=client.id if client else None,
        source_lang=body.source,
        target_lang=body.target,
        original=body.text,
        translated=out,
        flagged=flagged,
        requested_by=p.user.id,
    )
    db.add(rec)
    db.flush()
    chain.append(db, str(p.user.id), "translate", f"translation:{rec.id}", {"flagged": flagged})
    return {"id": str(rec.id), "translated": out, "notice": MARK, "needs_human_check": flagged}


@router.get("/clients/{client_id}/translations")
def timeline(
    client_id: uuid.UUID, p: Principal = Depends(require("client.read")), db: Db = Depends(get_db)
) -> list[dict[str, Any]]:
    c = get_visible(db, p, client_id)
    rows = db.scalars(
        select(TranslationRecord)
        .where(TranslationRecord.client_id == c.id)
        .order_by(TranslationRecord.created_at)
    )
    return [
        {
            "id": str(r.id),
            "source": r.source_lang,
            "target": r.target_lang,
            "original": r.original,
            "translated": r.translated,
            "needs_human_check": r.flagged,
            "notice": MARK,
            "at": r.created_at.isoformat(),
        }
        for r in rows
    ]
