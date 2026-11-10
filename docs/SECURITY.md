# Security specification and threat model

Status: first version, 2026-11-10. Produced by reading code and configuration only. No server
was started, no test was run, no secret value was read. Where a statement could not be
confirmed from the files it is marked **unverified**. Every item cites `path:line` (lines are
as of commit `70cf10a`).

Companion documents: [DESIGN.md](DESIGN.md) (section 8.1 sandbox), [OPERATIONS.md](OPERATIONS.md),
[PLATFORM-REQUIREMENTS.md](PLATFORM-REQUIREMENTS.md) (section 6 security requirements),
[LICENSES.md](LICENSES.md) (licence supply chain), [../infra/aws/README.md](../infra/aws/README.md).

## 1. Scope and assumptions

In scope: the FastAPI backend (`backend/app`), the sandbox service (`backend/sandbox`), the
React web app (`web/src`), the on-prem stack (`deploy/compose`, `deploy/backup`,
`deploy/objectstore`, `deploy/postgres`), the AWS stack (`infra/aws/*.tf`), CI
(`.github/workflows`) and the dependency and licence supply chain.

Out of scope: the static clickable mock (`index.html`, fictional data), the third-party
Surense applications kept untracked in `apks/` and `docs/surense-products/` (both are in
`.gitignore`), the cloud provider and the operating system of the host.

Assumptions (taken from README, OPERATIONS and PLATFORM-REQUIREMENTS section 6):

- Single tenant: one insurance agency per deployment. "Tenancy" means separation between
  agents inside the agency (an agent sees only own clients), not between companies.
- On-prem: the proxy is reachable only over the agency VPN (`BIND_ADDR`); AWS: the ALB accepts
  only `allowed_cidrs` (`infra/aws/variables.tf:42-46` forbids `0.0.0.0/0`). The application
  must still hold when this assumption fails (PLATFORM-REQUIREMENTS 149).
- Users are 5 agents, 2 back-office, 1 marketing, 1 manager (README), plus an `accountant`
  role. Clients have read-only access only through the portal link.
- Data is regulated personal and health-adjacent insurance data (Israeli Privacy Protection
  Law Amendment 13 and Data Security Regulations 5777-2017, PLATFORM-REQUIREMENTS 169).
- Attackers considered: (A1) anonymous network attacker who reaches the proxy or ALB;
  (A2) external party who sends content (e-mail, WhatsApp, fax, files uploaded by clients);
  (A3) malicious or curious authenticated insider with a low role; (A4) holder of a stolen
  session, portal link, backup or laptop; (A5) compromised dependency or build input;
  (A6) compromised application container.

## 2. Assets

| Id | Asset | Where it lives | Why it matters |
|---|---|---|---|
| AS1 | Client identity data: Israeli ID number (9 digits), name, phone, e-mail | `client` table, `backend/app/models.py:160-172`; plain text, unique index | Identity theft, marketing abuse; no field-level encryption |
| AS2 | Policies, claims, premiums, commissions, complaints | `policy`, `claim`, `commission_*`, `complaint` tables | Financial and business confidentiality |
| AS3 | Original documents (ID cards, medical reports, claim forms) | S3 or MinIO bucket `originals`, Object Lock; `backend/app/blob/s3.py` | Highest sensitivity; `medical_report` is always `restricted` (`documents/service.py:32,124`) |
| AS4 | Derived data: page images, OCR text, extracted entities | bucket `derived`, `document_page`, `intake_item.entities` | Same content as AS3 in another form |
| AS5 | Audit log (hash chain, monthly sealed Parquet in a lock bucket) | `audit_entry` table, `audit/chain.py`, `audit/archive.py` | Evidence for regulators and disputes |
| AS6 | Credentials: password hashes (argon2id), TOTP secrets, recovery codes, session tokens (hashed) | `user_account`, `recovery_code`, `session` | Account takeover |
| AS7 | Integration secrets: WhatsApp token and app secret, IMAP/SMTP passwords, e-signature webhook secret, SSO client secrets, DB, object store and backup passphrase | env vars (`config.py`), `deploy/compose/.env` (untracked, kind: dotenv), AWS Secrets Manager for DB only (`app.tf:19-22`) | Pivot into other systems |
| AS8 | Backups (encrypted dumps, WAL, copies of originals) | `BACKUP_PATH`, `BACKUP_WAL_PATH` (`deploy/backup/backup.sh`) | Full data set at rest |
| AS9 | Availability of intake and of the staff UI | whole stack | Statutory complaint and renewal deadlines |
| AS10 | Marketing consent state and message gate | `consent_*`, `messaging/gate.py` | Unlawful marketing exposure |
| AS11 | Portal link tokens | `portal_link` table (hash only), `portal/router.py` | Client-facing read access |

## 3. Trust boundaries and data flow

```
 Internet / VPN                                   Untrusted content
 ------------------                               --------------------------
 Staff browser (React SPA)   Portal visitor        E-mail (IMAP)  WhatsApp (Meta)  Fax folder  E-sign callback
        |  HTTPS, cookie+CSRF       | HTTPS, X-Portal-Token       |                 |             |
 =======|===========================|=============================|=================|=============|======  B1
   Caddy (on-prem) / ALB+WAF (AWS)  -- only /api/* to api, rest to web (nginx static)
        |
   +----v----+     SQL (B3)     +-----------+      S3 API (B4)     +-------------------+
   |  api    |-----------------> | PostgreSQL |   +---------------->| MinIO / S3 (lock)  |
   | FastAPI |--- clamd (B5) --> | ClamAV     |   |                 +-------------------+
   +----+----+                   +-----------+   |
        | job queue (table)                       |
   +----v----+   HTTP tar (B6)   +----------------+----+
   | worker  |------------------>| sandbox (no secrets, |   <- the ONLY place that opens PDF/image
   +----+----+   internal net    | internal network,    |      (pdfium, Pillow, OpenCV, Tesseract)
        |                        | read-only root)      |
        +--> outbound adapters (B7): Meta Graph API, SMTP, IMAP, translation provider, e-sign provider, OIDC IdPs
```

Boundaries:

- **B1 Client to edge.** Browser and portal to proxy. Controls: TLS (Caddy internal CA, or ACM on
  ALB with `ELBSecurityPolicy-TLS13-1-2-2021-06`, `app.tf:356`), source IP allow-list (AWS),
  VPN bind (on-prem).
- **B2 Edge to API.** Only `/api/*` is forwarded (`Caddyfile` `handle /api/*`;
  `app.tf:365-374`). `/metrics` is on the API port only (`metrics.py:186`).
- **B3 API/worker to database.** One database role for everything (finding F-12).
- **B4 API/worker to object store.** App credentials cannot delete or shorten retention
  (`deploy/objectstore/app-policy.json`, `infra/aws/iam.tf:199-222`).
- **B5 Application to ClamAV.** clamd INSTREAM over plain TCP (`adapters/antivirus.py`).
- **B6 Worker to sandbox.** Plain HTTP, tar in, tar out, no authentication
  (`backend/sandbox/server.py`, `backend/app/sandbox/container.py`). Output is re-validated by
  the host (`backend/app/sandbox/client.py:82-124`).
