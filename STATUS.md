# Status

Updated: 2026-11-06

| Step | State |
|---|---|
| 1. Platform requirements spec (`docs/PLATFORM-REQUIREMENTS.md`, v0.2) | Done |
| 2. Requirements brief and owner decisions D1..D10 | Done (decisions log below) |
| 3. Design spec (`docs/DESIGN.md`, v0.1) | Approved by owner 2026-11-06 |
| 4. Test plan (`docs/TESTPLAN.md`) | Written (v0.1); results table updated per milestone |
| 5. Milestones M0..M10 | M0 done; M1 next. Rule: commit and push after every completed milestone (owner instruction 2026-11-06) |

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
| M1 Identity, core data, audit | Not started | | |
| M2..M10 | Not started | | |
