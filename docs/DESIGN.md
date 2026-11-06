# ansurance - Design Specification

Status: draft v0.1 (2026-11-06), for owner review. Implements [PLATFORM-REQUIREMENTS.md](PLATFORM-REQUIREMENTS.md) (decisions D1 to D11 in [STATUS.md](../STATUS.md)). Product behaviour comes from [REQUIREMENTS.md](REQUIREMENTS.md) and the mock `index.html`, which stays in the repo as the visual and behavioural reference and is maintained while screens are ported.

## 1. Technology choices

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.12 | Best OCR, document, Excel and Parquet ecosystem (D2) |
| API | FastAPI, Pydantic v2, OpenAPI generated | Typed, fast to build, produces the contract for the TypeScript client |
| Database access | SQLAlchemy 2 (core and ORM), psycopg 3, Alembic migrations | Mature, explicit SQL when needed |
| Auth | argon2-cffi, pyotp, Authlib (OIDC client), server-side sessions in Postgres | Vetted libraries only; no hand-written crypto (D3) |
| Jobs | Postgres table, `FOR UPDATE SKIP LOCKED`, small in-repo runner with retry, backoff, scheduling | No broker (D5) |
| Blob store | `boto3` against an S3 API (MinIO on-prem by default, S3 in AWS) | One code path (D1, D11) |
| OCR | Tesseract (hebrew, arabic, russian, french, english) with OpenCV pre-processing, behind `OcrEngine` | Local by default (D6) |
| Parquet and Excel | pyarrow, openpyxl | Audit archive, exports, statement joins |
| Front end | TypeScript, React, Vite, TanStack Query, react-i18next, generated API client (openapi-typescript), a virtualized list library for the Lens and Inbox | D7 |
| Tests | pytest, testcontainers (Postgres, MinIO), Vitest, Playwright, OWASP ZAP in CI against `dev`, Locust for load | See TESTPLAN |
| Containers | Docker, compose for on-prem; ECS Fargate in AWS; Terraform | D4 |
| Lint and types | ruff, mypy strict on `api`; eslint and `tsc --strict` on `web` | |

## 2. Repository layout

```
ansurance/
  index.html, docs/            mock and specifications (unchanged location)
  backend/
    app/
      main.py                  FastAPI app factory
      config.py                profile and settings (onprem | cloud | demo)
      auth/                    login, TOTP, sessions, OIDC, capabilities
      audit/                   hash chain, verify, archive to Parquet
      clients/ policies/ ...   one package per domain: models, service, router
      documents/               blob store use, derived artifacts, retention
      intake/                  pipeline stages and adapters
      inbox/ consent/ processes/
      adapters/                ocr, mail, whatsapp, fax, import, antivirus (real + fake)
      jobs/                    queue runner and job handlers
      blob/                    BlobStore interface, s3 implementation, fake
    migrations/
    tests/
  web/                         React + Vite app
  deploy/compose/              docker-compose.yml, env example, Caddyfile
  infra/aws/                   Terraform
  Makefile, .github or .gitlab CI
```

Rule: a domain package may call another only through its service module; routers never touch the database directly; adapters are the only place that talks to outside systems.

## 3. Components and runtime

`web` (static files behind `proxy`), `api` (FastAPI, stateless), `worker` (same image, different command), `postgres`, `objectstore`, `clamav`, `ocr` (Tesseract in the worker image, or a separate container if image size matters), `proxy` (Caddy), `backup`. Identity is inside `api`. The `api` and `worker` share code and settings, so a business rule exists once.

Request path: browser, proxy (TLS, size limits), api (session check, capability check, audit, service layer), Postgres. File upload path: browser, api streams to the blob store while hashing, row written in the same unit of work as an `ingest` job, worker scans, digitizes, classifies, attributes.

## 4. Data model (outline)

Primary keys are UUIDv7. All tables have `created_at`, `updated_at`; times are UTC.

| Group | Tables |
|---|---|
| Identity | `user`, `role`, `user_role`, `capability`, `role_capability`, `session`, `totp_secret`, `recovery_code`, `sso_identity`, `login_event` |
| People | `client` (id_number unique, validated, names in original script plus `name_translit_he`, `name_translit_lat`, preferred language, agent_of_record), `family_link`, `contact` (phone, email, channel), `external_id` (system, id) |
| Insurance | `insurer`, `policy`, `policy_event`, `claim`, `complaint`, `commission_statement`, `commission_line` |
| Documents | `blob` (sha256, size, media type, bucket, key), `document` (client, type, restricted, source channel, received_at, retention_until, legal_hold), `document_page`, `derived_artifact`, `ocr_result` (text, per-field confidence), `intake_item` (state, candidates, confidence, reason), `intake_feedback` |
| Work | `inbox_item`, `inbox_event`, `task`, `process_def` (versioned JSON), `process_instance`, `stage_instance`, `check_instance` |
| Consent and marketing | `consent` (client, channel, state, source, at; append-only), `campaign`, `campaign_recipient`, `suppression` |
| Messaging | `thread`, `message`, `call`, `transcript_segment` |
| Platform | `audit_entry`, `audit_seal`, `job`, `setting`, `adapter_call_log`, `schema_migration` |

