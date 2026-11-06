"""Pure intake logic: extraction, classification, attribution rules, page handling."""

import pytest
from sqlalchemy.orm import Session

from app.intake import digitize
from app.intake.attribute import attribute, evidence_score
from app.intake.classify import classify
from app.intake.extract import IdHit, find_ids, policy_tokens
from app.intake.ocr import Word, result_from_text
from app.models import Client, Insurer, Policy
from app.text import build_search
from tests.intake_data import samples as S


def W(*pairs: tuple[str, float]) -> list[Word]:
    return [Word(t, c, 1) for t, c in pairs]


# ---- extraction ---------------------------------------------------------------------------
def test_find_ids_whole_split_and_with_invisible_marks() -> None:
    assert [h.id_number for h in find_ids(W(("123456782", 0.9)))] == ["123456782"]
    assert [h.id_number for h in find_ids(W(("123", 0.9), ("456", 0.8), ("782", 0.95)))] == [
        "123456782"
    ]
    assert [h.id_number for h in find_ids(W(("123-456-782", 0.9)))] == ["123456782"]
    marked = find_ids(W(("123456782‏", 0.9)))  # OCR appends a right-to-left mark
    assert [h.id_number for h in marked] == ["123456782"]


def test_find_ids_confidence_is_the_weakest_word() -> None:
    (hit,) = find_ids(W(("123", 0.99), ("456", 0.55), ("782", 0.97)))
    assert hit.conf == 0.55


@pytest.mark.parametrize(
    "words",
    [
        W(("123456789", 0.99)),  # fails the check digit: a misread digit is caught here
        W(("12345678", 0.99)),
        W(("1234567890", 0.99)),
        W(("123456782x", 0.99)),
        W(("ID123456782", 0.99)),
        W(("123", 0.9), ("abc", 0.9), ("456782", 0.9)),
    ],
)
def test_find_ids_rejects_non_ids(words: list[Word]) -> None:
    assert find_ids(words) == []


def test_find_ids_reads_arabic_indic_digits_and_dedupes() -> None:
    hits = find_ids(W(("١٢٣٤٥٦٧٨٢", 0.9), ("123456782", 0.8)))
    assert len(hits) == 1 and hits[0].conf == 0.9


def test_policy_tokens() -> None:
    toks = policy_tokens(W(("PL-2024-77881", 0.9), ("abc", 0.9), ("12345", 0.8), ("תעודה", 0.9)))
    assert toks == {"PL-2024-77881": 0.9, "12345": 0.8}


# ---- classification -----------------------------------------------------------------------
@pytest.mark.parametrize(
    "text,expected",
    [
        ("תעודת זהות מדינת ישראל", "id_card"),
        ("Israel Identity Card", "id_card"),
        ("ספח תעודת זהות ילדים", "id_addendum"),
        ("פוליסה מספר פוליסה 12345", "policy"),
        ("Policy Number PL-1", "policy"),
        ("טופס תביעה Claim form", "claim_form"),
        ("דוח רפואי אבחנה", "medical_report"),
        ("חשבונית מס Tax invoice", "invoice"),
        ("ייפוי כוח Power of Attorney", "power_of_attorney"),
        ("הוראת קבע", "bank_form"),
        ("דוח עמלות Commission statement", "commission_statement"),
        ("שלום וברכה", "other"),
        ("", "other"),
    ],
)
def test_classification(text: str, expected: str) -> None:
    c = classify(text)
    assert c.doc_type == expected and 0 < c.confidence <= 0.99 and c.reason


def test_classification_confidence_reflects_the_evidence() -> None:
    strong = classify("תעודת זהות identity card")
    none = classify("hello")
    assert strong.confidence > 0.9 > none.confidence


# ---- the confidence rule ------------------------------------------------------------------
def test_evidence_score_is_high_for_a_checked_match_and_drops_with_poor_ocr() -> None:
    assert evidence_score(0.95) >= 0.95 and evidence_score(1.0) == 0.99
    assert evidence_score(0.75) >= 0.95
    assert evidence_score(0.62) < 0.95 and evidence_score(0.0) == 0.8


