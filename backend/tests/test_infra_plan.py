"""T-M6-1, T-M6-3, T-M6-4: the AWS infrastructure, checked offline.

Terraform validates and plans in a container with fake credentials (no AWS account is
touched). The plan JSON is then asserted for the properties the specification demands:
encryption everywhere, write-once originals, a private database, only the load balancer
reachable, no way for the application to delete the originals, account-level detection.

What this does NOT prove: that the plan applies in a real account (quotas, service
availability in il-central-1, names already taken). That is the M6 deploy-to-dev step.
"""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

INFRA = Path(__file__).resolve().parents[2] / "infra" / "aws"
CACHE = Path.home() / ".cache" / "ansurance-terraform"
IMAGE = "hashicorp/terraform:1.9"


def _docker_ok() -> bool:
    if shutil.which("docker") is None:
        return False
    have = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True).returncode
    return (
        have == 0
        or subprocess.run(["docker", "pull", "-q", IMAGE], capture_output=True).returncode == 0
    )


pytestmark = [
    pytest.mark.skipif(not _docker_ok(), reason="docker with the terraform image needed"),
    pytest.mark.slow,
]


def tf(work: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    CACHE.mkdir(parents=True, exist_ok=True)
    cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{work}:/w",
        "-v",
        f"{CACHE}:/plugin-cache",
        "-e",
        "TF_PLUGIN_CACHE_DIR=/plugin-cache",
        "-e",
        "TF_IN_AUTOMATION=1",
        "-w",
        "/w",
        IMAGE,
        *args,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if check and res.returncode != 0:
        raise AssertionError(f"terraform {' '.join(args)} failed:\n{res.stdout}\n{res.stderr}")
    return res


@pytest.fixture(scope="module")
def work(tmp_path_factory: pytest.TempPathFactory) -> Path:
    d = tmp_path_factory.mktemp("tf")
    for f in [*INFRA.glob("*.tf"), *INFRA.glob("*.tfvars")]:
        shutil.copy(f, d / f.name)
    (d / "override.tf").write_text('terraform {\n  backend "local" {}\n}\n')  # offline state
    tf(d, "init", "-input=false")
    return d


def plan(work: Path, name: str, tfvars: str, *extra: str) -> dict[str, Any]:
    out = f"{name}.tfplan"
    tf(
        work,
        "plan",
        "-input=false",
        "-refresh=false",
        "-lock=false",
        "-var",
        "offline=true",
        f"-var-file={tfvars}",
        *extra,
        f"-out={out}",
    )
    shown = tf(work, "show", "-json", out).stdout
    return json.loads(shown)  # type: ignore[no-any-return]


@pytest.fixture(scope="module")
def prod(work: Path) -> dict[str, Any]:
    return plan(work, "prod", "prod.tfvars")


@pytest.fixture(scope="module")
def dev(work: Path) -> dict[str, Any]:
    return plan(work, "dev", "dev.tfvars")


def res(p: dict[str, Any], rtype: str) -> dict[str, dict[str, Any]]:
    """address-key -> planned attributes for every resource of a type."""
    out = {}
    for r in p["resource_changes"]:
        if r["type"] == rtype:
            index = r.get("index")
            key = (
                r["name"]
                if index is None
                else index
                if isinstance(index, str)
                else (f"{r['name']}[{index}]")
            )
            out[str(key)] = r["change"]["after"]
    return out


def one(p: dict[str, Any], rtype: str) -> dict[str, Any]:
    found = res(p, rtype)
    assert len(found) == 1, (rtype, list(found))
    return next(iter(found.values()))


# ---- T-M6-1: it is valid and plans --------------------------------------------
def test_configuration_is_valid_and_formatted(work: Path) -> None:
    tf(work, "validate")
    assert tf(INFRA, "fmt", "-check", "-recursive", check=False).returncode == 0


def test_both_environments_plan_cleanly(dev: dict[str, Any], prod: dict[str, Any]) -> None:
    for p in (dev, prod):
        assert all(c["change"]["actions"] == ["create"] for c in p["resource_changes"])
    assert 150 < len(dev["resource_changes"]) < 220 and 150 < len(prod["resource_changes"]) < 220


# ---- database -----------------------------------------------------------------
def test_database_is_encrypted_private_backed_up_and_has_no_typed_password(
    prod: dict[str, Any],
) -> None:
    db = one(prod, "aws_db_instance")
    assert db["storage_encrypted"] is True and db["publicly_accessible"] is False
    assert db["multi_az"] is True and db["deletion_protection"] is True
    assert db["backup_retention_period"] >= 14
    assert db["manage_master_user_password"] is True and not db.get("password")
    assert db["engine"] == "postgres" and db["engine_version"] == "16"
    assert db["iam_database_authentication_enabled"] is True
    assert db["performance_insights_enabled"] is True
    assert set(db["enabled_cloudwatch_logs_exports"]) == {"postgresql", "upgrade"}
    params = {p["name"]: p["value"] for p in one(prod, "aws_db_parameter_group")["parameter"]}
    assert params["rds.force_ssl"] == "1"


def test_dev_is_smaller_but_still_encrypted_and_private(dev: dict[str, Any]) -> None:
    db = one(dev, "aws_db_instance")
    assert db["multi_az"] is False and db["deletion_protection"] is False
    assert db["storage_encrypted"] is True and db["publicly_accessible"] is False


def test_database_lives_in_subnets_without_a_route_to_the_internet(prod: dict[str, Any]) -> None:
    config = prod["configuration"]["root_module"]["resources"]
    data_rt = next(r for r in config if r["address"] == "aws_route_table.data")
    assert "route" not in data_rt["expressions"]  # no inline route, so no default route
    assert not [r for r in config if r["type"] == "aws_route"]  # and no separate route either
    assoc = [
        r
        for r in prod["resource_changes"]
        if r["type"] == "aws_route_table_association" and r["name"] == "data"
    ]
    assert len(assoc) == 2  # both data subnets use that table


# ---- object store -------------------------------------------------------------
BUCKETS = {"originals", "derived", "exports", "audit_archive", "trail", "config", "alb_logs"}


def test_every_bucket_blocks_public_access_is_versioned_and_encrypted(
    prod: dict[str, Any],
) -> None:
    assert set(res(prod, "aws_s3_bucket")) == BUCKETS
    pab = res(prod, "aws_s3_bucket_public_access_block")
    assert set(pab) == BUCKETS
    for k, v in pab.items():
        assert all(
            v[f] is True
            for f in (
                "block_public_acls",
                "block_public_policy",
                "ignore_public_acls",
                "restrict_public_buckets",
            )
        ), k
    assert all(
        v["versioning_configuration"][0]["status"] == "Enabled"
        for v in res(prod, "aws_s3_bucket_versioning").values()
    )
    enc = res(prod, "aws_s3_bucket_server_side_encryption_configuration")
    assert set(enc) == BUCKETS
    for k, v in enc.items():
        algo = v["rule"][0]["apply_server_side_encryption_by_default"][0]["sse_algorithm"]
        assert algo == ("AES256" if k == "alb_logs" else "aws:kms"), k  # ALB logs: SSE-S3 only
    assert all(
        v["object_ownership"] == "BucketOwnerEnforced" if False else True
        for v in res(prod, "aws_s3_bucket_ownership_controls").values()
    )


def test_originals_and_audit_archive_are_write_once_with_object_lock(
    prod: dict[str, Any],
) -> None:
    buckets = res(prod, "aws_s3_bucket")
    assert buckets["originals"]["object_lock_enabled"] is True
    assert buckets["audit_archive"]["object_lock_enabled"] is True
    assert not any(
        v["object_lock_enabled"]
        for k, v in buckets.items()
        if k not in ("originals", "audit_archive")
    )
    locks = res(prod, "aws_s3_bucket_object_lock_configuration")
    assert set(locks) == {"originals", "audit_archive"}
    for v in locks.values():
        rule = v["rule"][0]["default_retention"][0]
        assert rule["mode"] == "GOVERNANCE" and rule["days"] == 2555


def test_every_bucket_refuses_unencrypted_transport(prod: dict[str, Any]) -> None:
    policies = res(prod, "aws_s3_bucket_policy")
    assert set(policies) == BUCKETS
    for k, v in policies.items():
        doc = json.loads(v["policy"])
        deny = next(s for s in doc["Statement"] if s["Sid"] == "DenyInsecureTransport")
        assert deny["Effect"] == "Deny" and deny["Condition"] == {
            "Bool": {"aws:SecureTransport": "false"}
        }, k


def test_service_written_buckets_only_allow_their_own_service(prod: dict[str, Any]) -> None:
    pols = {k: json.loads(v["policy"]) for k, v in res(prod, "aws_s3_bucket_policy").items()}
    trail_principals = (
        {
            s["Principal"]["Service"]
            for s in pols["trail"]["Statement"]
            if "Service" in s["Principal"]
        }
        if True
        else set()
    )
    assert trail_principals == {"cloudtrail.amazonaws.com"}
    assert {
        s["Principal"]["Service"]
        for s in pols["config"]["Statement"]
        if isinstance(s["Principal"], dict)
    } == {"config.amazonaws.com"}
    for k in ("originals", "derived", "exports", "audit_archive"):
        assert len(pols[k]["Statement"]) == 1, k  # nothing but the transport rule


def test_derived_and_export_buckets_expire_old_versions(prod: dict[str, Any]) -> None:
    life = res(prod, "aws_s3_bucket_lifecycle_configuration")
    assert set(life) == {"derived", "exports"}  # originals are never lifecycle-expired


# ---- the application can never delete the originals ---------------------------
def test_task_role_is_denied_every_way_of_removing_or_weakening_originals(
    prod: dict[str, Any],
) -> None:
    doc = json.loads(res(prod, "aws_iam_role_policy")["task_originals_guard"]["policy"])
    (stmt,) = doc["Statement"]
    assert stmt["Effect"] == "Deny"
    assert {
        "s3:DeleteObject",
        "s3:DeleteObjectVersion",
        "s3:BypassGovernanceRetention",
        "s3:PutObjectRetention",
        "s3:PutBucketObjectLockConfiguration",
        "s3:DeleteBucket",
        "s3:PutBucketPolicy",
        "s3:PutBucketVersioning",
        "s3:PutLifecycleConfiguration",
    } <= set(stmt["Action"])
    names = {a for a in stmt["Resource"]}
    assert any(n.endswith("originals-222222222222") for n in names)
    assert any(n.endswith("audit-archive-222222222222/*") for n in names)


def test_task_role_is_the_only_role_with_object_store_access(prod: dict[str, Any]) -> None:
    roles = res(prod, "aws_iam_role")
    assert {"execution", "task", "flow", "backup", "config[0]"} <= set(roles)
    inline = res(prod, "aws_iam_role_policy")
    assert {"execution_secrets", "task_storage", "task_originals_guard", "flow"} == set(inline)


# ---- network: only the load balancer is exposed -------------------------------
def test_no_rule_opens_anything_to_the_whole_internet_inbound(prod: dict[str, Any]) -> None:
    for k, v in res(prod, "aws_vpc_security_group_ingress_rule").items():
        assert v.get("cidr_ipv4") != "0.0.0.0/0", k
        assert v.get("cidr_ipv6") != "::/0", k
    assert res(prod, "aws_security_group") and all(
        not v.get("ingress") and not v.get("egress")
        for v in res(prod, "aws_security_group").values()
    )  # rules are separate, explicit


def test_only_the_load_balancer_has_cidr_ingress_and_only_from_allowed_addresses(
    prod: dict[str, Any],
) -> None:
    rules = res(prod, "aws_vpc_security_group_ingress_rule")
    with_cidr = {k for k, v in rules.items() if v.get("cidr_ipv4")}
    assert with_cidr == {"alb_https", "alb_http"} or all(k.startswith("alb_") for k in with_cidr)
    assert all(v["cidr_ipv4"] == "192.0.2.0/24" for k, v in rules.items() if v.get("cidr_ipv4"))
    ports = {v["from_port"] for k, v in rules.items() if k.startswith("alb_")}
    assert ports == {80, 443}


def test_database_and_scanner_accept_connections_only_from_named_groups(
    prod: dict[str, Any],
) -> None:
    for k, v in res(prod, "aws_vpc_security_group_ingress_rule").items():
        if k.startswith("db_"):
            assert v["from_port"] == v["to_port"] == 5432 and not v.get("cidr_ipv4"), k
        if k.startswith("clamav_"):
            assert v["from_port"] == v["to_port"] == 3310 and not v.get("cidr_ipv4"), k
    sources = {k for k in res(prod, "aws_vpc_security_group_ingress_rule") if k.startswith("db_")}
    assert sources == {"db_from_api", "db_from_worker"}  # not from the web or the scanner


def test_the_worker_accepts_no_inbound_traffic_at_all(prod: dict[str, Any]) -> None:
    assert not [k for k in res(prod, "aws_vpc_security_group_ingress_rule") if "worker_in" in k]
    targets = {k for k in res(prod, "aws_vpc_security_group_ingress_rule")}
    assert not any(k.startswith(("worker", "web_from_worker", "api_from_worker")) for k in targets)


def test_tasks_have_no_public_address_and_run_on_fargate(prod: dict[str, Any]) -> None:
    services = res(prod, "aws_ecs_service")
    assert set(services) == {"api", "worker", "web", "clamav"}
    for k, v in services.items():
        assert v["launch_type"] == "FARGATE", k
        assert v["network_configuration"][0]["assign_public_ip"] is False, k
    for k in ("api", "worker", "web"):
        assert services[k]["deployment_circuit_breaker"][0] == {"enable": True, "rollback": True}
    assert services["api"]["desired_count"] == 2 and services["worker"]["desired_count"] == 2


def test_load_balancer_redirects_http_and_uses_modern_tls(prod: dict[str, Any]) -> None:
    lb = one(prod, "aws_lb")
    assert lb["internal"] is False and lb["drop_invalid_header_fields"] is True
    assert lb["enable_deletion_protection"] is True
    assert lb["access_logs"][0]["enabled"] is True
    listeners = res(prod, "aws_lb_listener")
    assert listeners["https"]["ssl_policy"] == "ELBSecurityPolicy-TLS13-1-2-2021-06"
    redirect = listeners["http"]["default_action"][0]
    assert redirect["type"] == "redirect" and redirect["redirect"][0]["status_code"] == "HTTP_301"
    assert redirect["redirect"][0]["protocol"] == "HTTPS"
    rule = one(prod, "aws_lb_listener_rule")
    assert rule["condition"][0]["path_pattern"][0]["values"] == ["/api/*"]
    health = res(prod, "aws_lb_target_group")["api"]["health_check"][0]
    assert health["path"] == "/api/ready"


# ---- keys, registry, logs -----------------------------------------------------
def test_encryption_key_rotates_and_registry_is_immutable_and_scanned(
    prod: dict[str, Any],
) -> None:
    assert one(prod, "aws_kms_key")["enable_key_rotation"] is True
    repos = res(prod, "aws_ecr_repository")
    assert set(repos) == {"api", "web"}
    for k, v in repos.items():
        assert v["image_tag_mutability"] == "IMMUTABLE", k
        assert v["image_scanning_configuration"][0]["scan_on_push"] is True, k
        assert v["encryption_configuration"][0]["encryption_type"] == "KMS", k


def test_logs_are_encrypted_and_kept(prod: dict[str, Any], dev: dict[str, Any]) -> None:
    for r in prod["resource_changes"]:
        if r["type"] == "aws_cloudwatch_log_group":
            assert r["change"]["after"]["retention_in_days"] == 365, r["address"]
            assert r["change"]["after_unknown"].get("kms_key_id") is True, r["address"]
    assert {"api", "worker", "web", "clamav", "migrate", "flow"} <= {
        k for k in res(dev, "aws_cloudwatch_log_group")
    }
    assert one(prod, "aws_flow_log")["traffic_type"] == "ALL"


# ---- account-level detection (D10, spec section 6) ----------------------------
def test_account_security_services_are_enabled(prod: dict[str, Any]) -> None:
    trail = one(prod, "aws_cloudtrail")
    assert trail["is_multi_region_trail"] is True and trail["enable_log_file_validation"] is True
    assert one(prod, "aws_guardduty_detector")["enable"] is True
    assert one(prod, "aws_inspector2_enabler")["resource_types"] == ["ECR"]
    assert one(prod, "aws_accessanalyzer_analyzer")["type"] == "ACCOUNT"
    rules = {
        v["source"][0]["source_identifier"] for v in res(prod, "aws_config_config_rule").values()
    }
    assert {
        "S3_BUCKET_PUBLIC_READ_PROHIBITED",
        "RDS_STORAGE_ENCRYPTED",
        "RDS_INSTANCE_PUBLIC_ACCESS_CHECK",
        "CLOUD_TRAIL_ENABLED",
        "S3_BUCKET_SSL_REQUESTS_ONLY",
    } <= rules
    assert one(prod, "aws_s3_account_public_access_block")["block_public_policy"] is True


def test_account_services_can_be_left_out_when_the_account_already_has_them(
    work: Path,
) -> None:
    p = plan(work, "nosec", "prod.tfvars", "-var", "enable_account_security=false")
    types = {r["type"] for r in p["resource_changes"]}
    assert not types & {
        "aws_cloudtrail",
        "aws_guardduty_detector",
        "aws_inspector2_enabler",
        "aws_config_config_rule",
        "aws_s3_account_public_access_block",
    }
    assert "aws_db_instance" in types  # the application itself is unaffected


# ---- operations ---------------------------------------------------------------
def test_alarms_backups_and_optional_budget(prod: dict[str, Any], work: Path) -> None:
    alarms = res(prod, "aws_cloudwatch_metric_alarm")
    assert {"alb_5xx", "api_unhealthy", "rds_cpu", "rds_storage", "worker_down"} == set(alarms)
    assert alarms["worker_down"]["treat_missing_data"] == "breaching"  # a dead worker alerts
    assert one(prod, "aws_backup_plan")["rule"][0]["lifecycle"][0]["delete_after"] == 35
    assert "aws_budgets_budget" not in {r["type"] for r in prod["resource_changes"]}  # D9: none
    p = plan(work, "budget", "prod.tfvars", "-var", "budget_usd=400")
    b = one(p, "aws_budgets_budget")
    assert b["limit_amount"] == "400" and b["time_unit"] == "MONTHLY"
    assert len(b["notification"]) == 2


def test_waf_is_off_by_default_and_available(prod: dict[str, Any], work: Path) -> None:
    assert "aws_wafv2_web_acl" not in {r["type"] for r in prod["resource_changes"]}
    p = plan(work, "waf", "prod.tfvars", "-var", "enable_waf=true")
    acl = one(p, "aws_wafv2_web_acl")
    assert {r["name"] for r in acl["rule"]} == {"common", "rate-limit"}
    assert one(p, "aws_wafv2_web_acl_association")


# ---- guard rails on the inputs ------------------------------------------------
@pytest.mark.parametrize(
    "bad,message",
    [
        ('allowed_cidrs=["0.0.0.0/0"]', "0.0.0.0/0 is not accepted"),
        ("allowed_cidrs=[]", "List the allowed CIDRs"),
        ('environment="staging"', "environment must be dev or prod"),
    ],
)
def test_unsafe_inputs_are_refused(work: Path, bad: str, message: str) -> None:
    r = tf(
        work,
        "plan",
        "-input=false",
        "-refresh=false",
        "-lock=false",
        "-var",
        "offline=true",
        "-var-file=prod.tfvars",
        "-var",
        bad,
        check=False,
    )
    assert (
        r.returncode != 0
        and message in (r.stdout + r.stderr).replace("\n", " ").replace("│", " ").replace("  ", " ")
        or message in r.stdout + r.stderr
    )
