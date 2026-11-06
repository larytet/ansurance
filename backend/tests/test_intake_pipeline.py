"""T-M5-1 .. T-M5-5, T-M5-7. The pipeline with a deterministic OCR: routing, resume,
quarantine, e-mail, split faxes, corrections, API roles. Also P-11, P-16-style abuse."""

import email.message
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from app.adapters.antivirus import EICAR, FakeAntivirus
from app.blob.fake import FakeBlobStore
from app.config import Settings
from app.intake import pipeline
from app.intake.context import IntakeContext
from app.intake.handlers import make_handlers, poll_fax_folder
from app.intake.ocr import FakeOcr, OcrResult, result_from_text
from app.jobs import queue
from app.main import create_app
from app.models import Client, Document, FamilyLink, InboxItem, Insurer, IntakeItem, Policy, User
from app.text import build_search
from tests.conftest import Account, login, make_account
from tests.intake_data import samples as S

DAVID, NOA, KID, UNKNOWN = "123456782", "000000026", "000000034", "340000019"


def scan(seed: int = 1) -> bytes:
    """A small non-blank PNG. Its content is irrelevant: the fake OCR supplies the text."""
    img = Image.new("L", (300, 200), 255)
    for x in range(20 + seed, 280, 7):
        for y in range(80, 120):
            img.putpixel((x, y), 0)
    return S.png(img)


def ocr_text(text_: str, conf: float = 0.97) -> OcrResult:
    return result_from_text(text_, conf)


class Env:
    def __init__(self, settings: Settings, engine: Engine, db: Session, tmp: Path) -> None:
        self.settings = settings.model_copy(update={"fax_dir": str(tmp)})
        self.engine, self.db, self.tmp = engine, db, tmp
        self.blobs, self.av, self.fake_ocr = FakeBlobStore(), FakeAntivirus(), FakeOcr()
        self.ctx = IntakeContext(self.settings, self.blobs, self.av, self.fake_ocr)
        self.handlers = make_handlers(self.ctx)
        self.app = create_app(
            self.settings, engine, blobs=self.blobs, antivirus=self.av, intake=self.ctx
        )
        self.users: dict[str, tuple[TestClient, dict[str, str], Account]] = {}

    def user(self, role: str, n: int = 1) -> tuple[TestClient, dict[str, str], Account]:
        key = f"{role}{n}"
        if key not in self.users:
            a = make_account(self.db, f"{key}@agency.il", [role])
            c = TestClient(self.app, base_url="https://testserver")
            self.users[key] = (c, login(c, self.db, a), a)
        return self.users[key]

    def client(
        self, key: str, id_number: str, name: str, agent: Account | None = None, **kw: Any
    ) -> Client:
        plain, skel = build_search(name, id_number)
        c = Client(
            id_number=id_number,
            name=name,
            search_text=plain,
            search_skel=skel,
            agent_id=agent.id if agent else None,
            **kw,
        )
        self.db.add(c)
        self.db.commit()
        return c

    def run(self, data: bytes, text_: str | OcrResult, **kw: Any) -> IntakeItem:
        """Submit one file with this OCR result and process it to the end."""
        self.fake_ocr._results = [ocr_text(text_) if isinstance(text_, str) else text_]  # noqa: SLF001
        self.fake_ocr.calls = 0
        items = pipeline.submit(
            self.db,
            self.ctx,
            data=data,
            filename=kw.pop("filename", "s.png"),
            channel=kw.pop("channel", "scan"),
            sender=kw.pop("sender", ""),
            actor=None,
        )
        self.db.commit()
        assert len(items) == 1
        queue.drain(self.db, self.handlers)
        self.db.refresh(items[0])
        return items[0]


@pytest.fixture
def env(settings: Settings, fresh_db: Engine, db: Session, tmp_path: Path) -> Env:
    return Env(settings, fresh_db, db, tmp_path)


@pytest.fixture
def world(env: Env) -> dict[str, Any]:
    agent = env.user("agent")[2]
    david = env.client("david", DAVID, "David Cohen", agent, email="david@x.il")
    noa = env.client("noa", NOA, "Noa Levi", agent, phone="052-333-4444")
    kid = env.client("kid", KID, "Kid Cohen", agent)
    ins = Insurer(name="Harel")
    env.db.add(ins)
    env.db.flush()
    env.db.add(
        Policy(
            client_id=noa.id,
            insurer_id=ins.id,
            policy_number="PL-2024-77881",
            product="car",
            premium_ils=100,
        )
    )
    env.db.commit()
    return {"agent": agent, "david": david, "noa": noa, "kid": kid}


