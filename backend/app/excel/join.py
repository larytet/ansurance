"""Join several spreadsheets on a person, even when the keys are partial (REQUIREMENTS 8.10).

The agency export has the full ID number and the name, an insurer statement may have only the
last 4 digits and the name in Latin letters, the pension clearing house has the full ID and no
name. Rows are joined on the full ID where both sides have it, otherwise on the last 4 digits
plus a fuzzy name. The report says how every row was joined and flags doubtful ones; a match is
never forced: ambiguous rows are reported, not guessed."""

import io
import json
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from openpyxl import Workbook, load_workbook

from app.text import normalize, skeleton, to_latin

SYNONYMS = {
    "id": {"id", "id number", "idnumber", "תעודת זהות", "ת ז", "תז", "מספר זהות", "מס זהות"},
    "last4": {"last4", "last 4", "last four", "4 ספרות אחרונות", "ארבע ספרות אחרונות", "ת ז חלקי"},
    "name": {"name", "full name", "שם", "שם מלא", "שם לקוח", "customer"},
}
MIN_NAME_SIMILARITY = 0.75
AMBIGUITY_MARGIN = 0.10


class JoinError(ValueError):
    pass


@dataclass
class Sheet:
    label: str
    headers: list[str]
    rows: list[dict[str, Any]]
    id_col: str | None = None
    last4_col: str | None = None
    name_col: str | None = None


def _find(headers: list[str], kind: str) -> str | None:
    for h in headers:
        if normalize(str(h)) in {normalize(x) for x in SYNONYMS[kind]}:
            return h
    return None


def read_sheet(label: str, data: bytes) -> Sheet:
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:  # noqa: BLE001  (openpyxl raises many kinds on a bad file)
        raise JoinError(f"{label}: not a readable .xlsx file") from e
    ws = wb.active
    if ws is None:
        raise JoinError("workbook has no sheet")
    if ws is None:
        raise JoinError(f"{label}: the workbook has no sheet")
    it = ws.iter_rows(values_only=True)
    first = next(it, None)
    if first is None:
        raise JoinError(f"{label}: the sheet is empty")
    headers = [str(h).strip() if h is not None else f"column{i + 1}" for i, h in enumerate(first)]
    rows = [dict(zip(headers, r, strict=False)) for r in it if any(v is not None for v in r)]
    sheet = Sheet(
        label, headers, rows, _find(headers, "id"), _find(headers, "last4"), _find(headers, "name")
    )
    if sheet.id_col is None and sheet.last4_col is None:
        raise JoinError(f"{label}: no ID or last-4 column found (headers: {', '.join(headers)})")
    return sheet


def _digits(v: Any) -> str:
    return "".join(ch for ch in str(v if v is not None else "") if ch.isdigit())


def full_id(v: Any) -> str | None:
    d = _digits(v)
    return d.zfill(9) if 5 <= len(d) <= 9 else None


def last4(sheet: Sheet, row: dict[str, Any]) -> str | None:
    if sheet.last4_col:
        d = _digits(row.get(sheet.last4_col))
        return d[-4:].zfill(4) if d else None
    fid = full_id(row.get(sheet.id_col)) if sheet.id_col else None
    return fid[-4:] if fid else None


def similarity(a: str, b: str) -> float:
    """How alike two names are, across scripts: the better of the consonant skeletons and the
    Latin renderings."""
    if not a or not b:
        return 0.0
    sk = (
        SequenceMatcher(None, skeleton(a), skeleton(b)).ratio()
        if skeleton(a) and skeleton(b)
        else 0
    )
    lat = SequenceMatcher(None, to_latin(a), to_latin(b)).ratio()
    return max(sk, lat)


def script(name: str) -> str:
    for ch in name:
        if "\u0590" <= ch <= "\u05ff":
            return "he"
        if "\u0600" <= ch <= "\u06ff":
            return "ar"
        if "\u0400" <= ch <= "\u04ff":
            return "cyr"
    return "lat"


def spelled_differently(a: str, b: str) -> bool:
    """Same script, different spelling (David Cohen / Dawid Kohen): worth a look."""
    return script(a) == script(b) and normalize(a) != normalize(b)


@dataclass
class Match:
    base: dict[str, Any] | None
    method: str  # id | last4+name | last4 | none | ambiguous
    flags: list[str] = field(default_factory=list)
    detail: str = ""


