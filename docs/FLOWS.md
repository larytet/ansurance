# System flows

The business processes (stages and checklists) are in [PROCESSES.md](PROCESSES.md), generated from the mock.
This document draws the flows that belong to the platform itself and appear in no mock process: who can get in, how a user's life in the system is managed, what stands between a message and the client, and where an incoming document goes.
Each diagram states the code that implements it, so a reader can check the drawing against the source.

## Sign-in

Implemented in `backend/app/auth/service.py` (local login) and `backend/app/auth/sso.py` (OIDC).
Every outcome, success or failure, is written to `login_event` and to the audit chain.
A failure commits before the error is returned, so a rejected attempt is never lost.

```mermaid
flowchart TD
    A[Sign-in page] --> B{Method}
    B -->|Local| C[E-mail, password, 6-digit code or recovery code]
    B -->|SSO| S[Redirect to provider with state, nonce, PKCE]
    C --> D{IP over the failure limit?}
    D -->|Yes| X1[429 too_many_attempts]
    D -->|No| E{Account locked?}
    E -->|Yes| X2[423 account_locked]
    E -->|No| F{Active local user and password correct?}
    F -->|No| X3[Failure counted, lock after the threshold, same answer as unknown user]
    F -->|Yes| G{TOTP valid and newer than the last used, or unused recovery code?}
    G -->|No| X3
    G -->|Yes| H[Clear failures, start session]
    S --> T[Provider callback with code and state]
    T --> U{State known, unexpired, single use?}
    U -->|No| X4[400 bad_state or state_expired]
    U -->|Yes| V{ID token valid: issuer, audience, nonce, e-mail verified?}
    V -->|No| X5[401]
    V -->|Yes| W{Active SSO user created beforehand?}
    W -->|No| X6[403 unknown_user, audited]
    W -->|Yes| H
    H --> I[Session cookie and CSRF token]
```

## User lifecycle

The create step is implemented (`POST /api/users`, the Users page and the CLI).
The change, reset and deactivate steps are requirement F16 and milestone M11 in [STATUS.md](../STATUS.md).

```mermaid
flowchart TD
    A[Manager adds a user: e-mail, name, roles, local or SSO] --> B{Mode}
    B -->|Local| C[Password policy check, secret and 10 recovery codes created]
    B -->|SSO| D[Record only, no secret]
    C --> E[Manager hands over the authenticator setup once]
    E --> F[User signs in]
    D --> F
    F --> G[Working]
    G --> H{Change needed}
    H -->|Roles or name| I[Manager edits, audited]
    H -->|Lost authenticator| J[Manager resets: new secret and codes, all sessions revoked]
    H -->|Forgot password| K[Manager sets a new password, all sessions revoked]
    H -->|Wants a new password| L[User changes it with the current password and a code]
    H -->|Leaves the agency| M[Manager deactivates: all sessions revoked, history kept]
    I --> G
    J --> G
    K --> G
    L --> G
    M --> N[Cannot sign in, still named on past work and audit entries]
    N -->|Returns| O[Manager reactivates, resets authenticator]
    O --> G
```

## Sending a message

Implemented in `backend/app/messaging` and `backend/app/consent`.
One send path serves every module, so no flow can bypass the consent check.
The gate runs when the message is queued and again at delivery, because consent can change in between.

```mermaid
flowchart TD
    A[Module asks to send: campaign, reminder, tax certificate, reply] --> B{Kind of message}
    B -->|Marketing| C{Opt-in recorded for this channel?}
    B -->|Service| D{Opt-out recorded?}
    B -->|Legal notice| Q[Allowed]
    C -->|No| X[Refused with the reason]
    C -->|Yes| E{Opt-out later than opt-in?}
    E -->|Yes| X
    E -->|No| Q
    D -->|Yes| X
    D -->|No| Q
    Q --> R[Queued]
    R --> S{Gate again at delivery}
    S -->|Blocked| X
    S -->|Allowed| T[Adapter delivers: WhatsApp, e-mail]
    T --> U{Result}
    U -->|Temporary refusal| V[Retry with backoff]
    U -->|Permanent refusal| W[Failed, shown on the client file]
    U -->|Delivered| Y[Status recorded, reply goes to the agent Inbox]
```

## Incoming document

Implemented in `backend/app/intake`.
An item is filed automatically only when the evidence names exactly one client.
A name alone is never enough, and the sender only breaks a tie.

```mermaid
flowchart TD
    A[Fax drop folder, e-mail, WhatsApp media, upload] --> B[Virus scan]
    B -->|Infected or scan failed| Q[Quarantine, nobody can open it]
    B -->|Clean| C[Render pages, de-skew, crop, de-noise, split on blank pages]
    C --> D[OCR and classify the document type]
    D --> E[Extract ID number and policy number]
    E --> F{Evidence points to one client?}
    F -->|Yes, ID or policy match| G[Filed to the client, task if a human decision is needed]
    F -->|Several or weak candidates| H[Back-office review queue with the evidence]
    F -->|None| I[Unassigned queue]
    H --> J[Clerk confirms a client]
    I --> J
    J --> G
    G --> K{Wrong client found later?}
    K -->|Yes| L[Correction moves it, counts as a misfile, feeds the classifier]
```
