"""M8: join spreadsheets on partial keys, and export (REQUIREMENTS 8.10)."""

import io
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.excel import join as xl
from app.main import create_app
from app.text import build_search
from tests.conftest import login, make_account


def xlsx(headers: list[str], rows: list[list[Any]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# the example from the mock: full ID + name / last 4 + Latin name / full ID, no name
AGENCY = xlsx(
    ["id", "name", "phone"],
    [
        ["123456782", "דוד כהן", "050-1"],
        ["000000026", "Noa Levi", "050-2"],
        ["000034567", "Moshe Katz", "050-3"],
        ["111111234", "Dan Cohen", "050-4"],
        ["222221234", "Dana Cohen", "050-5"],
        ["333331234", "Sara Mizrahi", "050-6"],
        ["444440001", "Yossi Peretz", "050-7"],
    ],
)
INSURER = xlsx(
    ["last4", "name", "policy", "premium"],
    [
        ["6782", "David Cohen", "PL-1", 100],  # Hebrew name vs Latin name: joined on last 4 + name
        ["0026", "Noa Levy", "PL-2", 200],  # spelled differently: joined, flagged
        ["1234", "Dan Cohen", "PL-3", 300],  # two similar names end in 1234: ambiguous
        ["9999", "Nobody Known", "PL-4", 400],  # no such row
        ["4567", "Moshe Katz", "PL-5", 500],
        ["4567", "Moshe Katz", "PL-6", 600],  # two policies
    ],
)
CLEARING = xlsx(
    ["תעודת זהות", "product", "amount"],
    [
        ["123456782", "pension", 5000],
        ["000034567", "gemel", 700],
        ["555550000", "pension", 1],  # not an agency client
        [34567, "kupat", 2],  # leading zeros lost by Excel: 000034567
    ],
)


def sheets() -> tuple[xl.Sheet, xl.Sheet, xl.Sheet]:
    return (
        xl.read_sheet("agency", AGENCY),
        xl.read_sheet("insurer", INSURER),
        xl.read_sheet("clearing", CLEARING),
    )


# ---- reading --------------------------------------------------------------------------------------
def test_columns_are_found_by_name_in_hebrew_and_english() -> None:
    a, i, c = sheets()
    assert (a.id_col, a.name_col, a.last4_col) == ("id", "name", None)
    assert (i.id_col, i.last4_col, i.name_col) == (None, "last4", "name")
    assert (c.id_col, c.name_col) == ("תעודת זהות", None)


@pytest.mark.parametrize(
    "data,fragment",
    [
        (b"not a spreadsheet", "not a readable"),
        (xlsx(["foo", "bar"], [[1, 2]]), "no ID or last-4 column"),
        (xlsx([], []), "empty"),
    ],
)
def test_unusable_files_are_explained(data: bytes, fragment: str) -> None:
    with pytest.raises(xl.JoinError, match=fragment):
        xl.read_sheet("x", data)


def test_id_normalisation_restores_lost_leading_zeros() -> None:
    assert xl.full_id(34567) == "000034567" and xl.full_id("0000-34567") == "000034567"
    assert xl.full_id("12") is None and xl.full_id(None) is None and xl.full_id("") is None


# ---- name similarity ---------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "a,b,similar",
    [
        ("דוד כהן", "David Cohen", True),
        ("דוד כהן", "Давид Коэн", True),
        ("Noa Levi", "Noa Levy", True),
        ("Moshe Katz", "Moshe Kats", True),
        ("David Cohen", "Sara Mizrahi", False),
        ("", "David", False),
    ],
)
def test_similarity_across_scripts_and_spellings(a: str, b: str, similar: bool) -> None:
    assert (xl.similarity(a, b) >= xl.MIN_NAME_SIMILARITY) is similar


def test_spelled_differently_only_within_one_script() -> None:
    assert xl.spelled_differently("Noa Levi", "Noa Levy")
    assert not xl.spelled_differently("noa  levi", "Noa Levi")  # case and spacing do not count
    assert not xl.spelled_differently("דוד כהן", "David Cohen")  # a different script is expected


# ---- the join --------------------------------------------------------------------------------------------
def test_the_join_methods_and_counts() -> None:
    a, i, c = sheets()
    res = xl.join(a, [i, c])
    assert res.summary["methods"]["insurer"] == {"last4+name": 4, "ambiguous": 1, "none": 1}
    assert res.summary["methods"]["clearing"] == {"id": 3, "none": 1}
    assert res.summary["unmatched"] == 3 and res.summary["base_rows"] == 7


def row_for(res: xl.JoinResult, base_id: str, **where: str) -> dict[str, Any]:
    rows = [
        dict(zip(res.headers, r, strict=True))
        for r in res.rows
        if r[res.headers.index("agency.id")] == base_id
    ]
    for k, v in where.items():
        rows = [r for r in rows if r.get(k) == v]
    assert rows, (base_id, where)
    return rows[0]


def test_hebrew_name_joins_a_latin_name_on_last4_and_name() -> None:
    res = xl.join(*[sheets()[0]], [sheets()[1], sheets()[2]])
    r = row_for(res, "123456782", **{"insurer.policy": "PL-1"})
    assert r["join_insurer"] == "last4+name" and r["insurer.premium"] == 100
    assert r["flags"] == ""  # a different script is not a spelling problem


def test_a_different_spelling_is_joined_and_flagged() -> None:
    a, i, c = sheets()
    r = row_for(xl.join(a, [i, c]), "000000026")
    assert r["join_insurer"] == "last4+name" and "name_spelled_differently" in r["flags"]


def test_two_similar_names_with_the_same_last4_are_not_guessed() -> None:
    a, i, c = sheets()
    res = xl.join(a, [i, c])
    assert row_for(res, "111111234")["flags"] == "no_match_in_any"
    assert row_for(res, "222221234")["flags"] == "no_match_in_any"
    amb = [r for r in res.unmatched if r[1] == "ambiguous"]
    assert len(amb) == 1 and "similar names end in 1234" in amb[0][2]


def test_one_agency_row_with_several_other_rows_gives_several_output_rows() -> None:
    a, i, c = sheets()
    res = xl.join(a, [i, c])
    moshe = [r for r in res.rows if r[res.headers.index("agency.id")] == "000034567"]
    assert len(moshe) == 4  # PL-5, PL-6 from the insurer and gemel, kupat from the clearing house
    methods = sorted(
        (r[res.headers.index("join_insurer")], r[res.headers.index("join_clearing")]) for r in moshe
    )
    assert methods == [("", "id"), ("", "id"), ("last4+name", ""), ("last4+name", "")]


def test_rows_in_no_other_file_are_flagged_and_other_rows_without_a_match_are_listed() -> None:
    a, i, c = sheets()
    res = xl.join(a, [i, c])
    assert "no_match_in_any" in row_for(res, "444440001")["flags"]
    reasons = {(r[0], r[1]) for r in res.unmatched}
    assert reasons == {("insurer", "none"), ("insurer", "ambiguous"), ("clearing", "none")}
    assert any("9999" in r[2] for r in res.unmatched)


def test_a_full_id_match_with_a_different_name_is_flagged() -> None:
    base = xl.read_sheet("agency", xlsx(["id", "name"], [["123456782", "David Cohen"]]))
    other = xl.read_sheet("insurer", xlsx(["id", "name"], [["123456782", "Sara Mizrahi"]]))
    (row,) = xl.join(base, [other]).rows
    assert row[-1] == "name_differs"


def test_last4_without_a_name_is_a_weak_match_and_ambiguous_when_not_unique() -> None:
    base = xl.read_sheet(
        "agency",
        xlsx(["id", "name"], [["123456782", "A B"], ["999996782", "C D"], ["000001111", "E F"]]),
    )
    other = xl.read_sheet("insurer", xlsx(["last4", "x"], [["1111", 1], ["6782", 2]]))
    res = xl.join(base, [other])
    assert res.summary["methods"]["insurer"] == {"last4": 1, "ambiguous": 1}
    assert [r[-1] for r in res.rows if r[0] == "000001111"] == ["weak_match"]


def test_it_works_with_just_one_other_file_and_is_deterministic() -> None:
    a, i, _ = sheets()
    one = xl.join(a, [i])
    assert xl.join(a, [i]).rows == one.rows and "clearing" not in one.headers[-3]


def test_the_workbook_has_joined_unmatched_and_summary_sheets() -> None:
    a, i, c = sheets()
    wb = load_workbook(io.BytesIO(xl.to_xlsx(xl.join(a, [i, c]))))
    assert wb.sheetnames == ["Joined", "Unmatched", "Summary"]
    joined = list(wb["Joined"].iter_rows(values_only=True))
    assert joined[0][0] == "agency.id" and "join_insurer" in joined[0] and joined[0][-1] == "flags"
    assert len(joined) > 7 and len(list(wb["Unmatched"].iter_rows())) == 4  # header + 3
    summary = dict(wb["Summary"].iter_rows(min_row=2, values_only=True))
    assert summary["base_rows"] == 7 and summary["unmatched"] == 3


# ---- the API ---------------------------------------------------------------------------------------------------------
@pytest.fixture
def api(settings: Settings, fresh_db: Engine) -> TestClient:
    return TestClient(create_app(settings, fresh_db), base_url="https://testserver")


def as_role(api: TestClient, db: Session, role: str) -> tuple[TestClient, dict[str, str]]:
    c = TestClient(api.app, base_url="https://testserver")  # type: ignore[arg-type]
    return c, login(c, db, make_account(db, f"{role}@agency.il", [role]))


def post_join(c: TestClient, h: dict[str, str], **files: bytes | None) -> Any:
    return c.post(
        "/api/excel/join",
        headers=h,
        files={k: (f"{k}.xlsx", v) for k, v in files.items() if v is not None},
    )


def test_join_endpoint_returns_a_workbook_a_summary_and_is_audited(
    api: TestClient, db: Session
) -> None:
    c, h = as_role(api, db, "backoffice")
    r = post_join(c, h, agency=AGENCY, insurer=INSURER, clearing=CLEARING)
    assert r.status_code == 200 and r.headers["content-type"].startswith(
        "application/vnd.openxmlformats"
    )
    assert "joined.xlsx" in r.headers["content-disposition"]
    summary = json.loads(r.headers["x-join-summary"])
    assert summary["unmatched"] == 3 and summary["base_rows"] == 7
    assert load_workbook(io.BytesIO(r.content)).sheetnames == ["Joined", "Unmatched", "Summary"]
    assert (
        db.execute(text("SELECT count(*) FROM audit_entry WHERE action='excel.join'")).scalar_one()
        == 1
    )


def test_join_endpoint_roles(api: TestClient, db: Session) -> None:
    for role, status in (("agent", 403), ("backoffice", 200), ("marketing", 200), ("manager", 200)):
        c, h = as_role(api, db, role)
        assert post_join(c, h, agency=AGENCY, insurer=INSURER).status_code == status, role


def test_join_endpoint_errors_are_explained(api: TestClient, db: Session) -> None:
    c, h = as_role(api, db, "manager")
    assert "at least one more" in post_join(c, h, agency=AGENCY).json()["detail"]
    assert "not a readable" in post_join(c, h, agency=b"junk", insurer=INSURER).json()["detail"]
    r = post_join(c, h, agency=xlsx(["foo"], [[1]]), insurer=INSURER)
    assert r.status_code == 422 and "no ID or last-4 column" in r.json()["detail"]
    assert c.post("/api/excel/join", headers=h).status_code == 422  # nothing uploaded


def test_client_export(api: TestClient, db: Session) -> None:
    plain, skel = build_search("David Cohen", "123456782")
    from app.models import Client

    db.add(
        Client(
            id_number="123456782",
            name="David Cohen",
            search_text=plain,
            search_skel=skel,
            phone="050-1",
            email="d@x.il",
            preferred_language="ru",
        )
    )
    db.commit()
    c, h = as_role(api, db, "backoffice")
    r = c.get("/api/export/clients.xlsx")
    assert r.status_code == 200
    rows = list(load_workbook(io.BytesIO(r.content)).active.iter_rows(values_only=True))  # type: ignore[union-attr]
    assert rows[0][:3] == ("id_number", "name", "name_latin")
    assert rows[1][:2] == ("123456782", "David Cohen") and rows[1][3] == "ru" and rows[1][7] == 0
    assert db.execute(
        text("SELECT detail FROM audit_entry WHERE action='export.clients'")
    ).scalar_one() == {"rows": 1}  # exports are written to the audit log at once
    for role, status in (("agent", 403), ("marketing", 403), ("manager", 200)):
        other, _ = as_role(api, db, role)
        assert other.get("/api/export/clients.xlsx").status_code == status, role
