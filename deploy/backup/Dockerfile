# Backup and restore tooling: PostgreSQL client, the MinIO client (built from source like the
# object store itself), OpenSSL for encryption. One script, three jobs (see backup.sh).
FROM golang:1.24-alpine AS mc
ARG MC_VERSION=RELEASE.2025-08-13T08-35-41Z
RUN apk add --no-cache git
WORKDIR /src
RUN git clone --depth 1 --branch ${MC_VERSION} https://github.com/minio/mc.git mc \
 && cd mc && CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o /out/mc .

FROM postgres:16-alpine
RUN apk add --no-cache bash openssl coreutils jq tzdata
COPY --from=mc /out/mc /usr/local/bin/mc
COPY backup.sh /opt/backup/backup.sh
RUN chmod +x /opt/backup/backup.sh
ENTRYPOINT ["/opt/backup/backup.sh"]
CMD ["run"]