- **B7 Outbound integrations.** Gated by `approved_adapters` and logged
  (`integrations/calls.py:17-50`). Targets come from configuration, not from user input.
- **B8 External content into the system.** E-mail, WhatsApp media, fax drop folder, e-sign
  callbacks, uploads. All files go through sniffing, ClamAV and the sandbox before a human sees a
  rendered page (`documents/service.py:72-149`, `intake/pipeline.py`).

Important consequence of the design: the sandbox protects PDF and image parsing only. E-mail
(`email` library), XLSX (`openpyxl`), CSV and JSON are parsed in the API or worker process
(finding F-09).

## 4. Attack surface inventory

Exposure legend: **Anon** = no session needed; **Auth** = session cookie + CSRF header (writes);
**Cap:x** = named capability required (`backend/app/capabilities.py`); **Link** = bearer link;
**Sig** = HMAC signature; **Net** = reachable only from the internal network.

| Id | Surface | Location | Exposure | Current controls |
|---|---|---|---|---|
| S-01 | Login (password + TOTP/recovery code) | `auth/router.py:85`, `auth/service.py:178-203` | Anon | argon2id (`auth/passwords.py`), constant-ish time dummy hash, per-account lockout up to 60 min (`service.py:161-170`), per-IP 20 failures/15 min (`service.py:131`), audit of every outcome, single-use TOTP counter |
| S-02 | SSO (OIDC code + PKCE) start/callback | `auth/router.py:314-352`, `auth/sso.py` | Anon | state single use with 10 min TTL, nonce, PKCE S256, issuer and audience check, only pre-created users, `email_verified` required |
| S-03 | Session cookie and CSRF | `deps.py:44-69`, `auth/router.py:58-67` | Auth | random 256-bit token stored as SHA-256, HttpOnly, Secure (default), SameSite=Lax, idle 30 min, absolute 12 h, per-session CSRF token on non-GET |
| S-04 | User administration | `auth/router.py:159-284` | Cap:user.manage | last-manager guard (`service.py:300-320`), deactivate revokes sessions, audit |
| S-05 | Capability enforcement | `deps.py:72-86`, `capabilities.py` | all Auth routes | single `require()` dependency, denials audited, DB seed vs code test (`tests/test_capabilities.py`) |
| S-06 | Client and policy CRUD, search | `clients/router.py` | Cap:client.*, policy.* | `visible(p)` filter (own clients unless `client.read_all`), `get_visible` returns 404 for foreign ids (`clients/access.py:14-25`), read audit |
| S-07 | Document upload | `documents/router.py:57-90`, `documents/service.py:72-149` | Cap:document.write | magic-byte sniff (pdf, png, jpeg, tiff, eml), 100 MB cap, filename sanitising, ClamAV fail-closed, content-addressed store, audit |
| S-08 | Document pages (rendered images) | `documents/router.py:125-172` | Cap:document.read | pages made in the sandbox, host re-checks PNG, `no-store`, `nosniff`, CSP `sandbox`, restricted-document check |
| S-09 | Original download | `documents/router.py:175-223` | Cap:document.download_original (back-office, manager) | clean scan required, `acknowledge=true` query flag, `application/octet-stream` attachment, CSP sandbox, `nosniff`, hash integrity check, synchronous audit |
| S-10 | Legal hold | `documents/router.py:226-238` | Cap:document.hold (manager) | audit |
| S-11 | Intake (upload, review, confirm) | `intake/router.py`, `intake/pipeline.py` | Cap:intake.* | sandboxed render and OCR, auto-file only at confidence >= 0.95 (`config.py` `intake_auto_threshold`), review queue |
| S-12 | Sandbox service | `backend/sandbox/server.py` | Net (`sandbox_net`, internal) | no secrets, read-only root, tmpfs, `cap_drop ALL`, `no-new-privileges`, 3 GB / 128 pids / 2 CPU (`docker-compose.yml:84-110`), one child process per request, rlimits in the child (`sandbox/limits.py`), restarts after 10 requests, tar member whitelist (`server.py:22,42`), argument whitelist (`server.py:24-36`) |
| S-13 | WhatsApp webhook | `integrations/webhooks.py:39-187` | Sig (HMAC-SHA256, `whatsapp.py:96-100`) | refuses when secret missing (503), constant-time compare, message id dedupe, audit of rejected signatures; handshake uses verify token |
| S-14 | E-sign webhook | `flows/router.py:177-240`, `adapters/esign.py:45` | Sig | HMAC, idempotent per envelope with row lock, signed PDF goes through `docs.ingest` (sniff + scan) |
| S-15 | Portal summary | `portal/router.py:254-325` | Link (`X-Portal-Token`) | 256-bit token, hash only stored, 7-day expiry, revocable, unknown/expired/revoked answer the same 404, restricted and unscanned documents excluded, token kept in URL fragment (`web/src/pages/ClientLink.tsx:16`) |
| S-16 | Message queue (outbound WhatsApp/e-mail) | `messaging/router.py`, `messaging/gate.py` | Cap:message.send | consent gate on every message, legal override audited, delivery attempts capped |
| S-17 | Campaigns | `campaigns/router.py`, `campaigns/service.py` | Cap:campaign.write, flow.approve | approval step by another capability, frequency cap, batch throttle, audience snapshot |
| S-18 | Imports and exports (agency CSV, Excel join, clients.xlsx, complaints.csv, tax package) | `integrations/router.py`, `manager/router.py:66,217`, `tax/router.py:164` | Cap:import.run, excel.use, export.run, flow.approve, tax.read | per-file 20 MB cap on CSV/XLSX join (`integrations/router.py:18`), exports audited at once |
| S-19 | Commission statements (XLSX/JSON) | `manager/router.py:191-260` | Cap:commission.manage | totals reconciliation; **no size cap on XLSX read** (`manager/router.py:~222`) |
| S-20 | Translation | `translation/router.py:45-83`, `adapters/translate.py` | Cap:inbox.read | `approved_adapters` gate, adapter call log, 4000 char limit, human-check flags, "machine translation" notice. Only a fake provider exists today |
| S-21 | Process definitions and instances | `processes/router.py` | Cap:process.* | JSON schema validation (`processes/definition.py`), role checks per stage in service |
| S-22 | Inbox, tasks, meetings, desk | `inbox/router.py`, `tasks/router.py` | Cap:inbox.*, task.write, desk.read | role-scoped queries (`inbox/router.py:~110-125`), owner-scoped tasks |
| S-23 | Mailbox poller (IMAP) | `integrations/mailbox.py`, `mailbox_poll.py` | inbound from the Internet | `BODY.PEEK`, dedupe by Message-ID, attachments into the intake pipeline |
| S-24 | Fax drop folder | `intake/pipeline.py` (`fax_dir`), volume `faxdrop` (`docker-compose.yml:79`) | local filesystem | same pipeline as uploads |
| S-25 | Health, readiness, OpenAPI, docs | `main.py:~130-133,196-212` | Anon | `/api/health` and `/api/ready` return no data; `/api/docs` and `/api/openapi.json` are enabled |
| S-26 | Metrics | `metrics.py:186` | Net (not routed by Caddy or ALB) | no authentication on the port |
| S-27 | Operator CLI | `backend/app/cli.py` | shell in the api container | creates the first manager; `--password` option exists |
| S-28 | Web app (SPA) | `web/src` | Browser | React escaping, one `dangerouslySetInnerHTML` (QR code, `pages/Users.tsx:29`), no token in storage (only theme and language, `ui.tsx:22-41`), CSRF token in memory (`api.ts:109`) |
| S-29 | Edge headers | `deploy/compose/Caddyfile:12-21`, `web/nginx.conf` | Anon | Caddy sets `nosniff`, `X-Frame-Options DENY`, `Referrer-Policy`, CSP `default-src 'self'`, Permissions-Policy, removes `Server`; **nginx (the AWS path) sets none** |
| S-30 | Database | `deploy/compose/pg_hba.conf`, `infra/aws/data.tf:43-95` | Net | scram-sha-256, not published; AWS: private subnets, `rds.force_ssl=1`, KMS, SG only from api/worker |
| S-31 | Object store | `deploy/objectstore/*`, `infra/aws/data.tf:98-260`, `iam.tf` | Net | app user without delete, Object Lock (GOVERNANCE), public access block, `aws:SecureTransport` deny, SSE-KMS |
| S-32 | ClamAV | compose `clamav`, `infra/aws/network.tf:249-265` | Net | SG-only access from api and worker |
| S-33 | Backups | `deploy/backup/backup.sh`, `scripts/restore.sh` | host operator | AES-256-CBC + PBKDF2, monthly restore self-test, separate disk expected |
| S-34 | Containers and images | `backend/Dockerfile`, `backend/sandbox/Dockerfile`, `web/Dockerfile`, `deploy/*/Dockerfile` | build time | digest-pinned bases, `uv.lock` with hashes, apt snapshot pins, non-root users (10001, 10002, nginx-unprivileged) |
| S-35 | CI/CD | `.github/workflows/ci.yml`, `deploy-dev.yml` | repository | actions pinned by SHA, `check_pins.py`, `licenses.py check`, `pip-audit`, `npm audit --audit-level=high`, OIDC (no long-lived AWS keys), manual deploy |
| S-36 | AWS account controls | `infra/aws/security.tf` | AWS | CloudTrail (multi-region, validation), GuardDuty, Inspector ECR, Access Analyzer, Config rules, account public-access block |
| S-37 | Logging | `backend/app/logging.py`, `calls.py` | log sinks | JSON logs with request id, "no personal data" rule, adapter-call table |
| S-38 | Licence and notices | `scripts/licenses.py`, `docs/licenses-policy.yml` | build gate | policy gate, release gate, notices in Settings |

