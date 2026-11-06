"""T-M2-1..8 at the API level. Also P-4, P-11, P-14, P-17."""

import hashlib
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session

from app.adapters.antivirus import EICAR, FakeAntivirus
from app.blob.fake import FakeBlobStore
from app.config import Settings
from app.documents.reconcile import reconcile
from app.main import create_app
from tests.conftest import Account, login, make_account

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\n%%EOF scan of a policy"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
TIFF = b"II*\x00" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


class Env:
    def __init__(self, api: TestClient, store: FakeBlobStore, av: FakeAntivirus) -> None:
        self.api, self.store, self.av = api, store, av


@pytest.fixture
def env(settings: Settings, fresh_db: Engine) -> Env:
    store, av = FakeBlobStore(), FakeAntivirus()
    app = create_app(settings, fresh_db, blobs=store, antivirus=av)
    return Env(TestClient(app, base_url="https://testserver"), store, av)


def who(env: Env, db: Session, role: str, n: int = 1) -> tuple[TestClient, dict[str, str], Account]:
    a = make_account(db, f"{role}{n}@agency.il", [role])
    c = TestClient(env.api.app, base_url="https://testserver")  # type: ignore[arg-type]
    return c, login(c, db, a), a


def client_for(c: TestClient, hdr: dict[str, str], id_number: str = "123456782") -> str:
    r = c.post("/api/clients", json={"id_number": id_number, "name": "David Cohen"}, headers=hdr)
    return str(r.json()["id"])


def upload(
    c: TestClient,
    hdr: dict[str, str],
    cid: str,
    data: bytes = PDF,
    name: str = "policy.pdf",
    **form: str,
) -> Any:
    return c.post(
        f"/api/clients/{cid}/documents",
        headers=hdr,
        files={"file": (name, data, "application/octet-stream")},
        data={"doc_type": "policy", **form},
    )


# T-M2-1 -------------------------------------------------------------------------------------
def test_upload_download_roundtrip(env: Env, db: Session) -> None:
    c, hdr, _ = who(env, db, "agent")
    cid = client_for(c, hdr)
    r = upload(c, hdr, cid)
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["sha256"] == hashlib.sha256(PDF).hexdigest() and doc["scan_status"] == "clean"
    assert doc["media_type"] == "application/pdf" and doc["size"] == len(PDF)
    dl = c.get(f"/api/documents/{doc['id']}/content")
    assert dl.status_code == 200 and dl.content == PDF
    assert dl.headers["content-type"] == "application/pdf"
    assert dl.headers["x-content-type-options"] == "nosniff"
    assert "attachment" in dl.headers["content-disposition"]
    assert "sandbox" in dl.headers["content-security-policy"]


@pytest.mark.parametrize(
    "data,media", [(PNG, "image/png"), (TIFF, "image/tiff"), (JPEG, "image/jpeg")]
)
def test_images_accepted(env: Env, db: Session, data: bytes, media: str) -> None:
    c, hdr, _ = who(env, db, "agent")
    r = upload(c, hdr, client_for(c, hdr), data, "scan.bin")
    assert r.status_code == 201 and r.json()["media_type"] == media


def test_eml_accepted_by_content_and_extension(env: Env, db: Session) -> None:
    c, hdr, _ = who(env, db, "agent")
    eml = b"From: a@b.il\r\nSubject: id card\r\n\r\nbody"
    r = upload(c, hdr, client_for(c, hdr), eml, "mail.eml", channel="email")
    assert r.status_code == 201 and r.json()["media_type"] == "message/rfc822"
    assert upload(c, hdr, client_for(c, hdr, "000000026"), eml, "mail.txt").status_code == 415


