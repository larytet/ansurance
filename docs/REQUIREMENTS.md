# ansurance - Requirements Specification

Custom CRM for a small Israeli insurance agency.

- Status: draft v0.1 (2026-11-04)
- Team: 5 agents, 2 back-office clerks, 1 manager (8 users)
- Jurisdiction: Israel (Capital Market, Insurance and Savings Authority, "CMISA", Privacy Protection Law)

Items marked **[verify]** come from web research that was thin or contradictory and must be confirmed against the primary source (gov.il, the insurer, or counsel) before they become hard requirements.

## 1. Goals and non-goals

Goals:

1. One place for every client, policy, document, call and message.
2. Nothing that arrives (fax, email, WhatsApp, call) is lost or sits unassigned. It is digitized, classified and attached to the right client portfolio automatically.
3. Staff and clients can talk across Hebrew, Arabic, Russian and French in real time, including phone calls.
4. Every role starts the day in its own Inbox.
5. Regulatory duties (licensing, KYC/AML, complaints, privacy, record keeping) are built into the flows and not left to memory.

Non-goals (v1): insurer-side policy administration, claims adjudication, investment management (agents hold no Securities Authority licence, see Calcalist note in section 11), a client mobile app (WhatsApp is the client channel).

## 2. Roles and permissions

| Role | Count | Main job | Sees |
|---|---|---|---|
| Agent | 5 | Sell, advise, retain clients | Own clients and policies; team-shared clients by explicit share |
| Back-office | 2 | Issue, endorse, renew, file, reconcile | All clients (work queue); no commission totals per agent |
| Manager | 1 | Oversight, compliance, commissions, licences | Everything, including audit log and commission statements |

Rules:

- Role-based access plus per-client ownership (the agent of record).
- A role switcher in the UI (see section 8) is a view switch for demo and for the manager. For real users it only offers roles they actually hold.
- Every read of a client record is audit-logged (section 7).

## 3. Core flows

Each flow lists trigger, steps, actors, outputs and regulatory hooks.

### F1. New client onboarding with KYC/AML

- Trigger: inbound lead (phone, WhatsApp, email, walk-in) or referral.
- Steps:
  1. Agent creates the lead; duplicate check on ID number (9 digits, Luhn check digit validated) and phone.
  2. Collect ID card plus the paper addendum (Sefach), proof of address if needed. Client may send photos over WhatsApp or email; they route through the intake pipeline (F6).
  3. KYC form completed: identity, beneficiary, controlling shareholder for corporate clients, source of funds for large or unusual products.
  4. Client signs a mandate/representation letter and a consent for data lookup (section F5). Marketing consent (per channel, yes or no) is asked separately and stored in the consent registry, because F10 depends on it.
  5. Manager approval is required only for flagged cases (high risk, politically exposed, large single premium).
- Outputs: client record, KYC file, signed consents, audit trail.
- Regulatory hooks: the Prohibition on Money Laundering order for insurers and agents (identification, reporting and record keeping; the 2001 order is cited in the sources but its text was not retrieved) **[verify]**; Privacy Protection Law Amendment 13 (in force 14 Aug 2025): purpose-limited collection and notice at collection.
- Acceptance: a client cannot be moved to "active" without a validated ID number, a KYC form and signed consent. Unusual-transaction flag raises a manager task; suspicious reports are prepared as a draft for the manager and never auto-sent.

### F2. Quote, sale and policy issuance

- Trigger: client needs a new policy (car, home, health, life, business, travel).
- Steps:
  1. Agent captures needs analysis and records the advice given (suitability record).
  2. Agent requests quotes from insurers (manual entry in v1; insurer portal links stored per product).
  3. Client chooses; agent prepares the application.
  4. E-signature (section 9). Signed pack goes to back-office.
  5. Back-office submits to the insurer, records policy number, start date, premium schedule and commission terms.
  6. Policy PDF arrives (email or fax), is auto-classified (F6) and matched to the open application.
- Outputs: policy record, document set, renewal date, commission expectation.
- Acceptance: every policy has a linked advice record, signed application, insurer confirmation and renewal date. Missing items show as a checklist on the policy.

### F3. Renewal, endorsement and cancellation cycle

