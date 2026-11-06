"""M3 end-to-end check against the running compose stack (make up first):
F11 across agent and back-office, the Inbox and Desk, consent and the send gate."""

import re
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pyotp

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
    c = httpx.Client(base_url=BASE, verify=False, timeout=60)  # noqa: S501 (local CA)
    r = c.post("/api/auth/login", json={"email": email, "password": PASSWORD,
                                        "code": pyotp.TOTP(secret).now()})
    assert r.status_code == 200, r.text
    c.headers["x-csrf-token"] = r.json()["csrf"]
    return c


def make_id() -> str:
    body = f"{uuid.uuid4().int % 10**8:08d}"
    total = sum((lambda n: n - 9 if n > 9 else n)(int(d) * (1 + i % 2)) for i, d in enumerate(body))
    return body + str((10 - total % 10) % 10)


tag = uuid.uuid4().hex[:8]
agent, bo = user("agent", tag), user("backoffice", tag)
mkt, mgr = user("marketing", tag), user("manager", tag)

cid = agent.post("/api/clients", json={"id_number": make_id(), "name": f"Reg Test {tag}"}
                 ).json()["id"]
check(agent.post(f"/api/clients/{cid}/request-id").status_code == 201, "agent requests the ID")
desk = bo.get("/api/desk").json()
check(next(a for a in desk["areas"] if a["area"] == "id_registration")["open"] >= 1,
      "back-office Desk shows the ID request")
check(bo.post(f"/api/clients/{cid}/register").status_code == 422, "register refused without ID")
pdf = b"%PDF-1.4\n% id " + tag.encode() + b"\n%%EOF"
up = bo.post(f"/api/clients/{cid}/documents", files={"file": ("id.pdf", pdf)},
             data={"doc_type": "id_card"})
check(up.status_code == 201 and up.json()["scan_status"] == "clean", "ID card filed and scanned")
check(bo.post(f"/api/clients/{cid}/register").json()["status"] == "registered", "registered")
check(any("registered" in i["title"] for i in agent.get("/api/inbox").json()["items"]),
      "agent is told in the Inbox")

check(agent.post("/api/messages", json={"client_id": cid, "channel": "whatsapp",
                                        "purpose": "marketing", "body": "Offer"}
                 ).status_code == 409, "marketing message blocked without consent")
check(agent.post(f"/api/clients/{cid}/consent", json={
    "channel": "whatsapp", "state": "opt_in", "source": "signed form"}).status_code == 201,
      "opt-in recorded")
check(agent.post("/api/messages", json={"client_id": cid, "channel": "whatsapp",
                                        "purpose": "marketing", "body": "Offer"}
                 ).status_code == 201, "message queued with consent")
check(mkt.post(f"/api/clients/{cid}/consent", json={
    "channel": "whatsapp", "state": "opt_out", "source": "STOP reply"}).status_code == 201,
      "marketing applies an opt-out")
check(agent.post("/api/messages", json={"client_id": cid, "channel": "whatsapp",
                                        "purpose": "marketing", "body": "Offer"}
                 ).status_code == 409, "the next send is blocked")
old = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
check(mkt.post(f"/api/clients/{cid}/consent", json={
    "channel": "email", "state": "opt_out", "source": "late STOP", "received_at": old}
               ).json()["late"] is True, "late opt-out is flagged")
check(any(i["type"] == "consent_late" for i in mgr.get("/api/inbox").json()["items"]),
      "the manager gets an urgent Inbox item")
check(mgr.get("/api/audit/verify").json()["ok"] is True, "audit chain still verifies")
print("E2E M3 OK")