# T-M2-2 -------------------------------------------------------------------------------------
def test_duplicate_upload_one_blob_two_documents(env: Env, db: Session) -> None:
    c, hdr, _ = who(env, db, "agent")
    cid = client_for(c, hdr)
    a, b = upload(c, hdr, cid).json(), upload(c, hdr, cid, name="again.pdf").json()
    assert a["id"] != b["id"] and a["sha256"] == b["sha256"]
    assert db.execute(text("SELECT count(*) FROM blob")).scalar_one() == 1
    assert db.execute(text("SELECT count(*) FROM document")).scalar_one() == 2
    assert len(list(env.store.iter_keys())) == 1


# T-M2-3: refusals at the database layer -------------------------------------------------------
def test_database_refuses_blob_delete_and_rewrite_and_document_delete(
    env: Env, db: Session
) -> None:
    c, hdr, _ = who(env, db, "agent")
    upload(c, hdr, client_for(c, hdr))
    for sql in (
        "DELETE FROM blob",
        "UPDATE blob SET sha256 = repeat('a', 64)",
        "UPDATE blob SET key = 'sha256/zz/zz'",
        "UPDATE blob SET size = 1",
        "DELETE FROM document",
    ):
        with pytest.raises(DatabaseError):
            db.execute(text(sql))
        db.rollback()
    db.execute(text("UPDATE blob SET scan_status = 'clean'"))  # the scan result may change
    db.commit()


def test_no_http_route_deletes_or_replaces_a_document(env: Env, db: Session) -> None:
    c, hdr, _ = who(env, db, "manager")
    doc = upload(c, hdr, client_for(c, hdr)).json()
    for method in ("DELETE", "PUT", "POST"):
        r = c.request(method, f"/api/documents/{doc['id']}", headers=hdr)
        assert r.status_code in (404, 405), (method, r.status_code)
    assert c.request("DELETE", f"/api/documents/{doc['id']}/content", headers=hdr).status_code in (
        404,
        405,
    )


# T-M2-4 -------------------------------------------------------------------------------------
def test_infected_file_is_quarantined_kept_and_never_served(env: Env, db: Session) -> None:
    c, hdr, _ = who(env, db, "agent")
    cid = client_for(c, hdr)
    r = upload(c, hdr, cid, b"%PDF-1.4 " + EICAR)
    assert r.status_code == 201 and r.json()["scan_status"] == "infected"
    assert len(list(env.store.iter_keys())) == 1  # stays in originals, flagged
    dl = c.get(f"/api/documents/{r.json()['id']}/content")
    assert dl.status_code == 409 and dl.json()["detail"] == "scan_infected"
    actions = {x[0] for x in db.execute(text("SELECT action FROM audit_entry"))}
    assert {"document.quarantine", "document.download_refused"} <= actions
    # the same bytes uploaded again stay quarantined
    again = upload(c, hdr, cid, b"%PDF-1.4 " + EICAR, name="again.pdf").json()
    assert again["scan_status"] == "infected"


def test_scanner_outage_fails_closed(env: Env, db: Session) -> None:
    c, hdr, _ = who(env, db, "agent")
    env.av.available = False
    r = upload(c, hdr, client_for(c, hdr))
    assert r.status_code == 201 and r.json()["scan_status"] == "pending"
    assert c.get(f"/api/documents/{r.json()['id']}/content").status_code == 409


# T-M2-5 / P-4: restricted documents ------------------------------------------------------------
def test_medical_reports_are_restricted_by_type(env: Env, db: Session) -> None:
    c, hdr, _ = who(env, db, "agent")
    r = upload(c, hdr, client_for(c, hdr), doc_type="medical_report")
    assert r.json()["restricted"] is True


def test_restricted_visibility_rule() -> None:
    from types import SimpleNamespace

    from app.clients.access import can_see_restricted

    me, other = object(), object()
    client = SimpleNamespace(agent_id=me)
    agent_of_record = SimpleNamespace(user=SimpleNamespace(id=me), capabilities={"client.read"})
    other_agent = SimpleNamespace(user=SimpleNamespace(id=other), capabilities={"client.read"})
    backoffice = SimpleNamespace(
        user=SimpleNamespace(id=other), capabilities={"client.read", "client.read_all"}
    )
    assert can_see_restricted(agent_of_record, client)  # type: ignore[arg-type]
    assert can_see_restricted(backoffice, client)  # type: ignore[arg-type]
    assert not can_see_restricted(other_agent, client)  # type: ignore[arg-type]


