# ansurance - Requirements Specification

Custom CRM for a small Israeli insurance agency.

- Status: draft v0.1 (2026-11-04)
- Team: 5 agents, 2 back-office clerks, 1 marketing person, 1 manager (9 users, four roles)
- Jurisdiction: Israel (Capital Market, Insurance and Savings Authority, "CMISA", Privacy Protection Law)

Items marked **[verify]** come from web research that was thin or contradictory and must be confirmed against the primary source (gov.il, the insurer, or counsel) before they become hard requirements.

## 1. Goals and non-goals

Goals:

1. One place for every client, policy, document, call and message.
2. Nothing that arrives (fax, email, WhatsApp, call) is lost or sits unassigned. It is digitized, classified and attached to the right client portfolio automatically.
3. Staff and clients can talk across Hebrew, Arabic, Russian and French in real time, including phone calls.
4. Every role starts the day in its own Inbox.
5. Regulatory duties (licensing, KYC/AML, complaints, privacy, record keeping) are built into the flows and not left to memory.

Non-goals (v1): insurer-side policy administration, claims adjudication, investment management (agents hold no Securities Authority licence, see Calcalist note in section 11), a client mobile app in v1 (WhatsApp is the client channel; the mock includes a preview of a client app, see section 9.3 and 9.4 for what it is modelled on).

## 2. Roles and permissions

Four roles. The back-office role is the one the product is built around (see F11 to F15): most paperwork passes through it.

| Role | Count | Main job | Sees | Home screen |
|---|---|---|---|---|
| Agent / sales | 5 | Sell, advise, retain clients | Own clients and policies; team-shared clients by explicit share | Today: open leads, open processes, tasks (today, overdue, next, done, delegated), last clients, birthdays |
| Back-office | 2 | Get the ID, register the client, fill the forms, send post and email, print labels, answer other insurers, keep the tax files, issue, endorse, renew, file | All clients (work queue); no commission totals per agent | Desk: the five back-office areas with counts, then the Inbox |
| Marketing | 1 | Campaigns, segments, consent registry, unsubscribe handling, campaign results | Contact details, language, consent status and segment flags only; no documents, KYC, health or claim data **[verify with counsel: data minimization under Amendment 13]** | Campaigns |
| Manager | 1 | Oversight, approvals, compliance, commissions, licences, tax deadlines | Everything, including audit log and commission statements | Overview: what waits for a decision today and what is slipping |

Tabs per role (the mock implements exactly this):

| Role | Tabs |
|---|---|
| Agent | Today, Inbox, Client file, WhatsApp, Live call, Pension, Client app preview, Clients (preferences), Settings |
| Back-office | Desk, Inbox, Client file, Intake (fax, email), Pension, WhatsApp, Excel (join), Integrations, Clients (preferences), Settings |
| Marketing | Campaigns, Inbox, WhatsApp, Excel (join), Clients (preferences), Settings |
| Manager | Overview, Inbox, Desk, Campaigns, Client file, Client app preview, Integrations, Trust (audit), Clients (preferences), Settings |

Other roles considered (not separate roles in v1):

| Candidate | Decision |
|---|---|
| Compliance and AML officer | A hat the manager wears. Permissions are named capabilities (approve KYC flag, approve campaign, approve agent change, see audit) so the agency can later give them to a named person without a new role. |
| Finance and bookkeeping | A hat of the manager or a back-office clerk (commission reconciliation F7, agency invoices and VAT in F15). The external accountant gets a read-only export, not a login. |
| IT or system administrator | Not a role: user and permission admin belongs to the manager; hosting is managed (section 7). |
| Client | External and read-only to their own data; later phase (client app preview in the mock). Never sees other clients, internal notes or the audit log. |
| External accountant or tax adviser | Read-only package of tax certificates and the agency's yearly files (F15). No login in v1. |
| Insurer or partner | No login. Exchanges only through adapters and documents (section 9). |

Rules:

- Role-based access plus per-client ownership (the agent of record).
- A role switcher in the UI (see section 8) is a view switch for demo and for the manager. For real users it only offers roles they actually hold. A user may hold more than one role.
- Every read of a client record is audit-logged (section 7).
- Work can be handed between roles from the Inbox (reassign with a note). An item that needs another role's approval goes to that role's Inbox, never by side channel.

## 3. Core flows

Each flow lists trigger, steps, actors, outputs and regulatory hooks.

### F1. New client onboarding with KYC/AML

