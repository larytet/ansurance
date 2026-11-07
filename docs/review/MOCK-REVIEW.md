# Review of the web app against the mock (2026-11-07)

Method: the tab set of every role was read from the mock (`index.html`, English) and from the
running app, and the two were compared against REQUIREMENTS section 2 (tab sets per role).
Screenshots of both are in this folder (`mock-<role>.png`, `app-<role>.png`). This is a
structural review of what each role can reach and where it lives. It is **not** a pixel or
copy review: the app has its own layout, and nobody from the agency has looked at it next to
the mock yet.

## Tabs per role

| Role | Mock | App | Difference |
|---|---|---|---|
| Agent | Today, Processes, Inbox, Client file, WhatsApp, Live call, Pension, Client app preview, Clients, Settings | Today, Inbox, My work, Processes, Clients, Campaigns, Complaints, Settings | WhatsApp, Pension and the client preview are **sections of the client file** instead of tabs (the conversation belongs to one client). **Live call is a text panel** (lines are typed or pasted; no speech recognition, which needs a vendor). Campaigns and Complaints are extra: REQUIREMENTS F10 lets agents write campaigns and F8 lets anyone log a complaint, but the mock's agent has neither |
| Back-office | Desk, Processes, Inbox, Client file, Intake, Pension, WhatsApp, Excel, Integrations, Clients, Settings | Inbox, Desk, Scans, My work, Processes, Clients, Complaints, Commissions, Tax, Excel, Integrations, Settings | Same pattern for Pension and WhatsApp. Commissions, Tax and Complaints are extra (F7, F15, F8 have no tab in the mock) |
| Marketing | Marketing, Processes, Inbox, WhatsApp, Excel, Clients, Settings | Inbox, My work, Processes, Contacts, Campaigns, Excel, Settings | **No WhatsApp tab** (marketing sees replies and opt-outs through the Inbox, not a conversation view). **No Clients (preferences) tab**: marketing has a Contacts tab with the restricted contact list instead (details, language, consent) |
| Manager | Overview, Processes, Inbox, Desk, Marketing, Client file, Client app preview, Integrations, Trust, Clients, Settings | Overview, Inbox, Desk, Scans, My work, Clients, Campaigns, Complaints, Commissions, Tax, Excel, Integrations, Settings | Trust is a block on the Overview, not a tab. Client preview is a button on the client file. Extra tabs as above |

## Screens

| Mock screen | App | Gap |
|---|---|---|
| Today | ported, with tasks and leads | no claims or birthdays list yet (the data model has no birth date) |
| Processes | "My work" list, Processes tab with the diagram, pipeline on the client file | the definition editor is not ported |
| Inbox | ported | the Lens and "open in work area" jump are not ported |
| Client file | ported | tasks and meetings are built; no add-sheets for activities, employment or related clients (APIs do not exist) |
| WhatsApp | on the client file | no inline translation of incoming messages |
| Live call | text panel with translation, flags and a stated limit | no speech recognition or live subtitles from audio |
| Pension (Mislaka) | authorizations and lookup request on the client file | the portfolio view needs the clearing-house adapter |
| Client app preview | button on the client file creating the link, and the client's own page | the page is plain; it has no per-product detail tabs |
| Intake | ported (review and unassigned queues) | wrong-client correction and the metrics view are not ported |
| Excel (join) | ported | |
| Integrations | ported | no settings editing; configuration is by environment |
| Trust (audit) | verification button on the Overview | no browsing of audit entries |
| System settings | account, language, theme, permissions | no role switcher (the app uses real roles) |
| Languages | he, en, ar, ru, fr, with RTL for he and ar | ar, ru and fr strings are my translations and need a native-speaker review |

## What this means

The reachable functions match the requirements per role. Differences are mostly placement
(sections of the client file instead of tabs) and a set of tabs the mock never had because the
mock predates F7, F8 and F15. Open items: speech recognition for Live call, the marketing role's WhatsApp and
preferences views, and the review of copy and layout with the agency, which this document
cannot replace.
