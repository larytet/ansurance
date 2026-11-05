# ansurance

Custom CRM for a small Israeli insurance agency (5 agents, 2 back-office, 1 marketing, 1 manager).

- Requirements: [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md)
- Clickable Inbox mock (fictional data, no backend). Besides the Inbox it has simulated integration tabs: integrations overview with a Surense/Atid and Salesforce coexistence example, a Clients tab (preferred language per client), a System settings tab (width, theme, density, time zone), an Excel tab that searches three spreadsheets by name or a few ID digits, joins them and exports one report, fax/email intake, WhatsApp with a consent gate, live call subtitles, and a pension clearing house request: [index.html](index.html). Open it locally, or enable GitHub Pages (Settings > Pages > Deploy from a branch > `main` / root) and it is served at https://larytet.github.io/ansurance/

Processes: every flow runs as a pipeline of stages with checklists (locked or parallel order, finalized by the owner role, complete when all stages are final), with an editable diagram per process. See [docs/PROCESSES.md](docs/PROCESSES.md) and the Processes tab in the mock. The reference process is Exit to retirement planning.

Highlights: four roles (Agent, Back-office, Marketing, Manager), each with its own home screen, tabs and Inbox; the back-office Desk (get the ID and register, fill templates, post/email/labels, answer other insurers, tax work); a client file, a client app preview and light theme by default; automatic digitizing, classification and portfolio attribution of incoming fax, email and WhatsApp (an ID scan goes straight to the folder of the ID's owner); a "Notify client" channel for targeted, consent-checked upsell and regulatory notices; coexistence with existing Israeli agency systems; real-time Hebrew/Arabic/Russian/French translation with subtitles, including phone calls; light and dark theme, language switcher, role switcher, "Show me" tour and a "Lens" for long lists.