- Trigger: inbound lead (phone, WhatsApp, email, walk-in) or referral.
- Steps:
  1. Agent creates the lead; duplicate check on ID number (9 digits, Luhn check digit validated) and phone.
  2. Collect ID card plus the paper addendum (Sefach), proof of address if needed. Client may send photos over WhatsApp or email; they route through the intake pipeline (F6). The back-office steps of getting the ID and registering the client are F11.
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
  1. Client signs the data-lookup authorizations. There are three, each with its own legal text, signing date and expiry: for the insurers (policy information), for Har HaBituach and for the pension clearing house. The client can sign in the office or through a link sent to the client (and to the spouse). An authorization that expires within 6 months is flagged and a re-sign request is offered **[verify the validity period of each form]**.
  2. Back-office sends an information request through the pension clearing house and a Har HaBituach request. Sources say institutions must answer within about 3 business days and the consumer fee is about 20-40 NIS **[verify]**.
  3. Results are parsed into the client portfolio: all policies, funds, premiums, coverage periods, management fees.
  4. Agent prepares a review: gaps, duplicates, fee comparison, recommendation.
  5. If the client moves a product, the agent mandate/appointment letter is generated and sent to the insurer (how the formal transfer works between agents was not confirmed) **[verify]**.
- Integration: Mislaka-API (third-party REST gateway for clearing house requests, Har HaBituach requests and the authorization form) or direct clearing-house connection **[verify]**.
- Progress is shown as: request sent, waiting for the institutions, files received and filed, finished.
- Acceptance: portfolio view shows external policies next to the agency's own, with a "last refreshed" stamp. No lookup runs without all three valid signed authorizations on file. Each product opens to tabs: general, coverages (with percent of insured salary), beneficiaries, deposits and fees, returns and projection, claims, notes and recommendations; with flags for liens and exclusions and a "data as of" month.

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

### F11. Get the ID and register the client (back-office)

- Trigger: a new client or lead needs a file (from F1, a walk-in, a campaign reply, an insurer transfer).
- Steps:
  1. Case appears in the Desk list with a state: ID missing, ID requested, to check, registered.
  2. ID missing: one click sends a request in the client's own language (Hebrew, Arabic, Russian, French, English) over the channel the client uses (WhatsApp, email), asking for both sides of the ID card and the address page. The case moves to "ID requested" and an Inbox reminder is set.
  3. When the photo arrives (any channel, including fax and scan) the intake pipeline (F6) reads it. The case opens with the fields as read and a confidence figure per document; the clerk corrects fields in place.
  4. Checks before registering (all shown as a list, blocking ones stop the button): ID has 9 digits; check digit valid (a failure usually means an OCR error); ID document not expired; not already in the database (same ID number or phone: offer merge instead of creating a duplicate); address complete with a 7-digit postal code; mobile number valid (warning); tax residency declared (Israel only, US citizen or resident, other country).
  5. Register: creates the client with source, ID, address, phone, language and tax residency, stores the source system ID where relevant (section 9.1) and writes an audit entry with the ID masked. Marketing consent stays off until the client agrees (consent registry); it is never inferred.
  6. For a US or foreign tax resident a tax self-declaration task is created at once (F15).
  7. Next step is offered: fill the welcome letter, the three powers of attorney (F5) and the KYC form (F12).
- Outputs: client record, ID image filed to the client, audit entry, tasks.
- Acceptance: no client without a valid ID number, address and tax residency can be set to active; a duplicate can never be created silently; the audit log never shows a full ID number.

### F12. Fill templates (back-office)

- Trigger: any step that needs a letter or form: powers of attorney, agent appointment letter, address change, cancellation, KYC and tax residency declaration, request for an annual tax certificate, reply to another insurer (F14).
- Steps:
  1. Pick the client and the template. Fields that the system knows (name, ID, address, phone, email, date, agent name and licence, agency) are filled from the client record and shown as filled.
  2. Fields it cannot know (insurer, policy number, new address, effective date, tax year, their reference) are asked for. The preview marks each empty field in red with its name.
  3. The document is shown as it will be printed. Institutional documents are in Hebrew; client-facing documents go out in the client's language (section 5).
  4. While any field is empty the document cannot be saved, signed or queued. When complete: save to the client file, send for e-signature, or add to the dispatch queue as email or post (F13).
- Templates are owned by the manager (legal text reviewed by counsel), versioned, and every use is audit-logged with the template version.
- Outputs: document in the client file, signature request or dispatch item.
- Acceptance: a document with an empty field never leaves; changing a template does not change documents already generated.

### F13. Outgoing post and email, labels (back-office)

