# ansurance

Custom CRM for a small Israeli insurance agency (5 agents, 2 back-office, 1 manager).

- Requirements: [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md)
- Clickable Inbox mock (fictional data, no backend). Besides the Inbox it has simulated integration tabs: integrations overview with a Surense/Atid and Salesforce coexistence example, an Excel tab that searches three spreadsheets by name or a few ID digits, joins them and exports one report, fax/email intake, WhatsApp with a consent gate, live call subtitles, and a pension clearing house request: [index.html](index.html). Open it locally, or enable GitHub Pages (Settings > Pages > Deploy from a branch > `main` / root) and it is served at https://larytet.github.io/ansurance/

Highlights: a role-specific Inbox for Agent, Back-office and Manager; automatic digitizing, classification and portfolio attribution of incoming fax, email and WhatsApp (an ID scan goes straight to the folder of the ID's owner); a "Notify client" channel for targeted, consent-checked upsell and regulatory notices; coexistence with existing Israeli agency systems; real-time Hebrew/Arabic/Russian/French translation with subtitles, including phone calls; light and dark theme, language switcher, role switcher, "Show me" tour and a "Lens" for long lists.