- Trigger: renewal date approaching (default T-60, T-30, T-10 days), mid-term change, cancellation request or lapse for non-payment.
- Steps:
  1. System builds the renewal queue and assigns each item to the agent of record.
  2. Agent reviews coverage and price against the market, contacts the client (WhatsApp first, voice if no reply).
  3. Back-office processes endorsement or renewal with the insurer.
  4. Cancellations capture the reason and are routed to the manager for retention follow-up.
- Outputs: renewed or lapsed policy, retention statistics.
- Acceptance: a policy cannot pass its renewal date without a recorded outcome (renewed, changed, cancelled, lapsed with reason). Manager sees the leaks list daily.

### F4. Claims handling

- Trigger: client reports an event (call, WhatsApp, email).
- Steps:
  1. Intake: agent or back-office opens a claim linked to the policy; date, place, description.
  2. Document checklist per product (police report, medical forms, photos, invoices). Client uploads over WhatsApp; items classify automatically.
  3. Claim submitted to the insurer; reference number recorded.
  4. Status tracking with reminders when the insurer is late. Client updates in their language (section 5).
  5. Outcome and payment recorded; dispute or rejection opens a complaint (F8) if the client is unhappy.
- Acceptance: each claim has a timeline, a document checklist with completion percent, and an owner at every moment.

### F5. Pension and savings portfolio review (Mislaka and Har HaBituach)

- Trigger: annual review, new client, job change, or client request.
- Steps:
  1. Client signs a data-lookup authorization form.
  2. Back-office sends an information request through the pension clearing house and a Har HaBituach request. Sources say institutions must answer within about 3 business days and the consumer fee is about 20-40 NIS **[verify]**.
  3. Results are parsed into the client portfolio: all policies, funds, premiums, coverage periods, management fees.
  4. Agent prepares a review: gaps, duplicates, fee comparison, recommendation.
  5. If the client moves a product, the agent mandate/appointment letter is generated and sent to the insurer (how the formal transfer works between agents was not confirmed) **[verify]**.
- Integration: Mislaka-API (third-party REST gateway for clearing house requests, Har HaBituach requests and the authorization form) or direct clearing-house connection **[verify]**.
- Acceptance: portfolio view shows external policies next to the agency's own, with a "last refreshed" stamp. No lookup runs without a valid signed authorization on file.

### F6. Automatic intake of fax, email and WhatsApp (digitize, classify, attribute)

This is the signature feature. See section 4 for detail.

- Trigger: any inbound item on the agency fax number, shared mailbox, personal mailboxes (opt-in) or WhatsApp number.
- Outcome: the item lands in the right client portfolio folder, tagged with a document type, with an unambiguous owner and a task if a human decision is needed.

### F7. Commission reconciliation and agency finance

- Trigger: monthly insurer commission statements.
- Steps:
  1. Back-office imports statements (PDF, Excel, or via insurer portal download).
  2. System matches lines to expected commissions from policy terms.
  3. Differences (missing, wrong rate, clawback) become tasks.
  4. Manager reviews totals per insurer and per agent and approves payout.
- Outputs: reconciliation report, agent payout statement, issue list to insurers.
- Acceptance: unmatched lines are never dropped. Totals tie to the statement to the agora.

### F8. Complaints and compliance monitoring

- Trigger: client complaint (any channel), regulator inquiry, or a licence/CPD deadline.
- Steps:
  1. Complaint is logged with timestamps, linked to the client and policy.
  2. Manager assigns an owner; deadlines tracked; response recorded.
  3. Compliance dashboard: agent licence status and expiry, required continuing education, KYC files expiring, unanswered complaints, privacy requests.
- Regulatory hooks: CMISA conduct expectations on complaint handling **[verify]** (no Israeli circular text was retrieved; check the consolidated circulars on gov.il); Privacy Protection Law data-subject requests (access, correction, deletion).
- Acceptance: a complaint older than the configured SLA turns red on the manager inbox; the log is exportable.

### F9. Live multilingual call

See section 5.

### F10. Notify client (targeted outreach and upsell channel)