- Trigger: a document is queued from F12, a campaign letter, a renewal notice, a tax certificate to a client, a request to an insurer.
- Steps:
  1. The dispatch queue lists each item with client, document, channel (email or post, changeable) and status: queued, blocked, sent, posted.
  2. Checks per item: email channel needs an email on file; post needs a complete postal address; a marketing item needs a valid opt-in (F10). A failed check blocks the item and says why.
  3. Email: send the selected items; each send is logged against the client.
  4. Post: print the letters; print address labels on a sheet (sender line, recipient name, street, city, 7-digit postal code, barcode, registered-mail marker); then mark the items as posted with the registered-mail number, which is stored for tracking.
  5. Returned or undelivered post comes back as an Inbox item to fix the address (F11 checks).
- Outputs: send log, posted items with tracking number, printed labels.
- Acceptance: nothing is marked sent without an action by a named user; a blocked item cannot be force-sent except by the manager with a reason; label printing works from the browser print dialog on common label stock **[verify the label sizes the agency uses]**.

### F14. Requests from another insurance company (back-office)

- Trigger: another insurer, or the same insurer's other department, writes, faxes or sends a portal message about a client of the agency (found by the intake pipeline F6 or typed in).
- Types: request for policy information; agent-of-record change; policy transfer between insurers; claim information; tax or withholding certificate; commission query.
- Rule: no request stays unanswered. Each has an owner and a reply-by date from the moment it arrives; the list shows open, overdue, due within 2 days and answered. The time limits come from CMISA circulars and agreements with the insurers; the mock uses 7 days **[verify the limits per type]**.
- Steps:
  1. Verify the client and the authority: a valid power of attorney or the agent of record on file.
  2. Gather the data and documents asked for.
  3. Draft the reply from the template (F12) with their reference number.
  4. Manager approval when the client or the policy changes hands (agent-of-record change, policy transfer). The clerk escalates from the request; it lands in the manager's Inbox.
  5. Send the reply through the channel it came in on (or the one they ask for) and log the reference.
- Outputs: reply on file, reference, audit entries, closed request.
- Acceptance: a request past its reply-by date turns red and appears in the manager's Overview; the reply cannot be sent until steps 1 to 3 (and 4 where required) are done; every answered request keeps the exact text sent.

### F15. Tax-related work (back-office, manager, external accountant)

Principle: the agency supplies documents and keeps deadlines; tax advice and filing stay with the client or their accountant (agents hold no tax-adviser licence). All rules and dates below are working assumptions and each must be confirmed with the agency's accountant **[verify]**.

- A. Annual deposit certificates. Each year insurers and funds issue a certificate of deposits and payments that a client uses for the tax credit or deduction on pension and life insurance deposits. For every client and product the system tracks: to request, requested, received, sent to the client. Bulk actions: request all missing (creates the request letters in the dispatch queue, F13), send all received to the clients. Missing certificates after the agreed date create Inbox items.
- B. Tax coordination and refunds. Cases for a client who has several income sources or starts a pension payment (tax coordination certificate for the payer), withdraws severance or other funds (tax-exempt amounts), or whose tax refund file needs the deposit certificates. Steps per case: collect the ID and the payer's letter; get the balance statement from the clearing house or insurer; prepare the documents for the client or accountant; client or accountant files with the Tax Authority; certificate received and filed.
- C. Tax residency declarations. Insurers ask for a self-declaration of tax residency (a US citizen or resident, or a resident of another country needs an additional form). Collected at F11; clients without a declaration or with a foreign residency and no form are listed with "send form" (F12 template) and "mark received".
- D. The agency's own taxes. Monthly commission invoices to each insurer (invoice allocation numbers are required above a threshold that has been phased in), withholding-at-source certificates received from insurers (yearly), the periodic VAT report, and the annual package to the accountant. Each has a due date and a done mark; items due within 14 days appear on the manager's Overview and as an Inbox item.
- Outputs: certificates filed to the client, sent letters, tasks, a read-only yearly package for the accountant.
- Acceptance: for every tax year the manager can see, per client and product, whether the certificate was requested, received and sent; an agency tax deadline cannot pass without an Inbox item for the manager.

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
| Back-office | Review queue (medium-confidence documents), Unassigned queue, ID missing or unreadable, requests from other insurers, tax certificates not received, letters waiting to print or post, applications to submit, endorsements, claim document gaps, commission mismatches, campaign send failures | Item older than same-day SLA; insurer request within 5 days of its reply-by date |
| Marketing | Campaign drafts needing a consent check, opt-outs to process, bounced messages, campaign results, campaign replies to hand to agents | Opt-out waiting (must be applied within one minute of receipt in the registry, so any waiting item is urgent) |
| Manager | Approvals (KYC flags, payouts, agent changes), insurer requests escalated, complaints over SLA, compliance expiries, agency tax deadlines, misfile reports, team workload, retention leaks, campaigns awaiting approval | Any regulatory deadline within 7 days |

