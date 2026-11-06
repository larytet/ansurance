"""Black-box conformance checks against any running deployment (profile parity).

The same script runs against the on-prem compose stack and against AWS `dev`, so a change
cannot silently break one profile. It needs only a URL and a pre-created manager account.

    ANS_BASE_URL=https://host  ANS_VERIFY_TLS=0|1
    ANS_CONF_EMAIL=...  ANS_CONF_PASSWORD=...  ANS_CONF_TOTP_SECRET=...
    python scripts/conformance.py
"""

import io
import os
import sys
import time
import uuid

import httpx
import pyotp
from PIL import Image

BASE = os.environ["ANS_BASE_URL"].rstrip("/")
VERIFY = os.environ.get("ANS_VERIFY_TLS", "1") == "1"
EMAIL, PASSWORD = os.environ["ANS_CONF_EMAIL"], os.environ["ANS_CONF_PASSWORD"]
SECRET = os.environ["ANS_CONF_TOTP_SECRET"]
PASS2 = "a-conformance-password-1"
FAILED: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        FAILED.append(msg)


def client() -> httpx.Client:
    return httpx.Client(base_url=BASE, verify=VERIFY, timeout=120)


def login(c: httpx.Client, email: str, password: str, secret: str) -> None:
    r = c.post(
        "/api/auth/login",
        json={"email": email, "password": password, "code": pyotp.TOTP(secret).now()},
    )
    check(r.status_code == 200, f"login {email.split('@')[0]}")
    if r.status_code != 200:
        sys.exit(1)
    c.headers["x-csrf-token"] = r.json()["csrf"]


def valid_id() -> str:
    body = f"{uuid.uuid4().int % 10**8:08d}"
    total = 0
    for i, d in enumerate(body):
        n = int(d) * (1 + i % 2)
        total += n - 9 if n > 9 else n
    return body + str((10 - total % 10) % 10)


def tiny_scan() -> bytes:
    img = Image.new("L", (400, 120), 255)
    for x in range(10, 390, 6):
        for y in range(40, 80):
            img.putpixel((x, y), 0)
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def main() -> int:
    anon = client()
    h = anon.get("/api/health")
    check(h.status_code == 200 and h.json()["status"] == "ok", "health")
    check(anon.get("/api/ready").status_code == 200, "ready (database reachable)")
    check(anon.get("/").status_code == 200, "web app served")
    check(anon.get("/api/clients").status_code == 401, "API refuses anonymous requests")
    hdrs = anon.get("/api/health").headers
    check(
        hdrs.get("x-content-type-options") == "nosniff" and "x-request-id" in hdrs,
        "security headers and request id",
    )

    mgr = client()
    login(mgr, EMAIL, PASSWORD, SECRET)
    tag = uuid.uuid4().hex[:8]
    r = mgr.post(
        "/api/users",
        json={
            "email": f"conf-{tag}@conf.il",
            "display_name": "Conf",
            "roles": ["agent"],
            "password": PASS2,
        },
    )
    check(r.status_code == 201, "manager creates a user")
    agent_secret = r.json()["totp_uri"].split("secret=")[1].split("&")[0]
    agent = client()
    login(agent, f"conf-{tag}@conf.il", PASS2, agent_secret)

    cid = agent.post(
        "/api/clients", json={"id_number": valid_id(), "name": f"Conf Client {tag}"}
    ).json()["id"]
    found = agent.get("/api/clients", params={"q": tag}).json()
    check(any(c["id"] == cid for c in found), "client created and found by search")

    pdf = b"%PDF-1.4\n% conformance " + tag.encode() + b"\n%%EOF"
    up = agent.post(
        f"/api/clients/{cid}/documents",
        files={"file": ("c.pdf", pdf)},
        data={"doc_type": "policy"},
    )
    check(up.status_code == 201 and up.json()["scan_status"] == "clean", "upload scanned clean")
    dl = agent.get(f"/api/documents/{up.json()['id']}/content")
    check(dl.status_code == 200 and dl.content == pdf, "download returns the exact bytes")
    gone = agent.delete(f"/api/documents/{up.json()['id']}")
    check(gone.status_code in (404, 405), "documents cannot be deleted")

    msg = {"client_id": cid, "channel": "whatsapp", "purpose": "marketing", "body": "x"}
    check(agent.post("/api/messages", json=msg).status_code == 409, "consent gate blocks")
    consent = {"channel": "whatsapp", "state": "opt_in", "source": "conformance"}
    check(
        agent.post(f"/api/clients/{cid}/consent", json=consent).status_code == 201,
        "consent recorded",
    )
    check(agent.post("/api/messages", json=msg).status_code == 201, "message queued with consent")

    inst = agent.post("/api/processes", json={"key": "idreg", "client_id": cid})
    check(inst.status_code == 201, "process started")

    sub = mgr.post(
        "/api/intake", files={"file": ("s.png", tiny_scan())}, data={"channel": "scan"}
    )
    check(sub.status_code == 201, "intake accepts a scan")
    item_id, state = sub.json()["items"][0]["id"], "received"
    end = time.time() + 150
    while time.time() < end and state not in ("filed", "review", "unassigned", "quarantined"):
        time.sleep(3)
        state = mgr.get(f"/api/intake/{item_id}").json()["state"]
    check(state in ("review", "unassigned"), f"the worker processed the scan ({state})")

    v = mgr.get("/api/audit/verify").json()
    check(v["ok"] is True and v["entries"] > 10, f"audit chain verifies ({v['entries']} entries)")
    check(mgr.post("/api/auth/logout").status_code == 200, "logout")
    print("CONFORMANCE", "FAILED: " + "; ".join(FAILED) if FAILED else "OK")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
