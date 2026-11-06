#!/usr/bin/env python3
"""Fail if anything that goes into a deployment is not pinned (docs/PLATFORM-REQUIREMENTS.md,
"Predictable deployment"). A rebuild or redeploy on another day must give the same result.

Checked:
  Dockerfiles   base images by digest; apk and apt packages by exact version; pip by ==;
                no npm install, no `git clone` of a moving ref, no `@latest`, no curl | sh;
                source builds by commit (an *_COMMIT build argument holds 40 hex digits)
  compose       every image by digest (images that are built locally are exempt), no :latest
  workflows     every action by commit SHA, exact Node, Terraform and uv versions, no *-latest runner
  Terraform     an exact required_version, a provider lock file, image defaults by digest
  tests         container images used by the tests by digest
  lock files    uv.lock and package-lock.json exist and are used (uv sync --frozen, npm ci)

Usage: scripts/check_pins.py [repo-root]   (exit 1 and a list of problems if any)
"""

import re
import sys
from pathlib import Path

import yaml

DIGEST = re.compile(r"@sha256:[0-9a-f]{64}\b")
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SEMVER3 = re.compile(r"^\d+\.\d+\.\d+$")


def _logical_lines(text: str) -> list[str]:
    """Dockerfile lines with backslash continuations joined and comments dropped."""
    out, cur = [], ""
    for raw in text.splitlines():
        line = raw.strip()
        if not cur and (not line or line.startswith("#")):
            continue
        cur += (" " if cur else "") + line.rstrip("\\").strip()
        if not line.endswith("\\"):
            out.append(cur)
            cur = ""
    if cur:
        out.append(cur)
    return out


def _packages(cmd: str, installer: str) -> list[str]:
    """Package arguments of `<installer> ...` (everything that is not a flag)."""
    m = re.search(rf"\b{installer}\b(.*?)(?:&&|;|\|\||$)", cmd)
    if not m:
        return []
    return [t for t in m.group(1).split() if not t.startswith("-") and t not in ("add", "install")]


def check_dockerfile(name: str, text: str) -> list[str]:
    problems: list[str] = []
    stages: set[str] = set()
    for line in _logical_lines(text):
        up = line.split()[0].upper()
        if up == "FROM":
            parts = line.split()
            image = parts[1]
            alias = parts[3] if len(parts) >= 4 and parts[2].upper() == "AS" else None
            if image not in stages and image != "scratch" and not DIGEST.search(image):
                problems.append(f"{name}: base image {image} is not pinned by digest")
            if alias:
                stages.add(alias)
        elif up == "ARG":
            m = re.match(r"ARG\s+(\w*COMMIT)=(\S*)", line)
            if m and not SHA40.match(m.group(2)):
                problems.append(f"{name}: {m.group(1)} must be a full 40-digit commit, got {m.group(2)!r}")
        elif up == "ADD" and re.search(r"ADD\s+https?://", line):
            problems.append(f"{name}: ADD from a URL is not allowed")
        elif up == "RUN":
            for pkg in _packages(line, "apk add"):
                if "=" not in pkg:
                    problems.append(f"{name}: apk package {pkg} has no exact version (pkg=1.2.3-r0)")
            for pkg in _packages(line, "apt-get install"):
                if "=" not in pkg and pkg != "-y":
                    problems.append(f"{name}: apt package {pkg} has no exact version")
            for pkg in _packages(line, "pip install"):
                if "==" not in pkg:
                    problems.append(f"{name}: pip package {pkg} has no exact version (==)")
            if re.search(r"\bnpm install\b", line):
                problems.append(f"{name}: use `npm ci` (package-lock.json), not `npm install`")
            if "git clone" in line:
                problems.append(f"{name}: `git clone` follows a moving ref; fetch a commit instead")
            if re.search(r"git fetch\b", line) and not re.search(r"\$\{?\w*COMMIT\}?|\b[0-9a-f]{40}\b", line):
                problems.append(f"{name}: `git fetch` must name a commit")
            if re.search(r"@latest\b", line):
                problems.append(f"{name}: @latest is not allowed")
            if re.search(r"(curl|wget)[^|]*\|\s*(ba)?sh", line):
                problems.append(f"{name}: piping a download into a shell is not allowed")
            if re.search(r"\buv sync\b", line) and "--frozen" not in line:
                problems.append(f"{name}: `uv sync` must use --frozen")
    return problems


def check_compose(name: str, text: str) -> list[str]:
    problems = []
    data = yaml.safe_load(text) or {}
    for svc, spec in (data.get("services") or {}).items():
        image = spec.get("image")
        if image and "build" not in spec and not DIGEST.search(str(image)):
            problems.append(f"{name}: service {svc} image {image} is not pinned by digest")
    if re.search(r"image:\s*\S+:latest\b", text):
        problems.append(f"{name}: :latest is not allowed")
    return problems


