# ansurance - Test Plan

Version 0.1 (2026-11-06). Covers [PLATFORM-REQUIREMENTS.md](PLATFORM-REQUIREMENTS.md) and [DESIGN.md](DESIGN.md). Results per milestone are in the **Results** table at the end and in [STATUS.md](../STATUS.md). Rule: a milestone is done only when all its tests (section 3) pass and this file shows it.

## 1. Test layers

| Layer | Tooling | Runs |
|---|---|---|
| Unit | pytest (backend), Vitest (web) | every push, seconds |
| Integration | pytest with real Postgres and an S3-compatible store in containers (testcontainers or compose), migrations applied from empty | every push |
| API contract | OpenAPI schema check, generated client compiles (`tsc --strict`) | every push |
| End to end | Playwright against the compose stack, `demo` profile | merge to main, release |
| Conformance (profile parity) | same API suite against compose and against LocalStack-backed cloud settings | merge to main, release |
| Security | scripted penetration tests (section 4), pip-audit, npm audit, image scan, OWASP ZAP baseline against `dev` | every push (fast ones), release (full) |
| Performance | Locust: Inbox 10,000 items, client search at 50,000 clients, upload throughput | release, and M3 |
| Resilience | backup and restore, kill worker mid-job, kill database, disk-full warning | M7, then monthly |
| Static | ruff, mypy strict, eslint, tsc strict | every push |

## 2. Test data

- Synthetic only. A seed generator reproduces the mock's fictional clients and adds scale sets (5,000 and 50,000 clients).
- Israeli ID generator and validator vectors (valid Luhn, invalid Luhn, wrong length).
- Name sets in Hebrew, Arabic, Russian, French, Latin with accents, apostrophes, reordered tokens.
- Intake sample set (M5): labeled scans, faxes (low resolution, skewed, multi-page), email and WhatsApp media, with expected type, ID, owner and route.
- No production data in any test environment.

## 3. Tests by milestone

IDs are `T-<milestone>-<n>`. "Auto" means automated in CI.

### M0 Foundation

| ID | Test | Type |
|---|---|---|
| T-M0-1 | `GET /health` returns 200 and version; `GET /ready` returns 200 only when the database is reachable, 503 otherwise | Auto, integration |
| T-M0-2 | Migrations apply from an empty database to head, and `alembic check` shows no model drift | Auto |
| T-M0-3 | Settings: profile `onprem`, `cloud`, `demo` load; missing required setting fails fast with a clear error; secrets never appear in logs or `repr` | Auto, unit |
| T-M0-4 | `BlobStore` fake: put, get, head, exists, dedupe by hash; interface has no delete for originals | Auto, unit |
| T-M0-5 | Request ID is generated and echoed; logs are JSON | Auto |
| T-M0-6 | Web shell builds, type-checks strict, and its test renders the health status | Auto |
| T-M0-7 | `make up` on a clean checkout yields all containers healthy; proxy serves web and proxies `/api`; `make down` cleans up | Auto, smoke script |
| T-M0-8 | Security headers present on proxy responses; only proxy port published to the host | Auto, smoke |
| T-M0-9 | Lint, type check, unit tests, image build in CI config pass locally via `make ci` | Auto |

### M1 Identity, core data, audit

| ID | Test | Type |
|---|---|---|
| T-M1-1 | Password hashing argon2id, wrong password rejected, constant-ish timing, policy rejects weak and breached passwords | Unit |
| T-M1-2 | TOTP: valid, expired, reused code, clock skew window | Unit |
| T-M1-3 | Lockout after N failures per account and per IP, backoff, audited | Integration |
| T-M1-4 | Recovery codes single use | Integration |
| T-M1-5 | Session: idle timeout, absolute timeout, revoke, device list, cookie flags, CSRF enforced | Integration |
| T-M1-6 | OIDC (fake provider): state and nonce checked, PKCE, unknown subject rejected, no auto-provisioning | Integration |
| T-M1-7 | Role and capability matrix: every endpoint against every role, expected allow or deny | Auto, table-driven |
| T-M1-8 | ID validation vectors; duplicate ID rejected | Unit, integration |
| T-M1-9 | Search finds by Hebrew, Latin transliteration, accent-insensitive, token-reordered, any ID digits | Integration |
| T-M1-10 | Audit chain: entries linked, tamper with a row detected by verify, update and delete refused for app role | Integration |
| T-M1-11 | Every read of a client record writes an audit entry | Integration |

