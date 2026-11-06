"""Predictable deployment: nothing that goes into an image or a deploy may float.

The repository must pass `scripts/check_pins.py`, and the checker itself must catch each kind
of unpinned input (tested here with deliberately bad files, so it cannot rot into a no-op).
"""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("check_pins", ROOT / "scripts" / "check_pins.py")
assert spec and spec.loader
pins = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pins)

D = "@sha256:" + "a" * 64


def test_the_repository_is_fully_pinned() -> None:
    assert pins.check_repo(ROOT) == []


# ---- Dockerfiles ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text,fragment",
    [
        ("FROM python:3.12-slim\n", "not pinned by digest"),
        (f"FROM alpine:3.20{D}\nRUN apk add --no-cache curl\n", "apk package curl"),
        (f"FROM alpine:3.20{D}\nRUN apk add --no-cache curl=8.0-r0 bash\n", "apk package bash"),
        (
            f"FROM debian{D}\nRUN apt-get install -y --no-install-recommends tesseract-ocr\n",
            "apt package tesseract-ocr",
        ),
        (f"FROM python{D}\nRUN pip install uv\n", "pip package uv"),
        (
            f"FROM python{D}\nRUN pip install --no-cache-dir uv==0.5.*\n",
            "",
        ),  # wildcard still has ==
        (f"FROM node{D}\nRUN npm install\n", "npm ci"),
        (f"FROM golang{D}\nRUN git clone --depth 1 --branch v1 https://x/y.git\n", "git clone"),
        (f"FROM golang{D}\nRUN git fetch --depth 1 https://x/y.git main\n", "must name a commit"),
        (f"FROM golang{D}\nRUN go install example.com/tool@latest\n", "@latest"),
        (f"FROM alpine{D}\nRUN curl -sSL https://get.example.sh | sh\n", "piping a download"),
        (f"FROM alpine{D}\nADD https://example.com/x.tgz /x\n", "ADD from a URL"),
        (f"FROM python{D}\nRUN uv sync --no-dev\n", "--frozen"),
        (f"FROM golang{D}\nARG MINIO_COMMIT=RELEASE.2025\n", "40-digit commit"),
    ],
)
def test_dockerfile_rules_catch_unpinned_inputs(text: str, fragment: str) -> None:
    problems = pins.check_dockerfile("Dockerfile", text)
    if fragment:
        assert any(fragment in p for p in problems), problems
    else:
        assert problems == []


def test_dockerfile_good_example_and_stage_references() -> None:
    good = (
        f"FROM golang:1.24{D} AS build\nARG X_COMMIT={'b' * 40}\n"
        "RUN apk add --no-cache git=2.52.0-r0\n"
        "RUN git init -q m && cd m && git fetch -q --depth 1 https://x ${X_COMMIT}\n"
        f"FROM alpine:3.20{D}\nCOPY --from=build /o /o\nFROM build AS again\n"
    )
    assert pins.check_dockerfile("Dockerfile", good) == []


def test_continuation_lines_are_joined() -> None:
    text = f"FROM alpine{D}\nRUN apk add --no-cache \\\n    curl=8.14.1-r2 \\\n    bash\n"
    assert any("bash" in p for p in pins.check_dockerfile("Dockerfile", text))


# ---- compose -------------------------------------------------------------------------------
def test_compose_images_need_digests_unless_built_locally() -> None:
    bad = "services:\n  a:\n    image: postgres:16-alpine\n  b:\n    build: ./x\n  c:\n    image: foo:latest\n"
    problems = pins.check_compose("c.yml", bad)
    assert any("service a" in p for p in problems) and any("service c" in p for p in problems)
    assert not any("service b" in p for p in problems)
    assert pins.check_compose("c.yml", f"services:\n  a:\n    image: postgres:16{D}\n") == []


# ---- workflows -----------------------------------------------------------------------------
def test_workflow_rules() -> None:
    bad = (
        "jobs:\n  j:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - uses: actions/checkout@v4\n"
        f"      - uses: actions/setup-node@{'c' * 40}\n        with: {{ node-version: 20 }}\n"
        f"      - uses: astral-sh/setup-uv@{'d' * 40}\n"
        "      - uses: hashicorp/setup-terraform@"
        + "e" * 40
        + "\n        with:\n          terraform_version: 1.9\n"
    )
    problems = " | ".join(pins.check_workflow("w.yml", bad))
    for fragment in (
        "actions/checkout@v4",
        "node-version 20",
        "setup-uv must set",
        "terraform_version 1.9",
        "*-latest",
    ):
        assert fragment in problems, (fragment, problems)
    good = (
        "jobs:\n  j:\n    runs-on: ubuntu-24.04\n    steps:\n"
        f"      - uses: actions/checkout@{'a' * 40} # v4.4.0\n"
        f'      - uses: astral-sh/setup-uv@{"d" * 40}\n        with: {{ version: "0.5.31" }}\n'
        f"      - uses: actions/setup-node@{'c' * 40}\n        with: {{ node-version: 20.20.0 }}\n"
    )
    assert pins.check_workflow("w.yml", good) == []


# ---- test images ---------------------------------------------------------------------------
def test_test_images_need_digests() -> None:
    assert pins.check_test_images("t.py", 'DockerContainer("clamav/clamav:stable")')
    assert pins.check_test_images("t.py", 'IMAGE = "hashicorp/terraform:1.9"')
    assert pins.check_test_images("t.py", '    "alpine",\n')
    assert pins.check_test_images("t.py", f'DockerContainer("clamav/clamav:stable{D}")') == []


# ---- the actual files behave as pinned ------------------------------------------------------
def test_locks_exist_and_are_used() -> None:
    assert (ROOT / "backend" / "uv.lock").stat().st_size > 1000
    assert (ROOT / "web" / "package-lock.json").stat().st_size > 1000
    assert "uv sync --frozen" in (ROOT / "backend" / "Dockerfile").read_text()
    assert "npm ci" in (ROOT / "web" / "Dockerfile").read_text()


def test_the_source_builds_name_commits_that_match_the_documented_releases() -> None:
    text = (ROOT / "deploy" / "objectstore" / "Dockerfile").read_text()
    assert (
        "MINIO_COMMIT=9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a" in text
    )  # RELEASE.2025-10-15T17-29-55Z
    assert (
        "MC_COMMIT=7394ce0dd2a80935aded936b09fa12cbb3cb8096" in text
    )  # RELEASE.2025-08-13T08-35-41Z
    assert (
        "MC_COMMIT=7394ce0dd2a80935aded936b09fa12cbb3cb8096"
        in (ROOT / "deploy" / "backup" / "Dockerfile").read_text()
    )  # the same mc everywhere


def test_the_terraform_provider_lock_covers_several_platforms() -> None:
    lock = (ROOT / "infra" / "aws" / ".terraform.lock.hcl").read_text()
    assert (
        'version     = "5.100.0"' in lock and lock.count('"h1:') >= 4 and lock.count('"zh:') >= 10
    )
