# Porting the mock to the real web app

The mock (`index.html`) stays as the visual and behavioural reference. Each screen is ported
to the React and TypeScript app in `web/`, backed by the real API, and checked against the
mock. States: **not started**, **ported** (works against the API, unit and browser tests),
**matches mock** (side-by-side review done).

| Mock screen | Roles | State | Notes |
|---|---|---|---|
| Sign in (not in the mock) | all | ported | Local login with authenticator or recovery code; SSO button to follow |
| App shell: tabs per role, language (he, en, ar, ru, fr with RTL for he and ar), theme, sign out | all | ported | catalogs for ar, ru and fr are my translations and need a native-speaker review; "follow the system" theme and the role switcher still to add |
| Inbox | all | ported | counts, urgency filter, load more, done, dismiss with reason, reassign with note; the Lens and the "Open in work area" jump still to add |
| Clients (search with transliteration) | agent, back-office, manager | ported (list) | multi-select, preferences editing, Lens still to add |
| Client file | agent, back-office, manager | ported | details, policies, documents (upload, download for clean files only, scan status, restricted), consent per channel with source, process pipeline and start; 8 unit tests and a real-browser test (real upload and virus scan) |
| Today (agent home) | agent | ported (renewals due with stage and urgency, Inbox counts, open the client) | Inbox preview, tasks and claims still to add |
| Desk (back-office home) | back-office, manager | ported (work areas with counts and urgent marks) | opening an area's items still to add |
| Intake review (fax, email) | back-office | ported (review and unassigned queues, candidates with evidence, weak-match warning, confirm to a chosen candidate) | wrong-client correction, client picker for items with no candidate, and the metrics view still to add |
| Processes: pipeline, My work, diagrams | all | ported (My work list) | pipeline per client is on the client file; diagrams and the definition editor still to add |
| Campaigns | marketing, manager, agent | ported (create, preview with exclusions, approve, start, server reason shown) | translation review, audience snapshot and results screens still to add; consent registry is on the client file |
| Manager overview, Trust (audit verify) | manager | ported | ten counts with attention marks, chain verification; compliance, complaints and commission screens still to add |
| Complaints, Compliance, Commissions, Tax, Client app (the client's page at `/portal#token`) | manager, back-office, agent, accountant, client | ported | complaint assignment, statement file upload, tax residency and deadlines, translation review still to add |
| Pension (authorizations and lookup request, on the client file) | agent, back-office, manager | ported | the portfolio view itself needs the clearing-house adapter, which does not exist |
| WhatsApp (conversation and send on the client file, with the consent gate's reason shown) | agent, back-office, manager | ported | inline translation of incoming messages still to add |
| Excel join | back-office, marketing, manager | ported (upload, join, download, summary) | |
| Integrations (state, calls in 24 hours, last error, syncs) | manager, back-office | ported | |
| System settings (account, language, theme, permissions) | all | ported | editing roles and approvals is by command line and API |
| Live call | agent | not started | needs a speech-translation vendor |

Slices 3 to 5 (Overview, Trust, Compliance, My work, Campaigns, Desk, Scans, Today, Complaints, Commissions, Tax, the client's page) have 24 unit tests and 2 real-browser tests on the live stack with a fake API and no real-browser test yet.

Tests: `npm test` (unit, fake API) and `make e2e-web` (Playwright against the running stack).
