"""M4 end-to-end check against the running compose stack (make up first): the 18 seeded
processes, and the reference process (Exit to retirement planning) run to completion by
the three roles that own its stages."""

import re
import subprocess
import sys
import uuid

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


tag = uuid.uuid4().hex[:8]
users = {"agent": user("agent", tag), "backoffice": user("backoffice", tag),
         "manager": user("manager", tag)}
defs = users["agent"].get("/api/process-defs").json()
check(len(defs) == 18 and sum(d["stages"] for d in defs) == 72, "18 processes, 72 stages seeded")
mmd = users["agent"].get("/api/process-defs/retire/mermaid", params={"lang": "he"}).text
check(mmd.startswith("%%{init:") and "תכנון יציאה לפרישה" in mmd, "Hebrew diagram served")

inst = users["agent"].post("/api/processes", json={"key": "retire", "title": f"E2E {tag}"}).json()
i, order = inst["id"], []
while inst["status"] != "complete":
    ready = [s for s in inst["stages"] if s["status"] in ("ready", "in_progress")]
    check(bool(ready), "a stage is always ready until the process completes")
    s = ready[0]
    c = users[s["role"]]
    for ch in s["checks"]:
        if ch["kind"] != "heading":
            r = c.put(f"/api/processes/{i}/stages/{s['id']}/checks/{ch['idx']}", json={"done": True})
            assert r.status_code == 200, r.text
    r = c.post(f"/api/processes/{i}/stages/{s['id']}/finalize")
    assert r.status_code == 200, r.text
    inst = r.json()
    order.append(s["id"])
check(len(order) == 10, f"10 stages finalized in order: {' > '.join(order)}")
check(inst["progress"]["checks_done"] == 54, "all 54 checks ticked")
check(users["manager"].get("/api/audit/verify").json()["ok"] is True, "audit chain verifies")
print("E2E M4 OK")
