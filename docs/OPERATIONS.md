# Operations guide (on-prem)

For whoever looks after the server. Everything runs with `docker compose`; the commands are
`make` targets. The AWS deployment has its own guide in [infra/aws/README.md](../infra/aws/README.md).

## Install

1. A Linux host with Docker, 4 vCPU, 16 GB RAM, and a second disk for backups.
2. `git clone`, then `make up`. The first run creates `deploy/compose/.env` with random
   secrets. Edit it: set `BIND_ADDR` to the address of the VPN interface (never a public one),
   `BACKUP_PATH` and `BACKUP_WAL_PATH` to directories on the second disk.
3. **Copy `BACKUP_PASSPHRASE` somewhere safe, away from this host.** Every backup is
   encrypted with it. Without it a backup cannot be read, and nobody can recover it.
4. Create the first manager:
   `docker compose -f deploy/compose/docker-compose.yml --env-file deploy/compose/.env exec api python -m app.cli create-user --email you@agency.il --name "You" --role manager`
   It prints a TOTP URI (add it to an authenticator app) and ten recovery codes, once.
5. `make smoke` checks that the system answers, serves the app, sets the security headers and
   publishes only the proxy port. `make conformance` runs the full black-box checks.

TLS: the proxy terminates TLS with its own internal certificate authority. Give users the
CA certificate (`caddy_data` volume, `pki/authorities/local/root.crt`) or replace the
certificate with the agency's own in `deploy/compose/Caddyfile`.

## What is backed up, and how much can be lost

| Data | How | Frequency | Loss at most |
|---|---|---|---|
| Database | Continuous archiving of the write-ahead log (encrypted) plus a nightly base backup and a logical dump | log: every 5 minutes at most; backup: daily 01:30 UTC | minutes |
| Originals and the audit archive | Encrypted copy of every new object, content-addressed so only new files are copied | every 15 minutes | 15 minutes |
| Everything above, off the host | **You** copy `BACKUP_PATH` and `BACKUP_WAL_PATH` to another place (rsync, a NAS, cloud storage) | at least daily | depends on you |

The backups live in `BACKUP_PATH` (`<timestamp>/db.dump.enc`, `base.tar.gz.enc`,
`manifest.json`, and `files/`) and `BACKUP_WAL_PATH`. A backup on the same disk as the data
does not protect against losing the disk or the host: copy it off.

Check at any time: `make backup` takes one now; `make backup-verify` restores the latest
dump into a temporary database, compares every table count with the manifest, checks the
base backup and the checksum of the stored originals. The scheduler runs that check on the
1st of every month; a failure reaches the manager's Inbox and (with the observability
profile) Prometheus.

## Restore after a disaster

On a new host (or an empty project) with the same `.env` (same `BACKUP_PASSPHRASE`):

```sh
# the whole system as of the latest backup (database dump and originals)
scripts/restore.sh /path/to/backups/20261106T013000Z

# or: to an exact moment (undo a mistake, or lose minutes instead of a day)
scripts/restore.sh /path/to/backups/20261106T013000Z ansurance \
    --to "2026-11-06 14:30:00+00" --wal /path/to/wal
```

It starts an empty database and object store, restores into them, then starts the
application. Then run `make smoke`, log in, and use *Trust, verify chain* as manager.
Restoring never overwrites a database that already has data; use a new compose project name
(`scripts/restore.sh <dir> ansurance-new`) to rehearse. Rehearse once a quarter.

Originals written after the last 15-minute copy are not in the backup. After a point-in-time
recovery, documents added in the lost window may be missing from the object store; the
`reconcile` command lists them: `python -m app.cli reconcile`.

## Upgrade

```sh
git pull
make upgrade
```

It checks that the system is healthy, takes a backup, tags the running images for rollback,
builds and starts the new version (database migrations run when the API starts), and runs
the health gate. If the new version does not come up healthy, the previous images are
started again automatically. The database schema only moves forward: if a failed upgrade had
already applied a migration, the script says so and prints the command that restores the
pre-upgrade backup into a fresh stack.

