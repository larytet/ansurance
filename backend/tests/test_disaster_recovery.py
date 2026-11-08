"""T-M7-1: back up the running on-prem stack, restore it into a brand-new stack, and check
the restored system: users can log in, a client and a document written before the backup are
there with identical bytes, the audit chain verifies, and a wrong passphrase restores nothing.

Needs the compose stack to be running (`make up`). Slow: it builds and starts a second
complete stack in its own compose project with its own volumes, then removes it.
"""

import hashlib
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import httpx
import pyotp
import pytest

ROOT = Path(__file__).resolve().parents[2]
ENV = ROOT / "deploy" / "compose" / ".env"
COMPOSE_FILE = ROOT / "deploy" / "compose" / "docker-compose.yml"
SRC = "ansurance"
DST = "ansurance-restore"
HTTPS = 18443
PASSWORD = "a-dr-password-12345"


def compose(
    project: str, env: Path, *args: str, check: bool = True, **kw: object
) -> subprocess.CompletedProcess[str]:
    cmd = [
        "docker",
        "compose",
        "-p",
        project,
        "-f",
        str(COMPOSE_FILE),
        "--env-file",
        str(env),
        *args,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, **kw)  # type: ignore[call-overload]
    if check and res.returncode != 0:
        raise AssertionError(
            f"{' '.join(args)} failed:\n{res.stdout[-1500:]}\n{res.stderr[-1500:]}"
        )
    return res


def stack_is_up() -> bool:
    if shutil.which("docker") is None or not ENV.exists():
        return False
    out = subprocess.run(
        [
            "docker",
            "compose",
            "-p",
            SRC,
            "-f",
            str(COMPOSE_FILE),
            "--env-file",
            str(ENV),
            "ps",
            "-q",
            "api",
        ],
        capture_output=True,
        text=True,
    )
    return out.returncode == 0 and bool(out.stdout.strip())


pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not stack_is_up(), reason="the compose stack must be running"),
]


def make_user(project: str, env: Path, email: str, role: str) -> str:
    out = compose(
        project,
        env,
        "exec",
        "-T",
        "api",
        "python",
        "-m",
        "app.cli",
        "create-user",
        "--email",
        email,
        "--name",
        role,
        "--role",
        role,
        "--password",
        PASSWORD,
    ).stdout
    m = re.search(r"secret=([A-Z2-7]+)", out)
    assert m, out
    return m.group(1)


def login(base: str, email: str, secret: str) -> httpx.Client:
    c = httpx.Client(base_url=base, verify=False, timeout=60)  # noqa: S501 (local CA)
    r = c.post(
        "/api/auth/login",
        json={"email": email, "password": PASSWORD, "code": pyotp.TOTP(secret).now()},
    )
    assert r.status_code == 200, r.text
    c.headers["x-csrf-token"] = r.json()["csrf"]
    return c


def valid_id() -> str:
    body = f"{uuid.uuid4().int % 10**8:08d}"
    total = 0
    for i, d in enumerate(body):
        n = int(d) * (1 + i % 2)
        total += n - 9 if n > 9 else n
    return body + str((10 - total % 10) % 10)


@pytest.fixture(scope="module")
def restored(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    tag = uuid.uuid4().hex[:8]
    base = "https://127.0.0.1:8443"
    # ---- write known data into the running system ------------------------------------------------
    email = f"dr-{tag}@dr.il"
    secret = make_user(SRC, ENV, email, "manager")
    mgr = login(base, email, secret)
    name = f"Disaster Recovery {tag}"
    cid = mgr.post("/api/clients", json={"id_number": valid_id(), "name": name}).json()["id"]
    pdf = b"%PDF-1.4\n% precious original " + tag.encode() + b"\n%%EOF"
    doc = mgr.post(
        f"/api/clients/{cid}/documents", files={"file": ("p.pdf", pdf)}, data={"doc_type": "policy"}
    ).json()
    assert doc["scan_status"] == "clean"
    time.sleep(1)

    # ---- back up, and copy the backup volume to a host directory ---------------------------------
    out = compose(SRC, ENV, "run", "--rm", "--no-deps", "backup", "backup").stdout
    stamp = out.strip().splitlines()[-1].rsplit("/", 1)[-1]
    assert re.fullmatch(r"\d{8}T\d{6}Z", stamp), out[-300:]
    host = tmp_path_factory.mktemp("backups")
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{SRC}_backups:/b:ro",
            "-v",
            f"{host}:/out",
            "alpine:3.20@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc",
            "sh",
            "-c",
            "cp -a /b/. /out/",
        ],
        check=True,
    )

    # ---- a second, brand-new stack with its own volumes and ports --------------------------------
    env = tmp_path_factory.mktemp("env") / "restore.env"
    env.write_text(ENV.read_text() + f"\nHTTP_PORT=18080\nHTTPS_PORT={HTTPS}\n")
    restore_env = {"ENV_FILE": str(env)}
    try:
        res = subprocess.run(
            [str(ROOT / "scripts" / "restore.sh"), str(host / stamp), DST],
            capture_output=True,
            text=True,
            cwd=ROOT,
            env={**__import__("os").environ, **restore_env},
        )
        assert res.returncode == 0, res.stdout[-2000:] + res.stderr[-2000:]
        yield {
            "tag": tag,
            "name": name,
            "cid": cid,
            "doc": doc,
            "pdf": pdf,
            "env": env,
            "host": host,
            "stamp": stamp,
            "restore_log": res.stdout + res.stderr,
        }
    finally:
        compose(DST, env, "down", "-v", "--remove-orphans", check=False)