### M2 Documents and blob store

| ID | Test | Type |
|---|---|---|
| T-M2-1 | Upload and download round trip, hash matches | Integration (real store) |
| T-M2-2 | Same file twice stores one blob, two document rows | Integration |
| T-M2-3 | Overwrite and delete of an original refused at API, service, trigger and bucket-policy layers | Integration |
| T-M2-4 | Virus test file (EICAR) quarantined and not visible | Integration |
| T-M2-5 | Restricted document hidden from roles without access (RLS) | Integration |
| T-M2-6 | Download needs capability, is audited, signed URL expires and is user-bound | Integration |
| T-M2-7 | Size and media-type limits | Integration |
| T-M2-8 | Reconcile job reports orphans and never deletes originals | Integration |
| T-M2-9 | Object-store conformance suite against each candidate store | Spike |

### M3 Inbox, consent, desk

| ID | Test | Type |
|---|---|---|
| T-M3-1 | Inbox per role; reassign with note; nothing disappears without action or reason | Integration |
| T-M3-2 | Inbox with 10,000 items opens under 1 s (p95) | Load |
| T-M3-3 | Consent registry append-only; opt-out applied within 1 minute | Integration |
| T-M3-4 | Opted-out client blocked from every send path | Integration |
| T-M3-5 | Marketing projection exposes only contact, language, consent, segment | Integration, security |
| T-M3-6 | F11 get-the-ID-and-register flow | E2E |

### M4 Process engine

| ID | Test | Type |
|---|---|---|
| T-M4-1 | Locked stage cannot start or finalize; parallel stages both ready | Unit |
| T-M4-2 | Stage cannot finalize with unticked checks; maker-checker enforced | Unit |
| T-M4-3 | Instances keep the definition version they started with | Integration |
| T-M4-4 | All 18 seeded definitions load and validate; Mermaid output matches stored `.mmd` | Auto |
| T-M4-5 | Reference process (Exit to retirement planning) runs end to end | E2E |
| T-M4-6 | Start and finalize audited | Integration |

### M5 Intake pipeline

| ID | Test | Type |
|---|---|---|
| T-M5-1 | Stages idempotent: replaying a job creates no duplicates | Integration |
| T-M5-2 | ID scan lands in the ID owner's folder regardless of sender | Integration |
| T-M5-3 | Never filed on a name-only match | Integration |
| T-M5-4 | Routing by confidence: auto, review, unassigned | Unit |
| T-M5-5 | Wrong-client action moves item, audits, counts in misfile metric | Integration |
| T-M5-6 | Labeled sample set: at least 90% auto-filed, under 1% misfiled | Accuracy |
| T-M5-7 | Original never deleted; every artifact links to it | Integration |

### M6 Cloud deployment

| ID | Test | Type |
|---|---|---|
| T-M6-1 | `terraform validate`, `plan` clean, tflint and tfsec/checkov pass | Auto |
| T-M6-2 | `dev` deploys from CI; conformance suite passes against it | Release |
| T-M6-3 | RDS, S3, KMS encrypted; S3 public access blocked; Object Lock on originals | Auto, config tests |
| T-M6-4 | Security groups: only ALB public; database reachable only from tasks | Auto |
| T-M6-5 | Restore from snapshot verified | Manual, runbook |

### M7 Operations and backup

| ID | Test | Type |
|---|---|---|
| T-M7-1 | Backup then restore on a clean host recovers database and files; counts and hashes match | Auto, scripted |
| T-M7-2 | Point-in-time recovery to a chosen time | Scripted |
| T-M7-3 | Audit verify passes across hot table and sealed Parquet files | Auto |
| T-M7-4 | Upgrade and rollback by image tag | Scripted |
| T-M7-5 | Alerts fire for stale backup, queue age, failing verify | Auto |

### M8 to M10

Test cases are added to this file at the start of each milestone (adapter fakes, WhatsApp consent gate, mailbox ingestion, import round trip, flows F1 to F5, campaigns, commissions, translation, per-role tab sets).

## 4. Scripted penetration tests (security suite)

