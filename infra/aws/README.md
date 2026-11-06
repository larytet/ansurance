# AWS deployment (Terraform)

Single-tenant deployment of ansurance on AWS. The same container images run on-prem
(`deploy/compose`) and here. Design: [docs/DESIGN.md](../../docs/DESIGN.md) section 12;
requirements: [docs/PLATFORM-REQUIREMENTS.md](../../docs/PLATFORM-REQUIREMENTS.md) section 5.2.

## What it creates

| Concern | Resources |
|---|---|
| Network | VPC, public subnets (load balancer, NAT), private subnets (tasks), data subnets with no internet route (RDS), S3 gateway endpoint, VPC flow logs |
| Firewall (decision D10) | Security groups only: the load balancer accepts HTTPS from `allowed_cidrs` and nothing else is reachable from outside; the database accepts only the api and worker; the scanner only the api and worker; the worker accepts nothing |
| Data | PostgreSQL 16 on RDS (encrypted, private, password generated and kept in Secrets Manager, optional Multi-AZ, daily backups plus AWS Backup), customer-managed KMS key with rotation |
| Files | S3 buckets: `originals` and `audit-archive` with **Object Lock** (governance, 2555 days) and versioning, `derived`, `exports`, plus buckets for CloudTrail, Config and load balancer logs. Public access blocked, TLS required, KMS encryption |
| Compute | ECS on Fargate: `api`, `worker` (OCR and the intake pipeline), `web`, `clamav` (found through Cloud Map); a `migrate` task run before each release |
| Edge | Application load balancer, HTTPS with TLS 1.3/1.2 policy, HTTP redirects, `/api/*` to the API, optional WAF |
| Detection (spec section 6) | CloudTrail (multi-region, log validation), GuardDuty, Inspector (ECR), Access Analyzer, AWS Config baseline rules |
| Operations | CloudWatch alarms (5xx, unhealthy targets, RDS CPU and storage, **worker down**), SNS alerts, optional monthly budget alert (decision D9) |

The application role can read and write the object store but is explicitly **denied**
deleting, version-deleting, bypassing retention or changing the policy of `originals` and
`audit-archive`. A plain delete would only hide an object behind a delete marker, which is
why the permission is withheld altogether, as on-prem.

## One-time setup

1. Pick the region. `il-central-1` (Tel Aviv) is the default for data residency; confirm
   that RDS, Fargate, ECR, GuardDuty, Inspector, Config, Backup and Budgets are available
   there before the first apply (decision D4). Change `region` and `azs` otherwise.
2. Create a state bucket (versioned, encrypted) and a lock table, then copy
   `backend-dev.hcl.example` to `backend-dev.hcl`.
3. Request an ACM certificate for the host name and put its ARN in the variables.
4. Set `allowed_cidrs` to the office or VPN egress addresses (the plan refuses `0.0.0.0/0`).
5. In GitHub, create the `dev` environment with the variables used by
   `.github/workflows/deploy-dev.yml` and an IAM role it can assume through OIDC.
6. Create the first manager and the conformance account with
   `aws ecs run-task` using the `migrate` task definition and the command
   `python -m app.cli create-user ...` (the output holds the TOTP secret, shown once).

## Everyday use

```sh
terraform init -backend-config=backend-dev.hcl
terraform plan  -var-file=dev.tfvars -var account_id=... -var certificate_arn=... \
                -var alarm_email=... -var 'allowed_cidrs=["203.0.113.0/24"]'
```

CI deploys with `.github/workflows/deploy-dev.yml` (manual): plan, push images tagged with
the git SHA, apply, run the migration task, wait for stable services, run the conformance
script against the new deployment. Production changes only through CI after `dev` is green.

## Tests without an AWS account

`backend/tests/test_infra_plan.py` validates the configuration and plans it offline (fake
credentials, a container, no API calls), then asserts the security properties on the plan.
Run it with `make test-slow`. It cannot prove that the plan applies in a real account;
that is what the deploy-to-dev step is for.

## Cost (estimate, to be refined after the first month)

Roughly USD 200 to 350 a month for `prod` (Multi-AZ database, NAT per zone, Fargate
tasks, load balancer, logs) and 60 to 120 for `dev`. Set `budget_usd` to turn on a
monthly alert. The WAF is off by default because the system is intended to sit behind a
VPN or an address allow-list; enable it (`enable_waf = true`) if that stops being true.
