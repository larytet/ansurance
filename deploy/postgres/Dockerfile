# PostgreSQL plus OpenSSL, used by the WAL archive_command to encrypt each segment.
# Pinned: base image by digest, the package by exact version.
FROM postgres:16-alpine@sha256:0cefaabd882e42bcb2a2f3dc56d3ce4770560ba62fd7f197f1a2e008fd5731ab
RUN apk add --no-cache openssl=3.5.9-r0
