"""M5 end-to-end check against the running compose stack (make up first): real Tesseract in
the worker container, real ClamAV, MinIO, Postgres. Scans are rendered here and uploaded
through the API; the worker digitizes, classifies, attributes and files them."""

import io
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import pyotp

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from tests.intake_data import samples as S  # noqa: E402

BASE = "https://127.0.0.1:8443"
COMPOSE = ["docker", "compose", "-f", "deploy/compose/docker-compose.yml",
           "--env-file", "deploy/compose/.env"]
PASSWORD = "an-e2e-password-1"


def check(cond: bool, msg: str) -> None:
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        sys.exit(1)


def user(role: str, tag: str) -> httpx.Client:
    email = f"{role}-{tag}@e2e.il"
    out = subprocess.run(
        [*COMPOSE, "exec", "-T", "api", "python", "-m", "app.cli", "create-user", "--email",
         email, "--name", "E2E " + role, "--role", role, "--password", PASSWORD],
        check=True, capture_output=True, text=True).stdout
    secret = re.search(r"secret=([A-Z2-7]+)", out).group(1)  # type: ignore[union-attr]
    c = httpx.Client(base_url=BASE, verify=False, timeout=120)  # noqa: S501 (local CA)
    r = c.post("/api/auth/login", json={"email": email, "password": PASSWORD,
                                        "code": pyotp.TOTP(secret).now()})
    assert r.status_code == 200, r.text
    c.headers["x-csrf-token"] = r.json()["csrf"]
    return c


def make_id() -> str:
    body = f"{uuid.uuid4().int % 10**8:08d}"
    total = sum((lambda n: n - 9 if n > 9 else n)(int(d) * (1 + i % 2)) for i, d in enumerate(body))
    return body + str((10 - total % 10) % 10)


def wait_done(c: httpx.Client, item_id: str, timeout: int = 180) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        item = c.get(f"/api/intake/{item_id}").json()
        if item["state"] in ("filed", "review", "unassigned", "quarantined", "failed", "split"):
            return item
        time.sleep(2)
    raise SystemExit(f"FAIL item {item_id} did not finish: {item['state']}")


def upload(c: httpx.Client, img, name: str, **form: str) -> str:  # type: ignore[no-untyped-def]
    buf = io.BytesIO()
    img.save(buf, "PNG")
    r = c.post("/api/intake", files={"file": (name, buf.getvalue())}, data={"channel": "scan", **form})
    assert r.status_code == 201, r.text
    return r.json()["items"][0]["id"]


tag = uuid.uuid4().hex[:8]
agent, bo, mgr = user("agent", tag), user("backoffice", tag), user("manager", tag)
david_id, noa_id = make_id(), make_id()
david = agent.post("/api/clients", json={"id_number": david_id, "name": f"David Cohen {tag}"}).json()
noa = agent.post("/api/clients", json={"id_number": noa_id, "name": f"Noa Levi {tag}"}).json()
policy = f"PL-2026-{uuid.uuid4().int % 90000 + 10000}"
check(agent.post(f"/api/clients/{noa['id']}/policies", json={
    "insurer": "Harel", "policy_number": policy, "product": "home", "premium_ils": 120}
                 ).status_code == 201, "clients and a policy created")

# 1. an ID card scan goes straight to its owner
item = wait_done(bo, upload(bo, S.render(S.id_card(david_id)), "id.png", sender="stranger@gmail.com"))
check(item["state"] == "filed" and item["client_id"] == david["id"], "ID card filed to its owner")
check(item["doc_type"] == "id_card" and item["attribution_conf"] >= 0.95,
      f"classified as id_card, confidence {item['attribution_conf']}")
docs = agent.get(f"/api/clients/{david['id']}/documents").json()
check(any(d["id"] == item["document_id"] and d["scan_status"] == "clean" for d in docs),
      "the document is in the client's folder, scanned clean")
check(any("Filed" in i["title"] for i in agent.get("/api/inbox").json()["items"]),
      "the agent of record is told in the Inbox")

# 2. a letter with only a policy number (Latin token inside a Hebrew sentence)
item = wait_done(bo, upload(bo, S.render(S.insurer_letter(policy)), "letter.png"))
check(item["client_id"] == noa["id"] and item["state"] in ("filed", "review"),
      f"policy-number letter attributed to the policy holder ({item['state']})")

# 3. no identifier at all: unassigned, never guessed; the client's name alone does not file it
item = wait_done(bo, upload(bo, S.render(S.name_only(f"David Cohen {tag}")), "name.png",
                            sender="david@x.il"))
check(item["state"] == "unassigned" and item["document_id"] is None,
      "a name alone never files a document")
check(bo.post(f"/api/intake/{item['id']}/confirm", json={"client_id": noa["id"]}).status_code == 200,
      "back-office files it with one click")

# 4. wrong client correction and the misfile metric
filed = wait_done(bo, upload(bo, S.render(S.invoice(david_id)), "invoice.png"))
check(bo.post(f"/api/intake/{filed['id']}/wrong-client", json={
    "client_id": noa["id"], "reason": "belongs to Noa"}).status_code == 200,
      "wrong-client moves the document")
m = bo.get("/api/intake/metrics").json()
check(m["misfiled"] >= 1 and m["auto_filed"] >= 2, f"metrics: {m['auto_filed']} auto-filed, "
      f"{m['misfiled']} misfiled")
check(mgr.get("/api/audit/verify").json()["ok"] is True, "audit chain verifies")
print("E2E M5 OK")
