"""T-M7-2: point-in-time recovery from the base backup and the archived write-ahead log.

Scenario on the running on-prem stack: take a backup, create client A, note the time, create
client B, make sure the log is archived; then recover a brand-new stack to the moment between
A and B. A must be there, B must not. Slow (builds and starts a second stack).
"""

import os
import re
import subprocess
import time
import uuid
from pathlib import Path

import httpx
import pyotp
import pytest

from tests.test_disaster_recovery import (
    ENV,
    PASSWORD,
    ROOT,
    SRC,
    compose,
    stack_is_up,
    valid_id,
)

DST = "ansurance-pitr"
HTTPS = 19443

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not stack_is_up(), reason="the compose stack must be running"),
]


def psql(project: str, env: Path, sql: str) -> str:
    return compose(
        project,
        env,
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "ansurance",
        "-d",
        "ansurance",
        "-qAtX",
        "-c",
        sql,
    ).stdout.strip()


def cli_user(project: str, env: Path, email: str) -> str:
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
        "pitr",
        "--role",
        "manager",
        "--password",
        PASSWORD,
    ).stdout
    m = re.search(r"secret=([A-Z2-7]+)", out)
    assert m, out
    return m.group(1)


def login(port: int, email: str, secret: str) -> httpx.Client:
    c = httpx.Client(base_url=f"https://127.0.0.1:{port}", verify=False, timeout=60)  # noqa: S501
    r = c.post(
        "/api/auth/login",
        json={"email": email, "password": PASSWORD, "code": pyotp.TOTP(secret).now()},
    )
    assert r.status_code == 200, r.text
    c.headers["x-csrf-token"] = r.json()["csrf"]
    return c


def copy_volume(volume: str, dest: Path) -> None:
    """Copy a Docker volume to a host directory the current user can read."""
    owner = f"{os.getuid()}:{os.getgid()}"
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{volume}:/v:ro",
            "-v",
            f"{dest}:/out",
            "alpine",
            "sh",
            "-c",
            f"cp -a /v/. /out/ && chown -R {owner} /out && chmod -R u+rwX /out",
        ],
        check=True,
    )


@pytest.fixture(scope="module")
def recovered(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    tag = uuid.uuid4().hex[:8]
    # 1. a fresh backup, so the base backup predates everything below
    out = compose(SRC, ENV, "run", "--rm", "--no-deps", "backup", "backup").stdout
    stamp = out.strip().splitlines()[-1].rsplit("/", 1)[-1]
    assert re.fullmatch(r"\d{8}T\d{6}Z", stamp), out[-300:]

    email = f"pitr-{tag}@pitr.il"
    mgr = login(8443, email, cli_user(SRC, ENV, email))
    time.sleep(1)
    a = mgr.post("/api/clients", json={"id_number": valid_id(), "name": f"Pitr A {tag}"}).json()
    time.sleep(2)
    target = psql(SRC, ENV, "SELECT now()")  # the moment between A and B
    time.sleep(2)
    b = mgr.post("/api/clients", json={"id_number": valid_id(), "name": f"Pitr B {tag}"}).json()
    time.sleep(1)

    # 2. make sure the log that holds B is archived
    wal = psql(SRC, ENV, "SELECT pg_walfile_name(pg_current_wal_lsn())")
    psql(SRC, ENV, "SELECT pg_switch_wal()")
    deadline = time.time() + 90
    while time.time() < deadline:
        last = psql(SRC, ENV, "SELECT coalesce(last_archived_wal, '') FROM pg_stat_archiver")
        if last >= wal:
            break
        time.sleep(2)
    else:
        pytest.fail(f"WAL {wal} was not archived (last archived: {last})")

    # 3. take everything off the host stack, as it would be copied to another disk
    backups, wals = tmp_path_factory.mktemp("backups"), tmp_path_factory.mktemp("wal")
    copy_volume(f"{SRC}_backups", backups)
    copy_volume(f"{SRC}_walarchive", wals)

    env = tmp_path_factory.mktemp("env") / "pitr.env"
    env.write_text(ENV.read_text() + f"\nHTTP_PORT=19080\nHTTPS_PORT={HTTPS}\n")
    try:
        res = subprocess.run(
            [
                str(ROOT / "scripts" / "restore.sh"),
                str(backups / stamp),
                DST,
                "--to",
                target,
                "--wal",
                str(wals),
            ],
            capture_output=True,
            text=True,
            cwd=ROOT,
            env={**os.environ, "ENV_FILE": str(env)},
        )
        assert res.returncode == 0, res.stdout[-2500:] + res.stderr[-2500:]
        yield {
            "tag": tag,
            "a": a,
            "b": b,
            "target": target,
            "env": env,
            "stamp": stamp,
            "log": res.stdout + res.stderr,
        }
    finally:
        compose(DST, env, "down", "-v", "--remove-orphans", check=False)


def test_recovery_stops_exactly_between_the_two_changes(recovered: dict[str, object]) -> None:
    env = recovered["env"]
    email = f"pitr2-{recovered['tag']}@pitr.il"
    c = login(HTTPS, email, cli_user(DST, env, email))  # type: ignore[arg-type]
    found = c.get("/api/clients", params={"q": str(recovered["tag"])}).json()
    names = sorted(x["name"] for x in found if str(recovered["tag"]) in x["name"])
    assert names == [f"Pitr A {recovered['tag']}"], names  # A is there, B (after the target) is not


def test_the_source_system_still_has_both(recovered: dict[str, object]) -> None:
    """The recovery built a separate stack and changed nothing in the original."""
    n = psql(SRC, ENV, f"SELECT count(*) FROM client WHERE name LIKE '%{recovered['tag']}'")
    assert n == "2"


def test_the_recovered_database_is_a_new_timeline_and_writable(
    recovered: dict[str, object],
) -> None:
    env = recovered["env"]
    assert psql(DST, env, "SELECT pg_is_in_recovery()") == "f"  # type: ignore[arg-type]
    assert int(psql(DST, env, "SELECT timeline_id FROM pg_control_checkpoint()")) >= 2  # type: ignore[arg-type]
    psql(DST, env, "CREATE TABLE pitr_probe(x int); INSERT INTO pitr_probe VALUES (1)")  # type: ignore[arg-type]


def test_the_recovered_stack_is_healthy_and_the_audit_chain_verifies(
    recovered: dict[str, object],
) -> None:
    env = recovered["env"]
    email = f"pitr3-{recovered['tag']}@pitr.il"
    c = login(HTTPS, email, cli_user(DST, env, email))  # type: ignore[arg-type]
    v = c.get("/api/audit/verify").json()
    assert v["ok"] is True, v


def test_the_log_says_what_it_did(recovered: dict[str, object]) -> None:
    log = str(recovered["log"])
    assert "unpacking the base backup" in log and "replay up to" in log


def test_a_restore_refuses_a_data_directory_that_already_has_data(
    recovered: dict[str, object],
) -> None:
    res = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-u",
            "root",
            "-v",
            f"{DST}_pgdata:/pgdata",
            "-v",
            f"{DST}_backups:/restore",
            "--env-file",
            str(recovered["env"]),
            "--entrypoint",
            "/opt/backup/backup.sh",
            f"{SRC}-backup",
            "pitr",
            "/restore/x",
            "2030-01-01 00:00:00+00",
        ],
        capture_output=True,
        text=True,
    )
    assert res.returncode != 0  # it never overwrites a live data directory