# ---- attribution ---------------------------------------------------------------------------
@pytest.fixture
def people(db: Session) -> dict[str, Client]:
    out = {}
    for key, id_number, name, phone, email in [
        ("david", "123456782", "David Cohen", "050-111-2222", "david@x.il"),
        ("noa", "000000026", "Noa Levi", "052-333-4444", "noa@x.il"),
        ("kid", "000000034", "Kid Cohen", None, None),
    ]:
        plain, skel = build_search(name, id_number)
        c = Client(
            id_number=id_number,
            name=name,
            search_text=plain,
            search_skel=skel,
            phone=phone,
            email=email,
        )
        db.add(c)
        out[key] = c
    db.flush()
    ins = Insurer(name="Harel")
    db.add(ins)
    db.flush()
    db.add(
        Policy(
            client_id=out["noa"].id,
            insurer_id=ins.id,
            policy_number="PL-2024-77881",
            product="car",
            premium_ils=100,
        )
    )
    db.commit()
    return out


def att(
    db: Session,
    ids: list[IdHit] | None = None,
    tokens: dict[str, float] | None = None,
    doc_type: str = "other",
    sender: str = "",
    threshold: float = 0.95,
):  # type: ignore[no-untyped-def]
    return attribute(
        db,
        ids=ids or [],
        policy_tokens=tokens or {},
        doc_type=doc_type,
        sender=sender,
        threshold=threshold,
    )


def test_one_known_id_with_good_reading_is_filed(db: Session, people: dict[str, Client]) -> None:
    a = att(db, [IdHit("123456782", 0.93)])
    assert a.route == "filed" and a.chosen == people["david"].id and a.confidence >= 0.95
    assert a.candidates[0].evidence == ["id:123456782"]


def test_poor_reading_goes_to_review_with_a_suggestion(
    db: Session, people: dict[str, Client]
) -> None:
    a = att(db, [IdHit("123456782", 0.55)])
    assert a.route == "review" and a.chosen == people["david"].id and a.confidence < 0.95


def test_unknown_or_missing_identifiers_are_unassigned(
    db: Session, people: dict[str, Client]
) -> None:
    assert att(db).route == "unassigned"
    assert att(db).reason == "no identifier found"
    unknown = att(db, [IdHit("340000019", 0.99)])
    assert unknown.route == "unassigned" and "no known client" in unknown.reason


def test_two_clients_in_one_document_always_go_to_review(
    db: Session, people: dict[str, Client]
) -> None:
    a = att(db, [IdHit("123456782", 0.99), IdHit("000000026", 0.99)])
    assert a.route == "review" and len(a.candidates) == 2


def test_policy_number_attributes_to_the_policy_holder(
    db: Session, people: dict[str, Client]
) -> None:
    a = att(db, tokens={"PL-2024-77881": 0.9})
    assert a.route == "filed" and a.chosen == people["noa"].id
    assert a.candidates[0].evidence == ["policy:PL-2024-77881"]
    assert att(db, tokens={"pl-2024-77881": 0.9}).chosen == people["noa"].id  # case-insensitive
    assert att(db, tokens={"PL-2024-00000": 0.9}).route == "unassigned"


def test_id_and_policy_of_the_same_client_agree(db: Session, people: dict[str, Client]) -> None:
    a = att(db, [IdHit("000000026", 0.9)], {"PL-2024-77881": 0.9})
    assert a.route == "filed" and len(a.candidates) == 1
    assert len(a.candidates[0].evidence) == 2


def test_sender_alone_is_never_a_candidate(db: Session, people: dict[str, Client]) -> None:
    """P-name-only: an address or phone number can break a tie but never file a document."""
    for sender in ("david@x.il", "0501112222", "+972-50-111-2222"):
        a = att(db, sender=sender)
        assert a.route == "unassigned" and a.candidates == [], sender


def test_sender_breaks_a_tie_but_the_result_still_needs_review(
    db: Session, people: dict[str, Client]
) -> None:
    ids = [IdHit("123456782", 0.9), IdHit("000000026", 0.9)]
    a = att(db, ids, sender="noa@x.il")
    assert a.route == "review" and a.chosen == people["noa"].id
    assert [c.sender_match for c in a.candidates] == [True, False]


