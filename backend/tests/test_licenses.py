"""M20: the licence review as a gate (TESTPLAN L-1..L-8). The repository must pass
`scripts/licenses.py`, the checker must catch each kind of bad licence (tested on made-up
components so it cannot rot into a no-op), and the notices must match what ships."""

import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("licenses", ROOT / "scripts" / "licenses.py")
assert spec and spec.loader
lic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lic)

POLICY = lic.load_policy()


def comp(name: str, licence: str, scope: str = "shipped") -> object:
    return lic.Component("python", name, "1.0", scope, licence)


# ---- L-1 inventory ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def inventory() -> list:
    return lic.inventory()


def test_every_locked_package_is_listed_with_a_licence_and_a_version(inventory: list) -> None:
    py = {c.name for c in inventory if c.ecosystem == "python"}
    npm = {c.name for c in inventory if c.ecosystem == "npm"}
    locked = json.loads((ROOT / "web/package-lock.json").read_text())["packages"]
    assert {k.split("node_modules/")[-1] for k in locked if k} == npm
    assert {"fastapi", "sqlalchemy", "pypdfium2", "psycopg"} <= py
    assert all(c.version and c.licence for c in inventory)
    shipped = {c.name for c in inventory if c.scope == "shipped"}
    assert "react" in shipped and "@xyflow/react" in shipped and "pypdfium2" in shipped
    assert "pytest" not in shipped and "vitest" not in shipped  # tools are not shipped


# ---- L-2 classification ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "expr,expected",
    [
        ("MIT", "allowed"),
        ("BSD-3-Clause", "allowed"),
        ("Apache-2.0", "allowed"),
        ("MPL-2.0", "allowed"),
        ("Apache-2.0 OR BSD-3-Clause", "allowed"),
        ("MIT OR GPL-3.0-only", "allowed"),  # the licensee may choose the permissive one
        ("MIT AND GPL-3.0-only", "review"),  # both apply
        ("Apache-2.0 WITH LLVM-exception", "allowed"),
        ("LGPL-3.0-only", "review"),
        ("GPL-2.0-only", "review"),
        ("AGPL-3.0-only", "review"),
        ("Some-Unlisted-1.0", "review"),
        ("SSPL-1.0", "refused"),
        ("BUSL-1.1", "refused"),
        ("CC-BY-NC-4.0", "refused"),
        ("UNKNOWN", "refused"),
        ("", "refused"),
        ("MIT AND SSPL-1.0", "refused"),
    ],
)
def test_classes(expr: str, expected: str) -> None:
    assert lic.classify(expr, POLICY) == expected


@pytest.mark.parametrize(
    "raw,classifiers,expected",
    [
        ("MIT", [], "MIT"),
        ("MIT License", [], "MIT"),
        ("Apache License, Version 2.0", [], "Apache-2.0"),
        ("Apache-2.0 OR BSD-3-Clause", [], "Apache-2.0 OR BSD-3-Clause"),
        ("", ["License :: OSI Approved :: BSD License"], "BSD-3-Clause"),
        ("PSFL", [], "PSF-2.0"),
        ("UNKNOWN", [], "UNKNOWN"),
        ("", [], "UNKNOWN"),
        ("Proprietary, all rights reserved", [], "UNKNOWN"),
    ],
)
def test_names_are_normalised_and_the_unknown_stays_unknown(
    raw: str, classifiers: list, expected: str
) -> None:
    assert lic.to_spdx(raw, classifiers) == expected


# ---- L-3 the gate ----------------------------------------------------------------------------
def test_the_repository_passes_the_build_gate(inventory: list) -> None:
    problems = lic.check(inventory, POLICY)
    problems += lic.check_images(
        POLICY, lic.images_from_compose(), json.loads(lic.IMAGES.read_text())
    )
    assert problems == []


def test_a_refused_licence_fails_even_for_a_development_tool() -> None:
    assert lic.check([comp("evil", "SSPL-1.0")], POLICY)
    assert lic.check([comp("mystery", "UNKNOWN", "dev")], POLICY)


