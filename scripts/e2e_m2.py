"""M2 end-to-end check against the running compose stack (make up first).

Real MinIO with Object Lock, real ClamAV, real Postgres, HTTPS proxy: upload, scan,
store, download with integrity, duplicate handling, rejection of fake types, restricted
documents, and the reconcile CLI.
"""

import hashlib
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
EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


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
    c = httpx.Client(base_url=BASE, verify=False, timeout=60)  # noqa: S501 (local CA)
    r = c.post("/api/auth/login", json={"email": email, "password": PASSWORD,
                                        "code": pyotp.TOTP(secret).now()})
    assert r.status_code == 200, r.text
    c.headers["x-csrf-token"] = r.json()["csrf"]
    return c


def make_id() -> str:
    """A fresh valid Israeli ID so reruns never collide."""
    while True:
        body = f"{uuid.uuid4().int % 10**8:08d}"
        total = sum((lambda n: n - 9 if n > 9 else n)(int(d) * (1 + i % 2))
                    for i, d in enumerate(body))
        check_digit = (10 - total % 10) % 10
        return body + str(check_digit)


tag = uuid.uuid4().hex[:8]
email = f"e2e-{tag}@e2e.il"
agent = login(email, bootstrap(email, "agent"))
other_email = f"e2e2-{tag}@e2e.il"
other = login(other_email, bootstrap(other_email, "agent"))

cid = agent.post("/api/clients", json={"id_number": make_id(), "name": f"Doc Test {tag}"}
                 ).json()["id"]
pdf = b"%PDF-1.4\n% e2e " + tag.encode() + b"\n%%EOF"


def up(data: bytes, name: str = "policy.pdf", **form: str) -> httpx.Response:
    return agent.post(f"/api/clients/{cid}/documents", files={"file": (name, data)},
                      data={"doc_type": "policy", **form})


r = up(pdf)
check(r.status_code == 201 and r.json()["scan_status"] == "clean", "upload scanned clean by ClamAV")
doc = r.json()
check(doc["sha256"] == hashlib.sha256(pdf).hexdigest(), "sha256 recorded")
dl = agent.get(f"/api/documents/{doc['id']}/content")
check(dl.status_code == 200 and dl.content == pdf, "download returns the exact bytes")
check(up(pdf, "again.pdf").json()["sha256"] == doc["sha256"], "duplicate upload dedupes by hash")
check(up(EICAR, "eicar.com").status_code == 415, "executable-looking test file rejected by type")
check(up(b"MZ\x90 not a pdf", "fake.pdf").status_code == 415, "wrong content behind .pdf rejected")
check(up(b"%PDF-1.4 x" * 20, "../../etc/passwd.pdf").json()["filename"] == "passwd.pdf",
      "path traversal in filename neutralised")

med = up(pdf + b"med", "report.pdf", doc_type="medical_report").json()
check(med["restricted"] is True, "medical report is restricted")
check(other.get(f"/api/documents/{med['id']}/content").status_code == 404,
      "another agent cannot download it")
check(len(agent.get(f"/api/clients/{cid}/documents").json()) == 4, "documents listed")

rec = subprocess.run([*COMPOSE, "exec", "-T", "api", "python", "-m", "app.cli", "reconcile"],
                     capture_output=True, text=True)
check(rec.returncode == 0 and "missing (row, no object): 0" in rec.stdout,
      "reconcile: no database row is missing its object")
print("E2E M2 OK")