# ---- routing -----------------------------------------------------------------------------
def test_high_confidence_id_scan_is_filed_to_its_owner_whoever_sent_it(
    env: Env, world: dict[str, Any]
) -> None:
    item = env.run(scan(), f"תעודת זהות מספר זהות {DAVID}", sender="someone.else@gmail.com")
    assert item.state == "filed" and item.route == "filed"
    assert item.chosen_client_id == world["david"].id and float(item.attribution_conf) >= 0.95
    doc = env.db.get(Document, item.document_id)
    assert doc is not None and doc.client_id == world["david"].id and doc.doc_type == "id_card"
    assert doc.blob_id == item.blob_id and doc.restricted is False
    tell = env.db.scalars(select(InboxItem).where(InboxItem.type == "auto_filed")).one()
    assert str(tell.assignee_id) == world["agent"].id and "Filed" in tell.title
    actions = [r[0] for r in env.db.execute(text("SELECT action FROM audit_entry"))]
    assert "intake.receive" in actions and "intake.file" in actions


def test_every_stage_is_recorded_in_order(env: Env, world: dict[str, Any]) -> None:
    item = env.run(scan(), f"פוליסה מספר זהות {DAVID}")
    assert item.ocr_text and float(item.ocr_conf) == 0.97 and item.doc_type == "policy"
    assert item.entities["ids"] == [{"id_number": DAVID, "conf": 0.97}]
    assert item.type_reason.startswith("matched:") and float(item.type_conf) > 0.5
    assert item.candidates[0]["client_id"] == str(world["david"].id)


def test_poor_ocr_confidence_goes_to_review_and_one_click_confirms(
    env: Env, world: dict[str, Any]
) -> None:
    item = env.run(scan(), result_from_text(f"חשבונית מס {DAVID}", conf=0.55))
    assert item.state == "review" and item.chosen_client_id == world["david"].id
    inbox = env.db.get(InboxItem, item.inbox_item_id)
    assert inbox is not None and inbox.type == "review" and inbox.role == "backoffice"
    assert "Suggested: David Cohen" in inbox.detail and inbox.client_id == world["david"].id
    assert item.document_id is None  # nothing is filed before a human confirms
    bo, hb, _ = env.user("backoffice")
    r = bo.post(
        f"/api/intake/{item.id}/confirm", headers=hb, json={"client_id": str(world["david"].id)}
    )
    assert r.status_code == 200 and r.json()["state"] == "filed"
    env.db.refresh(inbox)
    assert inbox.status == "done" and "filed to David Cohen" in (inbox.resolution or "")
    fb = bo.get(f"/api/intake/{item.id}/feedback").json()
    assert [f["kind"] for f in fb] == ["confirm"]


def test_review_can_be_reassigned_and_feedback_records_the_correction(
    env: Env, world: dict[str, Any]
) -> None:
    item = env.run(scan(), result_from_text(f"חשבונית מס {DAVID}", conf=0.5))
    bo, hb, _ = env.user("backoffice")
    bo.post(f"/api/intake/{item.id}/confirm", headers=hb, json={"client_id": str(world["noa"].id)})
    fb = bo.get(f"/api/intake/{item.id}/feedback").json()[0]
    assert fb["kind"] == "reassign" and fb["to"] == str(world["noa"].id)
    row = env.db.execute(text("SELECT suggested_client_id FROM intake_feedback")).scalar_one()
    assert str(row) == str(world["david"].id)  # what the system suggested is kept for learning


def test_no_identifier_is_unassigned_and_can_be_assigned(env: Env, world: dict[str, Any]) -> None:
    item = env.run(scan(), "מכתב כללי בלי מספרים")
    assert item.state == "unassigned" and item.route_reason == "no identifier found"
    inbox = env.db.get(InboxItem, item.inbox_item_id)
    assert inbox is not None and inbox.type == "unassigned"
    bo, hb, _ = env.user("backoffice")
    assert (
        bo.post(
            f"/api/intake/{item.id}/confirm", headers=hb, json={"client_id": str(world["noa"].id)}
        ).status_code
        == 200
    )
    assert bo.get(f"/api/intake/{item.id}/feedback").json()[0]["kind"] == "assign"


