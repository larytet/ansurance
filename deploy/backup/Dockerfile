# Backup and restore tooling: PostgreSQL client, the MinIO client (built from source like the
# object store itself), OpenSSL for encryption. One script, several jobs (see backup.sh).
# Everything is pinned (digests, commit, exact package versions); see docs/OPERATIONS.md.
FROM golang:1.24-alpine@sha256:8bee1901f1e530bfb4a7850aa7a479d17ae3a18beb6e09064ed54cfd245b7191 AS mc
ARG MC_COMMIT=7394ce0dd2a80935aded936b09fa12cbb3cb8096
RUN apk add --no-cache git=2.52.0-r0
WORKDIR /src
RUN git init -q mc && cd mc \
 && git fetch -q --depth 1 https://github.com/minio/mc.git ${MC_COMMIT} \
 && git checkout -q FETCH_HEAD && test "$(git rev-parse HEAD)" = "${MC_COMMIT}" \
 && CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o /out/mc .

FROM postgres:16-alpine@sha256:0cefaabd882e42bcb2a2f3dc56d3ce4770560ba62fd7f197f1a2e008fd5731ab
RUN apk add --no-cache bash=5.3.9-r1 openssl=3.5.9-r0 coreutils=9.11-r0 jq=1.8.2-r0 tzdata=2026e-r0
COPY --from=mc /out/mc /usr/local/bin/mc
COPY backup.sh /opt/backup/backup.sh
RUN chmod +x /opt/backup/backup.sh
ENTRYPOINT ["/opt/backup/backup.sh"]
CMD ["run"]
