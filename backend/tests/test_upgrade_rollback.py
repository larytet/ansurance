"""T-M7-4: upgrade the running on-prem stack, and roll back automatically when the new
version does not pass the health gate.

Acts on the running compose stack (`make up`): a successful upgrade replaces the running
images with freshly built ones and takes a backup first; a simulated failed upgrade must
leave the previous images running, the stack healthy, and say what happened.
"""

import os
import subprocess

import pytest

from tests.test_disaster_recovery import COMPOSE_FILE, ENV, ROOT, SRC, stack_is_up

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not stack_is_up(), reason="the compose stack must be running"),
]


def sh(*cmd: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(cmd), capture_output=True, text=True, cwd=ROOT, env={**os.environ, **(env or {})}
    )


def running_image(service: str) -> str:
    cid = (
        sh(
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
            service,
        )
        .stdout.strip()
        .splitlines()[0]
    )
    return sh("docker", "inspect", "--format", "{{.Image}}", cid).stdout.strip()


def tag_id(tag: str) -> str:
    return sh("docker", "image", "inspect", "--format", "{{.Id}}", tag).stdout.strip()


def build_label(service: str) -> str:
    return sh(
        "docker",
        "image",
        "inspect",
        "--format",
        '{{index .Config.Labels "org.ansurance.build"}}',
        f"{SRC}-{service}:latest",
    ).stdout.strip()


def backups_now() -> set[str]:
    out = sh(
        "docker",
        "run",
        "--rm",
        "-v",
        f"{SRC}_backups:/b:ro",
        "alpine",
        "sh",
        "-c",
        "ls /b | grep '^20' || true",
    ).stdout
    return set(out.split())


@pytest.fixture(scope="module")
def upgraded() -> dict[str, object]:
    before_image = running_image("api")
    before_backups = backups_now()
    res = sh("./scripts/upgrade.sh", env={"BUILD_ID": "t7-upgrade"})
    return {"res": res, "before_image": before_image, "before_backups": before_backups}


def test_a_successful_upgrade_backs_up_first_and_replaces_the_running_version(
    upgraded: dict[str, object],
) -> None:
    res: subprocess.CompletedProcess[str] = upgraded["res"]  # type: ignore[assignment]
    assert res.returncode == 0, res.stdout[-1500:] + res.stderr[-1500:]
    assert "upgraded: schema" in res.stdout
    assert running_image("api") != upgraded["before_image"]  # a new image is running
    assert build_label("api") == "t7-upgrade" and build_label("web") == "t7-upgrade"
    new = backups_now() - upgraded["before_backups"]  # type: ignore[operator]
    assert len(new) >= 1  # the pre-upgrade backup was taken
    assert tag_id(f"{SRC}-api:rollback") == upgraded["before_image"]  # and the old one is kept


def test_the_order_of_the_steps_is_pre_flight_backup_build_gate(
    upgraded: dict[str, object],
) -> None:
    out = upgraded["res"].stdout  # type: ignore[attr-defined]
    order = [
        out.index(s)
        for s in (
            "1/5 pre-flight",
            "2/5 backing up",
            "3/5 keeping",
            "4/5 building",
            "5/5 health gate",
        )
    ]
    assert order == sorted(order)


def test_a_failed_health_gate_rolls_back_to_the_previous_version(
    upgraded: dict[str, object],
) -> None:
    good_image = running_image("api")  # the version that is running now
    res = sh(
        "./scripts/upgrade.sh",
        env={"BUILD_ID": "t7-bad", "UPGRADE_SIMULATE_FAILURE": "1", "SKIP_PREFLIGHT": "1"},
    )
    assert res.returncode != 0
    assert "The health gate failed (simulated)" in res.stderr and "ROLLBACK" in res.stdout
    assert "The schema did not change" in res.stdout  # no migration ran, so the data is intact
    assert running_image("api") == good_image  # the previous version is running again
    assert sh("./scripts/smoke.sh").returncode == 0  # and the system answers


def test_the_stack_still_works_after_both(upgraded: dict[str, object]) -> None:
    r = sh("make", "conformance")
    assert r.returncode == 0 and "CONFORMANCE OK" in r.stdout, r.stdout[-800:]


def test_an_unhealthy_stack_is_not_upgraded(upgraded: dict[str, object]) -> None:
    """The pre-flight check refuses to build on top of an existing problem."""
    sh(
        "docker",
        "compose",
        "-p",
        SRC,
        "-f",
        str(COMPOSE_FILE),
        "--env-file",
        str(ENV),
        "stop",
        "proxy",
    )
    try:
        res = sh("./scripts/upgrade.sh", env={"BUILD_ID": "t7-never"})
        assert res.returncode != 0 and "not healthy now" in res.stderr
        assert build_label("api") != "t7-never"  # nothing was built
    finally:
        sh(
            "docker",
            "compose",
            "-p",
            SRC,
            "-f",
            str(COMPOSE_FILE),
            "--env-file",
            str(ENV),
            "start",
            "proxy",
        )
        sh("./scripts/smoke.sh")