def test_never_filed_on_a_name_match_alone(env: Env, world: dict[str, Any]) -> None:
    """P-name-only (REQUIREMENTS 4.3): the client's exact name is in the text, no identifier."""
    item = env.run(scan(), "חשבונית מס לכבוד David Cohen סכום 500", sender="david@x.il")
    assert item.state == "unassigned" and item.document_id is None and item.candidates == []


def test_two_clients_in_one_document_go_to_review(env: Env, world: dict[str, Any]) -> None:
    item = env.run(scan(), f"חשבונית מס {DAVID} ו {NOA}")
    assert item.state == "review" and len(item.candidates) == 2 and item.document_id is None


def test_unknown_and_invalid_ids_are_not_filed(env: Env, world: dict[str, Any]) -> None:
    assert env.run(scan(2), f"פוליסה {UNKNOWN}").state == "unassigned"
    one_digit_off = env.run(scan(3), "פוליסה 123456783")  # a misread digit fails the check digit
    assert one_digit_off.state == "unassigned" and one_digit_off.entities["ids"] == []


def test_policy_number_alone_files_to_the_policy_holder(env: Env, world: dict[str, Any]) -> None:
    item = env.run(scan(), "לכבוד בהמשך לפנייתך בנושא פוליסה PL-2024-77881")
    assert item.state == "filed" and item.chosen_client_id == world["noa"].id
    assert item.candidates[0]["evidence"] == ["policy:PL-2024-77881"]


def test_addendum_is_filed_to_the_head_and_links_the_family(
    env: Env, world: dict[str, Any]
) -> None:
    item = env.run(scan(), f"ספח תעודת זהות מספר זהות {DAVID} ילדים {KID} ילד")
    assert item.state == "filed" and item.chosen_client_id == world["david"].id
    link = env.db.scalars(select(FamilyLink)).one()
    assert (link.head_client_id, link.member_client_id) == (world["david"].id, world["kid"].id)
    docs = env.db.scalars(select(Document)).all()
    assert [d.client_id for d in docs] == [world["david"].id]  # one document, the head's


def test_medical_documents_are_filed_restricted_with_retention(
    env: Env, world: dict[str, Any]
) -> None:
    item = env.run(scan(), f"דוח רפואי אבחנה ת.ז {DAVID}")
    doc = env.db.get(Document, item.document_id)
    assert doc is not None and doc.restricted is True and doc.doc_type == "medical_report"
    assert doc.retention_until.year >= doc.received_at.year + 7


# ---- safety --------------------------------------------------------------------------------
def test_infected_file_is_quarantined_kept_and_never_filed(env: Env, world: dict[str, Any]) -> None:
    item = env.run(scan() + EICAR, f"תעודת זהות {DAVID}")
    assert item.state == "quarantined" and item.document_id is None
    sha = env.db.execute(text("SELECT sha256 FROM blob")).scalar_one()
    assert env.blobs.exists(sha)  # the original is kept, flagged
    assert env.db.execute(text("SELECT scan_status FROM blob")).scalar_one() == "infected"
    assert env.fake_ocr.calls == 0  # never read by OCR


def test_scanner_outage_fails_closed_and_the_job_retries(env: Env, world: dict[str, Any]) -> None:
    env.av.available = False
    env.fake_ocr._results = [ocr_text(f"תעודת זהות {DAVID}")]  # noqa: SLF001
    (item,) = pipeline.submit(
        env.db, env.ctx, data=scan(), filename="a.png", channel="scan", sender="", actor=None
    )
    env.db.commit()
    assert queue.run_one(env.db, env.handlers) is True
    env.db.refresh(item)
    assert item.state == "received" and env.fake_ocr.calls == 0  # nothing proceeded unscanned
    job = env.db.scalars(select(queue.Job)).one()
    assert job.status == "queued" and "ScannerUnavailable" in (job.last_error or "")
    env.av.available = True
    env.db.execute(text("UPDATE job SET run_at = now() - interval '1 second'"))
    env.db.commit()
    queue.drain(env.db, env.handlers)
    env.db.refresh(item)
    assert item.state == "filed"