def match_row(base: Sheet, other: Sheet, row: dict[str, Any]) -> Match:
    name = str(row.get(other.name_col, "") or "") if other.name_col else ""
    fid = full_id(row.get(other.id_col)) if other.id_col else None
    if fid and base.id_col:
        hits = [b for b in base.rows if full_id(b.get(base.id_col)) == fid]
        if len(hits) == 1:
            m = Match(hits[0], "id")
            bname = str(hits[0].get(base.name_col, "") or "") if base.name_col else ""
            if name and bname and similarity(name, bname) < MIN_NAME_SIMILARITY:
                m.flags.append("name_differs")
            elif name and bname and spelled_differently(name, bname):
                m.flags.append("name_spelled_differently")
            return m
        if len(hits) > 1:
            return Match(None, "ambiguous", detail=f"{len(hits)} rows share ID {fid}")
        return Match(None, "none", detail="ID not in the agency export")
    l4 = last4(other, row)
    if not l4 or not base.id_col:
        return Match(None, "none", detail="no usable key")
    cands = [b for b in base.rows if (full_id(b.get(base.id_col)) or "")[-4:] == l4]
    if not cands:
        return Match(None, "none", detail=f"no agency row ends in {l4}")
    if not name or not base.name_col:
        if len(cands) == 1:
            return Match(cands[0], "last4", ["weak_match"])
        return Match(None, "ambiguous", detail=f"{len(cands)} rows end in {l4}, no name to choose")
    scored = sorted(
        ((similarity(name, str(c.get(base.name_col, "") or "")), i) for i, c in enumerate(cands)),
        reverse=True,
    )
    best, idx = scored[0]
    if best < MIN_NAME_SIMILARITY:
        return Match(None, "none", detail=f"ends in {l4} but no similar name (best {best:.2f})")
    if len(scored) > 1 and best - scored[1][0] < AMBIGUITY_MARGIN:
        return Match(None, "ambiguous", detail=f"{len(scored)} similar names end in {l4}")
    m = Match(cands[idx], "last4+name")
    if spelled_differently(name, str(cands[idx].get(base.name_col, "") or "")):
        m.flags.append("name_spelled_differently")
    return m


@dataclass
class JoinResult:
    headers: list[str]
    rows: list[list[Any]]
    unmatched: list[list[Any]]
    summary: dict[str, Any]


def join(base: Sheet, others: list[Sheet]) -> JoinResult:
    labels = [o.label for o in others]
    base_cols = [f"{base.label}.{h}" for h in base.headers]
    other_cols = [f"{o.label}.{h}" for o in others for h in o.headers]
    headers = base_cols + other_cols + [f"join_{x}" for x in labels] + ["flags"]
    pairs: dict[int, list[tuple[int, dict[str, Any], Match]]] = {
        i: [] for i in range(len(base.rows))
    }
    unmatched: list[list[Any]] = []
    methods: dict[str, dict[str, int]] = {x: {} for x in labels}
    for oi, o in enumerate(others):
        for row in o.rows:
            m = match_row(base, o, row)
            methods[o.label][m.method] = methods[o.label].get(m.method, 0) + 1
            if m.base is None:
                unmatched.append(
                    [
                        o.label,
                        m.method,
                        m.detail,
                        json.dumps({k: str(v) for k, v in row.items()}, ensure_ascii=False),
                    ]
                )
                continue
            pairs[base.rows.index(m.base)].append((oi, row, m))
    out: list[list[Any]] = []
    for bi, b in enumerate(base.rows):
        base_vals = [b.get(h) for h in base.headers]
        matched = pairs[bi]
        if not matched:
            blank = [None] * len(other_cols)
            out.append(base_vals + blank + ["" for _ in labels] + ["no_match_in_any"])
            continue
        # one output row per matched row of the other sheets (several policies, several files)
        for oi, row, m in matched:
            vals: list[Any] = []
            for k, o in enumerate(others):
                vals += [row.get(h) if k == oi else None for h in o.headers]
            joins = [m.method if k == oi else "" for k in range(len(others))]
            out.append(base_vals + vals + joins + [",".join(m.flags)])
    total = {x: len(o.rows) for x, o in zip(labels, others, strict=True)}
    summary = {
        "base_rows": len(base.rows),
        "other_rows": total,
        "joined_rows": len(out),
        "methods": methods,
        "unmatched": len(unmatched),
        "flagged": sum(1 for r in out if r[-1]),
    }
    return JoinResult(headers, out, unmatched, summary)


def to_xlsx(res: JoinResult) -> bytes:
    wb = Workbook()
    ws = wb.active
    if ws is None:
        raise JoinError("workbook has no sheet")
    ws.title = "Joined"
    ws.append(res.headers)
    for r in res.rows:
        ws.append(r)
    un = wb.create_sheet("Unmatched")
    un.append(["source", "reason", "detail", "row"])
    for r in res.unmatched:
        un.append(r)
    sm = wb.create_sheet("Summary")
    sm.append(["item", "value"])
    for k, v in res.summary.items():
        sm.append([k, json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else v])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def rows_to_xlsx(title: str, headers: list[str], rows: list[list[Any]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    if ws is None:
        raise JoinError("workbook has no sheet")
    ws.title = title
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