Each Inbox item of the back-office, marketing and manager types has an "Open in work area" action that jumps to the right place on the Desk or in Campaigns.

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

1. **Light and dark theme**: light by default (an office tool used in daylight and on printed-style documents), manual toggle to dark, remembered per user; "follow the system" is a user preference. Contrast checked in both.
2. **Language switcher**: UI in Hebrew, Arabic, Russian, French and English; remembered per user; right-to-left flips the whole layout. Document text stays in its original language, with translation shown next to it.
3. **Role switcher**: one click to view the app as Agent, Back-office, Marketing or Manager (limited to roles the user holds; the manager can preview all). Each role gets its own home screen and its own set of tabs (section 2) so a role sees only what it works with. Makes demos and training easy.
4. **Show me**: a guided tour mode where the app moves the highlight from hint to hint on its own and opens the details in place, for onboarding and for explaining a flow to a new hire. Each flow F1 to F15 ships with a Show me script.
5. **Lens for long lists**: a draggable sliding window over a long list or timeline. Items inside the lens are shown in full; items outside are squashed into thin colored bars (red urgent, amber important). Drag the handle, scroll, use arrow keys, or click a bar to jump. Header shows the window position ("41-60 of 312"). Used on Inbox, client timeline and renewals calendar.
6. **Role-first home**: the first screen is the role's work area (Today for agents, Desk for back-office, Campaigns for marketing, Overview for the manager), with the Inbox one click away and every Inbox item able to open its work area. Counts and urgency are always visible.
7. **Trust page**: shows the audit log, with the verify-chain button.
8. Plain language, one primary action per item, nothing touchable that does nothing: every element drills in or opens detail.
9. **Client picker, never a plain drop-down**: wherever a client (or several) is chosen, the control is a quick-search box (name in any script, transliteration, or any digits of the ID number) plus the Lens over the result list and multi-select with select-all-matching. It must stay usable with hundreds of clients. The Inbox has the same quick search.
10. **Excel as an integration**: import insurer commission statements (F7), export client lists and reports to Excel, and join several spreadsheets on a partial key. Example from the mock: an agency export with the full ID, an insurer statement with only the last 4 digits and the name in Latin letters, and a clearing house export with the full ID and no name are joined on ID, and on last 4 digits plus fuzzy name where the ID is partial. The report shows how each row was joined and flags rows where the name is spelled differently.
11. **Names carry a transliteration**: wherever a name is shown (lists, search results, detail panes, logs) the original spelling is followed by a transliteration in the script of the chosen UI language, Hebrew when the UI is Hebrew and Latin letters otherwise, and none when the name is already in that script. Search matches the original, the Hebrew transliteration, the Latin transliteration and any digits of the ID number, ignoring accents, apostrophes and word order.
12. **Client preferences**: each client has a preferred communication language (Hebrew, Arabic, Russian, French or English), editable for one client or for many at once. Messages and templates to the client go out in it, written by the user in their own language, and the client's messages are shown in their language with a translation. Language tags appear next to every client in lists.
13. **System preferences** (per user, stored in the browser): screen width wide or narrow; default theme (follow system, light, dark); list view comfortable or compact (compact shows 12 rows in the Lens instead of 8 and tighter tables); time zone used for times in chats, calls, sync logs and the audit log, either the browser's or set explicitly from the full list of zone names.

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

### 9.2 Surense product family (integration and competitor reference)

