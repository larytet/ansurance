"""M19: the container boundary. P-27 (escape attempts from inside), the compose file's
lock-down, and a real round trip through the service.

The fast test reads the compose file. The slow tests build the sandbox image and run the
service with exactly the options the compose file gives it (read from the file, not copied),
then attack from inside: network, environment, filesystem, privileges, processes, memory."""

import json
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest
import yaml

from app.sandbox.client import Limits, SandboxRejected
from app.sandbox.container import ContainerSandbox
from tests.intake_data import samples as S

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = yaml.safe_load((ROOT / "deploy/compose/docker-compose.yml").read_text())
SVC = COMPOSE["services"]["sandbox"]


def test_compose_locks_the_sandbox_down() -> None:
    assert SVC["read_only"] is True
    assert SVC["cap_drop"] == ["ALL"] and "cap_add" not in SVC
    assert "no-new-privileges:true" in SVC["security_opt"]
    assert not str(SVC["user"]).startswith("0") and str(SVC["user"]) != "root"
    assert (
        SVC["pids_limit"] <= 256 and SVC["mem_limit"] and SVC["memswap_limit"] == SVC["mem_limit"]
    )
    for forbidden in (
        "ports",
        "volumes",
        "environment",
        "env_file",
        "privileged",
        "devices",
        "cap_add",
    ):
        assert forbidden not in SVC, (
            forbidden
        )  # no published port, no host path, no secret, no extra right
    assert SVC["networks"] == ["sandbox_net"]
    assert COMPOSE["networks"]["sandbox_net"]["internal"] is True  # no route to the outside
    on_net = [
        n for n, s in COMPOSE["services"].items() if "sandbox_net" in (s.get("networks") or [])
    ]
    assert sorted(on_net) == ["sandbox", "worker"]  # only the worker may talk to it
    assert "build" in SVC and SVC["build"]["dockerfile"] == "sandbox/Dockerfile"


def test_the_api_image_has_no_parsers_and_the_sandbox_image_has_no_application() -> None:
    api = (ROOT / "backend/Dockerfile").read_text()
    box = (ROOT / "backend/sandbox/Dockerfile").read_text()
    assert "tesseract" not in api and "--only-group sandbox" not in api
    assert "COPY app" not in box and "COPY migrations" not in box and "alembic" not in box
    toml = (ROOT / "backend/pyproject.toml").read_text()
    main_deps = toml.split("[dependency-groups]")[0]
    for parser in ("pypdfium2", "pillow", "opencv", "numpy"):
        assert parser not in main_deps, parser


def test_no_application_code_imports_a_file_parser() -> None:
    bad = re.compile(r"^\s*(import|from)\s+(PIL|cv2|numpy|pypdfium2)\b", re.M)
    offenders = [p.name for p in (ROOT / "backend/app").rglob("*.py") if bad.search(p.read_text())]
    assert offenders == []  # only backend/sandbox may open an untrusted file


# ---- slow: a real container ------------------------------------------------------------------
def docker(*args: str, check: bool = True, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=check, timeout=timeout
    )


HAVE_DOCKER = (
    shutil.which("docker") is not None
    and subprocess.run(["docker", "info"], capture_output=True, check=False).returncode == 0
)
needs_docker = pytest.mark.skipif(not HAVE_DOCKER, reason="docker is not available")


def run_flags(net: str) -> list[str]:
    """The compose options of the sandbox service, as `docker run` arguments."""
    f = ["--read-only", "--user", str(SVC["user"]), "--network", net]
    f += ["--memory", SVC["mem_limit"], "--memory-swap", SVC["memswap_limit"]]
    f += ["--pids-limit", str(SVC["pids_limit"]), "--cpus", str(SVC["cpus"])]
    for cap in SVC["cap_drop"]:
        f += ["--cap-drop", cap]
    for opt in SVC["security_opt"]:
        f += ["--security-opt", opt.replace("no-new-privileges:true", "no-new-privileges=true")]
    for t in SVC["tmpfs"]:
        f += ["--tmpfs", t]
    return f


@pytest.fixture(scope="module")
def image() -> str:
    if not HAVE_DOCKER:
        pytest.skip("docker is not available")
    tag = f"ansurance-sandbox-test:{uuid.uuid4().hex[:8]}"
    docker("build", "-q", "-t", tag, "-f", "sandbox/Dockerfile", ".", timeout=1200)
    yield tag
    docker("rmi", "-f", tag, check=False)


@pytest.fixture(scope="module")
def net() -> str:
    if not HAVE_DOCKER:
        pytest.skip("docker is not available")
    name = f"sbx-test-{uuid.uuid4().hex[:8]}"
    docker("network", "create", "--internal", name)
    yield name
    docker("network", "rm", name, check=False)


