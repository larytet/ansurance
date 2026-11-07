# ansurance

Custom CRM for a small Israeli insurance agency (5 agents, 2 back-office, 1 marketing, 1 manager).

- Requirements: [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md)
- Clickable Inbox mock (fictional data, no backend). Besides the Inbox it has simulated integration tabs: integrations overview with a Surense/Atid and Salesforce coexistence example, a Clients tab (preferred language per client), a System settings tab (width, theme, density, time zone), an Excel tab that searches three spreadsheets by name or a few ID digits, joins them and exports one report, fax/email intake, WhatsApp with a consent gate, live call subtitles, and a pension clearing house request: [index.html](index.html). Open it locally, or enable GitHub Pages (Settings > Pages > Deploy from a branch > `main` / root) and it is served at https://larytet.github.io/ansurance/

## Deploy (on-prem, step by step)

1. **Prerequisites:** a Linux host with Docker and Docker Compose v2, `make` and `git`, about 4 vCPU and 16 GB RAM, and a second disk for backups.
2. **Get the code:** `git clone <repo-url> ansurance && cd ansurance`
3. **First start:** `make up`. The first run creates `deploy/compose/.env` with random secrets, builds the images (MinIO is built from source, so the first build takes a while) and starts the stack. Stop here if you want to edit `.env` first, then run `make up` again.
4. **Edit `deploy/compose/.env`:**
   - `BIND_ADDR`: the address of the VPN interface (never a public one). Default is `127.0.0.1`.
   - `HTTPS_PORT` (default `8443`) and `HTTP_PORT` (default `8080`).
   - `BACKUP_PATH` and `BACKUP_WAL_PATH`: directories on the second disk.
   - Optional: `ANS_PASSWORD_MIN_LENGTH` (minimum password length, default and floor 8).
5. **Save `BACKUP_PASSPHRASE` away from this host.** Every backup is encrypted with it, and without it nothing can be restored.
6. **Apply the settings:** `make up` again, then `make ps` (every service should be `running` or `healthy`).
7. **Create the first user (admin):** see the next section.
8. **Open the app:** `https://<BIND_ADDR>:<HTTPS_PORT>`, for example `https://127.0.0.1:8443`. The proxy uses its own certificate authority, so the browser warns until you trust it (see [docs/OPERATIONS.md](docs/OPERATIONS.md), TLS).
9. **Verify:** `make smoke`. Later: `make upgrade` (with backup and automatic rollback), `make backup`, `make restore`, `make logs`.

Operations, backups and disaster recovery: [docs/OPERATIONS.md](docs/OPERATIONS.md). AWS: [infra/aws/README.md](infra/aws/README.md).

## Add the first user (admin)

There is no sign-up and no default account. The first user, with the `manager` role (the admin), is created from the command line on the server:

```
docker compose -f deploy/compose/docker-compose.yml --env-file deploy/compose/.env exec api \
  python -m app.cli create-user --email you@agency.il --name "Your Name" --role manager
```

1. Type a password when prompted (at least 8 characters; not a common password or based on the e-mail). In a non-interactive shell add `--password '...'` instead (it stays in your shell history).
2. The command prints a **TOTP URI** (`otpauth://...`) and **10 recovery codes**. The recovery codes are shown once: store them safely.
3. Add the TOTP URI to an authenticator app (Google Authenticator, Authy, Microsoft Authenticator), as a setup key or as a QR code.
4. Sign in: e-mail, password, and in the **authenticator or recovery code** field the current 6-digit code from the app (or one unused recovery code).
5. As manager you can then create the other users (agent, back-office, marketing) with the same command and `--role` (or through the API, `POST /api/v1/users`). Add `--sso` for a user who signs in only through Microsoft 365 or Google.

If you lose the authenticator, use a recovery code. If you lose those too, another manager resets the user; there is no self-service reset. After repeated failures the account is locked for a growing delay.

## Add an SSO user (Google Workspace / Google account)

An SSO user signs in with Google instead of a password and authenticator code. There is no auto-provisioning: **the user must be created first, by a manager, with the same e-mail as the Google account.** The Google e-mail must be verified.

**One-time: register the app with Google**

1. In the [Google Cloud console](https://console.cloud.google.com/) pick or create a project, then *APIs & Services > OAuth consent screen* (choose *Internal* for a Workspace domain) and fill in the app name.
2. *APIs & Services > Credentials > Create credentials > OAuth client ID*, type **Web application**.
3. Under *Authorized redirect URIs* add exactly: `https://<your-host>:<HTTPS_PORT>/api/auth/sso/google/callback` (for example `https://ansurance.agency.il:8443/api/auth/sso/google/callback`). Google requires a real host name and HTTPS for anything but `localhost`.
4. Copy the **Client ID** and **Client secret**.

**One-time: tell ansurance about Google.** In `deploy/compose/.env` add one line (JSON, no line breaks), with your values:

```
ANS_SSO_PROVIDERS=[{"name":"google","client_id":"<CLIENT_ID>.apps.googleusercontent.com","client_secret":"<CLIENT_SECRET>","authorize_url":"https://accounts.google.com/o/oauth2/v2/auth","token_url":"https://oauth2.googleapis.com/token","jwks_url":"https://www.googleapis.com/oauth2/v3/certs","issuer":"https://accounts.google.com","redirect_uri":"https://<your-host>:<HTTPS_PORT>/api/auth/sso/google/callback"}]
```

Then `make up` to apply it. `redirect_uri` must match the one registered with Google character for character.

**Create the user** (as a signed-in manager on the *Users* tab, choosing sign-in *SSO only*, or from the server):

```
docker compose -f deploy/compose/docker-compose.yml --env-file deploy/compose/.env exec api \
  python -m app.cli create-user --email anna@agency.il --name "Anna" --role agent --sso
```

No password, authenticator or recovery codes are created for an SSO user.

**Sign in:** the user opens `https://<your-host>:<HTTPS_PORT>/api/auth/sso/google/start`, signs in with Google, and is sent back to ansurance. The first sign-in links the Google account to the user. Afterwards, open `https://<your-host>:<HTTPS_PORT>/`: the session is already active. (The login page has no "Sign in with Google" button yet, and the callback shows a short JSON reply instead of redirecting to the app. Both are small front-end changes still to do.)

If sign-in fails, the reply says why: `unknown_user` (no SSO user with that verified Google e-mail, or the user is inactive), `unverified_email`, `bad_state` or `state_expired` (start again; the sign-in must finish within 10 minutes), `token_exchange_failed` (wrong client secret or redirect URI). Every success and failure is in the audit log. To make a Google user the admin, create it with `--role manager --sso`.

Processes: every flow runs as a pipeline of stages with checklists (locked or parallel order, finalized by the owner role, complete when all stages are final), with an editable diagram per process. See [docs/PROCESSES.md](docs/PROCESSES.md) and the Processes tab in the mock. The reference process is Exit to retirement planning.

Highlights: four roles (Agent, Back-office, Marketing, Manager), each with its own home screen, tabs and Inbox; the back-office Desk (get the ID and register, fill templates, post/email/labels, answer other insurers, tax work); a client file, a client app preview and light theme by default; automatic digitizing, classification and portfolio attribution of incoming fax, email and WhatsApp (an ID scan goes straight to the folder of the ID's owner); a "Notify client" channel for targeted, consent-checked upsell and regulatory notices; coexistence with existing Israeli agency systems; real-time Hebrew/Arabic/Russian/French translation with subtitles, including phone calls; light and dark theme, language switcher, role switcher, "Show me" tour and a "Lens" for long lists.