## 5. Threats

Severity uses likelihood x impact for this deployment (small agency, VPN or IP-restricted).
Critical = realistic full compromise of personal data; High = serious breach or loss needing a
specific precondition; Medium = limited breach, abuse or weakened defence in depth; Low = hygiene.

| Id | Threat (STRIDE) | Actor | Severity | Related findings |
|---|---|---|---|---|
| T-01 | Mail server impersonation steals the IMAP password and reads or injects client mail (Spoofing, Info disclosure) | A1 on path | **High** | F-01 |
| T-02 | Crafted PDF or image exploits a parser (pdfium, Pillow, OpenCV, Tesseract) and pivots (Elevation) | A2 | **High** if the sandbox is absent or weak, otherwise Medium | F-02, F-03 |
| T-03 | Stolen or phished credentials; MFA material read from a leaked database (Spoofing) | A3, A4 | **High** | F-05, F-07 |
| T-04 | An agent reads or changes another agent's clients (Elevation, Info disclosure) | A3 | Medium (checks are present and consistent; no database backstop) | F-11 |
| T-05 | Compromise of the API container leads to database superuser, audit rewrite and bulk export (Tampering, Info disclosure) | A6 | **High** | F-04, F-12, F-13 |
| T-06 | Hostile document content steers auto-filing into another client's file (Tampering) | A2 | Medium | F-15 |
| T-07 | Forged sender on e-mail or WhatsApp puts phishing into an agent's Inbox (Spoofing) | A2 | Medium | F-14 |
| T-08 | Denial of service: large or many uploads, unbounded reads, lockout of a named account, sandbox exhaustion (DoS) | A1, A2 | Medium | F-03, F-08, F-09 |
| T-09 | Spreadsheet formula injection reaches back-office users through exports (Tampering) | A2, A3 | Medium | F-10 |
| T-10 | Portal link leaks or is guessed by a third party (Info disclosure) | A4 | Medium | F-16 |
| T-11 | Client browser or intermediary downgrade/clickjack on the AWS path (no HSTS, no CSP) | A1 | Medium | F-06 |
| T-12 | Supply-chain compromise of a dependency, base image or CI step (Tampering) | A5 | Medium | F-19 |
| T-13 | Secrets exposed through env files, `docker inspect`, process arguments, logs (Info disclosure) | A3, A4, A6 | Medium | F-17, F-18 |
| T-14 | Backup theft or tampering (Info disclosure, Tampering) | A4 | Medium | F-18 |
| T-15 | Personal data kept in places that cannot be erased (immutable audit, adapter error text) (privacy compliance) | regulator | Medium | F-20 |
| T-16 | Repudiation: a user denies an action. Mitigated by the hash chain and monthly seals; weakened by F-04 | A3 | Low | F-04 |
| T-17 | Prompt injection and model abuse. **No generative model is present in the code today** (search for LLM clients found none); the surface is OCR, a rule-based classifier and a translator interface with no real provider yet | A2 | Low now, Medium when a provider is added | F-15, F-21 |
| T-18 | SSRF from user-supplied URLs. None found: every outbound target is configuration | A3 | Low | F-22 |
| T-19 | Missing account-wide egress control lets a compromised task exfiltrate over 443 | A6 | Low | F-23 |

## 6. Findings

Each finding states what was observed, where, and the evidence level. IDs F-xx are used by the
remediation plan in section 7.

### F-01 IMAP connection does not verify the server certificate (High)
`backend/app/integrations/mailbox.py:44-60` uses `imaplib.IMAP4_SSL(host, port)` without an
`ssl_context`. In the standard library (read from the installed Python) the default is
`ssl._create_stdlib_context()`, which does not check the certificate or host name. The SMTP
adapter, by contrast, uses `ssl.create_default_context()` (`integrations/smtp.py:65`). A network
attacker between the worker and the mail server can read the IMAP password and every message,
and can inject messages that the intake pipeline will file. Evidence: code and stdlib source.