def attack(image: str, net: str, code: str, timeout: int = 120) -> dict[str, object]:
    """Run Python inside the sandbox container the way a compromised parser could."""
    out = docker(
        "run", "--rm", *run_flags(net), "--entrypoint", "python", image, "-c", code, timeout=timeout
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.mark.slow
@needs_docker
def test_an_exploited_parser_has_no_network_secrets_filesystem_or_privileges(
    image: str, net: str
) -> None:
    code = r"""
import json, os, socket, subprocess
r = {}
def attempt(name, fn):
    try: fn(); r[name] = "allowed"
    except Exception as e: r[name] = "denied:" + type(e).__name__
def connect(host, port):
    s = socket.socket(); s.settimeout(3); s.connect((host, port)); s.close()
attempt("internet_ip", lambda: connect("1.1.1.1", 443))
attempt("dns", lambda: socket.gethostbyname("example.com"))
attempt("database", lambda: connect("postgres", 5432))
attempt("write_etc", lambda: open("/etc/pwned", "w").write("x"))
attempt("write_srv", lambda: open("/srv/pwned", "w").write("x"))
attempt("write_work", lambda: open("/work/ok", "w").write("x"))
attempt("setuid", lambda: os.setuid(0))
attempt("chown", lambda: os.chown("/work/ok", 0, 0))
r["env"] = sorted(os.environ)
r["uid"] = os.getuid()
status = open("/proc/self/status").read()
r["cap_eff"] = [l.split()[1] for l in status.splitlines() if l.startswith("CapEff")][0]
r["no_new_privs"] = [l.split()[1] for l in status.splitlines() if l.startswith("NoNewPrivs")][0]
r["app_code"] = os.path.exists("/srv/app")
print(json.dumps(r))
"""
    r = attack(image, net, code)
    for name in ("internet_ip", "dns", "database", "write_etc", "write_srv", "setuid", "chown"):
        assert str(r[name]).startswith("denied"), (name, r[name])
    assert r["write_work"] == "allowed"  # the memory-backed work directory is the one place
    assert r["uid"] == 10002 and r["cap_eff"] == "0000000000000000" and r["no_new_privs"] == "1"
    assert not any(k.startswith(("ANS_", "AWS_", "POSTGRES")) for k in r["env"])  # type: ignore[attr-defined]
    assert r["app_code"] is False  # the API's code is not in the image


@pytest.mark.slow
@needs_docker
def test_a_fork_bomb_and_a_memory_bomb_are_stopped_by_the_limits(image: str, net: str) -> None:
    forks = r"""
import json, os
n = 0
try:
    for _ in range(2000):
        if os.fork() == 0:
            import time; time.sleep(20); os._exit(0)
        n += 1
except OSError:
    pass
print(json.dumps({"forked": n}))
os._exit(0)
"""
    r = attack(image, net, forks)
    assert int(r["forked"]) < SVC["pids_limit"]  # type: ignore[arg-type]
    mem = r"""
import json
chunks = []
try:
    while True: chunks.append(bytearray(256 * 1024 * 1024))
except MemoryError:
    print(json.dumps({"stopped": "MemoryError", "mb": len(chunks) * 256}))
"""
    proc = docker(
        "run", "--rm", *run_flags(net), "--entrypoint", "python", image, "-c", mem, check=False
    )
    killed = proc.returncode in (137, -9)  # the kernel killed it at the container limit
    assert killed or "MemoryError" in proc.stdout


@pytest.mark.slow
@needs_docker
def test_the_service_renders_through_the_container_and_refuses_hostile_files(
    image: str, net: str
) -> None:
    name = f"sbx-svc-{uuid.uuid4().hex[:8]}"
    docker("run", "-d", "--name", name, *run_flags(net), image)
    try:
        ip = ""
        for _ in range(30):
            ip = docker(
                "inspect",
                "-f",
                f'{{{{ (index .NetworkSettings.Networks "{net}").IPAddress }}}}',
                name,
            ).stdout.strip()
            if ip:
                break
            time.sleep(1)
        box = ContainerSandbox(Limits(timeout_s=90, max_pages=5), f"http://{ip}:8080")
        for _ in range(30):  # wait for the server
            try:
                if box._post("/health", b"")[0] in (200, 400, 404, 501):  # noqa: SLF001
                    break
            except Exception:  # noqa: BLE001
                time.sleep(1)
        pages = [S.render(S.id_card("123456782")), S.blank(), S.render(S.invoice("000000026"))]
        r = box.render(S.pdf(pages), "application/pdf", 100)
        assert len(r.pages) == 3 and r.blank == [False, True, False]
        words = box.ocr(r.pages[:1], "eng")
        assert words is not None and words.pages == 1
        with pytest.raises(SandboxRejected) as e:
            box.render(b"%PDF-1.4\n" + b"junk" * 100, "application/pdf", 100)
        assert e.value.reason in ("unreadable", "sandbox_failed")
        with pytest.raises(SandboxRejected) as e2:
            box.render(S.tiff([S.render(["x"])] * 7), "image/tiff", 50)
        assert e2.value.reason == "too_many_pages"
    finally:
        docker("rm", "-f", name, check=False)