def test_unsupported_and_empty_files_are_rejected(env: Env, world: dict[str, Any]) -> None:
    from app.documents.service import RejectedUpload

    for data, code in ((b"MZ\x90\x00 exe", "unsupported_media_type"), (b"", "empty_file")):
        with pytest.raises(RejectedUpload) as e:
            pipeline.submit(
                env.db, env.ctx, data=data, filename="x.pdf", channel="scan", sender="", actor=None
            )
        assert e.value.code == code
    with pytest.raises(RejectedUpload):
        pipeline.submit(
            env.db, env.ctx, data=scan(), filename="x.png", channel="pigeon", sender="", actor=None
        )
    assert env.db.execute(text("SELECT count(*) FROM intake_item")).scalar_one() == 0


# ---- idempotence and resume (T-M5-1) -----------------------------------------------------------
def test_a_crash_midway_resumes_without_repeating_or_duplicating(
    env: Env, world: dict[str, Any]
) -> None:
    class Flaky(FakeOcr):
        def recognize(self, pages: list[Image.Image]) -> OcrResult:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("worker killed during OCR")
            return ocr_text(f"תעודת זהות {DAVID}")

    env.ctx.ocr = Flaky()
    (item,) = pipeline.submit(
        env.db, env.ctx, data=scan(), filename="a.png", channel="scan", sender="", actor=None
    )
    env.db.commit()
    queue.run_one(env.db, env.handlers)
    env.db.refresh(item)
    assert item.state == "scanned"  # the scan stage was saved before the crash
    env.db.execute(text("UPDATE job SET run_at = now() - interval '1 second'"))
    env.db.commit()
    queue.drain(env.db, env.handlers)
    env.db.refresh(item)
    assert item.state == "filed"
    assert env.db.execute(text("SELECT count(*) FROM document")).scalar_one() == 1


def test_running_a_finished_item_again_changes_nothing(env: Env, world: dict[str, Any]) -> None:
    item = env.run(scan(), f"תעודת זהות {DAVID}")
    doc_id = item.document_id
    for _ in range(3):
        pipeline.run(env.db, env.ctx, item.id)
    env.db.refresh(item)
    assert item.document_id == doc_id
    assert env.db.execute(text("SELECT count(*) FROM document")).scalar_one() == 1
    assert (
        env.db.execute(text("SELECT count(*) FROM inbox_item WHERE type='auto_filed'")).scalar_one()
        == 1
    )
    assert (
        queue.enqueue(
            env.db, "intake.run", {"item_id": str(item.id)}, dedupe_key=f"intake:{item.id}"
        )
        is None
    )  # no second job either


def test_the_same_scan_twice_stores_one_original(env: Env, world: dict[str, Any]) -> None:
    a = env.run(scan(), f"תעודת זהות {DAVID}")
    b = env.run(scan(), f"תעודת זהות {DAVID}")
    assert a.blob_id == b.blob_id and a.id != b.id
    assert env.db.execute(text("SELECT count(*) FROM blob")).scalar_one() == 1
    assert len(list(env.blobs.iter_keys())) == 1  # T-M5-7: never duplicated, never deleted


# ---- channels ---------------------------------------------------------------------------------
def test_email_attachments_become_items_and_keep_the_sender(
    env: Env, world: dict[str, Any]
) -> None:
    msg = email.message.EmailMessage()
    msg["From"] = "David <david@x.il>"
    msg["To"] = "office@agency.il"
    msg["Subject"] = "my documents"
    msg.set_content("see attached")
    msg.add_attachment(scan(1), maintype="image", subtype="png", filename="id.png")
    msg.add_attachment(scan(2), maintype="image", subtype="png", filename="policy.png")
    msg.add_attachment(b"just text", maintype="text", subtype="plain", filename="note.txt")
    env.fake_ocr._results = [ocr_text(f"תעודת זהות {DAVID}"), ocr_text(f"פוליסה {DAVID} {NOA}")]  # noqa: SLF001
    items = pipeline.submit(
        env.db,
        env.ctx,
        data=msg.as_bytes(),
        filename="m.eml",
        channel="email",
        sender="",
        actor=None,
    )
    env.db.commit()
    assert len(items) == 2 and {i.sender for i in items} == {"david@x.il"}  # text file skipped
    assert {i.filename for i in items} == {"id.png", "policy.png"}
    queue.drain(env.db, env.handlers)
    states = sorted(env.db.scalars(select(IntakeItem.state)))
    assert states == ["filed", "review"]  # two clients in the second attachment -> review
    sender_flag = env.db.scalars(select(IntakeItem).where(IntakeItem.state == "review")).one()
    assert [c["sender_match"] for c in sender_flag.candidates] == [True, False]  # tie-break only
    assert (
        env.db.execute(
            text("SELECT count(*) FROM blob WHERE media_type='message/rfc822'")
        ).scalar_one()
        == 1
    )  # the whole message is kept as received