### F-02 The AWS stack has no sandbox service (High, partly unverified)
`deploy-dev.yml:79-82` builds and pushes only the `api` and `web` images. `infra/aws/app.tf`
defines api, worker, migrate, web and clamav, and no sandbox (no match for "sandbox" in
`infra/aws/*.tf`; `deploy-dev.yml:110` waits for `api worker web clamav`). `config.py` defaults
`sandbox_runner="container"` with `sandbox_url="http://sandbox:8080"`, so on AWS either rendering
and OCR fail, or an operator sets `ANS_SANDBOX_RUNNER=process`, which runs pdfium, Pillow, OpenCV
and Tesseract inside the worker task. That task holds the database password, the object store
role and egress to 443 (`iam.tf:147-222`, `network.tf:310-317`). The worker image also does not
contain the parsers (`backend/Dockerfile:1-3`), so `process` would fail there, which means the
real state is most likely "document processing does not work on AWS yet". **Unverified**: no
deployed environment was inspected. The ECS worker task definition also lacks
`readonlyRootFilesystem` (`app.tf:128-141`; only the api has it, `app.tf:104`).

### F-03 Sandbox service: unauthenticated, single-threaded, unbounded body (Medium)
`backend/sandbox/server.py`: any peer on `sandbox_net` can POST `/run` (no token or mTLS);
`MAX_BODY = 600 MB` is read into memory in one call (lines 23, 89) while the tmpfs is 1 GB and
the memory limit 3 GB (`docker-compose.yml:96-104`), so one request can exhaust the service;
`HTTPServer` handles one request at a time, so a slow or hostile job blocks all OCR/render
(timeout 200 s, with up to 4 host-side retries, `app/sandbox/container.py:312-326`); resource
limits apply to the child (`sandbox/limits.py`) but not to the server process that unpacks the
tar. Positives to keep: tar members are whitelisted by name and type, arguments are
whitelisted and regex-checked, the host re-validates every output byte
(`app/sandbox/client.py:82-124`), the network is `internal: true`. The child runs under the same
uid as the server, with no seccomp profile beyond Docker's default (**unverified**, none in
compose). Parser crashes are reported as refusals and sent to review (good).

### F-04 Audit log is tamper-evident but not tamper-proof against a database writer (High to Medium)
`audit/chain.py` hashes with plain SHA-256 (no key). Immutability is enforced by database
triggers (`migrations/versions/0002_m2... :206-216`), but the application connects as the table
owner (on-prem: the Postgres bootstrap superuser, see F-12), so a compromised API can drop the
trigger and rewrite the whole hot chain consistently. Defence: monthly seals to a lock bucket
(`audit/archive.py`), but (a) entries newer than the last seal are unprotected, (b) the lock mode
is GOVERNANCE, which a privileged principal may bypass (`infra/aws/data.tf:167`,
`deploy/objectstore/init.sh:91,93`), and (c) the MinIO root credentials live in the same `.env`.
`GET /api/audit/verify` loads the complete chain in one request (`audit/chain.py:73-83`,
`clients/router.py:278`), which an `audit.read` user can use to load the database.

### F-05 MFA secrets are stored in the clear and recovery codes are weak (Medium)
`user_account.totp_secret` is plain base32 (`models.py:71`, `auth/service.py:47`). Recovery
codes are `secrets.token_hex(5)` (40 bits) stored as unsalted SHA-256 (`service.py:50-52,
204-228`), which is brute-forceable offline in seconds if the table leaks. A database read
therefore defeats the second factor. Also: `LoginIn.email` has no length limit
(`auth/router.py:28-31`) and failed attempts write `user:{email}` into the immutable audit
(`service.py:_event`), so an anonymous caller can bloat or pollute the permanent log.

### F-06 Security headers only exist on the on-prem path (Medium, High for AWS)
Caddy sets the headers (`Caddyfile:12-19`); the web container's `nginx.conf` sets none, and the
ALB adds none, so on AWS the app is served without CSP, `X-Frame-Options`, `nosniff` or
`Referrer-Policy`. No HSTS anywhere, although PLATFORM-REQUIREMENTS 141 requires it. The CSP is
reasonable but lacks `base-uri`, `form-action`, `object-src 'none'`, and allows `img-src data:`.
The `/api/*` responses on AWS inherit nothing. The `tls internal` + `on_demand` block
(`Caddyfile:9-11`) issues a certificate for any SNI name sent by a client; harmless on a VPN-only
bind, undesirable if exposed.

### F-07 No general rate limiting; targeted lockout (Medium)
Only login has limits (`auth/service.py:131-170`). Nothing limits uploads, search, `/api/portal/summary`,
`/api/auth/password`, SSO callbacks or webhooks. On-prem has no edge rate limit (Caddy has none
configured); on AWS WAF is off by default (`variables.tf:148-151`, no override in either tfvars)
and, when on, only a 2000 requests per 5 minutes per IP rule and the common rule set exist
(`app.tf:395-447`). The account lockout is per account regardless of source
(`service.py:161-170`): anyone who knows a manager e-mail can keep that account locked for up to
an hour. `trusted_proxies` defaults to all private ranges (`config.py:57`), so a compromised
container on the Docker network can forge `X-Forwarded-For` and dodge the per-IP limit.

### F-08 Memory-bound reads of untrusted bodies (Medium)
Uploads read up to 100 MB into memory per request (`documents/router.py:70`,
`intake/router.py:51`, `config.py:70`); concurrent uploads multiply that. The XLSX commission
import reads without any limit (`manager/router.py:~222`). WhatsApp media download keeps the
whole body (`integrations/whatsapp.py:65-70`) inside the API process (webhook handler,
`webhooks.py:135-166`). ClamAV `INSTREAM` has its own default size limit that is below 100 MB
(**unverified** for the shipped config); an over-limit file stays `pending` forever, which is
fail-closed but silent (`documents/service.py:111-117`).

### F-09 Untrusted structured files are parsed outside the sandbox (Medium)
`openpyxl.load_workbook` on user XLSX in the API (`excel/join.py:~53`, `manager/router.py:222`),
Python `email` parsing of `.eml` from the public mail and fax channels (`intake/pipeline.py:145`,
`integrations/mailbox.py:93-111`), CSV parsing (`integrations/agency.py:100-105`) and JSON from
webhooks. XLSX and CSV need an authenticated role; `.eml` arrives from outside. Risks are zip or
XML expansion (whether `defusedxml` is installed is **unverified**; it is not a direct
dependency in `backend/pyproject.toml`) and memory use, not code execution. M19 notes that Office
files are to come later; they must be added to the sandbox, not to the API.

### F-10 Formula injection in exports (Medium)
`to_xlsx` and `rows_to_xlsx` (`excel/join.py:~228-262`), `GET /api/export/clients.xlsx`
(`integrations/router.py:80-111`), `complaints_csv` (`manager/router.py:66-73`) and the tax
package write cell text as is; `openpyxl` stores a string that starts with `=` as a formula. Names,
policy numbers and complaint text come from agency CSV imports, agents, clients and OCR. No
neutralising prefix was found in the code (searched for "=", "formula"). The back-office user
opening the file in Excel is the victim.

### F-11 Authorization design: application-only tenancy, a few loose spots (Medium)
Strengths: one `require()` entry point, a single `visible()` helper, 404 for foreign ids,
restricted documents hidden from other agents, marketing limited to a database view
(`migrations 0002:219`). Weaknesses:

- There is no row-level security in the database (search of `migrations/` for `ROW LEVEL` and
  `CREATE POLICY`: none) although PLATFORM-REQUIREMENTS 143 asks for it where cheap. One missed
  `get_visible` call equals a leak.