Search: `pg_trgm` and `unaccent` indexes over a normalized `search_text` column (original, both transliterations, ID digits, order-insensitive tokens) on `client`; Postgres full-text for OCR text.

Authorization at the database: row-level security policies for the marketing projection (a view exposing contact, language, consent, segment only) and for `document.restricted`; the API sets `app.user_id` and `app.roles` per transaction.

## 5. Authentication and authorization design

- **Local login:** argon2id (memory-hard parameters tuned at install), password policy (length, breached-password list check), per-account and per-IP lockout with exponential delay, TOTP (RFC 6238) required for local login, 10 single-use recovery codes stored hashed, password reset only by the manager (no self-service e-mail reset in v1).
- **SSO:** OIDC authorization code with PKCE for Microsoft 365 and Google Workspace. A login is accepted only if the verified `sub` and e-mail match a pre-created `user`; no auto-provisioning. Per user the manager chooses "SSO only" or "local + TOTP". For SSO-only users the provider's MFA is trusted (configurable).
- **Sessions:** random 256-bit token in an HttpOnly, Secure, SameSite=Lax cookie; stored hashed in `session`; idle timeout 30 min, absolute 12 h; device list and revoke; CSRF token for state-changing requests.
- **Authorization:** capabilities (named, e.g. `client.read`, `kyc.approve`, `audit.read`, `campaign.approve`) attached to roles; checked in the service layer via a single `require()` helper; ownership rules (agent of record, team share) in one policy module; denies are audited.
- **Everything audited:** login success and failure, 2FA events, session revoke, reads of client records, exports, downloads, role changes.

## 6. Audit chain

`audit_entry(seq, at, actor, action, object, detail, prev_hash, hash)`, `hash = SHA-256(prev_hash || canonical_json(entry))`. Inserts only through one function that takes a transaction-scoped advisory lock to serialize the chain; `UPDATE` and `DELETE` revoked for the application role and blocked by trigger. Nightly job verifies the chain and raises a manager Inbox item on failure. Monthly job seals the month: writes a Parquet file with the entries and the head hash to `audit-archive`, records `audit_seal` (file hash, head hash). Verification walks sealed files then the hot table.

## 7. Blob store design

Interface: `put(stream) -> BlobRef`, `get(ref) -> stream`, `head(ref)`, `presign(ref, ttl)`, `exists(sha256)`. No `delete` or `overwrite` exists on the interface for the `originals` bucket. Content addressing gives deduplication. Write-once is enforced in three layers: no code path, a database trigger refusing to delete or modify `blob` rows that are referenced, and bucket policy plus Object Lock where the store supports it. On read the API re-hashes while streaming for integrity checks on a sampled basis and always for exports. A reconcile job lists keys against `blob` rows and reports orphans (never auto-deletes originals). On-prem store is chosen by a spike at the start of M2 against a conformance test (put/get/lock/range/presign/list); MinIO is the default and is **built from source** (`deploy/objectstore/Dockerfile`, pinned release tag from github.com/minio/minio) because upstream no longer publishes images on Docker Hub; SeaweedFS and Garage are fallbacks.

## 8. Intake pipeline design

States: `received` → `scanned` (virus) → `digitized` → `classified` → `extracted` → `attributed` → `filed | review | unassigned`. Each transition is a job; each job is idempotent and keyed by blob hash and stage version, so a retry never duplicates. Digitize: render pages (pdf2image or image), de-skew, de-noise, split multi-page faxes by blank page and layout cues, OCR with per-word confidence. Classify: rules (keywords, layout, ID-card patterns) plus a lightweight model slot (starts rules-only; model optional later), storing label, confidence and reason. Extract: ID number validated by Luhn, policy numbers by per-insurer patterns, dates, amounts, plates. Attribute exactly as REQUIREMENTS 4.2: ID match first, policy or claim match second, sender only to break ties, never name-only. Routing thresholds are settings (default 0.95). Corrections write `intake_feedback`. A labeled sample set under `backend/tests/data/intake/` drives the acceptance numbers (90% auto-filed, under 1% misfiled).

## 9. Process engine design

Definitions are JSON (stages, owner role, depends_on, checks with kind fill, verify, send, do), validated by a schema and versioned; an instance stores the definition version it started with. Stage state: `locked`, `ready`, `in_progress`, `final`. A stage becomes `ready` when every dependency is `final`; finalizing requires all checks ticked and the owner role or the manager (maker-checker: the finalizer may not be the person who ticked all checks when the definition says so). Starting and finalizing write audit entries. "My work" is a query over `stage_instance` by owner role. The 18 processes in `docs/PROCESSES.md` are loaded from seed definitions generated by a script from the `.mmd` and PROCESSES sources, and Mermaid text is generated back from the definition.