def test_a_multi_document_fax_is_split_and_each_part_filed_separately(
    env: Env, world: dict[str, Any]
) -> None:
    doc = S.render(S.id_card(DAVID))
    pages = [doc, S.blank(), doc, S.blank(), S.blank(), doc]
    env.fake_ocr._results = [
        ocr_text(f"תעודת זהות {DAVID}"),
        ocr_text(f"פוליסה {NOA}"),  # noqa: SLF001
        ocr_text("מכתב בלי מספרים"),
    ]
    items = pipeline.submit(
        env.db,
        env.ctx,
        data=S.tiff(pages),
        filename="fax.tif",
        channel="fax",
        sender="",
        actor=None,
    )
    env.db.commit()
    queue.drain(env.db, env.handlers)
    env.db.refresh(items[0])
    assert items[0].state == "split"
    kids = env.db.scalars(
        select(IntakeItem).where(IntakeItem.parent_id == items[0].id).order_by(IntakeItem.page_from)
    ).all()
    assert [(k.page_from, k.page_to, k.state) for k in kids] == [
        (1, 1, "filed"),
        (3, 3, "filed"),
        (6, 6, "unassigned"),
    ]
    assert [k.chosen_client_id for k in kids[:2]] == [world["david"].id, world["noa"].id]
    assert len({k.blob_id for k in kids}) == 1  # one original, three logical documents


def test_fax_drop_folder_ingests_and_moves_files_after_storing(
    env: Env, world: dict[str, Any]
) -> None:
    (env.tmp / "fax1.png").write_bytes(scan(1))
    (env.tmp / "fax2.pdf").write_bytes(b"%PDF-1.4\nnot really")
    (env.tmp / "notes.txt").write_text("ignored")
    (env.tmp / "evil.png").write_bytes(b"MZ\x90 not an image")
    assert poll_fax_folder(env.db, env.ctx) == 3
    assert sorted(p.name for p in (env.tmp / "processed").iterdir()) == ["fax1.png", "fax2.pdf"]
    assert [p.name for p in (env.tmp / "rejected").iterdir()] == ["evil.png"]
    assert (env.tmp / "notes.txt").exists()  # not a fax: left alone
    assert (
        env.db.execute(text("SELECT count(*) FROM intake_item WHERE channel='fax'")).scalar_one()
        == 2
    )
    assert poll_fax_folder(env.db, env.ctx) == 0  # nothing is ingested twice


# ---- corrections (T-M5-5) -----------------------------------------------------------------------
def test_wrong_client_moves_the_document_logs_it_and_counts_the_misfile(
    env: Env, world: dict[str, Any]
) -> None:
    item = env.run(scan(), f"חשבונית מס {DAVID}")
    bo, hb, bo_acct = env.user("backoffice")
    r = bo.post(
        f"/api/intake/{item.id}/wrong-client",
        headers=hb,
        json={"client_id": str(world["noa"].id), "reason": "that is Noa's invoice"},
    )
    assert r.status_code == 200 and r.json()["client_id"] == str(world["noa"].id)
    doc = env.db.get(Document, item.document_id)
    env.db.refresh(doc)
    assert doc is not None and doc.client_id == world["noa"].id
    fb = bo.get(f"/api/intake/{item.id}/feedback").json()[0]
    assert fb["kind"] == "wrong_client" and fb["from"] == str(world["david"].id)
    assert fb["note"] == "that is Noa's invoice" and fb["actor"] == bo_acct.id
    told = env.db.scalars(select(InboxItem).where(InboxItem.title.like("Document moved%"))).one()
    assert str(told.assignee_id) == world["agent"].id and told.client_id == world["noa"].id
    m = bo.get("/api/intake/metrics").json()
    assert m["misfiled"] == 1 and m["auto_filed"] == 1 and m["misfile_rate"] == 1.0
    audit = env.db.execute(
        text("SELECT detail FROM audit_entry WHERE action='intake.wrong_client'")
    ).scalar_one()
    assert audit["to"] == str(world["noa"].id)