def test_other_agents_and_marketing_cannot_reach_a_clients_documents(env: Env, db: Session) -> None:
    c1, h1, _ = who(env, db, "agent", 1)
    c2, h2, _ = who(env, db, "agent", 2)
    cm, hm, _ = who(env, db, "marketing")
    cid = client_for(c1, h1)
    doc = upload(c1, h1, cid, doc_type="medical_report").json()
    assert c2.get(f"/api/documents/{doc['id']}").status_code == 404
    assert c2.get(f"/api/documents/{doc['id']}/content").status_code == 404
    assert c2.get(f"/api/clients/{cid}/documents").status_code == 404
    assert upload(c2, h2, cid).status_code == 404
    assert cm.get(f"/api/documents/{doc['id']}/content").status_code == 403
    assert cm.get(f"/api/clients/{cid}/documents").status_code == 403
    assert upload(cm, hm, cid).status_code == 403


def test_backoffice_and_manager_can_read_restricted(env: Env, db: Session) -> None:
    c1, h1, _ = who(env, db, "agent")
    cid = client_for(c1, h1)
    doc = upload(c1, h1, cid, doc_type="medical_report").json()
    for role in ("backoffice", "manager"):
        c, _, _ = who(env, db, role)
        assert c.get(f"/api/documents/{doc['id']}/content").content == PDF
        assert len(c.get(f"/api/clients/{cid}/documents").json()) == 1


# T-M2-6 -------------------------------------------------------------------------------------
def test_downloads_listing_and_uploads_are_audited(env: Env, db: Session) -> None:
    c, hdr, a = who(env, db, "agent")
    cid = client_for(c, hdr)
    doc = upload(c, hdr, cid).json()
    c.get(f"/api/clients/{cid}/documents")
    c.get(f"/api/documents/{doc['id']}/content")
    rows = db.execute(
        text(
            "SELECT actor, action, object FROM audit_entry WHERE action LIKE "
            "'document.%' ORDER BY seq"
        )
    ).all()
    assert [r[1] for r in rows] == ["document.upload", "document.list", "document.download"]
    assert all(r[0] == a.id for r in rows)


def test_unauthenticated_cannot_download(env: Env, db: Session) -> None:
    c, hdr, _ = who(env, db, "agent")
    doc = upload(c, hdr, client_for(c, hdr)).json()
    anon = TestClient(env.api.app, base_url="https://testserver")  # type: ignore[arg-type]
    assert anon.get(f"/api/documents/{doc['id']}/content").status_code == 401
    assert anon.get(f"/api/documents/{doc['id']}").status_code == 401


# T-M2-7 / P-11 --------------------------------------------------------------------------------
def test_limits_and_rejections(env: Env, db: Session, settings: Settings) -> None:
    c, hdr, _ = who(env, db, "agent")
    cid = client_for(c, hdr)
    assert upload(c, hdr, cid, b"MZ\x90\x00 an executable", "x.pdf").status_code == 415
    assert upload(c, hdr, cid, b"", "e.pdf").status_code == 422
    assert upload(c, hdr, cid, PDF, doc_type="nonsense").status_code == 422
    assert upload(c, hdr, cid, PDF, channel="pigeon").status_code == 422
    small = settings.model_copy(update={"max_upload_bytes": 100})
    app = create_app(small, db.get_bind(), blobs=env.store, antivirus=env.av)  # type: ignore[arg-type]
    c2 = TestClient(app, base_url="https://testserver")
    h2 = login(c2, db, make_account(db, "tiny@agency.il", ["agent"]))
    cid2 = client_for(c2, h2, "000000026")
    assert upload(c2, h2, cid2, PDF + b"x" * 200).status_code == 413
    assert upload(c2, h2, cid2, PDF).status_code == 201


