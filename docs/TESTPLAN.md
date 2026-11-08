# ansurance - Test Plan

Version 0.2 (2026-11-07). Covers [PLATFORM-REQUIREMENTS.md](PLATFORM-REQUIREMENTS.md) and [DESIGN.md](DESIGN.md). Results per milestone are in the **Results** table at the end and in [STATUS.md](../STATUS.md). Rule: a milestone is done only when all its tests (section 3) pass and this file shows it.

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

### M8 Messaging and integrations

Fake adapters only: no real provider, mailbox or agency system was reachable.

| ID | Test | Type |
|---|---|---|
| T-M8-1 | WhatsApp webhook: valid signature accepted, forged or replayed refused, verification handshake answered (`test_whatsapp.py`) | Auto |
| T-M8-2 | Consent gate at queue time and again at delivery; opt-out words recorded; status callbacks and retries (`test_whatsapp.py`) | Auto |
| T-M8-3 | Mailbox ingestion by IMAP, deduplicated by Message-ID (`test_mailbox.py`) | Auto |
| T-M8-4 | Agency sync: dry run, existing system wins for policy data, user-entered contact data kept, file-drop cursor (`test_agency_sync.py`) | Auto |
| T-M8-5 | Excel join and export round trip (`test_excel.py`) | Auto |
| T-M8-6 | Every external call recorded in `adapter_call`; integration status shows no secret (`test_integrations_status.py`) | Auto |
| T-M8-7 | SMTP delivery against a local server: consent re-check at send, temporary and permanent refusal told apart, advertisement label and List-Unsubscribe (`test_email.py`) | Auto |

### M9 Flows F1 to F5 and e-signature

| ID | Test | Type |
|---|---|---|
| T-M9-1 | F1: no activation without ID, KYC and signed consent; flagged cases need the manager and raise a task; an expired mandate does not count (`test_flows.py`) | Auto |
| T-M9-2 | F2: a policy needs advice, a signed application and a renewal date; a policy added by hand shows what is missing | Auto |
| T-M9-3 | F3: renewal queue stages and agent scope, required outcomes, cancellation to the manager, leaks list, one daily item per stage | Auto |
| T-M9-4 | F4: owner, timeline and checklist, reassignment keeps an owner, rejection opens a complaint, late insurer reminded once per period | Auto |
| T-M9-5 | F5: no lookup without all three valid authorizations; expiry within six months flagged with a re-sign action; month-end arithmetic | Auto |
| T-M9-6 | E-signature: signed form filed and audited; forged and replayed webhooks refused; declined is not signed; provider outage gives 502 and records nothing; foreign or unscanned documents refused | Auto |
| T-M9-7 | Every flow endpoint against every role (`test_flow_endpoints_respect_visibility_and_roles`) | Auto, table-driven |

### M10 Campaigns, manager, tax, translation, client view

| ID | Test | Type |
|---|---|---|
| T-M10-1 | Campaign: consent as a hard stop at snapshot and per message, 30-day frequency cap, approval for offers, translation reviewed before the first send, throttled batches, advertisement label, replies to the Inbox, results with exclusions (`test_campaigns.py`) | Auto |
| T-M10-2 | Complaints with SLA and owner, compliance dashboard, Overview counts, commission reconciliation of every line class with totals that tie (`test_manager.py`) | Auto |
| T-M10-3 | Tax: certificates per client, product and year, one dispatch item per insurer, deadlines that cannot pass without an item, accountant role read-only (`test_tax_portal.py`) | Auto |
| T-M10-4 | Translation keeps original and translation, flags amounts, dates and coverage words | Auto |
| T-M10-5 | Client view by unguessable expiring link: own data only, no internal notes, restricted or infected files, or audit | Auto |

### Web front end

Vitest with a fake API, and Playwright against the live stack (`make e2e-web`).

