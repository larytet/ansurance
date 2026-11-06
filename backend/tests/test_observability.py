"""T-M7-5: the alert rules are valid and fire (and stay quiet) on the right series, and the
dashboard only uses metrics the application really exports."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

OBS = Path(__file__).resolve().parents[2] / "deploy" / "observability"
IMAGE = "prom/prometheus:v2.55.1"


def _have_image() -> bool:
    if shutil.which("docker") is None:
        return False
    if subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True).returncode == 0:
        return True
    return subprocess.run(["docker", "pull", "-q", IMAGE], capture_output=True).returncode == 0


def promtool(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "promtool",
            "-v",
            f"{OBS}:/o:ro",
            "-w",
            "/o",
            IMAGE,
            *args,
        ],
        capture_output=True,
        text=True,
    )


needs_docker = pytest.mark.skipif(not _have_image(), reason="the prometheus image is needed")


@pytest.mark.slow
@needs_docker
def test_rules_are_valid() -> None:
    r = promtool("check", "rules", "alerts.yml")
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.slow
@needs_docker
def test_rules_fire_and_stay_quiet_on_synthetic_series() -> None:
    r = promtool("test", "rules", "alerts_test.yml")
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.slow
@needs_docker
def test_prometheus_configuration_is_valid() -> None:
    r = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "promtool",
            "-v",
            f"{OBS}/prometheus.yml:/etc/prometheus/prometheus.yml:ro",
            "-v",
            f"{OBS}/alerts.yml:/etc/prometheus/alerts.yml:ro",
            IMAGE,
            "check",
            "config",
            "/etc/prometheus/prometheus.yml",
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr


def exported_metric_names() -> set[str]:
    src = (Path(__file__).resolve().parents[1] / "app" / "metrics.py").read_text()
    names = set(re.findall(r'"(ansurance_[a-z0-9_]+)"', src))
    # histograms and counters expose suffixed series
    return names | {f"{n}_bucket" for n in names} | {f"{n}_count" for n in names}


def used(expr: str) -> set[str]:
    return set(re.findall(r"\bansurance_[a-z0-9_]+", expr))


def test_every_alert_and_panel_uses_a_metric_the_application_exports() -> None:
    known = exported_metric_names()
    rules = yaml.safe_load((OBS / "alerts.yml").read_text())["groups"][0]["rules"]
    assert len(rules) >= 12
    for rule in rules:
        missing = {m for m in used(rule["expr"]) if m not in known}
        assert not missing, (rule["alert"], missing)
    dash = json.loads((OBS / "dashboard.json").read_text())
    for panel in dash["panels"]:
        for target in panel["targets"]:
            missing = {m for m in used(target["expr"]) if m not in known}
            assert not missing, (panel["title"], missing)


def test_the_alerts_the_specification_asks_for_exist() -> None:
    rules = {
        r["alert"] for r in yaml.safe_load((OBS / "alerts.yml").read_text())["groups"][0]["rules"]
    }
    # DESIGN section 13: queue age, failing audit verify, old backup, plus the basics
    assert {
        "JobQueueStuck",
        "AuditVerifyFailed",
        "BackupStale",
        "RestoreTestFailed",
        "ApiDown",
        "DatabaseDown",
        "HighErrorRate",
        "ReadAuditDropping",
    } <= rules


def test_every_alert_has_a_severity_and_a_summary() -> None:
    for r in yaml.safe_load((OBS / "alerts.yml").read_text())["groups"][0]["rules"]:
        assert r["labels"]["severity"] in ("warning", "critical"), r["alert"]
        assert len(r["annotations"]["summary"]) > 20, r["alert"]