- Trigger: a new financial product from an insurer or fund, a regulatory change, a market event, a policy anniversary, or an agent idea ("everyone who has X and not Y").
- Purpose: one controlled channel for telling clients something that matters to them, and only the clients it matters to. Doubles as the upsell channel, but every campaign needs a stated client benefit, so it stays advice and not spam.
- Steps:
  1. Campaign created by an agent or the manager from a template (type: regulatory update, product offer, protection gap, renewal nudge, life event).
  2. Audience builder: rule-based segment over client and portfolio data, with a live count and a sample list. Examples below. A client appears only if the rule matches and the consent check passes.
  3. Consent gate (automatic, not skippable): see below. Excluded clients are listed with the reason (no consent, opted out, do-not-call, sensitive case).
  4. Content: message written once in one language, translated per client preferred language (section 5), with human review of each translation before first send. Channel per client: WhatsApp (approved template), email, SMS; voice follow-up as an agent task.
  5. Manager approval for product-offer campaigns and anything that mentions regulation (accuracy check); agents may send regulatory-update notices from approved templates without approval.
  6. Send in batches with throttling; replies land in the agent-of-record Inbox as "client replied to campaign"; interested clients become tasks or leads (F2, F5).
  7. Results: delivered, read, replied, converted, unsubscribed, complaints; fed back to the segment so a client is not contacted twice in a short period (frequency cap, default one marketing contact per client per 30 days).
- Segment examples built from recent Israeli developments (verify each before use, see sources in section 11):
  - Early pension withdrawal rules (Capital Market Authority, reported July 2026, effective about three months later): notify clients aged 50+ holding pension or provident products and clients who recently asked about withdrawals. Content: new verification steps, risk of fraud and impersonation, tax cost of early withdrawal, "call us before you decide". This is a protective notice. An insurance agency was reported fined NIS 10.5 million in August 2026 for illegally encouraging early withdrawals, so this campaign must never push withdrawal and must be reviewed by the manager.
  - Managers' insurance restriction (in force since September 2023): employees below twice the average wage cannot newly join managers' insurance. Segment: clients who changed jobs in the last 12 months with salary under the threshold. Content: what vehicle to use instead (pension or provident fund) and a savings review.
  - Investment provident fund ceiling (about ILS 83,641 per year in 2026): segment: clients with an investment provident fund and recorded deposits near the ceiling, or no such fund and spare savings capacity. Product offer, needs-analysis first.
  - Savings for Every Child (National Insurance Institute deposits ILS 58 a month per child to age 18; parents can redirect payments to an investment house): segment: clients with children under 18 and no child-savings product. Content: options and comparison.
  - Pension tax exemption ceiling on monthly pension rising to ILS 5,422 in 2026: segment: retirees and clients within 5 years of retirement. Low urgency, informational.
  - Group health insurance framework approvals (draft circular reported by Bizportal): segment: business clients with group health policies. Informational, send only after the circular is final.
  - Protection gaps (agency-defined, no regulator news needed): clients with a mortgage and no life cover, car policy renewing within 60 days with a claim-free record (price review), self-employed clients with no disability cover, clients whose long-term care cover is only the health-fund default.
