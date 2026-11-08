#!/usr/bin/env python3
"""Licence review (docs/TESTPLAN.md L-1..L-8): inventory, classify, gate, and write the notices.

    uv run python ../scripts/licenses.py check [--release]   gate (exit 1 on a problem)
    uv run python ../scripts/licenses.py write               regenerate docs/LICENSES.md data
                                                              and web/src/notices.json
    uv run python ../scripts/licenses.py images              record licences found inside the
                                                              container images (needs Docker)

The policy and the owner's decisions are in docs/licenses-policy.yml. The gate:
  - a refused licence (missing, unknown) always fails;
  - a "needs review" licence on a component that has no entry in `exceptions` fails;
  - `--release` also fails on an exception nobody has signed (approved_by empty), on a
    review older than `max_review_age_days`, and on a notices file that is out of date.
"""

import importlib.metadata as md
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "docs" / "licenses-policy.yml"
NOTICES = ROOT / "web" / "src" / "notices.json"

# Names found in package metadata -> SPDX. Anything not matched stays UNKNOWN (refused).
NAME_TO_SPDX = [
    (r"apache.*2", "Apache-2.0"),
    (r"bsd.*3|new bsd|modified bsd", "BSD-3-Clause"),
    (r"bsd.*2|simplified bsd", "BSD-2-Clause"),
    (r"^bsd", "BSD-3-Clause"),
    (r"mozilla public license.*2|mpl.*2", "MPL-2.0"),
    (r"^mit|mit license|the mit", "MIT"),
    (r"^isc", "ISC"),
    (r"python software foundation|^psf", "PSF-2.0"),
    (r"lgpl|lesser general public", "LGPL-3.0-or-later"),
    (r"affero", "AGPL-3.0-or-later"),
    (r"gnu general public|^gpl", "GPL-3.0-or-later"),
    (r"unlicense", "Unlicense"),
    (r"zlib", "Zlib"),
    (r"hpnd|historical permission", "HPND"),
]
CLASSIFIER_TO_SPDX = {
    "License :: OSI Approved :: MIT License": "MIT",
    "License :: OSI Approved :: BSD License": "BSD-3-Clause",
    "License :: OSI Approved :: Apache Software License": "Apache-2.0",
    "License :: OSI Approved :: ISC License (ISCL)": "ISC",
    "License :: OSI Approved :: Python Software Foundation License": "PSF-2.0",
    "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "License :: OSI Approved :: GNU Lesser General Public License v3 or later (LGPLv3+)": "LGPL-3.0-or-later",
    "License :: OSI Approved :: GNU Affero General Public License v3": "AGPL-3.0-only",
}
ALIASES = {"PSFL": "PSF-2.0", "Expat": "MIT", "X11": "X11", "Public Domain": "LicenseRef-PublicDomain",
           "2-clause BSD-like license": "BSD-2-Clause", "Apache License 2.0": "Apache-2.0"}
# The few distributions whose metadata carries no usable licence; each was read by hand from
# the project's own licence file, and the line says where. Re-check when the version changes.
MANUAL = {
    ("python", "python-bidi"): ("LGPL-3.0-or-later", "COPYING and COPYING.LESSER in the wheel (test helper, dev only)"),
    ("python", "pypdfium2"): ("Apache-2.0 OR BSD-3-Clause", "project LICENSE (also bundles PDFium, BSD-3-Clause)"),
}


@dataclass
class Component:
    ecosystem: str  # python | npm
    name: str
    version: str
    scope: str  # shipped | dev
    licence: str  # normalised SPDX expression, or UNKNOWN
    raw: str = ""


# ---- normalising and classifying -----------------------------------------------------------
def to_spdx(raw: str, classifiers: list[str] | None = None) -> str:
    raw = ALIASES.get((raw or "").strip(), (raw or "").strip())
    if raw and len(raw) < 120 and re.fullmatch(r"[A-Za-z0-9.+\- ()]+", raw) and (
        " OR " in raw or " AND " in raw or re.fullmatch(r"[A-Za-z0-9.+\-]+", raw)
    ):
        if re.fullmatch(r"[A-Za-z0-9.+\-]+(?: (?:OR|AND|WITH) [A-Za-z0-9.+\-]+)*", raw):
            if raw.upper() not in ("UNKNOWN", "OTHER", "NONE"):
                return raw
    if raw:
        low = raw.lower()
        for pat, spdx in NAME_TO_SPDX:
            if re.search(pat, low):
                return spdx
    found = sorted({CLASSIFIER_TO_SPDX[c] for c in classifiers or [] if c in CLASSIFIER_TO_SPDX})
    if found:
        return " OR ".join(found) if len(found) > 1 else found[0]
    return "UNKNOWN"