- Read capabilities gate state-changing routes: `process.read` on stage start, tick and
  finalize (`processes/router.py:230-280`) and `inbox.read` on reassign, done and dismiss
  (`inbox/router.py:252,284,299`). Role checks inside the service layer probably make this safe
  (**unverified**).
- `PUT /api/claims/{id}/owner` does not check that the new owner is an active user with the right
  role (`flows/router.py:596-604`); the checklist attach does not apply the restricted-document
  rule (`flows/router.py:58-62,554-566`, low: ids are unguessable UUIDs).
- `POST /api/translate` needs only `inbox.read`, which every role including marketing has, and
  sends arbitrary text to an external provider (`translation/router.py:45-83`).
- `GET /api/documents/{id}/pages` enqueues work on a GET (`documents/router.py:137-139`), so any
  read-capable user can create jobs; with SameSite=Lax a cross-site top-level GET also sends the
  cookie.
- Search patterns are not escaped, so `%` and `_` act as wildcards (`clients/router.py:~135`),
  minor and only within own visibility.
- The phone-number match returns a flag that another agent has a client with the same number
  (`clients/router.py:60-70`); intended, but it is an oracle.

### F-12 One powerful database identity (High on-prem, Medium on AWS)
Compose connects as `ansurance`, which is `POSTGRES_USER` and therefore a superuser
(`docker-compose.yml:6,194`). The same account runs migrations, the API, the worker and the
backup. A SQL injection or code execution in the API gives file access through the database,
trigger removal (F-04) and full data access. On AWS the application uses the RDS master user
(`app.tf:10-20`, `data.tf`), although IAM database authentication is enabled and unused
(`data.tf:90`). No SQL injection was found: queries use the SQLAlchemy expression API; the few
`text()` calls use bound parameters (`consent/router.py:62`, `clients/router.py:263`,
`portal/router.py:103`, `metrics.py`).

### F-13 Flat container network and hardening only on the sandbox (Medium)
All services except the sandbox share one default network (`docker-compose.yml`): a compromised
worker or web container can reach Postgres, MinIO (with the app credentials in its environment)
and ClamAV. Only the sandbox has `read_only`, `cap_drop`, `no-new-privileges` and resource limits.
Database, object store and ClamAV traffic is plain: the on-prem URL has no `sslmode`
(`docker-compose.yml:6`), MinIO is `http://` (line 7), clamd is raw TCP. The sandbox is correctly
isolated; the worker bridges both networks and is the realistic pivot.

### F-14 Spoofable senders and ambiguous phone matching (Medium)
The e-mail sender is the `From` header, no SPF/DKIM/DMARC result is checked
(`mailbox.py:sender_and_body`, `intake/pipeline.py:145-146`). It routes the message to a client's
agent (`mailbox_poll.py:47-55`) and is a tie-breaker in attribution (`intake/attribute.py:154-158`).
Attribution by ID number is stronger and the sender is "never a candidate" (`attribute.py:6`),
so the main effect is phishing text in an agent's Inbox and a poisoned work queue. For WhatsApp
the number is authentic (signed webhook) but matched by the last 9 digits with `LIKE` and
`LIMIT 1` (`webhooks.py:29-36`): two clients whose numbers share the last nine digits get one
arbitrary result.

### F-15 Content-driven auto-filing (Medium; the "AI injection" analogue)
OCR text from a hostile document is the input of the extractor and attribution
(`intake/extract.py`, `intake/attribute.py`). An attacker who can send a document (any e-mail
sender, fax) and who knows a victim's ID number and a policy token can include them to push the
confidence over 0.95 and have the document filed into the victim's file without a human review
(`intake/pipeline.py:300-330`). Confidentiality is not lost, but integrity of the file is, and
the file may then be sent to the client or shown in the portal (portal lists filenames and
document types, `portal/router.py:277-315`). There is no LLM in the code, so classic prompt
injection does not apply today. When a translation or summarisation model is added, its input
(document text, client messages) and its output must both be treated as untrusted.

### F-16 Portal link is a long-lived bearer secret (Medium)
Possession of the link gives a read view of name, policy numbers, document names, claims and
consent (`portal/router.py:295-325`) for 7 days. There is no second factor, no per-link
failure or rate limit, no client-device binding, and only `last_used_at` is recorded (no IP).
Strengths: 256-bit token, hash stored, fragment-only transport, same 404 for all failures.
The portal is served from the same origin as the staff application (`web/src/main.tsx:13`), so
any XSS would cross the boundary.

### F-17 Secrets handling (Medium)
Secrets are plain values in `deploy/compose/.env` (untracked), injected as environment variables
into api, worker, postgres and backup (the backup passphrase reaches the postgres container,
`docker-compose.yml:196`; the DB password reaches the backup container, line 161), so they are
visible to `docker inspect` and `/proc/<pid>/environ`. `make up` generates random values
(good: `Makefile:7-10`) and keeps the backup passphrase beside the backups by default.
PLATFORM-REQUIREMENTS 108 asks for Docker secrets or SOPS; not implemented. Grafana falls back
to the password `change-me` (`docker-compose.yml:133`, observability profile). On AWS only the DB
password comes from Secrets Manager (`app.tf:19-22`); WhatsApp, SMTP, IMAP, e-sign and SSO
secrets have no wiring yet and would otherwise be plain task environment. `app.cli
create-user --password` puts a password in the process list (`cli.py:29`; prompting is the
default and documented as preferred). `SecretStr` is used in settings (good).

### F-18 Backup protection (Medium to Low)
Backups use `openssl enc -aes-256-cbc -pbkdf2` (`backup.sh:42-43`, `docker-compose.yml:191`):
confidential but not authenticated, with OpenSSL's default PBKDF2 iteration count; the
manifest that is used to detect damage is not signed. The passphrase is generated 48 hex
characters, which makes the KDF cost acceptable. The default `BACKUP_PATH` is a volume on the
same host as the data.

### F-19 Supply chain and CI gaps (Medium)
Good: images by digest, `uv.lock` with hashes, `check_pins.py`, licence gate, `pip-audit`, `npm
audit`, actions pinned by commit, OIDC without static keys, manual deploy. Gaps: no container
image or filesystem scan in CI (Inspector covers only ECR on AWS), no SBOM, provenance or image
signing, `pip install uv==0.5.31` is version-pinned but not hash-pinned
(`backend/Dockerfile:6`, `sandbox/Dockerfile`), MinIO and `mc` are built from a fetched git
commit (`deploy/objectstore/Dockerfile`; commit pinning is **unverified**), apt uses
`Check-Valid-Until "false"` against a snapshot (acceptable with a fixed date and signed-by key),
`web/package.json` uses caret ranges (the lockfile and `npm ci` hold them), no dependency update
bot, `ci.yml` has no `permissions:` block (defaults to the repository setting) and no secret
scanning or SAST (Bandit, Semgrep, CodeQL, gitleaks). `deploy-dev.yml` uses `-auto-approve` for
the apply of a plan made in the same run; branch protection and environment approval for `dev`
are **unverified**. Third-party Surense `.apk` files and product notes sit in the working tree;
they are ignored by git, which must stay that way.