- Consent and law (Communications Law section 30A, the "spam law", in force since 2008 and amended in 2016; Privacy Protection Law Amendment 13):
  - Advertising by email, SMS, automated dialing or fax requires explicit advance written consent. WhatsApp is treated as covered in practice.
  - An existing-customer exception exists (contact details given when buying or negotiating a purchase, similar products, opt-out offered at collection) but its three conditions must be confirmed by counsel and encoded exactly **[verify]**. Until then, the system defaults to requiring recorded opt-in for any product offer.
  - Pure service or regulatory notices (for example a change that affects the client's existing policy) are not advertising and need no marketing consent, but must contain no sales content. The campaign type decides which rule applies, and the content check flags sales wording in a "service" campaign.
  - Advertising emails carry the advertisement label in the subject ("פרסומת"), sender identity and contact details, and a one-click unsubscribe that is honored immediately across all channels.
  - Exposure is up to ILS 1,000 per message without proof of damage, and officers can be personally liable, so the consent gate is a hard stop, not a warning. All consent records (when, how, wording, channel, source) are stored and exportable.
  - Check the national do-not-call register before voice campaigns **[verify]**.
- Outputs: campaign record, audience snapshot (who was selected and why), consent evidence per recipient, send log, replies, conversions.
- Acceptance: no message leaves without a passing consent check recorded on the recipient; every campaign keeps an immutable audience snapshot; an unsubscribe in any channel suppresses all marketing channels within one minute; the manager sees campaigns pending approval in the Inbox.

## 4. Automatic intake pipeline (fax, email, WhatsApp)

### 4.1 Channels

| Channel | Mechanism |
|---|---|
| Fax | Cloud fax number delivering TIFF/PDF by webhook or email (provider choice open; none were verified in research) |
| Email | Shared mailboxes plus opt-in personal mailboxes, via Microsoft 365 / Google Workspace APIs |
| WhatsApp | WhatsApp Business API (Meta Cloud API or a BSP) |
| Scan/upload | Drag and drop in the web app, mobile photo upload |

### 4.2 Stages

1. Capture: store the original immutably (hash, timestamp, channel, sender).
2. Digitize: de-skew, de-noise, split multi-page faxes into logical documents, run OCR for Hebrew, Arabic, Russian, French and English. Dot-matrix Hebrew and low-resolution fax are known weak spots, so keep a confidence score per field.
3. Classify: document type (ID card, ID addendum, driving licence, policy, application, claim form, medical report, invoice, bank form, power of attorney, insurer letter, commission statement, complaint, other). A rules plus model hybrid; every decision stores its confidence and reason.
4. Extract entities: ID number (validate with the check digit), policy number, name, phone, plate number, dates, amounts.
5. Attribute to a client portfolio:
   - ID number found: match to the client by ID number. Example: a scan of an ID card goes straight to the folder of the card's owner, whoever sent it and from whichever address.
   - Policy or claim number found: match to that policy and its client.
   - Sender address or phone matches a known contact: weak signal, used only to break ties.
   - Child or spouse ID found in an addendum: attach to the head-of-family portfolio and cross-link the family member.
6. Route by confidence:
   - High (default >= 0.95 and a single unambiguous match): file automatically, notify the agent of record in the Inbox as "filed".
   - Medium or multiple candidates: file to a Review queue, with a suggested match shown, for back-office one-click confirm.
   - Low or no match: Unassigned queue, back-office handles same day.
7. Learn: every human correction is stored as feedback to improve rules and model.

### 4.3 Requirements

- Never delete the original. Every derived artifact links to it.
- No document is filed into a client folder on a name-only match.
- Misfiled-document risk is a privacy incident: one-click "wrong client" action that moves the item, logs it, and counts toward a monthly misfile metric on the manager dashboard.
- Target: 90% of items auto-filed with under 1% misfiled (to be measured on the agency's own historical sample).
- Sensitive documents (medical) are filed with a restricted flag visible only to the agent of record, back-office and manager.

## 5. Real-time translation (Russian, Arabic, French, Hebrew)

### 5.1 Scope

Languages: Hebrew (he), Arabic (ar, with Israeli colloquial dialect awareness), Russian (ru), French (fr), any-to-any.

Surfaces:

1. Phone calls: live subtitles in the agent's chosen language shown in the call panel, plus the caller's side in the original language. Optional second-line spoken translation is phase 2.
2. WhatsApp and email: inline translation of incoming messages; outgoing drafts translated before sending, with the original kept.
3. Documents: translate on demand in the document viewer.
4. Face-to-face: a "subtitle" view that listens on the desk microphone (same engine).

### 5.2 Requirements

- Latency from speech to subtitle: target under 2 seconds p95.
- Speaker labels: Agent / Client.
- Call recording and transcripts are stored only with consent captured at call start (announcement text per language). Consent state is logged.
- A visible "machine translation, may contain errors" mark. Contract terms, claim statements and anything legally binding require a human check. The system flags such passages (amounts, dates, coverage words) for confirmation.
- Original and translation stored side by side in the client timeline.
- Glossary of insurance terms per language so that "deductible", "coverage", "exclusion" and similar words translate consistently.

### 5.3 Engine options (open decision)

| Option | Notes from research |
|---|---|
| Azure Speech | Real-time translation lists Hebrew, Arabic locales, Russian, French. Hebrew accuracy in one vendor benchmark was weak (25% WER, vendor claim) **[verify]** |
| OpenAI Realtime API / Whisper | Realtime API GA since 28 Aug 2025; Whisper itself is not streaming |
| Google Cloud | Streaming STT supports the four languages; speech-to-speech translation was an experimental preview only |
| Speechmatics | Dedicated Hebrew real-time STT |

Decision method: run the four on 20 real recorded agency calls (8 kHz telephony audio, with consent) and compare word error rate per language. Pick per language if needed, behind one internal interface.

## 6. Inbox (one per role)

Every role has an Inbox as the home screen. An Inbox item is anything that needs a human decision or action. Items carry type, client, age, urgency and a one-click primary action.

| Role | Typical items | Urgent rules |
|---|---|---|
| Agent | Renewals due, client replies, auto-filed documents for my clients, unsigned applications, callbacks, claim updates, birthday and policy-anniversary nudges, campaign replies | Renewal under 10 days, client waiting over 1 business day |
| Back-office | Review queue (medium-confidence documents), Unassigned queue, applications to submit, endorsements, claim document gaps, commission mismatches, campaign send failures | Item older than same-day SLA |
| Manager | Approvals (KYC flags, payouts), complaints over SLA, compliance expiries, misfile reports, team workload, retention leaks, campaigns awaiting approval | Any regulatory deadline within 7 days |

Behaviour:

- Filters by type and by status; counts such as "12 pending, 3 urgent, 9 done today".
- Urgent in red, important in amber.
- Nothing disappears silently: items leave the Inbox only by an action or an explicit dismiss with a reason.
- Items can be reassigned between roles (agent to back-office, back-office to manager) with a note.
- Inbox for a role is also viewable by the manager (read-only) for coverage during absence.
- Long inboxes use the Lens (section 8).

## 7. Non-functional requirements

- Privacy and security: Israeli Privacy Protection Law as amended by Amendment 13 (in force 14 Aug 2025) and the Data Security Regulations 5777-2017. Insurance data is sensitive, so confirm with counsel whether the agency must appoint a Privacy Protection Officer and notify or register its database **[verify]** (sources conflict on scope; insurers are cited as large-scale sensitive-data processors).
- Data residency: store data in Israel or an approved jurisdiction; confirm vendor regions for OCR, translation and WhatsApp BSP **[verify]**.
- Audit log: append-only, hash-chained (each entry includes the hash of the previous one) with a "verify chain" action for the manager. Logs reads, writes, exports, consent changes, AI actions and misfile corrections.
- Retention: configurable per document type; legal hold for open claims and complaints.
- Authentication: SSO with two-factor; session timeout; device list.
- Availability: business hours critical (07:00-19:00 Sun-Thu), nightly encrypted backup, restore tested quarterly.
- Hebrew/Arabic right-to-left layout throughout; Hebrew calendar and Israeli date format options; ILS currency; Israeli ID validation.
- Accessibility: Israeli standard 5568 (WCAG-based) **[verify]**.
- Performance: Inbox opens under 1 second for 10,000 items (virtualized list).
- Small team: operations must need no dedicated IT person; managed hosting.

## 8. UX principles

Borrowed from the praxis-demo project (larytet/praxis-demo, GitHub Pages demo):

1. **Light and dark theme**: follows the OS setting by default, manual toggle, remembered per user. Contrast checked in both.
2. **Language switcher**: UI in Hebrew, Arabic, Russian, French and English; remembered per user; right-to-left flips the whole layout. Document text stays in its original language, with translation shown next to it.
3. **Role switcher**: one click to view the app as Agent, Back-office or Manager (limited to roles the user holds; the manager can preview all). Makes demos and training easy.
4. **Show me**: a guided tour mode where the app moves the highlight from hint to hint on its own and opens the details in place, for onboarding and for explaining a flow to a new hire. Each flow F1 to F9 ships with a Show me script.
5. **Lens for long lists**: a draggable sliding window over a long list or timeline. Items inside the lens are shown in full; items outside are squashed into thin colored bars (red urgent, amber important). Drag the handle, scroll, use arrow keys, or click a bar to jump. Header shows the window position ("41-60 of 312"). Used on Inbox, client timeline and renewals calendar.
6. **Inbox-first home**: the first screen is always the user's Inbox with counts and urgency.
7. **Trust page**: shows the audit log, with the verify-chain button.
8. Plain language, one primary action per item, nothing touchable that does nothing: every element drills in or opens detail.

## 9. Integrations

Pick what Israeli agencies and their clients already use, not new tools to learn.

| Priority | Tool | Why | Use in ansurance |
|---|---|---|---|
| 1 | **WhatsApp Business API** (Meta Cloud API, or BSP such as Twilio, 360dialog or the Israeli platform Lynxbe) | The default way Israelis talk to businesses; clients already send photos of IDs and documents here | Two-way client messaging, document upload into F6, renewal reminders, claim status, translation inline. Templates must be pre-approved by Meta; clients must opt in |
| 1 | **Microsoft 365 or Google Workspace** (mail, calendar, contacts) | Agents already live in email and calendar | Mailbox ingestion for F6, appointment booking, calendar sync |
| 1 | **Pension clearing house and Har HaBituach**, directly or via **Mislaka-API** | Core to every portfolio review | F5 data pull |
| 2 | **Telephony**: Twilio or an Israeli SIP provider | Needed to tap call audio for translation and call logging | F9, click-to-call, screen pop by caller ID |
| 2 | **Speech and OCR**: Azure Speech or OpenAI Realtime or Speechmatics; Google Cloud Vision or Azure AI Vision Read | Section 4 and 5 engines | Translation and digitization |
| 2 | **Cloud fax provider** | Faxes still arrive from insurers and banks | F6 fax channel |
| 2 | **Green Invoice (now "morning")** or iCount | Widely used Israeli invoicing; supports sending invoices over WhatsApp | Agency invoices and fee receipts |
| 2 | **E-signature**: Comsign (Israeli) or DocuSign | Signed applications, mandates, consents | F1, F2, F5 |
| 3 | **monday.com** | Israeli-built and popular as a task board; some teams already use it | Optional two-way sync of tasks |
| 3 | **Zapier or Make** | Lets the agency connect anything else without code | Webhooks out, triggers in |
| 3 | **Insurer agent portals** | Policy data, quotes, commission statements | v1: deep links and file download; later: API where offered |

See 9.1 for a worked example with an existing Israeli agency system.

Integration rules: each integration is behind an adapter interface with a fake implementation for tests; every external call is logged; credentials live in a secret store; no integration may bypass the audit log.

### 9.1 Worked example: integrating with an existing Israeli agency system

Many Israeli agencies already run an agency management system and will not switch overnight. The Israeli systems found in research that connect to the pension clearing house and Har HaBituach:

| System | What it is (per vendor pages) |
|---|---|
| Surense | CRM for insurance and financial agents; clearing house files flow into the client record; mobile work, client digital service, API integrations |
| Jamm | Digital platform: lead management, CRM, process automation, Har HaBituach and clearing house connection |
| Atid (cloud agency software) | Cloud office management; clearing house requests for pre-advice and production download; automatic interfaces to WhatsApp, SMS, call recordings and Har HaBituach |
| Aloni | Ingests material from all insurers and funds, including clearing house data; Har HaBituach connection and product simulators |
| BAFI | Office management, car insurance simulator, online clearing house (vendor page is old, check it is current) |
| Salesforce (Financial Services Cloud) | Hebrew press reports it at large insurers: Migdal (described as the first Salesforce insurance project in Israel, built by Cloudtech) and Harel (360-degree customer view). One vendor blog adds Phoenix, unverified. Small agencies rarely run it, so treat it as the integration for a larger agency or for insurer-side data. Illustrative mapping: client to Account, policy to InsurancePolicy, lead to Lead, claim or complaint to Case, Inbox item to Task, document to ContentVersion, marketing consent to Individual and ContactPointTypeConsent |
| Sapiens IDITSuite | Insurer-grade core suite with open APIs (API Conductor), also used by brokers; more relevant to insurers than to a five-agent agency |

Example scenario (coexistence, ansurance as the front layer, a system such as Surense or Atid kept as the system of record for the first phase):

1. Day 0: import. A one-time export of clients and policies (CSV or API) is loaded into ansurance. Each record keeps the source system ID in a `external_ids` field (`{"system": "surense", "id": "..."}`). Matching key is the 9-digit ID number.
2. Daily: sync adapter. A scheduled adapter pulls changes since the last run (new or changed policies, premiums, renewal dates) from the existing system through its API or a scheduled file drop. Conflicts follow a stated rule: the existing system wins for policy data, ansurance wins for documents, messages, consents, campaigns and Inbox tasks.
3. Clearing house data. For phase 1 the agency keeps using the clearing house screen of the existing system. ansurance receives the resulting files, attaches them to the portfolio (F5) and flags changes. In a later phase ansurance requests directly (through Mislaka-API or its own connection) and the old system becomes read-only.
4. Documents and messages. The existing system's WhatsApp, SMS and call recordings are ingested through its interface or export, so F6 and F9 see one timeline. If the old system also sends WhatsApp, only one of the two may send marketing messages (the one holding the consent records), to avoid double contact and inconsistent opt-outs.
5. Write-back (optional, only if the vendor offers it): ansurance creates tasks or notes in the old system when an item is completed, for example "client replied to campaign". Otherwise ansurance stays one-way.
6. Cut-over test. Run both in parallel for one renewal cycle; compare renewal lists, commission expectations and portfolio totals; sign off by the manager.
7. Exit path. Every adapter and the data model support full export, so ansurance can replace the old system later, or be replaced, without data loss.

Adapter contract (the same for any vendor): `list_clients(since)`, `list_policies(since)`, `get_document(id)`, `push_task(client, text)` (optional), plus a health check. A fake adapter with synthetic data is used in tests. Vendor API access, rate limits, authentication and licensing terms must be agreed with the vendor before the build; none of these were verified in research **[verify]**.

## 10. Phasing

| Phase | Content |
|---|---|
| 0 (2-3 weeks) | Clients, policies, documents, roles, Inbox (all three), audit log, theme/language/role switchers, consent registry (opt-in, opt-out, source), import from the existing agency system (9.1) |
| 1 | F6 intake: email and WhatsApp first, then fax; ID-number attribution; Review and Unassigned queues |
| 2 | F1, F2, F3, F4 flows with e-signature |
| 3 | F9 live translation on calls, translation inline on WhatsApp and email |
| 4 | F5 clearing house and Har HaBituach, F7 commissions, F8 compliance dashboard, F10 notify client (consent registry must exist from phase 0) |
| 5 | Lens, Show me scripts, polish, Arabic RTL review |

## 11. Research notes and sources

What the research established, with source quality:

- Agents need a CMISA licence (individual and corporate; everyone brokering for a corporation needs their own). Two exams (general insurance; life/pension/financial plans) and an internship. Operating unlicensed is a criminal offence. Source: Lexology Q&A and Legal 500 guide (2021, so check current rules).
- An agent is generally treated as the insurer's agent unless the insured appoints them in writing. Paying the premium to the agent counts as paying the insurer. Source: Insurance and Reinsurance Law Review, Israel chapter (goslaw.co.il). This is why the system must record who the agent represents and when premiums are received.
- Calcalist (2026): agents are stronger than predicted, but hold no Securities Authority licence for investment management or mutual funds, which is a gap awaiting a regulatory solution. Hence the non-goal on investment management.
- Phoenix bought 86% of the Wobi agency in October 2025 (Wikipedia), a sign of consolidation pressure on small agencies and of the value of efficient back-office operations.
- Pension clearing house: standardized data between all funds; answers within about 3 business days (various sources). Har HaBituach lists all policies at harb.cma.gov.il. Mislaka-API documents a REST gateway that also sends the authorization form to the client.
- Privacy Protection Law Amendment 13 took effect 14 Aug 2025; sources agree on stronger enforcement and statutory damages, and differ on exact registration and officer duties.
- Israeli ID: 9 digits, last is a Luhn check digit (use it to validate OCR). Cards are filled in Hebrew with Hebrew and Arabic inscriptions; a paper addendum lists family.
- AML: sector order for insurers and agents under the Prohibition on Money Laundering Law 2000, with reporting to IMPA.

Gaps (not found by research, to be closed by the agency's compliance adviser): the current Hebrew consolidated circulars for agents (complaints, renewals, advice records), the formal agent-transfer procedure, fax providers, Israeli accessibility standard applicability, and any recent agent commission reforms.

Sources:

- [Lexology: Q&A insurance and reinsurance regulation in Israel](https://www.lexology.com/library/detail.aspx?g=4fb5f66e-5f6a-4f7b-9b4d-20e8c8c44ce9)
- [Insurance and Reinsurance Law Review, Israel (11th ed.)](https://goslaw.co.il/wp-content/uploads/2025/05/israel-1.pdf)
- [Calcalist: insurance agents and investment management licence](https://www.calcalistech.com/ctechnews/article/vlrnxvybl)
- [Globes: agents and the pension clearing house](https://en.globes.co.il/en/article-1000594797)
- [Mislaka-API developer documentation](https://docs.mislaka-api.co.il/)
- [Phoenix Insurance (Israel), Wikipedia](https://en.wikipedia.org/wiki/Phoenix_Insurance_(Israel))
- [Ius Laboris: major amendment to privacy law in Israel](https://iuslaboris.com/insights/major-amendment-to-privacy-law-in-israel/)
- [Amendment 13 practical guide 2026](https://dpoisrael.com/en/amendment-13/)
- [Sumsub: AML regulations in Israel](https://sumsub.com/blog/aml-kyc-israel/)
- [UNODC: Israel AML compilation](https://www.unodc.org/documents/corruption/WG-Prevention/Art_14_Prevention_of_money-laundering/Israel.pdf)
- [Didit: identity verification in Israel](https://didit.me/solutions/countries/israel/)
- [Hebrew OCR forms guide](https://skills.sh/skills-il/localization/hebrew-ocr-forms)
- [Azure Speech language support](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support)
- [Speechmatics Hebrew STT](https://www.speechmatics.com/speech-to-text/hebrew)
- [Vertex AI Live API speech-to-speech translation](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/live-api/speech-to-speech-translation)
- [AssemblyAI: real-time speech recognition APIs 2026](https://www.assemblyai.com/blog/best-api-models-for-real-time-speech-recognition-and-transcription)
- [Lynxbe: WhatsApp for business in Israel](https://www.lynxbe.co.il/en/lynxbe-platform-whatsapp)
- [Green Invoice (morning)](https://apps.shopify.com/green-invoice?locale=cs)
- [Surense: CRM for insurance agents](https://www.surense.co.il/crm-for-insurance-agents/)
- [Jamm](https://jamm.co.il/)
- [Atid Software Engineering](https://www.atidsm.co.il/homesite/)
- [Aloni Software](https://www.aloni.org.il/)
- [Start Point: CRM for insurance agents, 2026 comparison](https://start-point.co.il/crm-for-insurance-agents/)
- [Sapiens IDITSuite](https://sapiens.com/property-and-casualty/iditsuite/)
- [Salesforce CRM at Migdal (pc.co.il)](https://www.pc.co.il/featured/323342/)
- [Harel and Salesforce Israel CRM platform (ice.co.il)](https://www.ice.co.il/digital-140/news/article/1015517)
- [Israeli Anti-SPAM Legislation Amended for the First Time (Lexology)](https://www.lexology.com/library/detail.aspx?g=dfa80306-8bc5-4ac3-b01e-193ccd49dbb8)
- [WhatsApp marketing rules in Israel 2026](https://quickwa.com/blog/whatsapp-marketing-rules-israel)
- [gov.il: FAQ about spam mail](https://www.gov.il/en/departments/faq/17052018_7)
- [Capital Market Authority tightens rules on early pension withdrawals](https://news.itsbaba.com/story/42050/israeli-regulator-mandates-warning-calls-to-curb-early-pensi-42721)
- [Lexology: restrictions on managers' insurance plans](https://www.lexology.com/library/detail.aspx?g=b4c8d3c5-1c9c-49eb-9273-c64707671cfb)
- [Globes: changes that will affect personal finances in 2026](https://en.globes.co.il/en/article-changes-that-will-affect-personal-finances-in-2026-1001530085)
- [Ynet: what awaits Israelis on January 1](https://www.ynetnews.com/business/article/rkpl6m11n11l)
- [Bizportal: group health insurance rule change (draft circular)](https://www.bizportal.co.il/Insurance/news/article/20046700)
- [Taub Center: long-term care insurance in Israel](https://www.taubcenter.org.il/wp-content/uploads/2024/03/LTC-ENG-2024.pdf)
- [praxis-demo (UX reference)](https://github.com/larytet/praxis-demo)