Run against a `demo` stack in CI (fast subset) and against `dev` before each release (full). Within the AWS penetration testing policy (no denial of service). Each test states the expected defence.

| ID | Attack | Expected |
|---|---|---|
| P-1 | IDOR: agent A requests agent B's client, document, claim by ID | 403 or 404, audited |
| P-2 | Role escalation: change own role, forge capability, call manager endpoints as agent | Denied |
| P-3 | Marketing user queries documents, KYC, claims by every route including search | No data |
| P-4 | Restricted (medical) document access by unauthorized role, via search, thumbnails, signed URL | Denied |
| P-5 | SQL injection in search, filters, sort, IDs (sqlmap or crafted payloads) | No injection, parameterised |
| P-6 | XSS stored and reflected via names, notes, filenames, messages | Encoded, CSP blocks |
| P-7 | Brute force login and TOTP; credential stuffing | Lockout and rate limit, audited |
| P-8 | Session fixation, cookie theft flags, CSRF on state-changing calls | Rotated, flags set, CSRF rejected |
| P-9 | 2FA bypass: skip step, reuse code, recovery-code replay | Denied |
| P-10 | OIDC: replay state, wrong nonce, mix-up, unknown subject, unverified email | Rejected |
| P-11 | Upload abuse: EICAR, polyglot files, oversized, path traversal in filename, zip bomb | Rejected or quarantined |
| P-12 | Signed URL reuse by another user or after expiry | Denied |
| P-13 | Audit tamper: modify or delete entries via API and via SQL as app role | Refused, verify detects |
| P-14 | Blob overwrite and delete of originals | Refused |
| P-15 | SSRF through adapter URLs and webhook settings | Blocked by allow-list |
| P-16 | Webhook forgery (WhatsApp, fax) without valid signature | Rejected |
| P-17 | Mass assignment and over-posting on update endpoints | Ignored fields |
| P-18 | Information leakage: error messages, stack traces, headers, log contents (no personal data) | None |
| P-19 | TLS and header checks (ZAP baseline, testssl) | Pass |
| P-20 | Dependency and image vulnerabilities | No high or critical unaddressed |

## 5. Exit criteria

- Milestone: all its tests pass, no open high-severity defect, lint, type check and security fast subset green, STATUS.md and the Results table updated.
- Release candidate: all milestone suites, conformance, full penetration suite, restore test, load tests within targets.

## 6. Results

