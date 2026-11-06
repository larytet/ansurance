"""M8: import from and sync with the existing agency system (REQUIREMENTS 9.1)."""

import time
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations import agency as ag
from app.integrations.agency_sync import run_sync
from app.main import create_app
from app.models import AdapterCall, Client, ExternalId, InboxItem, Policy, SyncState
from tests.conftest import login, make_account

C1 = ag.ExtClient("S-1", "123456782", "David Cohen", "050-123-4567", "david@x.il", "ru")
C2 = ag.ExtClient("S-2", "000000026", "Noa Levi")
P1 = ag.ExtPolicy("SP-1", "123456782", "Harel", "PL-1", "car", 1450.5, date(2027, 3, 1))


# ---- CSV ------------------------------------------------------------------------------------
def test_parse_clients_csv_with_bom_and_optional_columns() -> None:
    text_ = "﻿external_id,id_number,name,phone\nS-1,123456782,דוד כהן,050-1\n"
    (c,) = ag.parse_clients_csv(text_)
    assert (c.external_id, c.id_number, c.name, c.phone, c.email) == (
        "S-1",
        "123456782",
        "דוד כהן",
        "050-1",
        "",
    )


def test_parse_policies_csv_types() -> None:
    csv = (
        "external_id,client_id_number,insurer,policy_number,product,premium_ils,renewal_date\n"
        "SP-1,123456782,Harel,PL-1,car,1450.5,2027-03-01\nSP-2,000000026,Migdal,PL-2,,,\n"
    )
    a, b = ag.parse_policies_csv(csv)
    assert (a.premium_ils, a.renewal_date, a.status) == (1450.5, date(2027, 3, 1), "active")
    assert (b.premium_ils, b.renewal_date, b.product) == (0.0, None, "other")


@pytest.mark.parametrize(
    "csv,fragment",
    [
        ("external_id,name\nS-1,x\n", "missing columns: id_number"),
        ("a,b\n1,2\n", "missing columns"),
    ],
)
def test_bad_client_csv(csv: str, fragment: str) -> None:
    with pytest.raises(ag.CsvError, match=fragment):
        ag.parse_clients_csv(csv)


def test_bad_policy_rows_name_the_line() -> None:
    csv = (
        "external_id,client_id_number,insurer,policy_number,premium_ils\n"
        "SP-1,1,Harel,P1,12\nSP-2,1,Harel,P2,twelve\n"
    )
    with pytest.raises(ag.CsvError, match="row 3"):
        ag.parse_policies_csv(csv)


# ---- the sync -------------------------------------------------------------------------------------
def test_first_import_creates_clients_links_ids_and_creates_policies(db: Session) -> None:
    rep = run_sync(db, ag.FakeAgencySystem([C1, C2], [P1]))
    db.commit()
    assert (rep.clients_created, rep.policies_created, rep.errors) == (2, 1, [])
    c = db.scalars(select(Client).where(Client.id_number == "123456782")).one()
    assert (c.name, c.phone, c.email, c.preferred_language, c.status) == (
        "David Cohen",
        "050-123-4567",
        "david@x.il",
        "ru",
        "active",
    )
    assert c.name_translit_he and "123456782" in c.search_text  # searchable like any client
    link = db.scalars(select(ExternalId).where(ExternalId.client_id == c.id)).one()
    assert (link.system, link.external_id) == ("fake", "S-1")
    pol = db.scalars(select(Policy)).one()
    assert (pol.policy_number, float(pol.premium_ils), pol.renewal_date) == (
        "PL-1",
        1450.5,
        date(2027, 3, 1),
    )
    state = db.get(SyncState, "fake")
    assert state is not None and state.cursor == "2|1" and state.last_result["clients_created"] == 2
    assert (
        db.execute(text("SELECT count(*) FROM audit_entry WHERE action='agency.sync'")).scalar_one()
        == 1
    )


def test_running_it_again_changes_nothing(db: Session) -> None:
    system = ag.FakeAgencySystem([C1, C2], [P1])
    run_sync(db, system)
    db.commit()
    rep = run_sync(db, system)
    assert not rep.changed()
    assert (rep.clients_unchanged, rep.policies_unchanged) == (2, 1)
    assert (
        db.scalars(select(Client)).all().__len__() == 2
        and len(db.scalars(select(Policy)).all()) == 1
    )