def _rank(cls: str) -> int:
    return {"allowed": 0, "review": 1, "refused": 2}[cls]


def classify(expr: str, policy: dict) -> str:
    """allowed | review | refused. OR takes the best alternative, AND the worst; WITH is ignored."""
    expr = expr.strip().strip("()")
    if not expr or expr == "UNKNOWN":
        return "refused"
    if " AND " in expr:
        return max((classify(p, policy) for p in expr.split(" AND ")), key=_rank)
    if " OR " in expr:
        return min((classify(p, policy) for p in expr.split(" OR ")), key=_rank)
    ident = expr.split(" WITH ")[0].strip()
    if ident in policy["refused"]:
        return "refused"
    if ident in policy["allowed"]:
        return "allowed"
    return "review"  # a named licence we have not classed: a person decides


# ---- inventory -------------------------------------------------------------------------------
def _uv_names(*args: str) -> set[str]:
    out = subprocess.run(  # noqa: S603
        ["uv", "export", "--frozen", "--no-hashes", "--no-emit-project", *args],  # noqa: S607
        capture_output=True, text=True, check=True, cwd=ROOT / "backend",
    ).stdout
    return {m.group(1).lower().replace("_", "-") for m in re.finditer(r"^([A-Za-z0-9_.\-]+)==", out, re.M)}


def python_components() -> list[Component]:
    shipped = _uv_names("--no-dev") | _uv_names("--no-dev", "--only-group", "sandbox")
    everything = _uv_names()
    out = []
    for name in sorted(everything):
        try:
            dist = md.distribution(name)
        except md.PackageNotFoundError:
            continue
        meta = dist.metadata
        raw = meta.get("License-Expression") or meta.get("License") or ""
        spdx = to_spdx(raw, meta.get_all("Classifier") or [])
        if (("python", name)) in MANUAL:
            spdx, raw = MANUAL[("python", name)][0], MANUAL[("python", name)][1]
        out.append(Component("python", name, dist.version, "shipped" if name in shipped else "dev", spdx, raw[:80]))
    return out


def npm_components() -> list[Component]:
    lock = json.loads((ROOT / "web" / "package-lock.json").read_text())
    out = []
    for path, v in sorted(lock["packages"].items()):
        if not path:
            continue
        name = path.split("node_modules/")[-1]
        raw = v.get("license") or ""
        if isinstance(raw, dict):
            raw = raw.get("type", "")
        out.append(Component("npm", name, v.get("version", ""), "dev" if v.get("dev") else "shipped", to_spdx(str(raw)), str(raw)[:80]))
    return out


def inventory() -> list[Component]:
    return python_components() + npm_components()


# ---- the gate ----------------------------------------------------------------------------------
def load_policy(path: Path = POLICY) -> dict:
    return yaml.safe_load(path.read_text())


def exception_for(c: Component, policy: dict) -> dict | None:
    for e in policy.get("exceptions") or []:
        if e["component"] == c.name and e.get("ecosystem", c.ecosystem) == c.ecosystem:
            return e
    return None


def check(components: list[Component], policy: dict, release: bool = False, today: date | None = None) -> list[str]:
    today = today or date.today()
    problems: list[str] = []
    for c in components:
        cls = classify(c.licence, policy)
        if cls == "refused":
            problems.append(f"{c.ecosystem}:{c.name} {c.version}: licence {c.licence!r} is refused (unknown or not allowed)")
        elif cls == "review" and c.scope == "shipped":
            e = exception_for(c, policy)
            if e is None:
                problems.append(f"{c.ecosystem}:{c.name} {c.version}: licence {c.licence} needs review and has no entry in docs/licenses-policy.yml")
            elif release and not e.get("approved_by"):
                problems.append(f"{c.name}: exception is not signed (approved_by is empty)")
    if release:
        for e in policy.get("exceptions") or []:
            if not e.get("approved_by"):
                problems.append(f"exception {e['component']}: not signed")
        for item in policy.get("terms") or []:
            if not item.get("approved_by"):
                problems.append(f"terms {item['name']}: not signed (L-5)")
        for item in policy.get("network_licences") or []:
            if not item.get("approved_by"):
                problems.append(f"network licence {item['name']}: not signed (L-4)")
        if not (policy.get("system_packages") or {}).get("approved_by"):
            problems.append("system packages rule: not signed (L-6)")
        rv = policy.get("review") or {}
        if not rv.get("signed_by") or not rv.get("date"):
            problems.append("the list has no signed review date (L-8)")
        else:
            age = (today - date.fromisoformat(str(rv["date"]))).days
            if age > int(policy.get("max_review_age_days", 180)):
                problems.append(f"the last review is {age} days old (limit {policy.get('max_review_age_days', 180)})")
        if notices_json(components, policy) != (NOTICES.read_text() if NOTICES.exists() else ""):
            problems.append("web/src/notices.json is out of date: run `licenses.py write`")
    return problems


