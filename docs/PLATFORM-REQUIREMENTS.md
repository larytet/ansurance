# ansurance - Platform and Deployment Requirements

Companion to [REQUIREMENTS.md](REQUIREMENTS.md) (product behaviour, taken from the clickable mock). This document covers how the system is built, stored, deployed and delivered.

- Status: v0.2 (2026-11-06). Owner decisions D1 to D10 are recorded in `STATUS.md` and applied below; the technical design is in `DESIGN.md`
- Items marked **[decide]** are open decisions for the owner. Items marked **[verify]** need confirmation against a primary source.

## 1. Scope

Turn the mock (`index.html`, no backend, fictional data) into a working system for one Israeli agency (9 users, four roles) that runs in two deployment profiles from the same code and the same container images:

| Profile | Target | Who operates it |
|---|---|---|
| **onprem** | One Linux host (or VM) at the agency or a hosting partner, `docker compose` | Agency or a service partner; no dedicated IT person (REQUIREMENTS section 7) |
| **cloud** | AWS, single region, single tenant per deployment | Service partner |

Non-goals for the platform v1: multi-tenant SaaS, Kubernetes, active-active multi-region, mobile native apps.

## 2. Principles

1. **One codebase, two profiles.** Differences live in configuration and in thin adapters (blob store, secrets, mail, identity, backup), never in business code.
2. **Postgres is the system of record** for everything structured. Binary files never go in Postgres.
3. **Originals are immutable.** Every scan, PDF, fax, email and WhatsApp media is stored once, content-addressed by SHA-256, never overwritten or deleted by application code (REQUIREMENTS 4.3).
4. **Everything is audited** through the append-only hash-chained log (REQUIREMENTS section 7); an action without an audit entry is a defect. Exception by decision (2026-11-06): audit entries for plain reads are written asynchronously in batches, and losing the unflushed tail on a crash is accepted. Writes, security events, exports and downloads stay synchronous.
5. **Adapters with fakes.** Every external system (OCR, WhatsApp, mail, fax, clearing house, e-sign, translation) is behind an interface with a fake used by the tests and by the demo profile (REQUIREMENTS section 9).
6. **Boring and small.** Fewest moving parts: no message broker, no search cluster, no cache in v1 unless a measured need appears.
7. **Israeli data stays in Israel** unless the owner approves otherwise (section 8).

## 3. Functional scope by area

All behaviour of the mock is in scope; the platform must provide:

| Area | Platform requirement |
|---|---|
| Identity and roles | Users, four roles plus named capabilities, a user can hold several roles; agent-of-record ownership and team shares; marketing role sees the minimized projection only |
| Clients and policies | Client, family links, 9-digit ID with Luhn validation, `external_ids`, preferred language, names with transliteration, search by name in any script or any ID digits |
| Documents | Upload and ingestion, original plus derived artifacts (OCR text, pages, thumbnails), type, restricted flag, retention and legal hold |
| Intake pipeline (F6) | Capture, digitize, classify, extract, attribute, route by confidence, Review and Unassigned queues, wrong-client correction, feedback storage |
| Inbox | One per role, reassign with note, never silently drop, sorting by urgency, 10,000 items under 1 s |
| Processes | Definitions as data (stages, owner role, dependencies, checks), instances pinned to a definition version, maker-checker finalization, My work, banner data, Mermaid source export |
| Consent registry | Per channel opt-in and opt-out with source and time; opt-out applied within one minute; consulted by every outbound send |
| Messaging | WhatsApp, email, fax, SMS through adapters; every external call logged |
| Translation and calls | Adapter for speech and text translation; transcript and subtitle storage (audio is a large binary in the blob store) |
| Excel integration | Import statements, export reports, join on partial keys with a join-explanation column |
| Audit and trust | Append-only hash chain, verify-chain action, export |
| Admin | Users and roles, settings, backup status, adapter health |

## 4. Data architecture