def test_ansurance_keeps_what_its_users_entered(db: Session) -> None:
    run_sync(db, ag.FakeAgencySystem([C1]))
    c = db.scalars(select(Client)).one()
    c.phone, c.preferred_language = "052-999-9999", "ar"  # edited in ansurance
    db.commit()
    changed = ag.ExtClient("S-1", "123456782", "David Cohen-Levi", "050-000-0000", "new@x.il", "he")
    rep = run_sync(db, ag.FakeAgencySystem([changed]))
    db.refresh(c)
    assert (c.phone, c.preferred_language, c.name) == ("052-999-9999", "ar", "David Cohen")
    assert c.email == "david@x.il"  # a non-empty value is never overwritten
    assert rep.clients_unchanged == 1 and not rep.changed()


def test_blank_fields_are_filled_from_the_old_system(db: Session) -> None:
    run_sync(db, ag.FakeAgencySystem([ag.ExtClient("S-2", "000000026", "Noa Levi")]))
    rep = run_sync(
        db, ag.FakeAgencySystem([ag.ExtClient("S-2", "000000026", "Noa Levi", "052-1", "noa@x.il")])
    )
    c = db.scalars(select(Client)).one()
    assert (c.phone, c.email, rep.clients_filled) == ("052-1", "noa@x.il", 1)


def test_the_existing_system_wins_for_policy_data(db: Session) -> None:
    run_sync(db, ag.FakeAgencySystem([C1], [P1]))
    pol = db.scalars(select(Policy)).one()
    pol.premium_ils = 9999  # someone changed it in ansurance
    db.commit()
    newer = ag.ExtPolicy(
        "SP-1", "123456782", "Harel", "PL-1", "car", 1600.0, date(2028, 3, 1), "cancelled"
    )
    rep = run_sync(db, ag.FakeAgencySystem([C1], [newer]))
    db.refresh(pol)
    assert (float(pol.premium_ils), pol.renewal_date, pol.status) == (
        1600.0,
        date(2028, 3, 1),
        "cancelled",
    )
    assert rep.policies_updated == 1 and len(db.scalars(select(Policy)).all()) == 1


def test_an_existing_ansurance_client_is_linked_not_duplicated(db: Session) -> None:
    from app.text import build_search

    plain, skel = build_search("David Cohen", "123456782")
    db.add(Client(id_number="123456782", name="David Cohen", search_text=plain, search_skel=skel))
    db.commit()
    rep = run_sync(db, ag.FakeAgencySystem([C1]))
    assert (rep.clients_created, rep.clients_linked) == (0, 1)
    assert (
        len(db.scalars(select(Client)).all()) == 1
        and len(db.scalars(select(ExternalId)).all()) == 1
    )


def test_bad_rows_are_reported_and_do_not_stop_the_rest(db: Session) -> None:
    bad_id = ag.ExtClient("S-9", "123456789", "Bad Id")  # fails the check digit
    orphan = ag.ExtPolicy("SP-9", "340000019", "Harel", "PL-9", "car", 1)
    rep = run_sync(db, ag.FakeAgencySystem([bad_id, C1], [orphan, P1]))
    db.commit()
    assert (rep.clients_created, rep.policies_created) == (1, 1)
    assert len(rep.errors) == 2
    assert "invalid ID number" in rep.errors[0] and "no client with ID" in rep.errors[1]
    item = db.scalars(select(InboxItem)).one()
    assert item.role == "backoffice" and "2 row(s) need attention" in item.title
    run_sync(db, ag.FakeAgencySystem([bad_id, C1], [orphan, P1]))
    assert len(db.scalars(select(InboxItem)).all()) == 1  # one open item, not one per run


def test_a_changed_id_number_for_a_linked_client_is_not_applied(db: Session) -> None:
    run_sync(db, ag.FakeAgencySystem([C1]))
    moved = ag.ExtClient("S-1", "000000026", "David Cohen")
    rep = run_sync(db, ag.FakeAgencySystem([moved]))
    assert "resolve by hand" in rep.errors[0]
    assert (
        db.scalars(select(Client).where(Client.name == "David Cohen")).one().id_number
        == "123456782"
    )


def test_a_dry_run_reports_but_keeps_nothing(db: Session) -> None:
    rep = run_sync(db, ag.FakeAgencySystem([C1], [P1]), dry_run=True)
    db.commit()
    assert (rep.clients_created, rep.policies_created) == (1, 1)
    assert db.scalars(select(Client)).all() == [] and db.scalars(select(Policy)).all() == []
    assert db.scalars(select(ExternalId)).all() == []


def test_an_unreachable_system_raises_and_is_logged(db: Session, fresh_db: Engine) -> None:
    system = ag.FakeAgencySystem([C1])
    system.up = False
    with pytest.raises(OSError, match="not reachable"):
        run_sync(db, system)
    assert db.scalars(select(AdapterCall).where(AdapterCall.operation == "health")).one().ok
    assert db.scalars(select(Client)).all() == []


