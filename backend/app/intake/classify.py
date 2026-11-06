"""Rules-based document classification. Every decision stores its confidence and reason."""

from dataclasses import dataclass

from app.text import normalize

# doc type -> (keywords, weight). Hebrew, English, Arabic, Russian and French terms.
RULES: dict[str, list[str]] = {
    "id_card": [
        "תעודת זהות",
        "identity card",
        "israel identity",
        "بطاقة الهوية",
        "удостоверение личности",
        "carte d identite",
        "ת ז",
        "date of birth",
        "תאריך לידה",
    ],
    "id_addendum": ["ספח", "ספח תעודת זהות", "addendum", "id appendix", "ילדים", "children"],
    "driving_licence": ["רישיון נהיגה", "driving licence", "driving license", "permis de conduire"],
    "policy": [
        "פוליסה",
        "policy number",
        "policy no",
        "מספר פוליסה",
        "פוליסת",
        "policy schedule",
        "договор страхования",
        "police d assurance",
    ],
    "application": ["הצעת ביטוח", "proposal form", "application form", "טופס הצעה", "בקשה לביטוח"],
    "claim_form": ["טופס תביעה", "claim form", "תביעה", "מועד האירוע", "date of loss"],
    "medical_report": [
        "דוח רפואי",
        "דו ח רפואי",
        "medical report",
        "אבחנה",
        "diagnosis",
        "סיכום רפואי",
        "אישור רפואי",
        "medical certificate",
    ],
    "invoice": ["חשבונית", "tax invoice", "invoice", "חשבונית מס", "מע מ", "vat"],
    "bank_form": ["הוראת קבע", "direct debit", "iban", "בנק", "bank account", "פרטי חשבון"],
    "power_of_attorney": ["ייפוי כוח", "יפוי כוח", "power of attorney", "procuration"],
    "insurer_letter": ["לכבוד", "dear sir", "dear madam", "בהמשך לפנייתך", "we refer to your"],
    "commission_statement": ["דוח עמלות", "commission statement", "סיכום עמלות", "commissions"],
    "complaint": ["תלונה", "complaint", "plainte", "жалоба"],
}
# These words are much stronger evidence than the rest
STRONG = {
    "תעודת זהות",
    "identity card",
    "ספח",
    "פוליסה",
    "policy number",
    "טופס תביעה",
    "claim form",
    "דוח רפואי",
    "medical report",
    "חשבונית מס",
    "tax invoice",
    "ייפוי כוח",
    "power of attorney",
    "הצעת ביטוח",
    "driving licence",
    "דוח עמלות",
    "commission statement",
}


@dataclass(frozen=True)
class Classification:
    doc_type: str
    confidence: float
    reason: str


def classify(text: str) -> Classification:
    norm = " " + normalize(text) + " "
    scored: list[tuple[float, str, list[str]]] = []
    for doc_type, words in RULES.items():
        hits = [w for w in words if f" {normalize(w)} " in norm or normalize(w) in norm]
        if hits:
            score = sum(2.0 if w in STRONG else 1.0 for w in hits)
            scored.append((score, doc_type, hits))
    if not scored:
        return Classification("other", 0.3, "no keyword matched")
    scored.sort(key=lambda x: (-x[0], x[1]))
    score, doc_type, hits = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    confidence = min(0.99, 0.55 + 0.12 * score) - (0.15 if runner_up >= score else 0.0)
    return Classification(doc_type, round(max(confidence, 0.3), 4), "matched: " + ", ".join(hits))