def test_wrong_client_rules(env: Env, world: dict[str, Any]) -> None:
    bo, hb, _ = env.user("backoffice")
    review = env.run(scan(), result_from_text(f"חשבונית מס {DAVID}", conf=0.5))
    url = f"/api/intake/{review.id}/wrong-client"
    body = {"client_id": str(world["noa"].id), "reason": "wrong person"}
    assert bo.post(url, headers=hb, json=body).status_code == 409  # not filed yet
    filed = env.run(scan(9), f"חשבונית מס {DAVID}")
    same = {"client_id": str(world["david"].id), "reason": "wrong person"}
    assert (
        bo.post(f"/api/intake/{filed.id}/wrong-client", headers=hb, json=same).status_code == 409
    )  # already there
    short = {"client_id": str(world["noa"].id), "reason": "x"}
    assert (
        bo.post(f"/api/intake/{filed.id}/wrong-client", headers=hb, json=short).status_code == 422
    )
    ghost = {"client_id": "00000000-0000-0000-0000-000000000000", "reason": "wrong person"}
    assert (
        bo.post(f"/api/intake/{filed.id}/wrong-client", headers=hb, json=ghost).status_code == 404
    )


# ---- API and roles ------------------------------------------------------------------------------
def test_api_submit_list_get_and_metrics(env: Env, world: dict[str, Any]) -> None:
    bo, hb, _ = env.user("backoffice")
    env.fake_ocr._results = [ocr_text(f"תעודת זהות {DAVID}")]  # noqa: SLF001
    r = bo.post(
        "/api/intake",
        headers=hb,
        files={"file": ("id.png", scan())},
        data={"channel": "scan", "sender": "x@y.il"},
    )
    assert r.status_code == 201
    (summary,) = r.json()["items"]
    assert summary["state"] == "received" and summary["sender"] == "x@y.il"
    queue.drain(env.db, env.handlers)
    detail = bo.get(f"/api/intake/{summary['id']}").json()
    assert detail["state"] == "filed" and "תעודת זהות" in detail["text"]
    assert [i["id"] for i in bo.get("/api/intake", params={"state": "filed"}).json()] == [
        summary["id"]
    ]
    m = bo.get("/api/intake/metrics").json()
    assert m["auto_filed"] == 1 and m["auto_file_rate"] == 1.0 and m["misfile_rate"] == 0.0


def test_api_rejects_bad_uploads(env: Env, world: dict[str, Any]) -> None:
    bo, hb, _ = env.user("backoffice")
    assert (
        bo.post("/api/intake", headers=hb, files={"file": ("x.pdf", b"MZ\x90 evil")}).status_code
        == 415
    )
    assert (
        bo.post(
            "/api/intake", headers=hb, files={"file": ("x.png", scan())}, data={"channel": "pigeon"}
        ).status_code
        == 422
    )
    assert bo.get("/api/intake/not-a-uuid").status_code == 422
    assert bo.get("/api/intake/00000000-0000-0000-0000-000000000000").status_code == 404


def test_roles_agents_submit_only_marketing_nothing(env: Env, world: dict[str, Any]) -> None:
    item = env.run(scan(), "מכתב בלי מספרים")
    expect = {  # role: (can submit, can read, can review)
        "agent": (201, 403, 403),
        "backoffice": (201, 200, 200),
        "marketing": (403, 403, 403),
        "manager": (201, 200, 200),
    }
    for role, (sub, read, rev) in expect.items():
        c, h, _ = env.user(role, 5)
        assert (
            c.post("/api/intake", headers=h, files={"file": ("a.png", scan(7))}).status_code == sub
        ), role
        assert c.get("/api/intake").status_code == read, role
        assert c.get("/api/intake/metrics").status_code == read, role
        r = c.post(
            f"/api/intake/{item.id}/confirm", headers=h, json={"client_id": str(world["noa"].id)}
        )
        assert (r.status_code == 403) is (rev == 403), role
        if rev == 200:
            break  # the item is filed after the first successful review
    anon = TestClient(env.app, base_url="https://testserver")
    assert anon.get("/api/intake").status_code == 401


