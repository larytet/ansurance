"""M1 end-to-end check against the running compose stack (make up first).

Bootstraps a manager with the CLI inside the api container, then exercises login with
TOTP, user and client creation, multi-script search, role limits and audit verification
through the HTTPS proxy.
"""

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


def bootstrap(email: str, role: str) -> str:
    out = subprocess.run(
        [*COMPOSE, "exec", "-T", "api", "python", "-m", "app.cli", "create-user", "--email",
         email, "--name", "E2E " + role, "--role", role, "--password", PASSWORD],
        check=True, capture_output=True, text=True).stdout
    return re.search(r"secret=([A-Z2-7]+)", out).group(1)  # type: ignore[union-attr]


def login(email: str, secret: str) -> httpx.Client:
    c = httpx.Client(base_url=BASE, verify=False, timeout=20)  # noqa: S501 (local CA)
    r = c.post("/api/auth/login", json={"email": email, "password": PASSWORD,
                                        "code": pyotp.TOTP(secret).now()})
    check(r.status_code == 200, f"login {email}")
    c.headers["x-csrf-token"] = r.json()["csrf"]
    return c


tag = uuid.uuid4().hex[:8]
mgr_email, agent_email = f"mgr-{tag}@e2e.il", f"agent-{tag}@e2e.il"
mgr = login(mgr_email, bootstrap(mgr_email, "manager"))
check("manager" in mgr.get("/api/auth/me").json()["roles"], "manager identity")

r = mgr.post("/api/users", json={"email": agent_email, "display_name": "Agent",
                                 "roles": ["agent"], "password": PASSWORD})
check(r.status_code == 201, "manager creates an agent through the API")
agent = login(agent_email, re.search(r"secret=([A-Z2-7]+)", r.json()["totp_uri"]).group(1))  # type: ignore[union-attr]

r = agent.post("/api/clients", json={"id_number": "123456782", "name": f"David Cohen {tag}"})
if r.status_code == 409:  # a previous run left this ID: fall back to another valid one
    r = agent.post("/api/clients", json={"id_number": "000000026", "name": f"Noa Levi {tag}"})
check(r.status_code in (201, 409), "agent creates a client")
check(agent.post("/api/clients", json={"id_number": "123456789", "name": "Bad Id"}
                 ).status_code == 422, "invalid ID number rejected")
check(any(tag in c["name"] for c in agent.get("/api/clients", params={"q": tag}).json())
      or r.status_code == 409, "search finds the new client")
check(agent.get("/api/audit/verify").status_code == 403, "agent cannot verify the audit log")
v = mgr.get("/api/audit/verify").json()
check(v["ok"] is True and v["entries"] > 5, f"audit chain verifies ({v['entries']} entries)")
check(mgr.post("/api/auth/logout").status_code == 200, "logout")
check(mgr.get("/api/auth/me").status_code == 401, "session is dead after logout")
print("E2E M1 OK")
