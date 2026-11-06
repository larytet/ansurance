"""Attribute an item to a client portfolio, and decide how to route it.

Hard rules (REQUIREMENTS 4.2 and 4.3):
- a document is never filed on a name-only match: only an ID number or a policy number
  found in the document can create a candidate;
- a sender's address or phone is only a tie-breaker between candidates, never a candidate;
- automatic filing needs one unambiguous client and a confidence of at least the threshold.
"""

import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session as Db

from app.intake.extract import IdHit
from app.models import Client, Policy


@dataclass
class Candidate:
    client_id: uuid.UUID
    score: float
    evidence: list[str] = field(default_factory=list)
    sender_match: bool = False
    weak: bool = False  # inexact policy match: may suggest, never file

    def as_json(self) -> dict[str, object]:
        return {
            "client_id": str(self.client_id),
            "score": round(self.score, 4),
            "evidence": self.evidence,
            "sender_match": self.sender_match,
            "weak": self.weak,
        }


@dataclass
class Attribution:
    candidates: list[Candidate]
    chosen: uuid.UUID | None
    confidence: float
    route: str  # filed | review | unassigned
    reason: str
    family_members: list[uuid.UUID] = field(default_factory=list)


def evidence_score(ocr_conf: float) -> float:
    """Confidence that an identifier read from the scan really belongs to the client it matched.

    The identifier already passed a check digit (ID numbers) or matched a stored policy
    number exactly, and a single misread digit cannot do both, so the evidence is strong
    whatever the raw OCR confidence is. The OCR confidence only lowers the score when the
    reading itself was shaky (poor fax, blur): 0.99 at confidence 0.95 and above, 0.94 at 0.70.
    """
    return round(min(0.99, 0.80 + 0.20 * ocr_conf), 4)


def _digits(token: str) -> str:
    return "".join(ch for ch in token if ch.isdigit())


def _sender_matches(c: Client, sender: str) -> bool:
    s = sender.strip().lower()
    if not s:
        return False
    digits = "".join(ch for ch in s if ch.isdigit())
    return bool(
        (c.email and c.email.lower() == s)
        or (
            digits
            and c.phone
            and "".join(ch for ch in c.phone if ch.isdigit()).endswith(digits[-9:])
        )
    )


def attribute(
    db: Db,
    *,
    ids: list[IdHit],
    policy_tokens: dict[str, float],
    doc_type: str,
    sender: str,
    threshold: float,
) -> Attribution:
    policy_tokens = {k.upper(): v for k, v in policy_tokens.items()}
    cands: dict[uuid.UUID, Candidate] = {}

    def add(client_id: uuid.UUID, score: float, why: str) -> None:
        c = cands.setdefault(client_id, Candidate(client_id, 0.0))
        c.score = max(c.score, score)
        c.evidence.append(why)

    clients_by_id = (
        {
            c.id_number: c
            for c in db.scalars(
                select(Client).where(Client.id_number.in_([h.id_number for h in ids]))
            )
        }
        if ids
        else {}
    )
    matched_ids = [(h, clients_by_id[h.id_number]) for h in ids if h.id_number in clients_by_id]
    for h, c in matched_ids:
        add(c.id, evidence_score(h.conf), f"id:{h.id_number}")

    if policy_tokens:
        rows = db.execute(
            select(Policy.client_id, Policy.policy_number).where(
                func.upper(Policy.policy_number).in_(list(policy_tokens))
            )
        ).all()
        for client_id, number in rows:
            add(client_id, evidence_score(policy_tokens[number.upper()]), f"policy:{number}")

        # The insurer prefix is often lost in OCR (PL-2024-77881 read as 2024-77881). Match
        # on the digits alone, but only as a suggestion for the back-office, never to file.
        exact = {n.upper() for _, n in rows}
        by_digits = {_digits(t): c for t, c in policy_tokens.items() if t not in exact}
        by_digits = {d: c for d, c in by_digits.items() if len(d) >= 7}
        if by_digits:
            digits_col = func.regexp_replace(Policy.policy_number, "[^0-9]", "", "g")
            for client_id, number, d in db.execute(
                select(Policy.client_id, Policy.policy_number, digits_col).where(
                    digits_col.in_(list(by_digits))
                )
            ):
                if client_id not in cands or cands[client_id].weak:
                    add(
                        client_id, max(evidence_score(by_digits[d]) - 0.05, 0.5), f"policy~{number}"
                    )
                    cands[client_id].weak = True

    family: list[uuid.UUID] = []
    if doc_type == "id_addendum" and len(matched_ids) > 1:
        # the appendix belongs to the head of family; the others are cross-linked
        head = matched_ids[0][1]
        for _, c in matched_ids[1:]:
            family.append(c.id)
        for cid in list(cands):
            if cid != head.id:
                del cands[cid]

    if not cands:
        why = (
            "no identifier found"
            if not ids and not policy_tokens
            else ("identifiers match no known client")
        )
        return Attribution([], None, 0.0, "unassigned", why)

    if sender:  # weak signal: ranks the candidates, never creates one
        for c in db.scalars(select(Client).where(Client.id.in_(list(cands)))):
            cands[c.id].sender_match = _sender_matches(c, sender)

    ranked = sorted(cands.values(), key=lambda c: (-c.score, not c.sender_match, str(c.client_id)))
    top = ranked[0]
    if len(ranked) > 1:
        return Attribution(
            ranked,
            top.client_id,
            top.score,
            "review",
            f"{len(ranked)} different clients found in the document",
            family,
        )
    if top.weak:
        return Attribution(
            ranked,
            top.client_id,
            top.score,
            "review",
            "policy number matched on its digits only; please confirm",
            family,
        )
    if top.score >= threshold:
        return Attribution(
            ranked,
            top.client_id,
            top.score,
            "filed",
            "single client, identifier read with high confidence",
            family,
        )
    return Attribution(
        ranked,
        top.client_id,
        top.score,
        "review",
        f"single client, confidence {top.score:.2f} below {threshold:.2f}",
        family,
    )