def test_addendum_goes_to_the_head_and_links_the_family(
    db: Session, people: dict[str, Client]
) -> None:
    ids = [IdHit("123456782", 0.93), IdHit("000000034", 0.93)]
    a = att(db, ids, doc_type="id_addendum")
    assert a.route == "filed" and a.chosen == people["david"].id
    assert a.family_members == [people["kid"].id]
    assert [c.client_id for c in a.candidates] == [people["david"].id]


def test_threshold_is_configurable(db: Session, people: dict[str, Client]) -> None:
    assert att(db, [IdHit("123456782", 0.75)], threshold=0.99).route == "review"
    assert att(db, [IdHit("123456782", 0.75)], threshold=0.90).route == "filed"


# ---- digitize -------------------------------------------------------------------------------
def test_render_pages_from_pdf_tiff_png_and_unknown() -> None:
    pages = [S.render(["a"]), S.render(["b"])]
    assert len(digitize.render_pages(S.pdf(pages), "application/pdf")) == 2
    assert len(digitize.render_pages(S.tiff(pages), "image/tiff")) == 2
    assert len(digitize.render_pages(S.png(pages[0]), "image/png")) == 1
    assert digitize.render_pages(b"x", "text/plain") == []


def test_blank_detection_and_splitting() -> None:
    a, b = S.render(S.id_card("123456782")), S.render(S.invoice("000000026"))
    assert digitize.is_blank(S.blank()) and not digitize.is_blank(a)
    groups = digitize.split_on_blank_pages([a, S.blank(), b, b, S.blank(), S.blank(), a])
    assert [(g.first, g.last) for g in groups] == [(1, 1), (3, 4), (7, 7)]
    assert [(g.first, g.last) for g in digitize.split_on_blank_pages([a, b])] == [(1, 2)]
    assert [(g.first, g.last) for g in digitize.split_on_blank_pages([S.blank()])] == [(1, 1)]


def test_deskew_and_crop() -> None:
    import numpy as np

    img = S.render(S.id_card("123456782"), skew=4.0)
    angle = digitize.deskew_angle(np.array(img))
    assert abs(abs(angle) - 4.0) < 1.5
    cleaned = digitize.clean(img)
    assert cleaned.width < img.width and cleaned.height < img.height  # margins are cut
    straight = digitize.clean(S.render(S.id_card("123456782")))
    assert abs(digitize.deskew_angle(np.array(straight))) < 1.0


def test_result_helper_and_unused_imports() -> None:
    r = result_from_text("a b c", 0.9)
    assert r.mean_conf == 0.9 and len(r.words) == 3


# ---- the shapes an ID number may take, and the digits-only policy fallback -----------------------
@pytest.mark.parametrize(
    "words,found",
    [
        (W(("12345678-2", 0.9)), True),
        (W(("12345678", 0.9), ("2", 0.8)), True),
        (W(("123", 0.9), ("456", 0.9), ("782", 0.9)), True),
        (W(("2024-77881", 0.9)), False),  # a policy-number fragment, not an ID
        (W(("202477881", 0.9)), True),  # the same digits contiguous ARE a valid ID number
        (W(("1234", 0.9), ("56782", 0.9)), False),  # not one of the usual groupings
        (W(("05-0123456", 0.9)), False),
        (W(("12/03/2026", 0.9)), False),
    ],
)
def test_only_real_id_shapes_are_read(words: list[Word], found: bool) -> None:
    assert bool(find_ids(words)) is found


def test_policy_number_with_a_lost_prefix_is_suggested_never_filed(
    db: Session, people: dict[str, Client]
) -> None:
    a = att(db, tokens={"2024-77881": 0.95})  # stored number is PL-2024-77881
    assert a.route == "review" and a.chosen == people["noa"].id
    assert a.candidates[0].weak and a.candidates[0].evidence == ["policy~PL-2024-77881"]
    assert "digits only" in a.reason
    assert att(db, tokens={"2024-77882": 0.95}).route == "unassigned"  # one digit off: nothing
    assert att(db, tokens={"7788": 0.95}).route == "unassigned"  # too short to mean anything


def test_an_exact_policy_match_beats_the_digits_only_fallback(
    db: Session, people: dict[str, Client]
) -> None:
    a = att(db, tokens={"PL-2024-77881": 0.95, "2024-77881": 0.95})
    assert a.route == "filed" and not a.candidates[0].weak
