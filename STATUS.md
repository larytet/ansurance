# Status

Updated: 2026-11-06

| Step | State |
|---|---|
| 1. Platform requirements spec (`docs/PLATFORM-REQUIREMENTS.md`, v0.2) | Done |
| 2. Requirements brief and owner decisions D1..D10 | Done (decisions log below) |
| 3. Design spec (`docs/DESIGN.md`, v0.1) | Approved by owner 2026-11-06 |
| 4. Test plan (`docs/TESTPLAN.md`) | Written (v0.1); results table updated per milestone |
| 5. Milestones M0..M10 | M0..M5 done; M6 built and verified offline (real AWS deployment pending); M7 done; M8 done (fakes only); M9 done (fake e-signature provider); M10 next. Rule: commit and push after every completed milestone (owner instruction 2026-11-06) |

## Decisions log (from the requirements brief)

| # | Decision | Answer | Date |
|---|---|---|---|
| D1 | Storage | Postgres for tables; S3-API objects for large binaries (scans, PDFs); Parquet for tabular archives and exports | 2026-11-06 |
| D2 | Back-end language | Python | 2026-11-06 |
| D3 | Identity | Built-in login in the API (argon2id, TOTP, lockout, recovery codes, revocable sessions, audited); no Keycloak. Updated same day: built-in login AND SSO (OIDC client in the API: Microsoft 365 and Google Workspace; local login stays as fallback and for agencies without SSO) | 2026-11-06 |
| D4 | AWS region | Configurable parameter, default il-central-1; final choice at M6 after service availability check | 2026-11-06 |
| D5 | Job queue | Inside Postgres (SKIP LOCKED), no broker | 2026-11-06 |
| D6 | OCR | Local Tesseract default behind an OCR adapter; cloud engines optional later (need residency approval) | 2026-11-06 |
| D7 | Front end | TypeScript SPA (React + Vite). The mock (`index.html`) stays as the reference and is maintained while screens are ported one by one | 2026-11-06 |
| D8 | Retention and volume | ~5,000 clients, ~100 documents/day at peak (owner). Retention default 7 years after policy or claim end, configurable per document type, counsel to confirm | 2026-11-06 |
| D9 | AWS budget | No fixed ceiling; monthly cost report plus a budget alert at a threshold the owner sets later (AWS Budgets, SNS, e-mail). Estimate about USD 200-350 a month for prod, 60-120 for dev | 2026-11-06 |
| D11 | On-prem object store | MinIO built from source in our own Dockerfile (owner instruction), pinned tag; M2 spike confirms | 2026-11-06 |
| D10 | Security testing | No mandatory external pen test. A set of scripted penetration tests is part of the test plan (authz bypass/IDOR, injection, brute force and lockout, session and 2FA abuse, SSO callback abuse, upload abuse, signed-URL abuse, audit tamper). Network assumptions: on-prem sits behind a VPN (no public exposure); AWS firewall is defined by security groups (ALB only public entry; WAF optional). External test stays a recommended option before go-live | 2026-11-06 |

## Milestones

