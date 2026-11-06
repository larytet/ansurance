"""Entity extraction: validated ID numbers with their OCR confidence, and policy-like tokens."""

import re
import unicodedata
from dataclasses import dataclass

from app.ids import is_valid_israeli_id
from app.intake.ocr import OcrResult, Word


@dataclass(frozen=True)
class IdHit:
    id_number: str
    conf: float  # lowest confidence among the words that make up the number


_ASCII = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")  # Arabic-Indic digits


def _clean(word: str) -> str:
    """Drop invisible format characters (such as the right-to-left mark OCR appends)."""
    return "".join(ch for ch in word if unicodedata.category(ch) != "Cf").strip()


_SINGLE = re.compile(r"^(\d{9}|\d{8}-\d|\d{3}-\d{3}-\d{3})$")
_SHAPES = {(9,), (3, 3, 3), (8, 1)}  # digit counts of the words that may form one ID


def find_ids(words: list[Word]) -> list[IdHit]:
    """Valid 9-digit ID numbers, whole or split into the usual groups (123 456 782, 12345678 2).

    A number is accepted only if the check digit validates, so an OCR slip on a digit is
    caught here instead of filing a document to the wrong person. Only the shapes ID
    numbers really take are accepted, so a policy number such as 2024-77881 or a phone
    number is not mistaken for one.
    """
    hits: dict[str, float] = {}
    clean = [(_clean(w.text).translate(_ASCII), w.conf) for w in words]
    n = len(clean)
    for i in range(n):
        text, conf = clean[i]
        if _SINGLE.match(text):
            digits = text.replace("-", "")
            if is_valid_israeli_id(digits):
                hits[digits] = max(hits.get(digits, 0.0), conf)
            continue
        for size in (2, 3):
            window = clean[i : i + size]
            if len(window) < size or not all(t.isdigit() for t, _ in window):
                continue
            if tuple(len(t) for t, _ in window) not in _SHAPES:
                continue
            digits = "".join(t for t, _ in window)
            if is_valid_israeli_id(digits):
                hits[digits] = max(hits.get(digits, 0.0), min(c for _, c in window))
    return [IdHit(k, v) for k, v in hits.items()]


def policy_tokens(words: list[Word]) -> dict[str, float]:
    """Alphanumeric tokens that could be a policy number, with their confidence."""
    out: dict[str, float] = {}
    for w in words:
        tok = re.sub(r"[^0-9A-Za-z\-/]", "", _clean(w.text).translate(_ASCII)).upper().strip("-/")
        if 5 <= len(tok) <= 24 and any(ch.isdigit() for ch in tok):
            out[tok] = max(out.get(tok, 0.0), w.conf)
    return out


def entities(ocr: OcrResult) -> dict[str, object]:
    ids = find_ids(ocr.words)
    return {
        "ids": [{"id_number": h.id_number, "conf": round(h.conf, 4)} for h in ids],
        "tokens": len(policy_tokens(ocr.words)),
    }