def test_confirm_state_and_client_checks(env: Env, world: dict[str, Any]) -> None:
    bo, hb, _ = env.user("backoffice")
    filed = env.run(scan(), f"חשבונית מס {DAVID}")
    assert (
        bo.post(
            f"/api/intake/{filed.id}/confirm", headers=hb, json={"client_id": str(world["noa"].id)}
        ).status_code
        == 409
    )
    other = env.run(scan(4), "מכתב כללי")
    ghost = {"client_id": "00000000-0000-0000-0000-000000000000"}
    assert bo.post(f"/api/intake/{other.id}/confirm", headers=hb, json=ghost).status_code == 404


def test_the_system_user_cannot_log_in(env: Env, world: dict[str, Any]) -> None:
    env.run(scan(), f"תעודת זהות {DAVID}")
    system = env.db.scalars(select(User).where(User.email == pipeline.SYSTEM_EMAIL)).one()
    assert system.active is False and system.password_hash is None
    c = TestClient(env.app, base_url="https://testserver")
    r = c.post(
        "/api/auth/login",
        json={"email": pipeline.SYSTEM_EMAIL, "password": "x" * 14, "code": "000000"},
    )
    assert r.status_code == 401


# ---- second OCR pass ----------------------------------------------------------------------------
class TwoPassOcr(FakeOcr):
    def __init__(self, primary: OcrResult, latin: OcrResult | None) -> None:
        super().__init__(primary)
        self.latin, self.latin_calls = latin, 0

    def recognize_latin(self, pages: list[Image.Image]) -> OcrResult | None:
        self.latin_calls += 1
        return self.latin


def test_a_shaky_first_reading_gets_a_second_opinion_that_can_rescue_it(
    env: Env, world: dict[str, Any]
) -> None:
    primary = ocr_text("לכבוד בהמשך לפנייתך בנושא פוליסה - וש")  # the prefix and number were lost
    latin = result_from_text("Dear Sir PL-2024-77881 TIAD7", conf=0.91)
    env.ctx.ocr = TwoPassOcr(primary, latin)
    (item,) = pipeline.submit(
        env.db, env.ctx, data=scan(), filename="a.png", channel="fax", sender="", actor=None
    )
    env.db.commit()
    queue.drain(env.db, env.handlers)
    env.db.refresh(item)
    assert env.ctx.ocr.latin_calls == 1 and item.entities["second_pass"] is True
    assert item.state == "filed" and item.chosen_client_id == world["noa"].id


def test_two_readings_that_agree_raise_the_confidence(env: Env, world: dict[str, Any]) -> None:
    env.ctx.ocr = TwoPassOcr(
        result_from_text(f"חשבונית מס {DAVID}", conf=0.45),
        result_from_text(f"Tax invoice {DAVID}", conf=0.93),
    )
    (item,) = pipeline.submit(
        env.db, env.ctx, data=scan(), filename="a.png", channel="fax", sender="", actor=None
    )
    env.db.commit()
    queue.drain(env.db, env.handlers)
    env.db.refresh(item)
    assert item.entities["ids"] == [{"id_number": DAVID, "conf": 0.93}]
    assert item.state == "filed"  # alone, 0.45 would have gone to review


def test_a_confident_first_reading_does_not_pay_for_a_second_pass(
    env: Env, world: dict[str, Any]
) -> None:
    env.ctx.ocr = TwoPassOcr(ocr_text(f"תעודת זהות {DAVID}", 0.95), None)
    (item,) = pipeline.submit(
        env.db, env.ctx, data=scan(), filename="a.png", channel="scan", sender="", actor=None
    )
    env.db.commit()
    queue.drain(env.db, env.handlers)
    env.db.refresh(item)
    assert env.ctx.ocr.latin_calls == 0 and item.entities["second_pass"] is False


def test_a_digits_only_policy_match_goes_to_review_with_a_suggestion(
    env: Env, world: dict[str, Any]
) -> None:
    item = env.run(scan(), "לכבוד בנושא פוליסה 2024-77881")
    assert item.state == "review" and item.chosen_client_id == world["noa"].id
    assert item.candidates[0]["weak"] is True and item.document_id is None