@pytest.mark.parametrize(
    "name,expected",
    [
        ("../../etc/passwd.pdf", "passwd.pdf"),
        ("..\\..\\win\\evil.pdf", "evil.pdf"),
        ("/abs/path/file.pdf", "file.pdf"),
        (".hidden.pdf", "hidden.pdf"),
        ("na\x00me<>.pdf", "name.pdf"),
        ("", "file"),
    ],
)
def test_filename_sanitised(name: str, expected: str) -> None:
    from app.documents.service import safe_filename

    assert safe_filename(name) == expected


def test_hebrew_filename_survives_and_downloads(env: Env, db: Session) -> None:
    c, hdr, _ = who(env, db, "agent")
    doc = upload(c, hdr, client_for(c, hdr), name="פוליסה.pdf").json()
    assert doc["filename"] == "פוליסה.pdf"
    dl = c.get(f"/api/documents/{doc['id']}/content")
    assert "UTF-8''%D7%A4" in dl.headers["content-disposition"]


# retention / legal hold ------------------------------------------------------------------------
def test_retention_default_per_type_and_legal_hold(
    env: Env, db: Session, settings: Settings
) -> None:
    from datetime import UTC, datetime

    c, hdr, _ = who(env, db, "agent")
    cid = client_for(c, hdr)
    doc = upload(c, hdr, cid).json()
    year = int(doc["retention_until"][:4])
    assert year in (datetime.now(UTC).year + 7, datetime.now(UTC).year + 8)
    assert doc["legal_hold"] is False
    assert (
        c.patch(
            f"/api/documents/{doc['id']}/hold", json={"legal_hold": True}, headers=hdr
        ).status_code
        == 403
    )  # agents cannot place a hold
    m, mh, _ = who(env, db, "manager")
    r = m.patch(f"/api/documents/{doc['id']}/hold", json={"legal_hold": True}, headers=mh)
    assert r.status_code == 200 and r.json()["legal_hold"] is True


def test_per_type_retention_override() -> None:
    from datetime import UTC, datetime

    from app.documents.service import retention_until

    s = Settings(
        profile="demo",
        database_url="x",  # type: ignore[arg-type]
        retention_years_by_type={"id_card": 10},
    )
    t = datetime(2026, 1, 1, tzinfo=UTC)
    assert retention_until(s, "id_card", t).year == 2036
    assert retention_until(s, "policy", t).year == 2033


# T-M2-8 -------------------------------------------------------------------------------------
def test_reconcile_reports_orphans_and_missing_and_deletes_nothing(env: Env, db: Session) -> None:
    c, hdr, _ = who(env, db, "agent")
    upload(c, hdr, client_for(c, hdr))
    assert reconcile(db, env.store, "originals").ok
    orphan = env.store.put(b"uploaded but never recorded")
    key = db.execute(text("SELECT key FROM blob")).scalar_one()
    del env.store._objects[key]  # noqa: SLF001  (object lost from the store)
    rep = reconcile(db, env.store, "originals")
    assert rep.orphans == [orphan.key] and rep.missing == [key]
    assert env.store.exists(orphan.sha256)  # nothing was deleted by reconciliation


# integrity ------------------------------------------------------------------------------------
def test_tampered_object_is_detected_on_download(env: Env, db: Session) -> None:
    c, hdr, _ = who(env, db, "agent")
    doc = upload(c, hdr, client_for(c, hdr)).json()
    key = db.execute(text("SELECT key FROM blob")).scalar_one()
    env.store._objects[key] = b"swapped"  # noqa: SLF001
    r = c.get(f"/api/documents/{doc['id']}/content")
    assert r.status_code == 500 and r.json()["detail"] == "integrity_check_failed"
    assert (
        db.execute(
            text("SELECT count(*) FROM audit_entry WHERE action='integrity.fail'")
        ).scalar_one()
        == 1
    )