### 4.1 Where each kind of data lives

| Data | Store | Notes |
|---|---|---|
| Clients, policies, claims, tasks, Inbox items, consents, processes and stage state, users, roles, settings, templates, campaign definitions | **PostgreSQL** | Relational schema, migrations under version control, foreign keys enforced |
| Audit log | PostgreSQL (hot, hash-chained) and **Parquet** in the object store (cold, sealed monthly) | Chain head is re-anchored in each sealed file; verification reads both |
| Scans, PDFs, faxes (TIFF), photos, email `.eml` and attachments, WhatsApp media, call audio, generated letters, signed documents | **Object store (S3 API)** | One bucket per class: `originals`, `derived`, `exports`, `audit-archive`, `backups` |
| OCR text and extracted fields | PostgreSQL (searchable text and confidence per field) | Large raw OCR output (word boxes) as JSON in `derived` |
| Insurer statements, clearing house files, imported spreadsheets | Original in `originals`; parsed rows in PostgreSQL; **Parquet** copy in `exports` for bulk analysis | |
| Reports and bulk exports (commissions, client lists, misfile metrics) | Generated to `exports` as XLSX, CSV and **Parquet** | Time-limited signed links |

**Parquet is a columnar table format, not a container for PDFs or scans.** Binary files are stored as plain objects (S3 or MinIO); Parquet is used where it fits: sealed audit archive, large tabular exports, analytical copies of statements. Recommendation: S3-API objects for binaries, Parquet for tabular archives. **[decide]** confirm.

### 4.2 Blob store contract

A single `BlobStore` interface with two implementations of the same S3 API:

- onprem: **MinIO** container (single node, one volume). **[decide]** MinIO's community edition has had licensing and feature changes; alternatives are Garage or SeaweedFS (S3 API, simpler) or the local filesystem behind the same interface. The design brief evaluates and recommends one.
- cloud: **Amazon S3**.

Requirements:

- Key = `sha256/<first2>/<hash>`; the database row holds hash, size, media type, original filename, channel, sender, received time, bucket, key.
- Write-once: object lock in compliance or governance mode on `originals` where the store supports it, otherwise application-level refusal plus bucket policy.
- Server-side encryption (SSE-KMS in AWS; encrypted volume or SSE with a local key on-prem).
- Virus scan before a file becomes visible to users (ClamAV container in both profiles); quarantined files stay in `originals` with a flag.
- Download only through the API with an authorization check and an audit entry; never a public bucket; signed URLs short-lived (60 s).
- Upload size limit 100 MB per file (call audio excepted, configurable).
- Lifecycle: `derived` and `exports` can be rebuilt and may expire; `originals` expire only by the retention policy after legal-hold check.

### 4.3 Capacity assumptions (to be corrected by the owner)

About 5,000 clients and about 100 documents a day at the busiest periods (owner, 2026-11-06), average 2 MB: about 200 MB a day, 25 to 50 GB a year with derived copies, so 175 to 350 GB over 7 years, plus call audio. Postgres stays under 10 GB for years. One host with 4 vCPU, 16 GB RAM and 1 TB disk is enough on-prem. Default retention: 7 years after the policy or claim ends, configurable per document type **[verify with counsel]**.

## 5. Runtime architecture

Containers (same images in both profiles):

| Service | Role |
|---|---|
| `web` | Static front end (evolved from the mock) |
| `api` | HTTP API, authorization, audit, process engine, Inbox |
| `worker` | Background jobs: intake pipeline, OCR calls, sends, sync adapters, sealing the audit archive. Jobs are queued in PostgreSQL (`SELECT ... FOR UPDATE SKIP LOCKED`), so no broker is needed |
| `postgres` | onprem container; RDS in cloud |
| `objectstore` | MinIO (or chosen alternative) onprem; S3 in cloud |
| `proxy` | Caddy or Traefik: TLS, HTTP to HTTPS, headers, request size limits |
| (identity) | Built into `api`: local login (argon2id, TOTP, recovery codes, lockout) and SSO through OIDC (Microsoft 365, Google Workspace). No separate identity container |
| `clamav` | Virus scanning |
| `ocr` | Local OCR engine container (Tesseract with Hebrew, Arabic, Russian, French) as default engine behind the OCR adapter; cloud vision engines are optional adapters |
| `backup` | Scheduled `pg_dump` plus WAL archiving (pgBackRest or wal-g) and bucket sync, encrypted, to a second location |