def notices_json(components: list[Component], policy: dict) -> str:
    shipped = [c for c in components if c.scope == "shipped"]
    items = [
        {"name": c.name, "version": c.version, "licence": c.licence, "ecosystem": c.ecosystem}
        for c in sorted(shipped, key=lambda c: (c.ecosystem, c.name.lower()))
    ]
    return json.dumps({"attributions": policy.get("attributions") or [], "components": items}, indent=1, ensure_ascii=False) + "\n"


def render_markdown(components: list[Component], policy: dict, images: list[dict]) -> str:
    """docs/LICENSES.md: written from the policy file and the inventory, never by hand."""
    def sig(item: dict) -> str:
        return f"{item['approved_by']} on {item['approved_on']}" if item.get("approved_by") else "**not signed**"

    sm = summary(components, policy)
    rv = policy.get("review") or {}
    L = ["# Licences", "",
         "Generated by `scripts/licenses.py write` from `docs/licenses-policy.yml` (the rules and the owner's decisions), the lock files and the images. Do not edit by hand.",
         "Method and tests: TESTPLAN section 4, L-1..L-8.", "",
         "## Status", "",
         f"- Last signed review: {('%s on %s' % (rv['signed_by'], rv['date'])) if rv.get('signed_by') else '**none yet**'}; a release needs one no older than {policy.get('max_review_age_days', 180)} days.",
         f"- Shipped components: {sum(sm['shipped'].values())} ({sm['shipped'].get('allowed', 0)} allowed, {sm['shipped'].get('review', 0)} needing review, {sm['shipped'].get('refused', 0)} refused).",
         f"- Development tools (not shipped): {sum(sm['dev'].values())} ({sm['dev'].get('allowed', 0)} allowed, {sm['dev'].get('review', 0)} needing review, {sm['dev'].get('refused', 0)} refused).",
         "- Every decision below is **proposed by the review** until the owner signs it; the normal build accepts a proposal, `check --release` does not.", "",
         "## Rules", "",
         f"- Allowed: {', '.join(policy['allowed'])}.",
         f"- Refused: {', '.join(policy['refused'])} and any missing or unknown licence.",
         "- Everything else (LGPL, GPL, AGPL and any other named licence) needs review and an entry below.", "",
         "## Needs-review components that ship", "",
         "| Component | Licence | Proposal | Reason | Signed |", "|---|---|---|---|---|"]
    for e in policy.get("exceptions") or []:
        L.append(f"| {e['component']} | {e['licence']} | {e['proposal']} | {' '.join(str(e['reason']).split())} | {sig(e)} |")
    if not policy.get("exceptions"):
        L.append("| none | | | | |")
    L += ["", "## Network-service licences (L-4)", "", "| Component | Licence | Where | Proposal | Signed |", "|---|---|---|---|---|"]
    for e in policy.get("network_licences") or []:
        L.append(f"| {e['name']} | {e['licence']} | {e['where']} | {' '.join(str(e['proposal']).split())} | {sig(e)} |")
    L += ["", "## Terms rather than licences (L-5)", "", "| Item | Kind | Note | Signed |", "|---|---|---|---|"]
    for e in policy.get("terms") or []:
        L.append(f"| {e['name']} | {e['kind']} | {' '.join(str(e['note']).split())} | {sig(e)} |")
    sp = policy.get("system_packages") or {}
    L += ["", "## Container images (L-6)", "",
          f"Rule for operating-system packages: {' '.join(str(sp.get('rule', '')).split())} Signed: {sig(sp)}.", "",
          "| Image | Distinct licences | To review | Refused |", "|---|---|---|---|"]
    for r in images:
        L.append(f"| {r['image'].split('@')[0]} | {len(r['licences'])} | {len(r['review'])} | {len(r['refused'])} |")
    L += ["", "The full list per image is in `docs/licenses-images.json`, read from the image itself.", "",
          "## Shipped components", "", "| Ecosystem | Component | Version | Licence |", "|---|---|---|---|"]
    for c in sorted((c for c in components if c.scope == "shipped"), key=lambda c: (c.ecosystem, c.name.lower())):
        L.append(f"| {c.ecosystem} | {c.name} | {c.version} | {c.licence} |")
    L += ["", "The development tools and their licences are in `docs/licenses-inventory.json`.", ""]
    return "\n".join(L)


def summary(components: list[Component], policy: dict) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {"shipped": {}, "dev": {}}
    for c in components:
        cls = classify(c.licence, policy)
        out[c.scope][cls] = out[c.scope].get(cls, 0) + 1
    return out


