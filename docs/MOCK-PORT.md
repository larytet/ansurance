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
| Client file | agent, back-office, manager | not started | client, policies, documents, consent, process banners |
| Today (agent home) | agent | not started | |
| Desk (back-office home) | back-office, manager | not started | API: `/api/desk` |
| Intake review (fax, email) | back-office | not started | API: `/api/intake` |
| Processes: pipeline, My work, diagrams | all | not started | API: `/api/processes`, `/api/my-work` |
| Campaigns, consent registry | marketing, manager | not started | |
| Manager overview, Trust (audit verify) | manager | not started | API: `/api/audit/verify` |
| Pension, WhatsApp, Live call, Excel join, Integrations, Client app preview, System settings | various | not started | need M8 and later |

Tests: `npm test` (unit, fake API) and `make e2e-web` (Playwright against the running stack).