| ID | Test | Type |
|---|---|---|
| T-W-1 | Sign-in, wrong password and lockout shown without detail, tabs follow capabilities, language and direction and theme remembered (`App.test.tsx`) | Auto |
| T-W-2 | Every screen in [MOCK-PORT.md](MOCK-PORT.md) with a fake API: role-limited controls, server reasons shown, no secrets in the page | Auto |
| T-W-3 | The client's page reads its token from the URL fragment and fails without saying why (`Remaining.test.tsx`) | Auto |
| T-W-4 | Users page: add a user, show the setup once (`Users.test.tsx`) | Auto |
| T-W-5 | Live call panel: agents only, both directions translated, nothing sent when the languages match (`LiveCall.test.tsx`) | Auto |
| T-W-6 | Real browser: sign in, client file with upload and virus scan, Inbox, manager work (`web/e2e`) | Auto, Playwright |
| T-W-7 | Every tab in the mock exists in the app or is listed in [review/MOCK-REVIEW.md](review/MOCK-REVIEW.md) as a section of the client file | Review at each slice |

### M11 User administration (F16)

| ID | Test | Type |
|---|---|---|
| T-M11-1 | A manager changes roles and name; the change is audited and takes effect on the next request | Auto |
| T-M11-2 | Deactivate: the user cannot sign in, an open session stops at the next request, the name stays on past work | Auto |
| T-M11-3 | Reactivate: sign-in works again only after the authenticator is reset | Auto |
| T-M11-4 | Reset the authenticator: old secret and old recovery codes stop working, new ones shown once, all sessions revoked | Auto |
| T-M11-5 | A manager sets a new password: policy enforced, sessions revoked, lockout cleared | Auto |
| T-M11-6 | A user changes their own password: wrong current password or wrong code refused and counted as a failure; sessions of other devices revoked | Auto |
| T-M11-7 | The last active manager cannot be deactivated or lose the manager role | Auto |
| T-M11-8 | Only `user.manage` may call these endpoints; every role against every endpoint; an SSO user has no password or authenticator to reset | Auto, table-driven |
| T-M11-9 | No audit entry, log line or later response contains a password, secret or recovery code | Auto |
| T-M11-10 | Users page: edit roles, reset, deactivate, with confirmation and the one-time setup shown after a reset | Auto, Vitest |

### M12 Tasks and meetings (agent Today and client file)

A lead already exists as a client with the status `lead` (F1), so this milestone adds tasks and meetings only.

| ID | Test | Type |
|---|---|---|
| T-M12-1 | An agent creates, completes, reopens and delegates a task; the five Today chips (today, overdue, next, done, delegated) show the right rows in date order | Auto |
| T-M12-2 | Delegation to an unknown or inactive user is refused; the people list has names only and skips inactive users; bad view, empty text and a missing CSRF token are refused | Auto |
| T-M12-3 | A meeting is recorded on the client file with a summary; the service contact date is 24 months after the newest meeting and moves when a newer one is added; newest first; unknown kind refused; audited | Auto |
| T-M12-4 | Agent scope: an agent sees only own tasks, cannot attach one to another agent's client, read or change another agent's meetings or tasks, or look at another person's list; the manager can; marketing and the accountant have no tasks | Auto |
| T-M12-5 | An overdue task raises one Inbox item, not one per day; a finished task raises none; a task that becomes late later raises its own | Auto |
| T-M12-6 | Today tasks and client meetings screens, delegation, CSRF, hidden for a role without `task.write` (`Tasks.test.tsx`) | Auto, Vitest |

The Playwright test for these screens is T-M13-5.

### M13 Leads: phone duplicate check, New client form, Today leads (F1)

| ID | Test | Type |
|---|---|---|
| T-M13-1 | Phone numbers are compared by digits (`+972 50 111 2222` equals `050-111-2222`); the same phone on another client is a warning, not a refusal; no number matches nothing; a duplicate ID is still refused (`test_leads.py`) | Auto |
| T-M13-2 | A match on another agent's client says only that one exists, never the name; the manager sees all matches | Auto |
| T-M13-3 | The leads list is the user's own clients with status lead; marketing has none | Auto |
| T-M13-4 | New client form: CSRF, phone warning with links, the other-agent notice, a refused ID explained, not offered without `client.write`; leads on Today (`Tasks.test.tsx`) | Auto, Vitest |
| T-M13-5 | Real browser: two clients with one phone written two ways, the warning, the leads list, a task added and finished, a meeting recorded (`web/e2e/agent-day.spec.ts`) | Auto, Playwright |