## 10. API conventions

REST, JSON, `/api/v1`, OpenAPI published; cursor pagination; ETag/If-Match for optimistic concurrency on editable records; idempotency keys on POSTs that cause external effects; problem+json errors; request ID on every response; list endpoints support the Inbox performance target through keyset pagination and covering indexes.

## 11. Adapters

`OcrEngine`, `Mailbox`, `WhatsApp`, `FaxInbox`, `Antivirus`, `Translator`, `ClearingHouse`, `ESign`, `ExistingSystem` (9.1 contract). Each has a `Fake` (deterministic, used by tests and the `demo` profile) and a real implementation added in the milestone that needs it. Each call goes through a wrapper that logs to `adapter_call_log` and the audit chain and enforces the approved-region list. Secrets come from the environment or secret store through one `Secrets` interface.

## 12. Deployment design

- **onprem:** `docker-compose.yml` with profiles `core`, `demo`, `observability`; images pinned by digest; `.env` for settings, Docker secrets for credentials; proxy listens only on the VPN-facing interface; data on named volumes; `make up|down|backup|restore|upgrade`. Backup container runs `pg_dump` nightly plus WAL archiving to a mounted path, and syncs the object store to a second location, all encrypted with `age` or `gpg`.
- **cloud:** Terraform modules `network`, `data` (RDS, S3, KMS), `app` (ECS, ALB), `security` (CloudTrail, GuardDuty, Inspector, Config), `ops` (CloudWatch, Budgets). Region from a variable, default `il-central-1`. Tasks read secrets from Secrets Manager. Migrations run as a one-off ECS task before the new task set receives traffic.
- **Parity:** the same container image runs in both; only environment variables and the adapter set differ. A conformance test target runs the API suite against compose and against LocalStack-backed settings.

## 13. Observability

JSON logs with request and job IDs and no personal data; Prometheus metrics (`/metrics`, internal only): request latency, job queue depth and age, intake auto-file and misfile rates, OCR confidence histogram, audit-verify age, backup age; Grafana dashboards in the `observability` profile; CloudWatch equivalents in AWS. Alerts: queue age over 15 min, audit verify failing, backup older than 26 h, disk over 80%.

## 14. Front end design

Port the mock screen by screen, in the order of the milestones (login and shell with role switcher, theme, language and RTL first). Shared pieces from the mock become components: Lens, client picker (quick search with transliteration), Inbox list, process banner and pipeline, "Show me" tour, time-zone-aware timestamps. i18n catalogs for he, ar, ru, fr, en; `dir` flips per language. API types are generated, never hand-written. The mock's fictional data seeds the `demo` profile so that the first ported screens can be compared one-to-one with `index.html`. A `docs/MOCK-PORT.md` table tracks each mock screen: not started, ported, matches mock.

## 15. Security design notes

Network: on-prem behind VPN, AWS security groups, the app still behaves as if exposed (D10). Headers: CSP, HSTS, X-Content-Type-Options, frame-ancestors none. Uploads: type sniffing, size limits, ClamAV, no execution, served with `Content-Disposition` and sandboxed viewing. Downloads: only via the API with a capability check; signed URLs are short-lived and bound to the user. Personal data masked in logs. Dependency pinning, `pip-audit` and `npm audit`, image scanning in CI. Scripted penetration tests (IDOR across clients and roles, marketing projection leakage, restricted documents, injection, brute force and lockout, session fixation and revoke, 2FA bypass, OIDC state and nonce replay, upload abuse, signed-URL reuse, audit tamper) are part of TESTPLAN and run in CI and before each release.

## 16. M0 scope (first implementation)

Backend skeleton with config profiles, `/health` and `/ready`, Alembic with an initial empty migration and a migration test, the `BlobStore` interface with a fake, a minimal `web` shell (Vite, TypeScript strict, one page that calls `/health`), compose with `proxy`, `api`, `postgres`, `objectstore`, `web`, Makefile targets, CI pipeline with lint, type-check, unit tests, image build, vulnerability scan. Exit: `make up` yields a healthy stack from a clean checkout; all CI jobs green; the stack passes its smoke test.

## 17. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Custom login is security-critical | Vetted libraries, scripted penetration tests, lockout and audit, optional external review |
| Hebrew and Arabic OCR quality on faxes | Adapter plus labeled sample from the agency before M5; confidence routing keeps humans in the loop |
| On-prem object store licence or lock support | M2 spike and conformance test; app-level write-once as the primary guarantee |
| Port of a 300 KB mock | Screen-by-screen tracker, mock kept as reference, shared components first |
| Existing-system integration unverified | Adapter contract with fake, vendor terms agreed before M8 |
| Legal unknowns (retention, registration) | Marked **[verify]**; configurable settings, counsel before go-live |