Sources: the vendor pages [products](https://www.surense.co.il/products/), [SurenseOne](https://www.surense.co.il/surense-one/) and [CRM for insurance agents](https://www.surense.co.il/crm-for-insurance-agents/) (read 2026-11-05), and the two Android apps from [Google Play](https://play.google.com/store/apps/details?id=com.surense) (SurenseGo 4.2.0) and [Google Play](https://play.google.com/store/apps/details?id=com.surense.one) (SurenseOne 1.3.0, minSdk 24 for both) as installed on a test phone. These are vendor marketing claims, not verified in use. Vendor images are kept locally only (not committed, they are the vendor's marketing material); file names below refer to that local folder. Numbers shown in the screenshots are demo data.

**Product map.** The vendor sells four things that share one data set. SurenseFire is the central system, SurenseGo extends it to the field, SurenseOne faces the agency's own clients, SurenseFlow (the API) connects other tools.

#### SurenseFire (web CRM, SaaS in the browser, no local install)

For independent agents and large agencies alike. Image: `Frame-2147224618.jpg` (dashboard: policies 1,796, open processes 68, open leads 447, new 32; donut charts of clients by status and by classification; 12-month clients bar chart; "active only" toggles).

| Module | What the vendor says it does |
|---|---|
| Smart lead management | Automatic intake from marketing funnels, full closing process |
| Client file | Flexible view of insurance and savings products of all insurers and investment houses in one place; views arranged by clearing house and Har HaBituach data; choose fields, define custom statuses |
| Information requests and controls | Generate powers of attorney; pull data and files from the clearing house automatically (the client's signed power of attorney is the trigger); files are filed into the client record |
| Portfolio analysis | Accumulations and coverages (provident funds, study funds, life and health policies), gap detection, products "not in my care" |
| Business data | Performance analysis, comparison with previous months, segmentation, sales opportunities |
| Client reports | Automatic production intake; personal or family report at a chosen frequency (monthly reports mentioned by an agent) |
| Operations | Tasks by work queue and deadlines inside built-in workflows; separate Activities, Tasks and Processes modules |
| Direct issuance | Issue selected products from inside the system, including the Phoenix Invest savings policy |
| Meetings | Dedicated module; two-way sync with Google Calendar and Outlook; branded meeting summary sent to the client with a digital approval saved in the client file (proof the meeting happened and what was advised) |
| Full documentation | Calls, emails, documents and summaries under the client file |
| Security | Stated as strict standards, regular security tests, Amendment 13 compliance |
| Configuration | Roles with custom permissions; process types (sales and operations) with an owner and time limits; automatic client reports in a chosen layout; done by an authorised user, no custom development |

Onboarding detail from the CRM page: with one click the client signs the power-of-attorney appendices (the page names appendices A, B and E), approves the updated terms of use (Amendment 13) and approves marketing mail; a process then opens automatically. Consent management records who may be contacted (Spam Law).

Regulatory features it claims: a 2-yearly service-contact duty tracked through the meetings module (the vendor cites the Capital Market Authority service circular: the agent must initiate a service contact at least every two years and document it), meeting documentation, consent management, Amendment 13 readiness.

Not everything is in the mobile app: complex reports, data cuts and multiple views per module stay in Fire.

#### SurenseGo (agent mobile app, Android `com.surense`, iOS, Hebrew, English, Russian)

Image: `Frame-2147224693.svg` (vector mock-up). Features: instant access to full client files; create clients and leads; order information requests and open new processes; delegate tasks and track status; log every meeting or field action; meetings synced to Google or Outlook calendar; smart alerts and instant reports; activity graph (active clients, open opportunities, client birthdays); call, WhatsApp or email in one tap; send reports and attach documents to clients.

#### SurenseOne (client app, Android `com.surense.one`, iOS, Hebrew, English, Russian)

New product (announced on the site banner, 2026). Images: `Frame-2147224695.jpg`, `Hero-Surense-One1.webp`, `SurenseOne-App-mockup-1.webp` (phone: greeting, total saved to date, year-to-date return with monthly bars, tiles for deposits, projected annuity, loan repayments, premium; bottom tabs: Home, Products, Processes, Documents, Profile; a floating "+" button; a branded envelope logo with the agency name).

- Client sees: insurance and savings information, documents, reports and meeting summaries; status of open processes (set by the agent); agent updates and recommendations with push notifications on each change.
- Agent configures in 6 steps: choose extra services (self-purchase of travel insurance and a savings policy through a link tied to the agent number); choose what is shown (information types, documents, processes); choose which clients get access; invitation by email and SMS with a download link; contact channels (message, phone, email, and a meeting-booking link).
- Claimed value for the agency: fewer service calls, more sales opportunities (clients send new requests from the app), a digital service channel that differentiates the agency, better retention.
- Packaging: Fire and Go are in every plan; SurenseOne and advanced capabilities are in higher plans or a paid add-on.

#### SurenseFlow (API and automations)

Images: `SurenseFlow_v1-scaled.png` (logo strip), `ChatGPT-Image-Aug-11-2026-05_00_59-PM-1024x824.png` (illustration). An API extension for connecting any external system (mailing, marketing, other tools) and building automations such as sending client updates, scheduling reminders and producing reports. Works with Zapier, Make and n8n. Requires buying the API service; scope depends on the plan.

#### Other facts

- Connected to the pension clearing house and Ministry of Finance data sources; calendar sync with Google Calendar and Outlook.
- Support by chat, WhatsApp and email (Customer Success team) plus a knowledge centre.
- The vendor states that Fire, Go and One work together as one ecosystem on one data set.
- Customers quoted: a network of about 100 users (the vendor's first client), a pension agent, a sales manager at a house agency.
- The vendor's own comparison table (generic CRM vs self-built CRM vs dedicated CRM) lists the buying criteria it expects: clearing house file pull into the client record; accumulation and coverage analysis; service-contact duty; meeting records with digital approval; Amendment 13 updates; information security.

#### What this means for ansurance

- **Feature parity checklist.** The Fire table above is the baseline an agency compares us with. Gaps in ansurance today: process and task queues with deadlines (partly Inbox), direct issuance (F2 covers quotes and sale), portfolio gap analysis (F5), dashboard with status and classification donuts (F8 partly).
- **What ansurance adds that the vendor pages do not mention:** automatic fax, email and WhatsApp intake with ID-number attribution (F6), live multilingual call (F9), role-based Inboxes, and an audit log on every action.
- **Client app is a later phase, not v1.** SurenseOne is the reference for F10 and a client view. Reuse its content list: savings summary, products, processes, documents, profile, push on status change, and agent-set visibility per client.
- **Mobile is expected.** Every Inbox item and client card must work at phone width with one-tap call and WhatsApp; the vendor keeps heavy reporting on the web only, which is a sound split.
- **Integration surface.** The API is a paid add-on exposed mainly through Zapier, Make and n8n, so the 9.1 adapter should assume REST or webhook access with a licence cost, not a free bulk export. Clearing house files already land in the client record there, which fits the 9.1 step "ansurance receives the resulting files". Still **[verify]** with the vendor.
- **Consent from self-service leads.** SurenseOne sells travel and savings policies through an agent-number link. Our consent registry (F10) must record the source of any lead that arrives this way.
- **Two-year service duty.** Add a renewal-style reminder "service contact due" per client to F3 or F8, with a meeting record as proof (branded summary, digital approval saved to the file).

### 9.3 Surense mobile apps: UI structure and flows (static analysis)

Method: both Android apps were decoded with apktool and their Hermes bytecode string tables were read with hermes-dec (React Native / Expo apps, so the UI logic sits in `index.android.bundle`). Nothing was run and no account was used. Screens, flows and fields below are inferred from route files, component names, API resource names and Hebrew UI strings, so treat them as a map, not as verified behaviour **[verify in a demo]**. No credentials or keys from the apps are recorded here.

#### Agent app (SurenseGo): navigation

File-based routes: `customers`, `customer/[id]` (tabs below), `employers`, `employer/[id]`, `leads`, `meetings`, `workflows`, `contacts`, `settings`. Bottom navigation plus a drawer; a home dashboard with cards for customers, open leads, workflows, tasks (today, overdue, next, done, delegated), last customers and birthdays (today, recent, next).

| Customer tab | Content |
|---|---|
| Overview | Header with name, phone, email, ID, status, class, source, manager, agency; products summary (insurance, pension, savings, loans, health fund, car, home, business) with a graph; recommendations and notes |
| Activities | Phone, email and text activities, add, edit, delete; call duration and recordings |
| Documents | Upload (camera, file, screenshot), share, download, password-protected files |
| Employments | Employer, role, type (employee, self-employed, both, not employed), reported salary; add, remove |
| Leads | Leads linked to the customer |
| Meetings and meeting summaries | Types: in person, phone, online; summary created in-app, opened as a document |
| Related customers | Spouse, child, parent, sibling, grandchild; add and remove with a relationship type |
| Reports | Report to the client with a period, preview, send; per-client report settings |
| Workflows | Open and handled processes |

Employer screen: employees, activities, documents, workflows. Lists everywhere have search, empty states and pull to load more.

#### Agent app: creation flows (bottom sheets)

Create customer, lead (type, interest, status), employer, employment, related customer, activity, task and reminder, meeting, meeting summary, report, workflow (type, handler, description). Each is a sheet with required-field messages (missing first name, last name, ID, subject, type, handler) and a success or error toast.

#### Agent app: information requests and powers of attorney

- "Order information" from the client file or during a meeting, or remotely: the client receives a link, and a link can also be made for the spouse.
- Three powers of attorney, with legal text on screen: policies from insurers, Har HaBituach (appendix E), pension clearing house (appendix A); a signing date, the ID issue date and the name of the person giving the power. A "continuing power of attorney expires within 6 months" flag and a "re-sign" action. An invitation action to proxies.
- After the request: a progress flow that ends with "information request finished".

#### Agent app: product views (clearing-house data)

Per product: coverage list with cost, insured salary, waiver of disability cover above 60, survivor pensions (widow, supported parent, orphan), disability pension (full), claims (number, start of payment, disability percent, status date), attachments and liens, exemption notes (Section 190), exclusions, tracks and investment funds, management fees on deposit and on balance, deposits by type (employer, employee, severance, parents, national insurance), projected balance and pension at retirement age, conversion coefficient and guaranteed life-expectancy terms, return since start of year and 36-month average, balance by period, insurance "umbrellas" (collective), notes and recommendations per product, "data as of" month. Product families: pension, provident, study fund, executive insurance, life, health (sick fund tiers), home, car, business, travel, investment portfolio, loans, direct issuance (new) including the Phoenix Invest savings policy.

#### Agent app: system behaviour

Single sign-on with a hosted identity provider; locked-user message with a one-tap "unlock my account" email; biometric lock; forced and optional version update sheets; push notifications and an in-app notification list; Intercom support chat; bug report and feedback modals; ID number check digit validation; Hebrew first, English names for transliteration (first name, last name, address in English).

#### Client app (SurenseOne): navigation

Auth group: welcome, login (phone), verification (one-time code), then an optional biometric prompt ("quick login", expires and returns to login). Protected group with four tabs: Home, Products (list and `products/[id]` detail), Workflows (processes), Documents. Modals: profile, personal information (editable address, postal code, house number, city, family status), communication info (how to reach the agency), notification settings, general settings, terms and privacy (accept before continuing), "what is Surense", an app tour, a bug-report modal, and a blocked screen and a provisioning screen ("your data is being prepared, may take a few moments"). Deep link of the form `link.surense.com/one/...` carries the agent and customer so the invitation opens straight into the right agency.

- **Onboarding by invitation only.** A client without a link sees a message asking them to send the agency a ready-made request ("please activate the app for me and send a personal login link"), shareable by WhatsApp or email. A client can belong to more than one agency and picks one ("choose agency").
- **Home:** greeting by time of day, birthday banner, total saved to date, return since start of year with monthly bars, tiles (deposits, projected annuity, loan repayments, premium), agent recommendation cards, new-information-on-process cards, quick actions (schedule a meeting, request a quote).
- **Products:** grouped by type (pension, provident, study fund, life, health, car, home, loans, investment); the detail screen has tabs: general, coverages (with percentage of insured salary, survivors, disability), beneficiaries, deposits, returns, loans, health claims, insurance; per-product contact buttons (inquiry about a policy, about a savings policy, travel insurance).
- **Workflows:** a card per open process with company, date, document count and a flag for new information; documents attached per process.
- **Documents:** meeting summaries (portfolio review, pension planning, coverage check, study-fund transfer, beneficiaries update), reports (quarterly, production, financial; life and health), forms (consent to insurance), view, download, share.
- **Contact:** WhatsApp (asks to install it if missing), phone, email, meeting link; the agency decides which channels show.
- **Legal text in the app:** the agent is the controller of the database, the software vendor only supplies it; terms of use; a note about use of AI systems for processing personal data in the app; cookies and similar tools.

#### Data entities implied by the API resource names

Customers (with classes, ID types, sources, statuses, mailing statuses, dashboard, birthdays, search, search by employer, related customers, per-customer reports and report settings, product recommendations, proxies and approve-proxy actions for each of the three powers of attorney), employers, employments, leads (with statuses, interests, dashboard, search), activities (with an activity log), tasks (todo, done, delegated), reminders, meetings, meeting summaries, workflows (with types, statuses, dashboard, search), reports. Customer data model fields: first and last name (also in English), ID and ID type, gender, family status, birth date, death date, address, extra phone and email, "email for filing" (a mailbox address for copying emails into the file), smoking status and health details, mailing status (approved or not, removed from mailing), consent required flag.

#### What this adds for ansurance

- **Navigation baseline.** The customer tab list above was used for the Client file tab of the mock (overview, products, activities, documents, employment, leads, meetings and summaries, related clients, reports, processes, information request). Coverage of every flow is in 9.4.
- **Fields to consider:** "email for filing" (a per-client address that files copied emails into the record), a mailing status with three values (approved, not approved, removed), ID type, English spelling of names, and a "consent required" flag on first use of the client app.
- **Information request as a guided flow:** three powers of attorney with legal text, signing date, a warning six months before expiry, and a re-sign action. This should be an explicit sub-flow of F5.
- **Client app:** invitation-only onboarding with a ready-made message the client can send to the agency, multi-agency membership, quick login with biometrics, "data is being prepared" state, and the data-controller wording (the agency is the controller) are worth copying into the F10 design.
- **Not copied:** the vendor keeps heavy reporting on the web, so the mobile views are read-oriented; this split also suits ansurance.

### 9.4 Coverage of the vendor flows in the spec and the mock

The 57 flows found in the two mobile apps (9.3) were checked against this spec and against the mock (`index.html`). "Mock" means a clickable simulation with fictional data. Status: Yes, Partly (simplified), No (not in the mock; reason given).

| Flows | Spec | Mock |
|---|---|---|
| Sign-in with a hosted identity provider, biometric lock | Yes, section 7 (SSO with two-factor, session timeout) | Partly: biometric quick login in the client app preview; agent sign-in not mocked (no accounts in a demo) |
| Account lock after inactivity and one-tap unlock email; forced and optional app update | Section 7 (session timeout, device list) | No: operational behaviour, not a workflow to demonstrate |
| Push notifications, notification list, support chat, bug report | Section 8 (Inbox is the notification centre) | Partly: client notification settings; bug report and support chat No |
| Home dashboard: open leads, processes, tasks (today, overdue, next, done, delegated), last clients, birthdays with greeting | Section 2 (Agent home) | Yes: Today tab; the greeting is blocked without marketing consent |
| Create client, lead, employer, employment, related client, activity, task or reminder, meeting, meeting summary, process, report | F1, F2, F3, F10, F11 | Yes: Client file "+" sheets with required-field checks and ID check digit; registration with 7 checks in the Desk |
| Customer card tabs: overview and products summary, activities, documents (camera, file, screenshot, share, download, password-protected), employments, leads, meetings and summaries, related clients (relationship types), reports (frequency, preview, send), processes | Section 2, F5 | Yes: Client file tabs |
| Employer screen (employees, activities, documents, processes) | Not specified: employers matter only for payroll-deducted products | Partly: employer can be created; employer screen No |
| Information request: three powers of attorney with legal text and signing date, in office or remote link (also for the spouse), invite proxies, 6-month expiry warning with re-sign, progress to "finished" | F5 | Yes: Client file "Information request" tab; bulk version in the Pension tab |
| Product views from clearing house data: coverages, claims, fees, deposits, projection, returns, beneficiaries, liens and exclusions, notes and recommendations | F5 | Yes: Client file "Products" tab (7 sub-tabs) |
| Direct issuance of selected products (including a savings policy) | F2 (issuance through insurer adapters, 9) | No: needs insurer APIs; spec only |
| Client app onboarding: invitation by email and SMS with deep link, welcome, choose agency, terms before continuing, phone login with one-time code, biometric prompt, "no link" request message | F10 and phase 5 | Yes: Client app preview (full path; demo code 1234) |
| Client home: greeting, birthday banner, total saved, year-to-date return with bars, tiles, recommendations, new-information card, quick actions | Phase 5 | Yes |
| Client products with detail tabs, processes, documents (summaries, reports, forms), contact sheet (WhatsApp, phone, email, meeting link) | Phase 5 | Yes (loans tab merged into general) |
| Client profile edit with postal-code check, notification settings, sign out | Phase 5 | Yes |
| Self-purchase of travel insurance and a savings policy through an agent-number link | F2, F10 | Yes: links in the client app, switchable in the agency settings |
| Agency settings for the client app: services, what is shown, which clients, invitation, contact channels | Phase 5 | Yes: panel next to the phone |
| Provisioning ("data is being prepared") and blocked screens | Phase 5 | No |

Beyond the vendor flows, the mock adds: the back-office Desk (F11 to F15), the Marketing role (campaigns with consent check, approval and send, consent registry, segments), the manager Overview, the role-specific tabs, and light theme by default.

## 10. Phasing

| Phase | Content |
|---|---|
| 0 (2-3 weeks) | Clients, policies, documents, four roles with their home screens, Inbox (all four roles), audit log, theme/language/role switchers (light by default), consent registry (opt-in, opt-out, source), import from the existing agency system (9.1), F11 get the ID and register |
| 1 | F6 intake: email and WhatsApp first, then fax; ID-number attribution; Review and Unassigned queues; F12 templates and F13 outgoing post, email and labels |
| 2 | F1, F2, F3, F4 flows with e-signature; F14 requests from other insurers |
| 3 | F9 live translation on calls, translation inline on WhatsApp and email |
| 4 | F5 clearing house and Har HaBituach, F7 commissions, F8 compliance dashboard, F10 notify client (consent registry must exist from phase 0), F15 tax work, marketing role screens, manager Overview |
| 5 | Lens, Show me scripts, polish, Arabic RTL review, client app (read-only, invitation only) |

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