### M14 Process diagram view (Processes tab)

| ID | Test | Type |
|---|---|---|
| T-M14-1 | Layers: a stage sits one column right of its deepest dependency, parallel stages share a column, a cycle does not loop (`Processes.test.tsx`) | Auto, Vitest |
| T-M14-2 | The screen draws every stage and every dependency, lists the checklists with heading lines, shows the Mermaid source and a link that names the third-party site, switches to the Hebrew names, and is hidden without `process.read` | Auto, Vitest |
| T-M14-3 | Real browser: all 18 seeded definitions draw; the reference process has 10 stages, at least 9 dependencies and stages that share a column; the Mermaid source and link are present (`web/e2e/processes.spec.ts`) | Auto, Playwright |
| T-M14-4 | Every path the screen calls is served by the back end (`test_web_contract.py`) | Auto |

Not covered: editing a definition in the UI (the API creates a new version; the editor is milestone M18).

### M15 Contacts for marketing (the role's Clients view in the mock)

| ID | Test | Type |
|---|---|---|
| T-M15-1 | The list shows contact details and consent per channel, says what is not shown, and never calls the full client API (`Contacts.test.tsx`) | Auto, Vitest |
| T-M15-2 | Filter by name in any script and by who opted in to a channel; the tab belongs to the role without `client.read` only | Auto, Vitest |
| T-M15-3 | The view reports "no consent" as `none`, which the screen must show as "No consent recorded" (the unit test first used null and missed this) | Auto, Vitest and Playwright |
| T-M15-4 | Real browser: an agent creates a client with a phone; marketing signs in, has no Clients tab, finds the contact with phone and consent, and gets 403 from the full client API (`web/e2e/marketing.spec.ts`) | Auto, Playwright |
| T-M15-5 | Marketing reads only the `marketing_contact` view (P-3, `test_clients.py`, `test_consent.py`) | Auto |

### M16 Inbox Lens (REQUIREMENTS section 8, rule 5)

| ID | Test | Type |
|---|---|---|
| T-M16-1 | Twenty items in full, the rest squashed to bars with their title as the label, the window stated ("1-20 of 45"), bars coloured by urgency (`Lens.test.tsx`) | Auto, Vitest |
| T-M16-2 | Click a bar to centre its item; arrow and page keys move the window and stop at both ends; a drag follows the pointer and stops on release; scrolling over the strip moves it by one | Auto, Vitest |
| T-M16-3 | A list of twenty or fewer is complete and shows no strip; the window stays inside the list (`clampStart`) | Auto, Vitest |
| T-M16-4 | Real browser: thirty new items, twenty in full, keys and a squashed bar move the window, the chosen item is shown in full (`web/e2e/lens.spec.ts`) | Auto, Playwright |

Not covered: the Lens on the client timeline and the renewals calendar (the spec lists them; only the Inbox has it), and the compact density preference (12 rows).

### M17 Working a process on the client file (REQUIREMENTS F17, first part)

| ID | Test | Type |
|---|---|---|
| T-M17-1 | The stage's role ticks an item: the call is `PUT .../checks/{idx}` with `{done: true}`, the box shows the server's answer, a locked stage's boxes are disabled, "Finish the stage" is disabled while an item is open (`ClientFile.test.tsx`) | Auto, Vitest |
| T-M17-2 | A user of another role sees the items disabled with the reason and has no finish button | Auto, Vitest |
| T-M17-3 | Finishing is offered once every item is ticked and calls `POST .../finalize` | Auto, Vitest |
| T-M17-4 | A refusal from the server (`four_eyes_violation`) is explained in words in the user's language and the view is reloaded | Auto, Vitest |
| T-M17-5 | Real browser: a manager starts "Exit to retirement planning" on a new client, ticks every item of two parallel stages and finishes them, the dependent stage unlocks only after both, and the work is still there after a reload (`web/e2e/process-run.spec.ts`) | Auto, Playwright |