def check_workflow(name: str, text: str) -> list[str]:
    problems = []
    for m in re.finditer(r"uses:\s*([^\s#]+)", text):
        ref = m.group(1)
        if ref.startswith("./") or ref.startswith("docker://"):
            continue
        if "@" not in ref or not SHA40.match(ref.split("@", 1)[1]):
            problems.append(f"{name}: action {ref} must be pinned to a full commit SHA")
    for m in re.finditer(r"node-version:\s*([\w.'\"]+)", text):
        if not SEMVER3.match(m.group(1).strip("'\"")):
            problems.append(f"{name}: node-version {m.group(1)} must be exact (x.y.z)")
    for m in re.finditer(r"terraform_version:\s*([\w.'\"]+)", text):
        if not SEMVER3.match(m.group(1).strip("'\"")):
            problems.append(f"{name}: terraform_version {m.group(1)} must be exact")
    for m in re.finditer(r"setup-uv@[0-9a-f]{40}[^\n]*\n(?P<rest>(?:\s+with:[^\n]*\n)?)", text):
        if 'version: "' not in m.group("rest"):
            problems.append(f"{name}: setup-uv must set an exact uv version")
    if re.search(r"runs-on:\s*\S*-latest", text):
        problems.append(f"{name}: runner must be pinned (ubuntu-24.04), not *-latest")
    return problems


def check_terraform(root: Path) -> list[str]:
    problems = []
    tf = root / "infra" / "aws"
    versions = (tf / "versions.tf").read_text()
    if not re.search(r'required_version\s*=\s*"=\s*\d+\.\d+\.\d+"', versions):
        problems.append("infra/aws/versions.tf: required_version must be exact (= x.y.z)")
    lock = tf / ".terraform.lock.hcl"
    if not lock.exists():
        problems.append("infra/aws: .terraform.lock.hcl (provider lock) is missing")
    else:
        text = lock.read_text()
        if text.count('h1:') < 2:
            problems.append("infra/aws/.terraform.lock.hcl: provider hashes for several platforms expected")
    for f in tf.glob("*.tf"):
        text = f.read_text()
        for m in re.finditer(r'variable\s+"(\w*image\w*)"\s*\{[^}]*default\s*=\s*"([^"]+)"', text):
            if not DIGEST.search(m.group(2)):
                problems.append(f"{f.relative_to(root)}: default of {m.group(1)} is not pinned by digest")
        if re.search(r'variable\s+"image_tag"\s*\{[^}]*default\s*=', text):
            problems.append(f"{f.relative_to(root)}: image_tag must have no default (name the SHA you deploy)")
    return problems


def check_test_images(name: str, text: str) -> list[str]:
    problems = []
    patterns = [r'DockerContainer\(\s*"([^"]+)"', r'PostgresContainer\(\s*"([^"]+)"', r'IMAGE\s*=\s*"([^"]+)"',
                r'"(alpine[^"]*)"\s*,']
    for pat in patterns:
        for m in re.finditer(pat, text):
            if not DIGEST.search(m.group(1)):
                problems.append(f"{name}: test image {m.group(1)} is not pinned by digest")
    return problems


def check_repo(root: Path) -> list[str]:
    problems: list[str] = []
    for df in [p for p in root.rglob("Dockerfile") if not {"node_modules", ".venv"} & set(p.parts)]:
        problems += check_dockerfile(str(df.relative_to(root)), df.read_text())
    for cf in (root / "deploy" / "compose").glob("docker-compose*.yml"):
        problems += check_compose(str(cf.relative_to(root)), cf.read_text())
    for wf in (root / ".github" / "workflows").glob("*.yml"):
        problems += check_workflow(str(wf.relative_to(root)), wf.read_text())
    problems += check_terraform(root)
    for tf in (root / "backend" / "tests").glob("*.py"):
        if tf.name == "test_pins.py":
            continue  # its fixtures are deliberately unpinned
        problems += check_test_images(str(tf.relative_to(root)), tf.read_text())
    for lock in ("backend/uv.lock", "web/package-lock.json"):
        if not (root / lock).exists():
            problems.append(f"{lock} is missing")
    pyproject = (root / "backend" / "pyproject.toml").read_text()
    if re.search(r"git\+|@\s*https?://", pyproject):
        problems.append("backend/pyproject.toml: dependencies from git or URLs are not allowed")
    pkg = (root / "web" / "package.json").read_text()
    if re.search(r'"(latest|\*)"|git\+|github:', pkg):
        problems.append("web/package.json: dependencies on latest, * or git are not allowed")
    return problems


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
    problems = check_repo(root)
    for p in problems:
        print("UNPINNED", p)
    if problems:
        print(f"\n{len(problems)} problem(s). See docs/OPERATIONS.md, 'Updating pinned versions'.")
        return 1
    print("pins OK: images by digest, sources by commit, packages by version, actions by SHA")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