def test_a_review_licence_fails_unless_it_has_an_entry_and_dev_tools_are_exempt() -> None:
    assert lic.check([comp("gplthing", "GPL-3.0-only")], POLICY)
    assert lic.check([comp("gplthing", "GPL-3.0-only", "dev")], POLICY) == []
    policy = {**POLICY, "exceptions": [{"component": "gplthing", "approved_by": "", "reason": "x"}]}
    assert (
        lic.check([comp("gplthing", "GPL-3.0-only")], policy) == []
    )  # a proposal passes the build


def test_a_release_needs_every_decision_signed_and_a_current_review() -> None:
    today = date(2026, 11, 8)
    assert lic.check([], POLICY, release=True, today=today)  # the repository has unsigned proposals
    signed = json.loads(json.dumps(POLICY))
    for group in ("exceptions", "network_licences", "terms"):
        for e in signed[group]:
            e["approved_by"], e["approved_on"] = "Owner", "2026-11-08"
    signed["system_packages"]["approved_by"] = "Owner"
    signed["review"] = {"signed_by": "Owner", "date": "2026-11-08"}
    inv = lic.inventory()
    assert lic.check(inv, signed, release=True, today=today) == []
    stale = lic.check(inv, signed, release=True, today=date(2027, 6, 1))
    assert any("days old" in p for p in stale)
    unsigned = {**signed, "review": {"signed_by": "", "date": ""}}
    assert any(
        "signed review date" in p for p in lic.check(inv, unsigned, release=True, today=today)
    )


# ---- L-4, L-5 the manual review has an entry for every known case ----------------------------
def test_the_known_network_licence_and_the_terms_are_on_the_list() -> None:
    nets = " ".join(n["name"] for n in POLICY["network_licences"])
    assert "MinIO" in nets and "AGPL" in " ".join(n["licence"] for n in POLICY["network_licences"])
    terms = " ".join(t["name"] for t in POLICY["terms"])
    for needed in ("WhatsApp", "Tesseract", "Translation", "React Flow", "ClamAV", "Fonts"):
        assert needed in terms, needed
    assert all(t["note"].strip() for t in POLICY["terms"])
    assert all(n["proposal"].strip() for n in POLICY["network_licences"])


def test_the_object_store_dockerfile_still_says_unmodified() -> None:
    text = (ROOT / "deploy/objectstore/Dockerfile").read_text()
    assert (
        "AGPL" in text and "unmodified" in text and "patch" not in text.lower().replace("path", "")
    )


# ---- L-6 images ------------------------------------------------------------------------------
def test_an_image_without_a_record_or_with_a_refused_licence_fails() -> None:
    assert lic.check_images(POLICY, ["a"], None)
    assert lic.check_images(POLICY, ["a", "b"], [{"image": "a", "refused": [], "review": []}])
    assert lic.check_images(POLICY, ["a"], [{"image": "a", "refused": ["SSPL-1.0"], "review": []}])
    assert lic.check_images(POLICY, ["a"], [{"image": "a", "refused": [], "review": []}]) == []


def test_debian_and_alpine_licence_names_are_read() -> None:
    assert lic.split_ids("GPL-2+") == ["GPL-2.0-or-later"]
    assert lic.split_ids("LGPL-3+ or GPL-2+") == ["LGPL-3.0-or-later", "GPL-2.0-or-later"]
    assert lic.split_ids("Expat") == ["MIT"]
    assert lic.split_ids("MPL-2.0 AND MIT") == ["MPL-2.0", "MIT"]


# ---- L-7 notices -----------------------------------------------------------------------------
def test_the_notices_file_matches_what_ships_and_lists_the_attribution(inventory: list) -> None:
    on_disk = (ROOT / "web/src/notices.json").read_text()
    assert on_disk == lic.notices_json(inventory, POLICY), "run `scripts/licenses.py write`"
    data = json.loads(on_disk)
    names = {c["name"] for c in data["components"]}
    assert {c.name for c in inventory if c.scope == "shipped"} == names
    assert any(a["name"] == "React Flow" for a in data["attributions"])


def test_the_licences_document_is_generated_from_the_same_data(inventory: list) -> None:
    images = json.loads(lic.IMAGES.read_text())
    assert (ROOT / "docs/LICENSES.md").read_text() == lic.render_markdown(inventory, POLICY, images)


def test_the_settings_screen_shows_the_notices() -> None:
    settings = (ROOT / "web/src/pages/Settings.tsx").read_text()
    assert "<Notices />" in settings and (ROOT / "web/src/pages/Notices.tsx").exists()