# ---- the file drop adapter ----------------------------------------------------------------------------------
def test_file_drop_reads_the_newest_export_and_syncs(db: Session, tmp_path: Path) -> None:
    (tmp_path / "clients_old.csv").write_text(
        "external_id,id_number,name\nS-1,123456782,Old Name\n"
    )
    time.sleep(0.02)
    (tmp_path / "clients_new.csv").write_text(
        "external_id,id_number,name,phone\nS-1,123456782,David Cohen,050-1\nS-2,000000026,Noa Levi,\n"
    )
    (tmp_path / "policies_2026.csv").write_text(
        "external_id,client_id_number,insurer,policy_number,product,premium_ils\n"
        "SP-1,123456782,Harel,PL-1,car,100\n"
    )
    drop = ag.FileDropAgency(str(tmp_path))
    assert drop.health()
    rep = run_sync(db, drop)
    db.commit()
    assert (rep.clients_created, rep.policies_created) == (2, 1)
    assert (
        db.scalars(select(Client).where(Client.id_number == "123456782")).one().name
        == "David Cohen"
    )
    assert not run_sync(db, drop).changed()  # the same files again: nothing happens


def test_file_drop_with_no_files_or_no_folder(db: Session, tmp_path: Path) -> None:
    assert not run_sync(db, ag.FileDropAgency(str(tmp_path))).changed()
    assert not ag.FileDropAgency(str(tmp_path / "nope")).health()
    with pytest.raises(OSError):
        run_sync(db, ag.FileDropAgency(str(tmp_path / "nope")))


def test_write_back_is_optional_and_one_way_by_default(tmp_path: Path) -> None:
    fake = ag.FakeAgencySystem()
    fake.push_task("S-1", "client replied to the campaign")
    assert fake.tasks == [("S-1", "client replied to the campaign")]
    ag.FileDropAgency(str(tmp_path)).push_task("S-1", "ignored")  # no error, no effect


# ---- the API ------------------------------------------------------------------------------------------------------------
@pytest.fixture
def api(settings: Settings, fresh_db: Engine) -> TestClient:
    return TestClient(create_app(settings, fresh_db), base_url="https://testserver")


def role_client(api: TestClient, db: Session, role: str) -> tuple[TestClient, dict[str, str]]:
    c = TestClient(api.app, base_url="https://testserver")  # type: ignore[arg-type]
    return c, login(c, db, make_account(db, f"{role}@agency.il", [role]))


CLIENTS_CSV = (
    "external_id,id_number,name,phone\nS-1,123456782,David Cohen,050-1\nS-2,123456789,Bad,\n"
)
POLICIES_CSV = (
    "external_id,client_id_number,insurer,policy_number,product,premium_ils\n"
    "SP-1,123456782,Harel,PL-1,car,100\n"
)


def post_import(c: TestClient, h: dict[str, str], **kw: Any) -> Any:
    files: dict[str, Any] = {"clients": ("clients.csv", kw.get("clients", CLIENTS_CSV))}
    if kw.get("policies", POLICIES_CSV):
        files["policies"] = ("policies.csv", kw.get("policies", POLICIES_CSV))
    return c.post(
        "/api/import/agency",
        headers=h,
        files=files,
        data={"dry_run": "true" if kw.get("dry") else "false"},
    )


def test_import_endpoint_applies_and_reports(api: TestClient, db: Session) -> None:
    c, h = role_client(api, db, "manager")
    r = post_import(c, h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["clients_created"], body["policies_created"], len(body["errors"])) == (1, 1, 1)
    assert db.execute(text("SELECT system FROM external_id")).scalar_one() == "csv"


def test_import_dry_run_changes_nothing(api: TestClient, db: Session) -> None:
    c, h = role_client(api, db, "backoffice")
    body = post_import(c, h, dry=True).json()
    assert body["dry_run"] is True and body["clients_created"] == 1
    assert db.scalars(select(Client)).all() == []


def test_import_is_for_back_office_and_manager_only(api: TestClient, db: Session) -> None:
    for role, status in (("agent", 403), ("marketing", 403), ("backoffice", 200), ("manager", 200)):
        c, h = role_client(api, db, role)
        assert post_import(c, h, dry=True).status_code == status, role


def test_import_rejects_bad_files(api: TestClient, db: Session) -> None:
    c, h = role_client(api, db, "manager")
    assert post_import(c, h, clients="a,b\n1,2\n").status_code == 422
    assert post_import(c, h, policies="x\n1\n").status_code == 422
    assert (
        c.post(
            "/api/import/agency", headers=h, files={"clients": ("c.csv", b"\xff\xfe\x00bad")}
        ).status_code
        == 422
    )
