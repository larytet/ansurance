"""M19 at the API: users see only page images made in the sandbox, and the original is
a guarded download. P-30, P-32 (access), the viewer's headers, the sandbox refusal route."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.adapters.antivirus import EICAR, FakeAntivirus
from app.blob.fake import FakeBlobStore
from app.config import Settings
from app.intake.context import IntakeContext
from app.intake.handlers import make_handlers
from app.intake.ocr import FakeOcr
from app.jobs import queue
from app.main import create_app
from tests.intake_data import samples as S
from tests.sandbox_inline import InlineSandbox
from tests.test_documents import PDF, Env, client_for, upload, who


@pytest.fixture
def env(settings: Settings, fresh_db: Engine) -> Env:
    store, av = FakeBlobStore(), FakeAntivirus()
    app = create_app(settings, fresh_db, blobs=store, antivirus=av)
    return Env(TestClient(app, base_url="https://testserver"), store, av)


def render_jobs(env: Env, db: Session) -> None:
    ctx = IntakeContext(
        env.api.app.state.settings,  # type: ignore[attr-defined]
        env.store,
        env.av,
        FakeOcr(),
        InlineSandbox(),
    )
    queue.drain(db, make_handlers(ctx))
    db.commit()


def two_page_pdf() -> bytes:
    return S.pdf([S.render(S.id_card("123456782")), S.render(S.invoice("000000026"))])


def test_pages_are_made_by_the_sandbox_and_served_as_images(env: Env, db: Session) -> None:
    c, hdr, _ = who(env, db, "agent")
    cid = client_for(c, hdr)
    doc = upload(c, hdr, cid, two_page_pdf()).json()
    first = c.get(f"/api/documents/{doc['id']}/pages").json()
    assert first == {"status": "pending", "pages": 0, "reason": None}
    render_jobs(env, db)
    info = c.get(f"/api/documents/{doc['id']}/pages").json()
    assert info == {"status": "ready", "pages": 2, "reason": None}
    img = c.get(f"/api/documents/{doc['id']}/pages/2")
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    assert img.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert img.content != two_page_pdf() and b"%PDF" not in img.content
    assert img.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in img.headers["content-security-policy"]
    assert "no-store" in img.headers["cache-control"]
    assert c.get(f"/api/documents/{doc['id']}/pages/3").status_code == 404
    assert c.get(f"/api/documents/{doc['id']}/pages/0").status_code == 404


def test_an_unreadable_file_is_refused_with_a_reason_and_shows_nothing(
    env: Env, db: Session
) -> None:
    c, hdr, _ = who(env, db, "agent")
    cid = client_for(c, hdr)
    doc = upload(c, hdr, cid, b"%PDF-1.4\n" + b"garbage" * 50, "bad.pdf").json()
    render_jobs(env, db)
    info = c.get(f"/api/documents/{doc['id']}/pages").json()
    assert info["status"] == "refused" and info["pages"] == 0 and info["reason"]
    assert c.get(f"/api/documents/{doc['id']}/pages/1").status_code == 404
    actions = [
        r[0]
        for r in db.execute(
            text("SELECT action FROM audit_entry WHERE action LIKE 'document.render%'")
        )
    ]
    assert actions == ["document.render_refused"]


def test_pages_follow_the_same_access_rules_as_the_document(env: Env, db: Session) -> None:
    c1, h1, _ = who(env, db, "agent", 1)
    c2, _, _ = who(env, db, "agent", 2)
    cm, _, _ = who(env, db, "marketing")
    cid = client_for(c1, h1)
    doc = upload(c1, h1, cid, two_page_pdf(), doc_type="medical_report").json()
    render_jobs(env, db)
    assert c1.get(f"/api/documents/{doc['id']}/pages/1").status_code == 200
    assert c2.get(f"/api/documents/{doc['id']}/pages/1").status_code == 404  # not their client
    assert cm.get(f"/api/documents/{doc['id']}/pages/1").status_code == 403
    anon = TestClient(env.api.app, base_url="https://testserver")  # type: ignore[arg-type]
    assert anon.get(f"/api/documents/{doc['id']}/pages/1").status_code == 401


def test_an_infected_file_has_no_pages(env: Env, db: Session) -> None:
    c, hdr, _ = who(env, db, "agent")
    cid = client_for(c, hdr)
    doc = upload(c, hdr, cid, b"%PDF-1.4 " + EICAR, "eicar.pdf").json()
    assert c.get(f"/api/documents/{doc['id']}/pages").status_code == 409
    render_jobs(env, db)
    assert db.execute(text("SELECT count(*) FROM blob_render")).scalar() == 0


def test_viewing_a_page_is_audited(env: Env, db: Session) -> None:
    c, hdr, _ = who(env, db, "agent")
    cid = client_for(c, hdr)
    doc = upload(c, hdr, cid, two_page_pdf()).json()
    render_jobs(env, db)
    c.get(f"/api/documents/{doc['id']}/pages/1")
    env.api.app.state.read_audit.flush()
    rows = db.execute(text("SELECT action FROM audit_entry WHERE action = 'document.view'")).all()
    assert len(rows) == 1


# ---- the original is a guarded download (P-30) -----------------------------------------------
def test_the_original_needs_the_capability_and_the_warning(env: Env, db: Session) -> None:
    ca, ha, _ = who(env, db, "agent")
    cb, _, _ = who(env, db, "backoffice")
    cid = client_for(ca, ha)
    doc = upload(ca, ha, cid, PDF).json()
    url = f"/api/documents/{doc['id']}/content"
    assert ca.get(url + "?acknowledge=true").status_code == 403  # an agent sees pages only
    r = cb.get(url)
    assert r.status_code == 409 and r.json()["detail"] == "warning_required"
    assert not db.execute(
        text("SELECT 1 FROM audit_entry WHERE action = 'document.download'")
    ).all()
    ok = cb.get(url + "?acknowledge=true")
    assert ok.status_code == 200 and ok.content == PDF
    assert ok.headers["content-type"] == "application/octet-stream"
    assert ok.headers["content-disposition"].startswith("attachment")
    row = db.execute(
        text("SELECT detail FROM audit_entry WHERE action = 'document.download'")
    ).scalar()
    assert row["warning_acknowledged"] is True  # type: ignore[index]


def test_the_original_is_never_served_inline_by_any_other_route(env: Env, db: Session) -> None:
    c, hdr, _ = who(env, db, "manager")
    cid = client_for(c, hdr)
    doc = upload(c, hdr, cid, PDF).json()
    for path in ("", "/inline", "/view", "/preview", "/thumbnail", "/raw", "/download"):
        r = c.get(f"/api/documents/{doc['id']}{path}")
        assert r.content != PDF, path
    listing = c.get(f"/api/clients/{cid}/documents").text
    assert "content" not in listing.replace("content-", "")  # no link to the file in the data
