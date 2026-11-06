# Porting the mock to the real web app

The mock (`index.html`) stays as the visual and behavioural reference. Each screen is ported
to the React and TypeScript app in `web/`, backed by the real API, and checked against the
mock. States: **not started**, **ported** (works against the API, unit and browser tests),
**matches mock** (side-by-side review done).

| Mock screen | Roles | State | Notes |
|---|---|---|---|
| Sign in (not in the mock) | all | ported | Local login with authenticator or recovery code; SSO button to follow |
| App shell: tabs per role, language (he/en, RTL), theme, sign out | all | ported | Arabic, Russian and French catalogs still to add; "follow the system" theme and the role switcher still to add |
| Inbox | all | ported | counts, urgency filter, load more, done, dismiss with reason, reassign with note; the Lens and the "Open in work area" jump still to add |
| Clients (search with transliteration) | agent, back-office, manager | ported (list) | multi-select, preferences editing, Lens still to add |
| Client file | agent, back-office, manager | ported | details, policies, documents (upload, download for clean files only, scan status, restricted), consent per channel with source, process pipeline and start; 8 unit tests and a real-browser test (real upload and virus scan) |
| Today (agent home) | agent | not started | |
| Desk (back-office home) | back-office, manager | ported (work areas with counts and urgent marks) | opening an area's items still to add |
| Intake review (fax, email) | back-office | ported (review and unassigned queues, candidates with evidence, weak-match warning, confirm to a chosen candidate) | wrong-client correction, client picker for items with no candidate, and the metrics view still to add |
| Processes: pipeline, My work, diagrams | all | ported (My work list) | pipeline per client is on the client file; diagrams and the definition editor still to add |
| Campaigns | marketing, manager, agent | ported (create, preview with exclusions, approve, start, server reason shown) | translation review, audience snapshot and results screens still to add; consent registry is on the client file |
| Manager overview, Trust (audit verify) | manager | ported | ten counts with attention marks, chain verification; compliance, complaints and commission screens still to add |
| Pension, WhatsApp, Live call, Excel join, Integrations, Client app preview, System settings | various | not started | need M8 and later |

Slices 3 and 4 (Overview, Trust, My work, Campaigns, Desk, Scans) have 14 unit tests with a fake API and no real-browser test yet.

Tests: `npm test` (unit, fake API) and `make e2e-web` (Playwright against the running stack).