Compose profiles: `core` (always), `demo` (fake adapters and seeded synthetic data, the mock's fictional clients), `observability` (Prometheus, Grafana, Loki; optional).

### 5.1 onprem deployment

- One `docker-compose.yml` plus an `.env.example`; `make up`, `make down`, `make backup`, `make restore`, `make upgrade`.
- Install in under 30 minutes on a clean host with Docker; documented hardening: firewall, only 443 open, unattended security updates.
- Secrets: Docker secrets or SOPS-encrypted file; never in images or in git.
- Data on named volumes on a separate disk; documented free-space alarms.
- Upgrade: pull new images, run migrations automatically, health-gated; rollback by image tag and database restore point.
- Offline-tolerant: no hard dependency on a cloud service for core work; cloud engines (OCR, translation, WhatsApp) are optional and degrade to queues and visible warnings.

### 5.2 AWS deployment

Region **il-central-1 (Tel Aviv)** if all required services are available there at build time, else eu-central-1 with the owner's approval **[verify availability and decide]**.

| Concern | AWS service |
|---|---|
| Containers | ECS on Fargate (api, worker, web, clamav, ocr) |
| Database | RDS for PostgreSQL, Multi-AZ, encrypted with KMS, PITR 14 days |
| Binary files | S3 with versioning, Object Lock on `originals`, SSE-KMS, lifecycle rules, optional cross-region replica for backups |
| Ingress | ALB with ACM certificate; security groups define the firewall (ALB the only public entry, optionally restricted to agency IP ranges or VPN; tasks and RDS accept traffic only from named security groups). AWS WAF and CloudFront optional |
| Network | One VPC, private subnets for tasks and RDS, VPC endpoints for S3 and Secrets Manager, no public database |
| Secrets and keys | Secrets Manager, KMS customer-managed keys |
| Identity | Built into the API (no Cognito or Keycloak); SSO through OIDC |
| Mail in and out | SES for system mail; Microsoft 365 or Google Workspace for mailbox ingestion (adapter) |
| Logs and metrics | CloudWatch, alarms to SNS and e-mail |
| Threat detection and audit | CloudTrail (all regions, log file validation), GuardDuty, Inspector (ECR image and dependency scanning), AWS Config rules (no public bucket, encrypted RDS), Security Hub optional; S3 Block Public Access on. Penetration tests run against `dev` within the AWS penetration testing policy (no DoS) **[verify current policy]** |
| Backup | RDS snapshots and PITR, S3 versioning, AWS Backup plan with a copy to a second account or region |
| Infrastructure as code | Terraform in `infra/aws`, one state per environment (`dev`, `prod`), CI plan on merge request |
| Environments | `dev` and `prod`; `prod` changes only through CI |

Cloud cost target: published as an estimate in the design brief **[decide]** budget ceiling.

### 5.3 Profile parity

A conformance test suite (section 10) runs the same API tests against an onprem stack (compose) and against the cloud adapters locally emulated (LocalStack for S3 and SES, a plain Postgres), so one change cannot break one profile silently.

## 6. Security requirements

- TLS 1.2+ everywhere; HSTS; encrypted at rest (disk or KMS).
- Authentication built into the API: local login with TOTP 2FA, plus SSO through OIDC; sessions are server-side and revocable; session timeout (configurable, default 30 min idle), device list.
- Authorization enforced in the API and in the database where it is cheap (row-level security for the marketing projection and restricted documents).
- Marketing role reads a database view with contact, language, consent and segment only.
- Restricted (medical) documents visible only to agent of record, back-office and manager.
- No secrets in logs; personal data masked in logs; ID numbers shown in full only where the role needs them.
- Dependency and image scanning in CI; SBOM per release.
- Audit chain verified nightly; a failure raises a manager Inbox item.
- Network: on-prem installs are assumed to sit behind a VPN with no public exposure (only the VPN reaches the proxy); AWS uses security groups as the firewall. The application still enforces authentication and authorization as if exposed.
- Scripted penetration tests are part of the test plan (see TESTPLAN); an external test before go-live is recommended, not mandatory.

## 7. Non-functional requirements

| Area | Requirement |
|---|---|
| Availability | Business hours 07:00 to 19:00 Sun to Thu: RTO 4 h, RPO 15 min (WAL archiving) |
| Backup | Nightly full plus continuous WAL, encrypted, copy outside the host or account; **restore test automated monthly** and a report in the manager Overview |
| Performance | Inbox with 10,000 items opens under 1 s; client search under 300 ms at 50,000 clients; a 5-page fax digitized and classified within 2 minutes |
| Scalability | Vertical only in v1; the worker is the only horizontally scalable part |
| Observability | Structured JSON logs, request IDs, `/health` and `/ready` endpoints, job queue depth and age metrics, misfile and auto-file rate metrics |
| Internationalization | Hebrew, Arabic, Russian, French, English UI strings from catalogs; RTL; UTF-8 everywhere; names stored with original script and transliterations; Postgres full-text and trigram indexes for multi-script search |
| Time | Stored in UTC; displayed in the user's zone (default Asia/Jerusalem) |
| Accessibility | Israeli standard 5568 target **[verify]** |
| Maintainability | Schema migrations only forward and tested from an empty database and from the previous release; API versioned; OpenAPI published |
| Licensing | Components must allow commercial use without per-seat fees where possible; list kept in `docs/LICENSES.md` |

## 8. Data protection and residency

- Privacy Protection Law Amendment 13 and Data Security Regulations 5777-2017 apply; database registration and officer duties **[verify with counsel]**.
- Cloud region and every cloud engine (OCR, translation, WhatsApp BSP) must be on an approved-location list kept in configuration; the system refuses to enable an adapter whose region is not on the list.
- Data subject requests: export one client's complete data (database rows and files) and a documented erase-or-restrict path that respects legal hold and retention.
- Test and demo environments use synthetic data only; production data is never copied to them.

## 9. Milestones

Each milestone is implemented, tested and recorded in `STATUS.md` before the next starts. Every milestone ends with a demo script and updated test plan results.

| # | Milestone | Content | Exit criteria |
|---|---|---|---|
| **M0** | Foundation | Repo layout, CI, compose skeleton (`proxy`, `api`, `worker`, `postgres`, `objectstore`, `web`), migrations tool, `/health`, config profiles, Make targets, `.env.example` | `make up` gives a healthy stack on a clean machine; CI green; migration from empty DB works |
| **M1** | Identity, core data, audit | Built-in login with TOTP, SSO (OIDC), roles and capabilities, clients with ID validation, policies, family links, preferred language, transliteration search, audit hash chain with verify | Role access matrix tests pass; chain tamper is detected; search finds Hebrew, Latin and ID-digit queries |
| **M2** | Documents and blob store | `BlobStore` (S3 API), content-addressed write-once originals, derived artifacts, virus scan, download through the API with audit, restricted flag, retention and legal hold fields | Upload and download round trip; duplicate upload deduplicates; overwrite and delete of an original is refused; checksum verified on read |
| **M3** | Inbox, consent, back-office desk | Inbox per role with reassign and urgency, consent registry with one-minute opt-out, Desk work areas, F11 (get the ID and register) | 10,000-item Inbox performance test; an opted-out client is blocked from every send |
| **M4** | Process engine | Definitions as data, versioned; instances; locked and parallel stages; maker-checker; My work; Mermaid export; the 18 processes loaded from `docs/PROCESSES.md` | Reference process (Exit to retirement planning) runs end to end in a test; a locked stage cannot be finalized |
| **M5** | Intake pipeline | Fake and real adapters for email and a fax drop folder, OCR (local), classify, extract, attribute by ID, confidence routing, Review and Unassigned queues, wrong-client action, feedback | Labeled sample set: at least 90% auto-filed, under 1% misfiled; ID scan lands in the owner's folder; never a name-only match |
| **M6** | Cloud deployment (AWS) | Terraform for VPC, ECS, RDS, S3, ALB, KMS, Secrets Manager, CloudWatch; CI deploy to `dev`; backup and restore runbook | `dev` environment deploys from CI; conformance suite passes against `dev`; restore from snapshot verified |
| **M7** | Operations and backup | onprem backup and restore automation, monthly restore test, observability profile, upgrade and rollback procedure, audit archive to Parquet | Restore on a clean host recovers database and files; verify-chain passes across hot and archive |
| **M8** | Messaging and integrations | WhatsApp adapter with consent gate, mailbox ingestion (Microsoft 365 or Google), import from the existing agency system (9.1), Excel join and export | Fake and sandbox adapter tests; import round trip keeps `external_ids` |
| **M9** | Flows F1 to F5 and e-signature | Onboarding, sale and issuance, renewal, claims, pension review on top of the process engine and adapters | Acceptance conditions of F1 to F5 from REQUIREMENTS pass |
| **M10** | Remaining roles and polish | Marketing Campaigns and F10, manager Overview and compliance, F7 commissions, F15 tax, live translation, client app (read-only) | Per-role tab sets match REQUIREMENTS section 2 |

M0 to M7 produce a deployable, secure core in both profiles. M8 and later add integrations and flows and can be re-ordered by the owner.

## 10. Delivery and quality requirements

- Test plan in `docs/TESTPLAN.md`; progress in `STATUS.md`, updated at the end of every milestone and on every failing test left open.
- Test layers: unit, API integration against real Postgres and an S3-compatible store in containers, end-to-end browser tests on the main flows, conformance suite across profiles, restore test, load test for Inbox and search.
- CI on every push: lint, tests, image build, vulnerability scan; release by tag.
- Synthetic data generator reproducing the mock's fictional clients, plus a labeled fax and scan sample set for the intake pipeline.
- Documentation in the repo: install guide (onprem), AWS runbook, backup and restore guide, architecture decision records for each **[decide]** item once resolved.

## 11. Decisions (all resolved, see STATUS.md for dates)

| # | Decision | Answer |
|---|---|---|
| D1 | Storage | Postgres for tables; S3-API objects for scans and PDFs; Parquet for tabular archives and exports |
| D2 | Back-end | Python (FastAPI) |
| D3 | Identity | Built-in login (argon2id, TOTP) plus SSO (OIDC); no Keycloak |
| D4 | AWS region | Configurable, default il-central-1, final at M6 |
| D5 | Queue | PostgreSQL-based, no broker |
| D6 | OCR | Local Tesseract behind an adapter |
| D7 | Front end | TypeScript SPA (React + Vite); the mock stays as reference |
| D8 | Volume and retention | About 5,000 clients, 100 documents a day peak; retention 7 years default, per type, counsel to confirm |
| D9 | AWS budget | No fixed ceiling; cost report and budget alert |
| D10 | Security testing | Scripted penetration tests in the test plan; VPN on-prem, security groups in AWS; external test optional |
| D11 | On-prem object store | Chosen by an M2 spike (MinIO default; SeaweedFS or Garage if licence or lock support requires), behind the `BlobStore` interface |