| # | State | Tests | Notes |
|---|---|---|---|
| M0 Foundation | Done 2026-11-06 | 16 backend + 2 web + smoke, all pass | `make up` healthy on a clean checkout; MinIO built from source |
| M1 Identity, core data, audit | Done 2026-11-06 | 106 backend (incl. 12 SSO, 42 clients/authz/audit) + e2e on live stack | Built-in login (argon2id, TOTP, recovery codes, lockout, IP limit, revocable sessions, CSRF), SSO (OIDC + PKCE, no auto-provisioning), roles and capabilities, clients with Luhn ID validation and multi-script search, policies, family links, marketing view, hash-chained audit with DB triggers, CLI to bootstrap the first manager, TLS at the proxy. Web login screen comes with the first ported screens |
| M2 Documents and blob store | Done 2026-11-06 | 155 backend, e2e on live stack | S3 BlobStore (MinIO built from source, Object Lock, no-delete app user), content-addressed write-once originals, dedupe, ClamAV scan (fail closed, quarantine), content sniffing and size limits, restricted documents, retention and legal hold, audited downloads with integrity check, reconcile command, DB triggers refusing blob delete or rewrite |
| M3 Inbox, consent, back-office desk | Done 2026-11-06 | 196 backend, e2e on live stack | Inbox per role (urgency rising with deadlines, reassign needs a note, dismiss needs a reason, history is immutable, manager read-only view, keyset pagination, 10,000 items under 1 s), append-only consent registry with latest-wins state and one-minute opt-out rule (late ones flagged to the manager), the consent gate on the single send path (marketing needs opt-in, opt-out blocks all but legal notices), Desk work areas, F11 request-ID and register, marketing projection with consent |
| M4 Process engine | Done 2026-11-06 | 225 backend, e2e on live stack | Definitions as validated, versioned data (acyclic, roles, four-eyes option), the 18 processes seeded from the mock (72 stages, 221 checks), locked and parallel stages, checks, role-restricted finalize, immutable final stages (DB trigger), instances pinned to their version, My work, banner data per client, cancel, Mermaid diagrams generated and identical to the stored files; mock bug in Hebrew headings fixed |
| M5 Intake pipeline | Done 2026-11-06 | 314 backend, e2e on live stack | Postgres job queue and worker container, fax drop folder, .eml ingest, virus scan (fail closed), page rendering, de-skew, crop, de-noise, split on blank pages, real Tesseract with a second Latin pass, rules classifier, ID and policy extraction, evidence-based attribution (never name-only, sender only breaks ties), routing to filed, review (back-office Inbox) or unassigned, wrong-client correction with misfile metric, feedback records. Synthetic accuracy 96.7% auto-filed, 0 misfiled; real scans still to be measured |
| M6 Cloud deployment (AWS) | Built, verified offline 2026-11-06; **deployment to a real account not done** | 27 infra plan tests, conformance on compose | Terraform (network, KMS, RDS, S3 with Object Lock, IAM guard, ECS Fargate services, ALB, WAF option, CloudTrail, GuardDuty, Inspector, Config, alarms, budget, backup), deploy-dev workflow, conformance script, settings from parts. Needs from the owner: AWS account and OIDC role, ACM certificate, allowed addresses, region availability check (D4) |
| M7 Operations and backup | Done 2026-11-06 | 17 audit archive + 4 daily ops + 8 metrics + 6 observability + 5 disaster recovery + 6 point-in-time recovery + 5 upgrade/rollback, all on the real stack | Encrypted backups (dump, base backup, 15-minute incremental originals), continuous WAL archiving, restore.sh (full and point-in-time), monthly self-test, audit sealing to Parquet with verification across hot and archive, nightly audit and backup checks reaching the manager, Prometheus/Grafana profile with 13 alert rules, make upgrade with backup, health gate and automatic rollback, OPERATIONS.md |
| M8 Messaging and integrations | Done 2026-11-06 (against fakes) | WhatsApp (Meta Cloud, signed webhook, consent gate at queue and delivery), mailbox ingestion (IMAP, Message-ID dedupe), agency-system sync (existing system wins for policy data, user-entered contact data kept), Excel join and export; every external call recorded in `adapter_call` | No real provider, mailbox or agency system was reachable: everything is tested against fake adapters. Meta's webhook must be reachable from Meta's addresses, so on AWS the `allowed_cidrs` allow-list needs a path-specific exception for `/api/webhooks/whatsapp` (signature-checked) |
| M9 Flows F1 to F5 and e-signature | Done 2026-11-07 (fake provider) | KYC and activation gate with manager approval for flagged cases (F1), advice, application and issuance with a policy checklist (F2), renewal queue T-60/30/10, required outcomes, leaks list, daily items (F3), claims with owner, timeline, checklist and late-insurer reminders (F4), the three data-lookup authorizations with expiry warning and the lookup gate (F5), e-signature requests with a signed webhook | No Comsign or DocuSign adapter: the provider contract is in `app/adapters/esign.py` with a fake only; outside the demo profile e-signature answers 503 until an adapter is wired. The Mislaka and Har HaBituach request itself is only gated and audited, not sent. Authorization validity (12 months) is a setting to verify per form |
| M10 | Not started | | |

Owner decision 2026-11-06: audit of reads is asynchronous and batched; losing unflushed read entries on a crash is accepted (implemented, tested).

## Web front end (decision D7)

Slice 1 done 2026-11-06: sign in, shell (role-based tabs, he/en with RTL, light/dark), Inbox, Clients list; 13 unit tests and a Playwright browser test against the live stack. Screen-by-screen tracker: docs/MOCK-PORT.md. Not started: client file, Today, Desk, intake review, processes, campaigns, manager overview, and everything that needs M8 and later.

## Predictable deployment (owner decision 2026-11-06)

Every external input is pinned and `make pins` / CI fail on anything that floats: images by digest, MinIO and mc by commit, Debian packages from a dated snapshot, Alpine and pip packages at exact versions, lock files used strictly, Terraform exact version plus a four-platform provider lock, CI actions by commit SHA, exact Node/uv/Terraform versions, fixed runner, AWS deploys name a git SHA. Requirement: PLATFORM-REQUIREMENTS principle 8; procedure: OPERATIONS.md "Updating pinned versions".