### F-20 Personal data in logs, errors and the immutable audit (Medium)
Logging policy says no personal data (`logging.py:1`), but adapter failure text is stored (500
chars in `adapter_call.error`, `calls.py:35,46`) and shown to staff
(`webhooks.py:157-165` stores `str(e)[:200]` into an Inbox item), and provider error bodies are
embedded (`whatsapp.py:49`). Login audit entries carry the e-mail address and IP
(`auth/service.py:_event`). The audit log is immutable by design, so any personal data written
there cannot be erased, which conflicts with the erase-or-restrict requirement
(PLATFORM-REQUIREMENTS 171). ID numbers are stored and exported in clear
(`clients/router.py:_client_dict`, `integrations/router.py:99-105`), and the export of all
clients is one call for `export.run` (audited). Restricted documents are protected by
application checks and storage encryption, not by per-document keys. The `x-request-id` request
header is accepted as is, without length or character checks (`main.py:~182-190`).

### F-21 AI/LLM surface (Low today)
No Anthropic, OpenAI, Bedrock or similar client exists in the repository. The "AI" components
are Tesseract OCR and rules (`intake/classify.py`, `intake/extract.py`) inside the sandbox, and a
`Translator` interface with only a fake implementation (`main.py:~160`). Controls already in
place that carry over to a future model: the `approved_adapters` allow-list, call logging, 4000
char limit, the "machine translation" notice and human-check flags (`translation/router.py`).
Missing for a real provider: data-processing agreement and residency check, PII minimisation,
output encoding on every sink (the stored `translated` text is returned as JSON and rendered by
React, which escapes it), a rule that model output never triggers actions (sending a message,
filing a document, changing consent), and tests with injection strings.

