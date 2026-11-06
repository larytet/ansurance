"""T-M5-6: accuracy of the whole pipeline with the real OCR (Tesseract, Hebrew and English).

The sample is synthetic (rendered Hebrew and English documents, some skewed, noisy or
fax-like), so it proves the pipeline end to end and guards against regressions. It does
NOT replace measuring on the agency's own scans (REQUIREMENTS 4.3): real faxes are worse.
Eight workers process the queue concurrently, which also exercises SKIP LOCKED.
"""

import json
import random
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.adapters.antivirus import FakeAntivirus
from app.blob.fake import FakeBlobStore
from app.config import Settings
from app.ids import is_valid_israeli_id
from app.intake import pipeline
from app.intake.context import IntakeContext
from app.intake.handlers import make_handlers
from app.intake.ocr import OcrUnavailable, TesseractOcr, installed_languages
from app.jobs import queue
from app.models import Client, Insurer, IntakeItem, Policy
from app.text import build_search
from tests.intake_data import samples as S

try:
    HAVE_OCR = {"heb", "eng"} <= installed_languages()
except OcrUnavailable:
    HAVE_OCR = False

pytestmark = [
    pytest.mark.skipif(not HAVE_OCR, reason="tesseract with heb+eng is not installed"),
    pytest.mark.slow,
]
RESULTS = Path(__file__).parent / "intake_data" / "last_accuracy.json"


def valid_id(rng: random.Random) -> str:
    while True:
        body = str(rng.randint(10**7, 10**8 - 1))
        for d in "0123456789":
            if is_valid_israeli_id(body + d):
                return body + d


def test_pipeline_accuracy_with_real_ocr(
    settings: Settings, fresh_db: Engine, db: Session, tmp_path: Path
) -> None:
    rng = random.Random(20261106)
    ids: set[str] = set()
    while len(ids) < 45:
        ids.add(valid_id(rng))
    id_list = sorted(ids)
    known, unknown_ids = id_list[:40], id_list[40:]
    names = ["David Cohen", "Noa Levi", "Moshe Katz", "Sara Mizrahi", "Yossi Peretz"]
    clients: list[Client] = []
    ins = Insurer(name="Harel")
    db.add(ins)
    for i, nid in enumerate(known):
        name = f"{names[i % 5]} {i}"
        plain, skel = build_search(name, nid)
        clients.append(Client(id_number=nid, name=name, search_text=plain, search_skel=skel))
    db.add_all(clients)
    db.flush()
    policies = {}
    for i, c in enumerate(clients):
        number = f"PL-2024-{rng.randint(10000, 99999)}"
        policies[c.id] = number
        db.add(
            Policy(
                client_id=c.id,
                insurer_id=ins.id,
                policy_number=number,
                product="car",
                premium_ils=100 + i,
            )
        )
    db.commit()

    templates = [
        ("id_card", lambda c: S.id_card(c.id_number)),
        ("policy", lambda c: S.policy(policies[c.id], c.id_number)),
        ("claim_form", lambda c: S.claim(policies[c.id], c.id_number)),
        ("medical_report", lambda c: S.medical(c.id_number)),
        ("invoice", lambda c: S.invoice(c.id_number)),
        ("bank_form", lambda c: S.bank_form(c.id_number)),
        ("insurer_letter", lambda c: S.insurer_letter(policies[c.id])),
    ]
    # (image, expected client id or None, expected states)
    cases: list[tuple[str, bytes, str | None, set[str]]] = []
    for n in range(60):
        c = clients[n % len(clients)]
        kind, make = templates[n % len(templates)]
        if n < 45:
            img = S.render(make(c), seed=n)  # clean scan
        elif n < 55:
            img = S.render(make(c), skew=rng.choice([-2.0, 2.0]), noise=14, seed=n)  # skewed
        else:
            img = S.render(make(c), fax=True, skew=1.5, noise=20, seed=n)  # fax-like
        # a degraded page may legitimately be reviewed, but must never be filed wrongly
        ok = {"filed"} if n < 45 else {"filed", "review", "unassigned"}
        cases.append((f"{kind}-{n}", S.png(img), str(c.id), ok))
    # negatives: must never be filed
    for n in range(3):
        cases.append(
            (f"letter-{n}", S.png(S.render(S.letter_no_ids(), seed=100 + n)), None, {"unassigned"})
        )
        cases.append(
            (
                f"nameonly-{n}",
                S.png(S.render(S.name_only(clients[n].name), seed=110 + n)),
                None,
                {"unassigned"},
            )
        )
        cases.append(
            (
                f"unknown-{n}",
                S.png(S.render(S.invoice(unknown_ids[n]), seed=120 + n)),
                None,
                {"unassigned"},
            )
        )
        two = S.invoice(clients[n].id_number) + [f"ת.ז נוספת {clients[n + 5].id_number}"]
        cases.append((f"two-{n}", S.png(S.render(two, seed=130 + n)), None, {"review"}))

    ocr = TesseractOcr("heb+eng")
    ctx = IntakeContext(settings, FakeBlobStore(), FakeAntivirus(), ocr)
    handlers = make_handlers(ctx)
    by_name: dict[str, tuple[str | None, set[str]]] = {}
    for name, data, expected, states in cases:
        (item,) = pipeline.submit(
            db, ctx, data=data, filename=f"{name}.png", channel="fax", sender="", actor=None
        )
        by_name[str(item.id)] = (expected, states)
        item.filename = name
    db.commit()

    maker = sessionmaker(fresh_db, expire_on_commit=False)

    def worker(_: int) -> int:
        with maker() as s:
            return queue.drain(s, handlers)

    with ThreadPoolExecutor(max_workers=8) as ex:
        processed = sum(ex.map(worker, range(8)))
    assert processed == len(cases)

    db.expire_all()
    items = {str(i.id): i for i in db.scalars(select(IntakeItem))}
    positives = [(k, v) for k, v in by_name.items() if v[0] is not None]
    correct = misfiled = 0
    problems = []
    for key, (expected, states) in by_name.items():
        item = items[key]
        if item.state == "filed":
            if expected is None or str(item.chosen_client_id) != expected:
                misfiled += 1
                problems.append((item.filename, "MISFILED", item.entities.get("ids")))
            else:
                correct += 1
        if item.state not in states:
            problems.append((item.filename, item.state, item.route_reason))
    auto_rate = correct / len(positives)
    misfile_rate = misfiled / len(cases)
    by_kind = {}
    for key, (expected, _) in by_name.items():
        if expected is not None:
            kind = items[key].filename.rsplit("-", 1)[0]
            by_kind.setdefault(kind, []).append(items[key].state == "filed")
    RESULTS.write_text(
        json.dumps(
            {
                "items": len(cases),
                "positives": len(positives),
                "auto_filed_correct": correct,
                "auto_file_rate": round(auto_rate, 4),
                "misfiled": misfiled,
                "misfile_rate": round(misfile_rate, 4),
                "auto_rate_by_type": {
                    k: round(sum(v) / len(v), 2) for k, v in sorted(by_kind.items())
                },
                "problems": [list(map(str, p)) for p in problems],
            },
            indent=1,
            ensure_ascii=False,
        )
    )
    assert misfiled == 0, problems
    assert misfile_rate < 0.01
    assert auto_rate >= 0.90, (auto_rate, problems)