Not covered: ticking items automatically from a document (not built, see F17), and the four-eyes refusal in a real browser (it needs two users on one stage; the backend rule is covered in M4).

### M18 Process definition editor with React Flow (REQUIREMENTS F17, second part). Planned

| ID | Test | Type |
|---|---|---|
| T-M18-1 | Definition to graph and back: `toGraph` and `fromGraph` round-trip every one of the 18 seeded definitions to an identical document | Auto, Vitest |
| T-M18-2 | A connection that makes a cycle or a self-loop is refused with the reason; a valid one adds `depends_on`; deleting an edge removes it | Auto, Vitest |
| T-M18-3 | Stage and checklist editing: add, rename, delete a stage, change role and four-eyes, add, reorder and remove items and headings; a stage without a tickable item cannot be published | Auto, Vitest |
| T-M18-4 | The preview diff names added, removed and moved stages and items, and says how many running cases keep the old version | Auto, Vitest |
| T-M18-5 | The form view does everything the canvas does, with the keyboard alone (no pointer events in the test) | Auto, Vitest |
| T-M18-6 | Server refusals (422 for unknown or later stage) are shown against the stage; a stage id cannot be changed after the first publish | Auto, Vitest |
| T-M18-7 | The editor loads lazily: the main bundle does not contain the library (build output check) | Auto, build script |
| T-M18-8 | Real browser: a manager opens a definition, adds a stage, connects it, edits its checklist, publishes, and a new case on a client uses the new version while a running case keeps the old (`web/e2e/process-editor.spec.ts`) | Auto, Playwright |
| T-M18-9 | Right-to-left: the editor in Hebrew keeps the canvas left-to-right and the panels right-to-left; the library's attribution is visible | Auto, Playwright |
| T-M18-10 | Dependency pin: `package.json` and the lock file name an exact version of `@xyflow/react`; `make pins` passes | Auto, existing pin check |

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
| P-21 | User administration abuse: a non-manager reaches reset, deactivate, role change or set-password; a manager gives themselves a role by over-posting; the last manager removed | Denied, audited, the last manager kept |
| P-22 | Session after deactivation or reset: reuse a cookie taken before the change | Rejected on the next request |
| P-23 | Account takeover through password change: wrong current password, replayed code, CSRF | Refused, counted as a failure |

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
| Web slice 1 (front end port, decision D7) | 2026-11-06 | 13 unit tests with a fake API (sign in, wrong password and lockout messages without detail, role-dependent tabs, language and direction and theme remembered, sign out sends the CSRF token, Inbox counts and urgency, filter, done, dismiss and reassign with required text, load more, empty and error states, client search with transliteration shown only when different) and one **real-browser Playwright test against the live stack** (wrong then right password with a real authenticator code, items created through the browser session, filter, done, dismiss with reason, Hebrew to English with direction flip, reload keeps the session, sign out); conformance and smoke still pass on the rebuilt image | 0 | Findings: the form relied on the browser to block a too-short reason (the app now enforces the server's 3-character rule itself); `npm audit` flagged 13 dev-tooling vulnerabilities (none in production dependencies) and the audit gate stays at `high`, so the test toolchain was upgraded (Vitest 4, Vite 8, React plugin 6) and `npm ci` verified from a clean install. Scope: login, shell, Inbox, Clients list only; see docs/MOCK-PORT.md for what is not ported yet |
| Predictable deployment (owner decision: everything pinned) | 2026-11-06 | 23 tests for `scripts/check_pins.py` (the repository passes; each kind of unpinned input is caught by a deliberately bad fixture: base image without digest, apk/apt/pip package without version, `npm install`, `git clone` or `git fetch` of a moving ref, `@latest`, curl piped to a shell, `ADD` from a URL, `uv sync` without --frozen, a non-commit `*_COMMIT`, compose image without digest, workflow action without SHA, floating Node, Terraform or uv versions, `*-latest` runner, test images without digest); the whole stack rebuilt from the pinned inputs (Debian packages from a dated snapshot, Alpine packages at exact versions, MinIO and mc fetched by commit with the commit verified after checkout) and re-verified: smoke, conformance, backup and restore self-test, both browser tests; Terraform: exact version, provider lock for four platforms, image tag must be a git SHA (4 tests refuse `latest`, `v1`, `main` and empty) | 0 | Findings: the AWS image tag variable defaulted to `latest` (now no default, and validated as a commit SHA); the checker flagged its own deliberately bad test fixtures (skipped); GitHub runner and uv versions were floating (pinned). Known limits: the MinIO binary reports `DEVELOPMENT.GOGET` as its version because upstream's build injects the version at release time (the commit is pinned and verified instead); Alpine and Debian pins can disappear from their repositories over time, which makes the build fail loudly rather than drift (the update procedure is in OPERATIONS.md); the slow CI job installs Tesseract from the runner's Ubuntu packages (version 5.3 vs 5.5 in the image), so the OCR accuracy number is measured against the host version |
| Web slice 2: client file | 2026-11-06 | 8 unit tests (details with transliteration only when it differs, policies, scan status per document and download offered only for clean files, upload with chosen type and CSRF, role-limited controls, consent per channel recorded only with a source, process pipeline and start, error state) and one real-browser test on the live stack (open a client, real upload scanned by ClamAV and downloaded back byte for byte, consent opt-in and opt-out, start a process and see two parallel ready stages and two locked) | 0 | 21 web unit tests and 2 browser tests in total |
| M8 | 2026-11-06 | WhatsApp 56 tests (signature, consent gate at queue time and again at delivery, opt-out words, retries, status callbacks), mailbox 13, agency sync 23 (dry run, existing system wins, blanks only filled, file drop cursor, migrations 0008-0010 up/down/up), Excel join and export 26 (the mock's three-sheet example: full ID, last 4 plus Latin name, ID without a name; Hebrew and English headers; leading zeros lost by Excel; ambiguity reported, never guessed; roles; audit) | 0 | Findings: a test hashed two different messages (random boundary); `sync_state.cursor` was too short for a file-drop cursor (now text); a test fixture had a wrong last 4 for the ambiguity case; Excel drops leading zeros so IDs of 5 to 9 digits are restored to 9 (shorter values are not treated as IDs). Limits: **only fakes** (no Meta, mailbox or agency system access), so provider behaviour and rate limits are unverified; real webhook exposure on AWS needs a documented firewall exception |
| M9 | 2026-11-07 | 24 flow tests (544 fast tests in total): e-signature (signed form filed and audited, forged and replayed webhooks, declined, provider outage gives 502 and nothing recorded, foreign or unscanned documents refused); F1 acceptance (no activation without registered ID, KYC, signed mandate and data-lookup consent; flagged cases need the manager; a changed form needs a new decision; expired consent does not count); F5 (no lookup without all three valid authorizations, expiry flag within six months, re-sign); F2 (no policy without advice, signed application and renewal date; checklist); F3 (queue stages, agent scope, required outcomes, cancellation to the manager, leaks, idempotent daily tick); F4 (owner, timeline, completion percent, late reminders, complaint on unhappy rejection); visibility and roles | 0 | Findings: the inbox `done_today` counter used the local date with a UTC zone, so it was wrong for two hours every night in a timezone ahead of UTC (fixed; an existing test caught it the first night it ran at that hour); the e-signature webhook needed an uploader for the filed document, so the user who sent the request is recorded. Limits: **fake e-signature provider only**; the portfolio request to Mislaka and Har HaBituach is gated and audited but not sent; renewal and claim reminders run in the daily job and were tested by calling it with chosen dates, not by waiting for the clock; the 12-month authorization validity and the legal texts are marked to verify |
| M10 | 2026-11-07 | Campaigns 18 tests (consent hard stop, frequency cap, approval, translation review, throttled batches, ad label, replies, results), manager 9 (complaints and SLA, compliance, Overview, reconciliation of every line class, totals tie to the agora, approval and payout, Excel import), tax, translation and client view 11 (certificates through the consent gate, residency gaps, deadlines, accountant package and role limits, translation flags, client view isolation, link expiry, revocation and identical failures) | 0 | Findings: a campaign unsubscribe count missed recipients blocked at send time (now counted); the migration needed a named foreign key to downgrade; an uploaded test file with only the EICAR string is rejected as an unknown media type before scanning (test now embeds it in a PDF). Limits: **no email or SMS delivery adapter** (those messages are queued), **no real translation or e-signature provider**, no speech translation, the existing-customer exception to the spam law is not encoded (opt-in always required), the do-not-call register is not checked, the client view has no web page yet, commission expectation is premium x rate (assumption), tax rules are not encoded |
| Slow suite after M10 | 2026-11-07 | All 55 slow tests pass against the schema at migration 0014: real OCR accuracy, Terraform plans, real ClamAV, disaster recovery, point-in-time recovery, upgrade and rollback, observability | 0 | Run once after M10 on the host; 613 backend tests in total (558 fast + 55 slow) and 35 web unit tests |
| Web slices 3 to 5 | 2026-11-07 | 45 web unit tests (all screens above with a fake API, including role-limited controls, server reasons shown, the client's page reading its token from the URL fragment and failing without saying why) and 4 real-browser tests on the rebuilt stack: manager walk (Overview, audit verification, complaint logged and answered, campaign audience preview, tax certificate list and request, client page through a real link and with a bad one) and an agent's Today | 0 | Finding: a newly created client is a lead and correctly receives no campaign until registered, so the browser test registers it first. Limits: screens for Pension, WhatsApp, Live call, Excel join, Integrations and Settings do not exist; no Arabic, Russian or French catalogs; no side-by-side review against the mock |
| Web slice 6 and language catalogs | 2026-11-07 | 2 backend tests (client conversation in both directions with a blocked send, role limits; integration status without secrets) and 10 web tests (language selector switching Hebrew, English, Arabic, Russian and French with direction and a remembered choice; the client's page in the client's language; conversation and consent-block reason; authorization states and the lookup refusal reason; Integrations highlights; Settings; Excel join sending the CSRF token and downloading); the 4 browser tests re-run on the rebuilt stack | 0 | Finding: a browser test matched the role label by text and broke when other tests' Inbox items contained the same word (now scoped to the header). Limits: Arabic, Russian and French strings are my translations, not reviewed by a native speaker; Live call has no screen; no side-by-side review against the mock |
| Web slice 7 and e-mail delivery | 2026-11-07 | 6 web tests (translation saved only for a non-base language, results with exclusions listed and delivery counts, no results for a draft, tax dates marked near and added and done, residency gaps recorded, the accountant read-only) and 7 backend tests for e-mail: the SMTP client against a local SMTP server (headers, List-Unsubscribe, permanent versus temporary refusal, server down), delivery (sent and recorded, label in the subject and not doubled, consent re-checked, no address fails with a task, outage retries and an unconfigured sender waits, queue job end to end) | 0 | Findings: smtplib raises one error type for a refused recipient whether the refusal is temporary (4xx) or final (5xx), so the code reads the per-recipient codes; the tax certificate send queued messages without a delivery job (fixed); an older test expected e-mail to be ignored by the worker (now SMS). Limits: tested against a local SMTP stub, not a real provider (bounces, SPF and DKIM are the mail domain's job); SMS has no adapter |
| Commit before response (found by the browser tests) | 2026-11-07 | 1 backend test that watches the order of events and fails if the database commit happens after the first byte of the response (verified to fail without the fix); 568 fast backend tests; the 4 browser tests run 4 times in a row on the rebuilt stack | 0 | **Defect found and fixed**: the request's database transaction was committed after the response was sent, so a fast follow-up request could miss the write. The web app's check of the new session right after sign-in failed intermittently (about one browser run in two) with the sign-in error. All 121 routes now commit before the response (`Depends(get_db, scope="function")`). The slow suite was not re-run after this change |
| Last screens and mock review | 2026-11-07 | 1 backend test (user list for choosing an owner: no secrets, manager only) and 7 web tests (owner choice sent with the CSRF token and hidden from agents who never fetch the user list, statement upload enabled only when complete and sending the fields, server reason shown, client link created with a new-tab preview, hidden without the right, error shown); tab sets per role compared mechanically between the mock and the running app | 0 | Findings: the structural comparison found one missing function (a way to create the client's link) which was added; it also lists gaps that remain (Live call, marketing WhatsApp and preferences, Mislaka portfolio view). Limits: this is not a visual, copy or usability review and nobody at the agency has seen it |
| Live call panel | 2026-11-07 | 4 web tests (agents only; caller line translated with flags, notice and the client id sent; agent reply translated the other way and no server call when both languages are the same; provider failure shown and the text kept) | 0 | Limits: no speech recognition, so this is not a replacement for live subtitles; uses the fake translation provider in tests and the demo profile |
| M11 | 2026-11-07 | T-M11-1..9: 17 backend tests (roles and name, role change on the next request, deactivate and reactivate, authenticator reset, set password, own password change with wrong password and wrong code counted, last manager protected, only `user.manage`, SSO users, over-posting ignored, no secret in the audit or the list) and the web-contract test; 587 fast backend tests; 83 web unit tests (T-M11-10) | 0 | Defect found by the contract test: the Users page called `/api/auth/users` (route is `/api/users`). P-21 to P-23 are covered by the role, session and own-password tests above, not yet by the scripted suite. The Playwright test `users.spec.ts` passes on the rebuilt live stack (2026-11-07) |
| M12 | 2026-11-07 | T-M12-1..6: 8 backend tests (chips and date order, delegation, refusals, meetings and the service contact date, scope per role, overdue Inbox item once) and 4 web tests; 596 fast backend tests; 87 web unit tests | 0 | Migration 0015 (tables `task` and `meeting`, capability `task.write`). Browser test for these screens: `agent-day.spec.ts` (see M13). The slow suite was not re-run after M12 |
| M13 | 2026-11-07 | T-M13-1..5: 6 backend tests (digits comparison, warning not refusal, nothing leaks across agents, duplicate ID still refused, leads list scope, roles), 4 web tests, and the browser test `agent-day.spec.ts`; all 6 Playwright tests pass on the live stack rebuilt with `make up` (schema 0015); backend fast suite result in STATUS.md | 0 | The existing Today browser test needed a narrower locator because a new client is now also a lead on Today |
| M14 | 2026-11-07 | T-M14-1..4: 6 web tests (96 in total), the path-contract test, and `processes.spec.ts`; all 7 Playwright tests pass on the live stack rebuilt with `make up` | 0 | The diagram is drawn in the browser from the definition (no diagram library added, so nothing new to pin); the Mermaid source can be opened in Mermaid Live only by following a link that says so. First browser run read the dropdown before the definitions loaded (test race, fixed) |
| M15 | 2026-11-07 | T-M15-1..5: 3 web tests (99 in total), `marketing.spec.ts`, and the existing backend tests of the marketing view; all 8 Playwright tests pass on the live stack rebuilt with `make up` | 0 | Defect found by the first browser run: the back end sends `none` for no consent, the screen looked up a translation for it and showed blanks; the unit test had used null. Fixed, and the unit test now uses the real value |
| M16 | 2026-11-07 | T-M16-1..4: 6 web tests (105 in total) and `lens.spec.ts`; all 9 Playwright tests pass on the live stack rebuilt with `make up`. The whole slow suite was run after M15: 50 passed and 5 disaster-recovery tests errored because the restore builds the web image with `tsc` over the whole of `src` and my working tree briefly had a type error in a new test file; after the fix the 5 disaster-recovery tests passed (3 min), so all 55 slow tests pass | 0 | The Lens made the old Inbox browser test wrong in two ways that are now handled in the test: the live manager inbox holds more than one page (50) and more than the twenty-item window. A second finding: a web type error in a test file breaks the restore of a backup, because the restore builds the web image |
| M17 | 2026-11-08 | T-M17-1..5: 4 new web tests (109 in total) and `process-run.spec.ts`; all 10 Playwright tests pass on the live stack rebuilt with `make up` (the Inbox test was made robust against open items left by earlier runs) |
