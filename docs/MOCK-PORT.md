# Porting the mock to the real web app

A structural comparison of tabs per role and screens is in [review/MOCK-REVIEW.md](review/MOCK-REVIEW.md) (with screenshots). It is not a visual or copy review.

The mock (`index.html`) stays as the visual and behavioural reference. Each screen is ported
to the React and TypeScript app in `web/`, backed by the real API, and checked against the
mock. States: **not started**, **ported** (works against the API, unit and browser tests),
**matches mock** (side-by-side review done).

| Mock screen | Roles | State | Notes |
|---|---|---|---|
| Sign in (not in the mock) | all | ported | Local login with authenticator or recovery code; SSO button to follow |
| App shell: tabs per role, language (he, en, ar, ru, fr with RTL for he and ar), theme, sign out | all | ported | catalogs for ar, ru and fr are my translations and need a native-speaker review; "follow the system" theme and the role switcher still to add |
| Inbox | all | ported | counts, urgency filter, load more, done, dismiss with reason, reassign with note; the "Open in work area" jump still to add; the Lens is ported (twenty items in full, the rest squashed) |
| Clients (search with transliteration, New client or lead with the phone warning) | agent, back-office, manager | ported (list and create) | multi-select, preferences editing, Lens still to add |
| Client file | agent, back-office, manager | ported | meetings with summary and the service contact date (from the roles with `task.write`); details, policies, documents (upload, download for clean files only, scan status, restricted), consent per channel with source, process pipeline and start; 8 unit tests and a real-browser test (real upload and virus scan) |
| Today (agent home) | agent | ported (renewals due with stage and urgency, Inbox counts, open the client, tasks with the five chips, add, tick done, delegate) | Inbox preview, claims and birthdays still to add (leads are listed) |
| Desk (back-office home) | back-office, manager | ported (work areas with counts and urgent marks) | opening an area's items still to add |
| Intake review (fax, email) | back-office | ported (review and unassigned queues, candidates with evidence, weak-match warning, confirm to a chosen candidate) | wrong-client correction, client picker for items with no candidate, and the metrics view still to add |
| Processes: pipeline, My work, diagrams | all | ported (My work list, Processes tab with the pipeline diagram, checklists and Mermaid source) | pipeline per client is on the client file, with checklists to tick and stages to finish (M17); the definition editor on React Flow is built (M18) |
| Campaigns | marketing, manager, agent | ported (create, preview with exclusions, approve, start, server reason shown) | translation review (per language, saved as reviewed by a person) and results with the exclusions are ported; consent registry is on the client file |
| Manager overview, Trust (audit verify) | manager | ported | ten counts with attention marks, chain verification; compliance, complaints and commission screens still to add |
| Complaints, Compliance, Commissions, Tax, Client app (the client's page at `/portal#token`) | manager, back-office, agent, accountant, client | ported | complaint assignment (manager chooses an owner from the users) and statement file upload are ported; tax deadlines and residency are ported |
| Pension (authorizations and lookup request, on the client file) | agent, back-office, manager | ported | the portfolio view itself needs the clearing-house adapter, which does not exist |
| WhatsApp (conversation and send on the client file, with the consent gate's reason shown) | agent, back-office, manager | ported | inline translation of incoming messages still to add |
| Excel join | back-office, marketing, manager | ported (upload, join, download, summary) | |
| Integrations (state, calls in 24 hours, last error, syncs) | manager, back-office | ported | |
| System settings (account, language, theme, permissions, change your own password) | all | ported | a user changes their own password with the current password and a code (local users only) |
| Users (not in the mock; F16) | manager | ported | add a user with the authenticator setup shown once; edit roles; reset the authenticator; set a password; deactivate and reactivate, each with a confirmation |
| Live call | agent | ported as a text panel (typed or pasted lines translated, both texts kept in the client timeline, risky passages flagged) | **no speech recognition**: live subtitles from audio need a speech vendor |

Slices 3 to 7 (Overview, Trust, Compliance, My work, Campaigns, Desk, Scans, Today, Complaints, Commissions, Tax, the client's page) have 24 unit tests and 2 real-browser tests on the live stack with a fake API and no real-browser test yet.

Tests: `npm test` (unit, fake API) and `make e2e-web` (Playwright against the running stack).