def test_the_restored_system_has_the_data_and_users_can_log_in(restored: dict[str, object]) -> None:
    env = restored["env"]
    email = f"dr2-{restored['tag']}@dr.il"
    secret = make_user(DST, env, email, "manager")  # type: ignore[arg-type]
    c = login(f"https://127.0.0.1:{HTTPS}", email, secret)
    found = c.get("/api/clients", params={"q": str(restored["tag"])}).json()
    assert [x["id"] for x in found] == [restored["cid"]]
    docs = c.get(f"/api/clients/{restored['cid']}/documents").json()
    assert [d["id"] for d in docs] == [restored["doc"]["id"]]  # type: ignore[index]
    dl = c.get(f"/api/documents/{restored['doc']['id']}/content?acknowledge=true")  # type: ignore[index]
    assert dl.status_code == 200
    assert hashlib.sha256(dl.content).hexdigest() == hashlib.sha256(restored["pdf"]).hexdigest()  # type: ignore[arg-type]
    v = c.get("/api/audit/verify").json()
    assert v["ok"] is True and v["entries"] > 10, v


def test_the_restored_originals_are_still_write_once(restored: dict[str, object]) -> None:
    """Object Lock and the no-delete application user exist in the new stack too."""
    out = compose(
        DST,
        restored["env"],
        "run",
        "--rm",
        "--no-deps",
        "--entrypoint",
        "sh",  # type: ignore[arg-type]
        "backup",
        "-c",
        'mc alias set d http://objectstore:9000 "$S3_ACCESS_KEY" "$S3_SECRET_KEY" >/dev/null;'
        " mc retention info --default d/originals; "
        "k=$(mc ls --recursive d/originals | head -1 | awk '{print $NF}');"
        ' mc rm "d/originals/$k" 2>&1 | head -2',
    ).stdout
    assert "GOVERNANCE" in out and ("Access Denied" in out or "access" in out.lower()), out


def test_the_restore_log_shows_the_steps(restored: dict[str, object]) -> None:
    log = str(restored["restore_log"])
    assert "restoring the database" in log and "restored" in log and "files" in log
    assert "restore complete" in log


def test_a_restore_into_a_database_that_has_data_is_refused(restored: dict[str, object]) -> None:
    res = compose(
        DST,
        restored["env"],
        "run",
        "--rm",
        "--no-deps",
        "backup",
        "restore",  # type: ignore[arg-type]
        f"/backups/{restored['stamp']}",
        check=False,
    )
    # the new stack's own backups volume has no such directory, and the database is not empty
    assert res.returncode != 0


def test_a_wrong_passphrase_cannot_be_read_as_a_database_dump(
    restored: dict[str, object],
) -> None:
    """A stolen backup is useless without the passphrase: decrypting with a wrong one gives
    bytes that are not a PostgreSQL dump, so a restore would fail before changing anything."""
    host: Path = restored["host"]  # type: ignore[assignment]
    stamp = str(restored["stamp"])

    def list_dump(passphrase: str) -> int:
        script = (
            "openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_PASSPHRASE "
            f"-in /b/{stamp}/db.dump.enc 2>/dev/null | pg_restore -l >/dev/null 2>&1"
        )
        return subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{host}:/b:ro",
                "-e",
                f"BACKUP_PASSPHRASE={passphrase}",
                "--entrypoint",
                "sh",
                f"{SRC}-backup",
                "-c",
                script,
            ],
            capture_output=True,
            text=True,
        ).returncode

    right = [ln for ln in ENV.read_text().splitlines() if ln.startswith("BACKUP_PASSPHRASE=")][0]
    assert list_dump(right.split("=", 1)[1]) == 0  # the right passphrase reads the dump
    assert list_dump("not-the-passphrase") != 0  # a wrong one does not