### F-22 Outbound calls and SSRF (Low)
No endpoint accepts a URL. Targets are `whatsapp_api_base`, SSO URLs, SMTP and IMAP hosts from
settings. One internal hop trusts a remote value: the media URL returned by Meta is fetched with
the bearer token and no host allow-list (`whatsapp.py:64-66`); the media id from the signed
payload is placed in a URL path unvalidated (`whatsapp.py:59`). SSO fetches the JWKS on every
callback (`sso.py:~84`) without caching, and `jwt.decode` is called without an explicit
algorithm allow-list (authlib's default rejects `none`; **unverified** for the pinned version).
First SSO login links by e-mail to a pre-created user (`sso.py:~100-107`); with a multi-tenant
issuer this would allow takeover by anyone who can set that e-mail in their own tenant, but the
issuer is compared with the configured one (`sso.py:~92`), which covers a tenant-specific issuer.
The webhook verify token and CSRF token are compared with `==` / `!=` rather than constant-time
functions (`webhooks.py:46-50`, `deps.py:60-64`), low impact.

### F-23 AWS infrastructure review (Low to Medium)
Good: private data and app subnets, per-service security groups, RDS private, encrypted,
`rds.force_ssl`, deletion protection in prod, S3 public access block (bucket and account),
TLS-only bucket policy, SSE-KMS, versioning, Object Lock on originals and audit archive, task role
denies delete and retention changes (`iam.tf:199-222`), CloudTrail with validation, GuardDuty,
Inspector, Config rules, flow logs, ECR immutable tags with scan on push, ALB TLS 1.3 policy,
`drop_invalid_header_fields`. Gaps:

- Object Lock mode GOVERNANCE (`data.tf:167`): root or a principal with bypass permission can
  delete originals and seals. Consider COMPLIANCE for `audit_archive` at least.
- One task role shared by api, worker and migrate (`iam.tf:147`), one KMS key for logs, data and
  backups with a `kms:*` account statement (`data.tf:8-20`).
- CloudTrail has no data-event selector (`security.tf:12-21`), so reads of originals from S3 are
  not logged; only application read-audit exists.
- WAF is optional and off (`variables.tf:148`); only two rules, no SQLi, bot or IP-reputation
  groups.
- Egress to `0.0.0.0/0:443` for api, worker, web, clamav (`network.tf:285-351`); no VPC
  interface endpoints for ECR, Logs, Secrets Manager; no egress filtering. The worker has no rule
  for 993 (IMAP) or 587 (SMTP) (**unverified** whether the design intends 443-only providers).
- `/api/docs` and `/api/openapi.json` are reachable through `/api/*`.
- The ALB access log bucket uses SSE-S3 (a documented ALB limitation).

### F-24 Frontend (Low)
No `innerHTML`, `eval`, `postMessage` or token in `localStorage` (only `lang` and `theme`).
React escapes all interpolated data. The only raw HTML is the QR code SVG generated from the TOTP
URI by `qrcode-generator` (`pages/Users.tsx:29`): safe if the library never emits unescaped
input; the URI contains the user's e-mail, which a manager can set. The original-file link is a
plain GET with `acknowledge=true` (`ClientFile.tsx:151`): the warning is a UI step only (F-25).
"Open in mermaid.live" hands process-definition text to a third-party site on a click
(`pages/Processes.tsx:80`); the text is not personal data. External links use
`rel="noopener noreferrer"`. CSRF: header token on all non-GET requests, SameSite=Lax cookie.

### F-25 Original download warning is client-asserted (Low)
`acknowledge=true` is a query parameter the server cannot distinguish from a real click
(`documents/router.py:179,196`), and is logged as "warning_acknowledged". It still adds the
audited, scan-gated, attachment-only, back-office-only controls, so the residual risk is that
the legal and training intent of the warning is not enforceable, plus a state-changing GET.

### F-26 Document metadata trust (Low)
`restricted` is a form field chosen by the uploader (`documents/router.py:63`); only the type
`medical_report` forces it (`service.py:32,124`). A medical document uploaded as `other` and
`restricted=false` is visible to every user with document access in that client's file.
`channel` is a free choice among five values, so the source channel in the audit can be
mislabelled by the uploader.

## 7. Possible fixes / remediation plan

Priority: P0 fix before any real data or exposure; P1 before go-live or within the first
release; P2 next hardening cycle; P3 backlog. Effort: S under 1 day, M 1 to 3 days, L over 3
days. Items marked **unverified** depend on something that must be confirmed first (a deployed
environment, a library version or a policy decision). Verification ideas are meant as automated
tests where possible (the repository already has pytest, vitest, Playwright and Terraform plan
tests).

| Finding | Fix | Priority | Effort | Verification |
|---|---|---|---|---|
| F-01 IMAP certificate | Pass `ssl_context=ssl.create_default_context()` to `IMAP4_SSL` (allow a configured CA bundle for on-prem); fail closed on verify errors; add `imap_verify` setting only for lab use, default true | P0 | S | Unit test with a fake `IMAP4_SSL` class asserting the context has `CERT_REQUIRED` and `check_hostname`; integration test against a local IMAP server with a self-signed certificate must fail |
| F-02 AWS sandbox missing | Add a `sandbox` ECS service (own image in ECR, no task role, no secrets, no internet route, SG that accepts 8080 only from worker, worker SG egress only to it), Cloud Map name `sandbox`, `readonlyRootFilesystem`, `ANS_SANDBOX_URL`; add image build to `deploy-dev.yml`; wait for the service in the stable check; make the app refuse `sandbox_runner=process` when profile is `cloud` or `onprem`. **Unverified**: current AWS behaviour | P0 | M | Terraform plan test (existing `test_infra_plan.py`) asserting no route from sandbox SG to anything but the worker; conformance check uploads a PDF and expects rendered pages on dev; startup test that `process` + `cloud` raises |
| F-03 Sandbox service | Shared-secret bearer (`Authorization`) or mTLS between worker and sandbox, rejected with 401 otherwise; lower `MAX_BODY` to the configured upload limit plus tar overhead and stream to the tmpfs instead of memory; apply rlimits to the server process too; handle requests in a bounded worker pool with a queue limit; add a seccomp profile and `read_only` verification in tests; cap host retries by total time | P1 | M | Test: request without secret gets 401; oversized body gets 413 before reading; concurrent slow job does not block `/health`; container test with a decompression-bomb TIFF returns a refusal; run the existing 3 container tests |
| F-04 Audit tamper-resistance | Run the app with a non-owner role that has INSERT and SELECT only on `audit_entry` (trigger owner is a separate migration role); use an HMAC with a key held outside the database for new entries, or anchor the chain head externally every hour (object store with COMPLIANCE lock, or a signed timestamp); paginate and rate-limit `/api/audit/verify` and run it as a job | P1 | M | Migration test: app role cannot `ALTER`, `DROP TRIGGER` or `UPDATE audit_entry`; verify detects a chain rewritten with a different key; load test of verify with 1M rows is a background job |
| F-05 MFA and recovery codes | Encrypt `totp_secret` with an application key (AES-GCM, key from KMS or Docker secret) with a migration that re-encrypts; recovery codes of at least 80 bits stored with argon2id or HMAC-SHA256 with a server key; add `max_length` to `LoginIn` fields and truncate before writing to audit; consider WebAuthn later | P1 | M | Test: database dump contains no usable TOTP secret; old SHA-256 codes migrated or rotated; 10 KB email in login returns 422; unit test for key rotation |
| F-06 Headers, HSTS, CSP | Put the same header set into `web/nginx.conf` (nosniff, frame-ancestors, referrer, permissions policy, CSP) and add `Strict-Transport-Security` in Caddy and nginx/ALB (a CloudFront or an ALB listener rule cannot add headers, so nginx is the place); extend CSP with `base-uri 'none'; form-action 'self'; object-src 'none'`; drop `data:` from `img-src` if the QR code is rendered as SVG markup; restrict `tls internal on_demand` to known names | P1 | S | `make smoke` and a new test that fetches `/` and `/api/health` and asserts each header on both stacks; Playwright run must still pass (CSP regressions); securityheaders-style script in CI |
| F-07 Rate limiting and lockout | Add a rate-limit layer: Caddy `rate_limit` module or an in-app limiter (per IP and per user, for upload, search, portal, password change, SSO callback); enable WAF in prod tfvars with an IP-reputation and a path-scoped login rule; change the lockout to per (account, source IP) with exponential delay and a manager-visible alert so that a stranger cannot lock out a manager; shrink `trusted_proxies` to the actual proxy address | P1 | M | Test: 21st login from one IP gets 429, a different IP is still allowed for a locked account after the delay; locust or k6 smoke for upload flood stays under memory limit; WAF plan test |
| F-08 Memory-bound reads | Stream uploads to a temp file with a running size counter and reject at the limit (`Content-Length` check first); read XLSX with a size cap; cap WhatsApp media size via `Content-Length` and streaming; set Caddy `request_body` per route (smaller for non-document routes); confirm and configure `clamd` `StreamMaxLength` to at least the upload limit, and surface "pending scan" to users | P2 | M | Test: 101 MB upload is rejected with bounded RSS; XLSX of 50 MB rejected; clamd with the large EICAR file test |
| F-09 Parsers outside the sandbox | Move XLSX and `.eml` parsing into the sandbox service (a third mode), or limit the member count and uncompressed size before `openpyxl`; install `defusedxml` and confirm openpyxl uses it (**unverified**); parse `.eml` structure in the sandbox and return only attachments and headers | P2 | L | Zip-bomb and XML-entity-expansion fixtures must return a refusal in under N seconds and N MB; add to `tests/test_sandbox.py` |
| F-10 Formula injection | Escape on export: prefix a single quote or tab to any text cell that starts with `=`, `+`, `-`, `@`, tab or CR (for both XLSX and CSV) in one helper used by every export; keep imports unchanged | P2 | S | Unit test with names like `=HYPERLINK(...)` and `+1+1`; opening the exported file with openpyxl shows `data_type == "s"` |
| F-11 Authorization hardening | (a) Add PostgreSQL row-level security on `client`, `document`, `policy`, `claim` keyed by a per-request `SET LOCAL app.user_id` and `app.read_all`; (b) split capabilities: `process.act`, `inbox.act`; (c) validate owner on claim owner and checklist attach; (d) move render enqueue to POST or to upload time; (e) escape `LIKE` wildcards; (f) restrict `/translate` to a `translate.use` capability; (g) test every router with a "foreign agent" principal automatically | P1 (a, g), P2 (rest) | L | A generated test that enumerates all routes with an `{id}` and checks that agent B gets 404 for agent A's objects; RLS test that a forgotten `visible()` still returns zero rows; capability matrix test (`test_capabilities.py`) extended to methods |
| F-12 Database identity | Create separate roles: `ansurance_migrate` (owner), `ansurance_app` (DML only, no trigger or DDL rights, no `COPY PROGRAM`), `ansurance_backup` (read, replication); on-prem do not use the bootstrap superuser for the application; on AWS use IAM database authentication for the app and keep the master user for migrations | P1 | M | Migration test that app role cannot create or drop objects; `test_migrations.py` and the upgrade test still pass; conformance run on both stacks |
| F-13 Network and container hardening | Put api, worker, web in separate Docker networks: `edge` (proxy, web, api), `data` (api, worker, postgres, objectstore, clamav), `sandbox_net`; add `read_only`, `cap_drop: [ALL]`, `no-new-privileges`, memory and pid limits to api, worker, web, clamav and proxy; enable TLS to Postgres (`sslmode=verify-full`) and to MinIO; per-service object-store users (the worker needs write, the API read and write) | P2 | M | `make smoke` checks published ports; compose lint test asserting hardening keys on each service; container test that api cannot resolve `sandbox` |
| F-14 Sender trust | Verify SPF, DKIM and DMARC results from the receiving mailbox (`Authentication-Results`) and tag unauthenticated mail in the Inbox; never route unauthenticated mail to a client's agent automatically; disambiguate WhatsApp numbers by full E.164 and flag more than one match instead of `LIMIT 1` | P2 | M | Fixtures with failing DKIM must yield a "sender unverified" Inbox type; two clients sharing a last-9 suffix produce a review item |
| F-15 Content-driven auto-file | Raise the bar for auto-filing from external channels: require agreement of ID number and a second signal (policy token or sender match) and never auto-file when the sender is unauthenticated; show "auto-filed from e-mail" to the agent with undo; keep documents from external channels out of the portal until reviewed; add adversarial documents to `test_intake_accuracy.py`. For a future model: treat inputs and outputs as data, no tool use that sends or files, structured output validation | P2 | M | Adversarial fixtures that embed another client's ID and policy number from an unknown sender must land in review; measure auto-filed precision |
| F-16 Portal link | Exchange the link once for a short session cookie (HttpOnly) bound to a new random value; require a second factor the client already has (last 4 digits of ID or an SMS/WhatsApp code) before showing data; lower default to 24 to 72 hours; per-link and per-IP rate limit; record IP and user agent on use; drop filenames from the portal or show only the document type | P2 | M | Tests for wrong second factor lockout, expiry, revocation, one-time exchange; portal responses contain no filename |
| F-17 Secrets handling | Move compose secrets to Docker secrets or a SOPS-encrypted file read at start (`*_FILE` variables in config); stop passing the backup passphrase to the postgres container by using a wrapper script that reads a file; remove the `change-me` default (fail if unset when the observability profile is on); wire AWS Secrets Manager entries for WhatsApp, SMTP, IMAP, e-sign, SSO; remove the `--password` flag or warn; add gitleaks to CI | P2 | M | CI secret scan; test that start fails without `GRAFANA_PASSWORD`; `docker inspect` shows no secret values in a compose test |
| F-18 Backups | Use an authenticated format (age, or `openssl` with an HMAC over the ciphertext, or `gpg --symmetric` with AEAD); sign the manifest; document and test an off-host copy; keep the passphrase off the host (print and escrow); add a periodic restore from the offsite copy | P3 | M | Restore test with a flipped byte must fail loudly; `make backup-verify` checks the signature |
| F-19 Supply chain and CI | Add image scanning (Trivy or Grype) and SBOM generation (syft) to CI, sign images (cosign) and verify on deploy; hash-pin `uv` (`pip install --require-hashes`); confirm MinIO and `mc` commits and verify signatures; Dependabot or Renovate; `permissions: contents: read` at the top of `ci.yml`; CodeQL or Semgrep and gitleaks; require reviews on `main` and an approval gate on the `dev` environment; keep `apks/` and `docs/surense-products/` ignored and add a pre-commit check | P2 | M | CI fails on a seeded high CVE and on a seeded secret; `check_pins.py` extended to the new pins; repository settings screenshot or API check for branch protection (**unverified**) |
| F-20 PII in logs and audit | Redact provider error bodies (keep status code and a hash); never store `str(e)` into Inbox details; store e-mail in login events only as a salted hash plus a short prefix in the audit chain, keep the full value in the erasable `login_event` table; define and implement the erase path (`DELETE` of the PII in tables while the chain keeps only pseudonymous ids); validate `x-request-id` (hex, max 64); decide with counsel on retention for `login_event` | P2 | M | Test that an erasure request leaves a verifiable chain; log-scan test on fixtures with fake PII; request-id fuzz test |
| F-21 Future AI surface | Before enabling a real translator or any model: DPA and residency decision (D4), minimisation, no PII beyond the needed text, per-adapter allow-list (exists), output length and charset limits, no autonomous actions, injection test suite, provider outage behaviour; add an "AI use" row to the audit | P3 (becomes P1 on adoption) | M | Red-team strings in translation tests; contract test that model output is only returned as inert text |
| F-22 Outbound and SSO details | Validate that the media URL host matches the Graph API host and a size limit; validate `media_id` against `^[0-9]+$`; cache JWKS with a TTL and a timeout, and set `algorithms=["RS256", "ES256"]` explicitly (**unverified** library default); use `hmac.compare_digest` for the verify token and CSRF token; log SSO failures with IP and apply the per-IP limit to callbacks | P3 | S | Unit tests for each guard; a token with `alg=none` and one with `HS256` signed by the public key must be rejected |
| F-23 AWS infrastructure | Add COMPLIANCE-mode lock (at least `audit_archive`) after a legal review of the retention period; separate task roles for api, worker, migrate; split KMS keys (data, logs, backups) and remove `kms:*` from the account statement where possible; enable CloudTrail S3 data events for originals and audit archive; turn WAF on in prod and add the SQLi, bad-inputs and IP-reputation groups; VPC endpoints for ECR, Logs, Secrets Manager, STS and a restricted egress group; disable `/api/docs` and `/api/openapi.json` in cloud and on-prem profiles; add worker egress for 993 and 587 if used | P2 | L | Terraform plan tests asserting the new properties; AWS Config rules pass; `terraform validate` in CI; try to delete a locked object with the app role (expect denied) and with an admin role (expect denied in COMPLIANCE) |
| F-24 Frontend | Keep the QR renderer but pass only generated modules; add an ESLint rule banning `dangerouslySetInnerHTML` except this wrapper; add a Trusted Types or strict CSP experiment; make the original-download a POST that returns a blob (F-25) | P3 | S | ESLint rule in CI; Playwright test that injects `<img onerror>` as a client name and checks nothing executes |
| F-25 Download acknowledgement | Replace the query flag by a two-step flow: `POST /documents/{id}/download-token` (records that the warning was shown with the user and time) returning a single-use token valid 60 seconds, then `GET ... ?token=`; keep the audit of both | P3 | S | Test that a download without a fresh token returns 409; replay of a used token returns 409 |
| F-26 Restricted and channel trust | Make the server decide `restricted` from document type and from a configurable list (claim_form for health claims, medical_report, power_of_attorney); let only a user with `document.write` and the owning agent or back-office lower it; make `channel` server-set for non-upload routes | P3 | S | Test matrix of type vs restricted; uploader cannot choose `email` or `fax` on the upload route |

### Suggested order of work

1. P0 (this week): F-01, F-02.
2. P1 (before real data on AWS or exposure beyond the VPN): F-03, F-04, F-05, F-06, F-07, F-11
   (RLS and the generated cross-agent test), F-12.
3. P2: F-08 to F-10, F-13 to F-17, F-19, F-20, F-23.
4. P3: F-18, F-21, F-22, F-24 to F-26.

### Positive controls to keep (do not regress)

- Sandbox network `internal: true`, no secrets, host-side output validation (`app/sandbox/client.py`).
- Single `require()` dependency and the capability seed test.
- 404 for foreign objects; marketing sees a database view only.
- Argon2id, per-session CSRF token, hashed session tokens, single-use TOTP counter, revoke-all on
  deactivation and password change.
- Content-addressed write-once originals, app role without delete, Object Lock, integrity check on
  every read.
- Hash-chained audit with monthly sealing; synchronous audit for writes, exports and downloads.
- Consent gate on every outbound message.
- Digest-pinned images, locked dependencies, licence and CVE gates in CI, SHA-pinned actions.

### Open questions for the owner

1. Is there any deployed AWS environment, and does document processing run there (F-02)?
2. Which erase-or-restrict decision applies to personal data inside the immutable audit (F-20)?
3. Is GOVERNANCE vs COMPLIANCE lock a legal decision on the retention period (F-23)?
4. Will a real translation provider or any model be adopted, and in which region (F-21)?
5. Are branch protection and environment approval configured on the Git host (F-19)?