# ---- container images ------------------------------------------------------------------------
def images_from_compose() -> list[str]:
    spec = yaml.safe_load((ROOT / "deploy" / "compose" / "docker-compose.yml").read_text())
    names = []
    for key, svc in spec["services"].items():
        names.append(svc["image"] if "image" in svc else f"ansurance-{key}")
    return sorted(set(names))


def _debian_id(name: str) -> str:
    n = name.strip()
    n = ALIASES.get(n, n)
    n = re.sub(r"(?i)bsd-(\d)-clause", lambda m: f"BSD-{m.group(1)}-Clause", n)
    m = re.fullmatch(r"(A?L?GPL)-(\d(?:\.\d)?)(\+?)", n)
    if m:
        v = m.group(2) if "." in m.group(2) else m.group(2) + ".0"
        n = f"{m.group(1)}-{v}-{'or-later' if m.group(3) else 'only'}"
    return n


def split_ids(text: str) -> list[str]:
    """Licence identifiers in one package's licence field (AND, OR, 'and', 'or' separate them)."""
    parts = re.split(r"\s+(?:AND|OR|and|or|with|WITH)\s+|,", text)
    return [_debian_id(p) for p in parts if p.strip()]


def image_licences(image: str, policy: dict) -> dict | None:
    """Licence names found inside the image itself (L-6): the image label, the `License:`
    lines of Debian packages' copyright files, and the Alpine package database."""
    def run(*a: str) -> str | None:
        r = subprocess.run(["docker", *a], capture_output=True, text=True, check=False)  # noqa: S603, S607
        return r.stdout if r.returncode == 0 else None

    meta = run("image", "inspect", "-f", "{{json .Config.Labels}}", image)
    if meta is None:
        return None
    labels = json.loads(meta or "null") or {}
    deb = run("run", "--rm", "--entrypoint", "sh", image, "-c", "grep -rh '^License:' /usr/share/doc/*/copyright 2>/dev/null | sed 's/^License: *//' | sort | uniq -c")
    apk = run("run", "--rm", "--entrypoint", "sh", image, "-c", "[ -f /lib/apk/db/installed ] && grep -h '^L:' /lib/apk/db/installed | sed 's/^L://' | sort | uniq -c")
    counts: dict[str, int] = {}
    for line in ((deb or "") + (apk or "")).splitlines():
        m = re.match(r"\s*(\d+)\s+(.*)", line)
        if not m:
            continue
        for ident in split_ids(m.group(2)):
            counts[ident] = counts.get(ident, 0) + int(m.group(1))
    classes = {i: classify(i, policy) for i in counts}
    return {
        "image": image,
        "label": labels.get("org.opencontainers.image.licenses", ""),
        "package_database": bool(counts),
        "licences": dict(sorted(counts.items())),
        "review": sorted(i for i, c in classes.items() if c == "review"),
        "refused": sorted(i for i, c in classes.items() if c == "refused"),
    }


IMAGES = ROOT / "docs" / "licenses-images.json"


def check_images(policy: dict, compose_images: list[str], recorded: list[dict] | None) -> list[str]:
    """The recorded licences of every compose image must exist and contain nothing refused."""
    if recorded is None:
        return ["docs/licenses-images.json is missing: run `licenses.py images` where Docker is available"]
    have = {r["image"] for r in recorded}
    problems = [f"image {i}: no recorded licences (L-6)" for i in compose_images if i not in have]
    for r in recorded:
        for ident in r["refused"]:
            problems.append(f"image {r['image']}: contains a refused licence {ident}")
    return problems


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "check"
    policy = load_policy()
    if cmd == "images":
        found = [r for r in (image_licences(i, policy) for i in images_from_compose()) if r]
        IMAGES.write_text(json.dumps(found, indent=1) + "\n")
        for r in found:
            print(r["image"][:40], len(r["licences"]), "licences;", len(r["review"]), "to review;", len(r["refused"]), "refused")
        return 0
    comps = inventory()
    if cmd == "write":
        NOTICES.write_text(notices_json(comps, policy))
        (ROOT / "docs" / "licenses-inventory.json").write_text(json.dumps([asdict(c) | {"class": classify(c.licence, policy)} for c in comps], indent=1) + "\n")
        imgs = json.loads(IMAGES.read_text()) if IMAGES.exists() else []
        (ROOT / "docs" / "LICENSES.md").write_text(render_markdown(comps, policy, imgs))
        print(json.dumps(summary(comps, policy)))
        return 0
    problems = check(comps, policy, release="--release" in argv)
    problems += check_images(policy, images_from_compose(), json.loads(IMAGES.read_text()) if IMAGES.exists() else None)
    print(json.dumps(summary(comps, policy)))
    for p in problems:
        print("PROBLEM:", p)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