## Updating pinned versions

Everything is pinned (images by digest, MinIO and its client by commit, packages by exact
version, lock files, CI actions by commit) so that a rebuild gives the same system.
`make pins` fails if anything has floated. To update something on purpose, change the pin in
one reviewed commit and run the full tests:

| What | How |
|---|---|
| A base image | `docker pull <name:tag>`, then `docker image inspect --format '{{index .RepoDigests 0}}' <name:tag>`; replace the digest in the Dockerfile, compose file and the test that names it (`make pins` lists every place) |
| MinIO or its client | pick the new release tag, then `git ls-remote https://github.com/minio/minio refs/tags/<tag>^{}` gives the commit; update `MINIO_COMMIT` / `MC_COMMIT` in `deploy/objectstore/Dockerfile` and `deploy/backup/Dockerfile`, and the test that documents them |
| Debian or Alpine packages | Debian: move the snapshot date in `backend/Dockerfile` and update the `=version` pins; Alpine: bump the `pkg=version` pins (`apk list` in a fresh container shows the available ones). If a pinned version disappears from the repository the build fails: that is intended |
| Python / Node packages | `uv lock --upgrade-package <name>` / `npm update <name>`; commit the lock file |
| Terraform provider | `terraform init -upgrade`, then `terraform providers lock -platform=linux_amd64 -platform=linux_arm64 -platform=darwin_amd64 -platform=darwin_arm64` |
| A CI action | resolve the new tag to its commit SHA and replace it (keep the version in a comment) |

Never use `latest`, a branch, or a floating tag. A deployment to AWS names the git SHA of the
images that CI pushed (`image_tag`); the plan refuses anything else.

## Integrations

Settings are `ANS_*` variables in `.env`; an integration only runs if it is in `ANS_APPROVED_ADAPTERS`.
Every call to an outside system is recorded in `adapter_call` (no message bodies).

- WhatsApp (Meta Cloud API): set the token, phone number id and app secret. The webhook
  `/api/webhooks/whatsapp` verifies Meta's signature; it must be reachable from Meta. On-prem
  behind a VPN that means a published path; on AWS add an exception to the allowed-CIDR rule
  for that path only.
- Mailbox (IMAP): polled by the worker; each Message-ID is ingested once.
- Agency system: `POST /api/import/agency` (dry run first). The existing system wins for policy
  data; contact data entered in ansurance is kept and only blanks are filled.
- Excel: `POST /api/excel/join` and `GET /api/export/clients.xlsx` (manager and back-office).

## Watching it

- Daily, the worker seals finished months of the audit log into Parquet files in the
  write-once `audit-archive` bucket, verifies the whole chain (database and archive), and
  checks that a backup exists from the last 26 hours. A problem appears as an urgent item in
  the manager's Inbox.
- Optional: `docker compose --profile observability up -d` adds Prometheus (alert rules for
  a stuck queue, failed audit verification, stale backups, failed restore tests, error rate,
  latency) and Grafana with a dashboard, on ports 9090 and 3000 of `BIND_ADDR`. Set
  `GRAFANA_PASSWORD` in `.env`.
- Logs are JSON on stdout: `make logs`.

## Security notes

- Only the proxy is published, on the VPN address. Assume the VPN is not the only defence:
  the application enforces login, two-factor authentication and roles itself.
- The application's object-store user can read and write but never delete: originals are
  protected by Object Lock and cannot be removed or shortened, even by the application.
- Reads of client records are audited asynchronously and in batches: a few seconds of read
  entries can be lost if the machine loses power. Writes, logins, denials, exports and
  downloads are written immediately.
- Passwords of the database and the object store are in `.env`; keep that file readable by
  root only (`chmod 600`), and out of backups of the host that are not themselves encrypted.
