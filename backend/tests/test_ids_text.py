"""T-M1-8 (ID vectors), T-M1-9 (multi-script matching helpers)."""

import pytest

from app.ids import is_valid_israeli_id, normalize_id_number, uuid7
from app.text import build_search, normalize, skeleton, to_hebrew


@pytest.mark.parametrize("raw", ["123456782", "000000018", "000000026", "000000034"])
def test_valid_ids(raw: str) -> None:
    assert is_valid_israeli_id(raw), raw


@pytest.mark.parametrize("raw", ["123456789", "12345", "1234567890", "abcdefghi", "", "12 3"])
def test_invalid_ids(raw: str) -> None:
    assert not is_valid_israeli_id(raw), raw


def test_id_padding() -> None:
    assert normalize_id_number("18") is None and normalize_id_number("00018") == "000000018"
    assert normalize_id_number("123-456-782") == "123456782"


def test_uuid7_is_time_ordered_and_versioned() -> None:
    ids = [uuid7() for _ in range(50)]
    assert all(i.version == 7 for i in ids)
    assert [i.int >> 80 for i in ids] == sorted(i.int >> 80 for i in ids)
    assert len({*ids}) == 50


def test_normalize_ignores_accents_niqqud_punctuation() -> None:
    assert normalize("Dávid  O'Cohen") == "david o cohen"
    assert normalize("דָּוִד כהן") == normalize("דוד כהן")
    assert normalize("Jean-Luc  Müller") == "jean luc muller"


@pytest.mark.parametrize(
    "names",
    [
        ["דוד כהן", "Давид Коэн", "David Cohen", "Dávid COHEN", "david kohen"],
        ["משה לוי", "Moshe Levi", "Моше Леви"],
    ],
)
def test_skeleton_meets_across_scripts(names: list[str]) -> None:
    assert len({skeleton(n) for n in names}) == 1, [skeleton(n) for n in names]


def test_skeleton_differs_for_different_names() -> None:
    assert skeleton("David Cohen") != skeleton("David Levi")


def test_build_search_contains_all_forms_and_id() -> None:
    plain, skel = build_search("David Cohen", "123456782")
    assert "david" in plain and "123456782" in plain and "דוד" in plain
    assert skel == "dvd kn"


def test_to_hebrew_final_letters() -> None:
    assert to_hebrew("Cohen").endswith("ן")