| Milestone | Date | Passed | Failed | Notes |
|---|---|---|---|---|
| M0 | 2026-11-06 | T-M0-1..9: 16 backend tests, 2 web tests, `make ci` (ruff, ruff format, mypy strict, tsc strict, eslint), `make smoke` on the live stack, pip-audit and npm audit clean | 0 | Defects found and fixed during the run: alembic could not import `app` inside the container (fixed with `prepend_sys_path`); smoke used HEAD on a GET-only route; MinIO is no longer on Docker Hub, so it is built from source (D11) |
| M1 | 2026-11-06 | T-M1-1..11 and penetration tests P-1, P-2 (role escalation via matrix), P-3, P-7, P-8, P-9, P-10, P-13, P-17: 90 new backend tests (106 backend in total), `make e2e-m1` against the live stack over HTTPS, mypy strict, ruff, pip-audit clean | 0 | Defects found and fixed during the run: unknown SSO provider returned 500 (now 404); `httpx` was a dev-only dependency but is used at runtime (container failed to start); migration env needed the working directory on the path. Deviations: marketing projection is a database view read through a dedicated endpoint (row-level security is added with consent in M3); session cookies are Secure so the proxy now terminates TLS with Caddy's internal CA; the audit head must be anchored externally (sealed archive, M7) to detect truncation of the newest entries |
| M2 | 2026-11-06 | T-M2-1..9 and penetration tests P-4, P-11, P-14 (49 new tests, 155 backend in total; blob contract against the fake and real MinIO built from source; real ClamAV; `make e2e`-style script `scripts/e2e_m2.py` on the live stack with MinIO, ClamAV, Postgres, HTTPS) | 0 | Findings: (1) a plain S3 delete only adds a delete marker that hides the object, so the app user has no delete permission at all and Object Lock protects versions (verified: even root cannot delete a locked version); (2) real ClamAV flags the EICAR test only when it is the whole file, so the real-scanner test uses the exact file and the API tests use the fake scanner; (3) fixed: reconcile command edit was silently not applied (caught by the e2e run); no signed URLs are issued (downloads always go through the API), so P-12 is satisfied by design. Decision D11 confirmed: MinIO built from source passes the conformance suite |
| M3 | 2026-11-06 | T-M3-1..6 and P-3 (marketing projection incl. consent): 41 new tests, 196 backend in total; 10,000-item Inbox p95 under 1 s; `scripts/e2e_m3.py` on the live stack (F11 across agent and back-office, Inbox, Desk, consent, send gate, late opt-out reaching the manager) | 0 | Findings: (1) a capability edit I made in M2 silently did not apply to `app/capabilities.py` after reformatting; the database seed was right, but the constant was stale. Fixed, and `tests/test_capabilities.py` now fails whenever the code and the seeded database differ; (2) bulk-insert test bug (NOT NULL column) fixed. Deviations: F11 is verified at API level and on the live stack, not in a browser (web screens start with the first ported screen; Playwright E2E is added then); the marketing projection is still a view, not row-level security; the send path queues messages (outbox) and delivery adapters arrive in M8; opt-out latency is measured as received-at versus applied-at (a request carries its receipt time, late ones are flagged and escalated) |
| M4 | 2026-11-06 | T-M4-1..6: 29 new tests (225 backend in total); the 18 seeded definitions (72 stages, 221 checks) and every English and Hebrew diagram compared byte for byte with `docs/processes/*.mmd`; the reference process (Exit to retirement planning, 10 stages, 54 checks) run end to end by agent, back-office and manager in a test and by `scripts/e2e_m4.py` on the live stack | 0 | Findings: (1) the mock's diagram generator cut the first letter off Hebrew group headings ("יסודות" became "סודות", "ציות" became "יות"); fixed in `index.html` and in `docs/processes/retire.he.mmd`; (2) a silent no-op edit risk again: edits are now verified by grep after applying. Deviations: process definitions are edited through the API only (the editing UI comes with the web port); no Playwright run yet |
| M5 | 2026-11-06 | T-M5-1..7: 89 new tests (314 backend in total): job queue (idempotent enqueue, exclusive claims under 6 concurrent workers, retry with backoff, dead letters, stale reclaim), extraction, classification, attribution rules, 31 pipeline tests (routing, resume after a crash, quarantine, scanner outage fails closed, e-mail attachments, split faxes, fax drop folder, corrections, API roles, second OCR pass), and an accuracy run with the **real Tesseract** (heb+eng) on 72 synthetic scans processed by 8 concurrent workers: **96.7% auto-filed (58 of 60), 0 misfiled, every negative case (no identifier, name only, unknown ID, two clients) kept out of automatic filing**; `scripts/e2e_m5.py` on the live stack with Tesseract in the worker container | 0 | What the accuracy number is and is not: the sample is synthetic (rendered Hebrew and English pages, some skewed, noisy or fax-like), so it proves the pipeline and guards against regressions; it does NOT replace measuring on the agency's own scans, which will be worse (REQUIREMENTS 4.3). Findings fixed during the milestone: (1) OCR appends invisible right-to-left marks that broke ID matching; (2) a raw word-confidence threshold of 0.95 would file almost nothing, so the confidence is now evidence-based (check-digit-valid ID matching one known client scores 0.99 at OCR confidence 0.95 and above and drops with shaky readings); (3) a degraded fax can misread a number into a different but check-digit-valid one, and policy fragments such as 2024-77881 were accepted as IDs, so only real ID shapes are read and a match against a known client is required; (4) Latin tokens inside Hebrew sentences were mangled (policy prefix lost), fixed with a second English-only OCR pass on shaky pages, and a digits-only policy match that may only suggest, never file; (5) page cleanup took 5 s of heavy CPU, now 1 s (crop first, smaller denoise window, thread caps); (6) the API container lacked `httpx` earlier, and a test used a wrongly typed id. Known limitations: ID numbers in narrow table cells at the page edge (family addenda) were lost by the OCR in one trial; Arabic, Russian and French OCR models are in the container image but untested here (host has heb+eng only); no real mailbox yet (M8), e-mail is ingested as .eml |
| M6 | 2026-11-06 | T-M6-1, T-M6-3, T-M6-4 (offline): 27 Terraform tests (validate, fmt, offline plan of dev and prod with 163 and 165 resources, and assertions on the plan: RDS encrypted, private, Multi-AZ and deletion-protected in prod, password generated by RDS; every bucket public-access-blocked, versioned, KMS-encrypted (ALB logs SSE-S3), TLS-only; `originals` and `audit-archive` with Object Lock governance 2555 days; the task role explicitly denied delete, version delete, bypass and policy changes on the originals; only the load balancer has CIDR ingress and only from the allowed addresses, the database accepts only api and worker, the worker accepts nothing; tasks without public addresses; HTTP redirects to HTTPS with TLS 1.3/1.2; KMS rotation; immutable scanned ECR; CloudTrail multi-region with log validation, GuardDuty, Inspector, Access Analyzer, Config rules; alarms including worker-down; unsafe inputs such as 0.0.0.0/0 refused), settings assembled from parts for AWS (3 tests), the black-box conformance script run against the compose stack (20 checks, `make conformance`), 12 tests for batched read auditing | 0 | **Not verified: T-M6-2 (deploy to a real `dev` account and run conformance there) and T-M6-5 (restore from a snapshot). They need an AWS account, credentials, a certificate and region availability checks, and are scripted in `.github/workflows/deploy-dev.yml` and `infra/aws/README.md` but have not run.** Findings fixed: (1) behind a proxy every login was recorded from the proxy's address, so the per-IP limit would have applied to everyone at once (one attacker could lock out the office); the API now trusts X-Forwarded-For only from configured proxy ranges (tested, and verified live on the compose stack); (2) policy documents were unknown at plan time and could not be asserted, so bucket ARNs are derived from names and the IAM guard is its own policy; (3) the conditional lists needed consistent types. Decision applied: audit of reads is asynchronous and batched, losing the unflushed tail on a crash is accepted, writes, security events, exports and downloads stay synchronous (REQUIREMENTS section 7, DESIGN section 6) |
| M7 | 2026-11-06 | T-M7-1..5 on the real on-prem stack: **disaster recovery** (back up the running system, restore into a brand-new stack with its own volumes: user logs in, client and document present with identical bytes, audit chain verifies, restored originals still Object-Locked, wrong passphrase unreadable, restore refuses a non-empty database) 5 tests; **point-in-time recovery** (base backup, client A, note the time, client B, recover to between them: A present, B absent, new timeline writable, audit chain verifies) 6 tests; **upgrade and rollback** (pre-flight, backup first, new build replaces running images, simulated failed health gate rolls back to the previous images with the system answering, refuses to upgrade an unhealthy stack) 5 tests; audit sealing to Parquet and verification across the hot table and the archive, including attacks the plain chain check cannot catch (consistent rewrite of history, deletion of sealed tail entries, altered or missing archive file, missing seal) 17 tests; daily operations alerts 4; Prometheus metrics 8 and the alert rules validated and unit-tested with promtool 6; the observability profile started live (Prometheus scrapes the API, 13 rules load, `/metrics` is 404 through the proxy) | 0 | Findings fixed: (1) the stock PostgreSQL alpine image has no `openssl`, so every WAL archive attempt failed and the base backup waited forever (custom image now); (2) the WAL volume was owned by root, not the database user (init step); (3) the manifest's row counts were read after the dump, so a busy system could produce a false mismatch (one exported snapshot now serves both); (4) `grep -q` under `pipefail` made a healthy base backup look unreadable; (5) the restore project reused a stale tooling image (`run --build`); (6) the fuzzy consonant search was applied to tokens containing digits, so a code like 8bf2f032 matched unrelated names (now literal; tested); (7) read-audit buffers of earlier tests could flush into later tests (test isolation fixed). Limits stated honestly: the scheduler's timing (01:30, monthly test) was exercised by running its jobs directly, not by waiting for the clock; WAL and backups protect against loss of the database but copying them off the host is the operator's job (OPERATIONS.md); the monthly restore self-test restores into a temporary database on the same server, while the full new-host restore is the quarterly rehearsal and the tested `restore.sh` |
