# MinIO server and client (mc) built from source; upstream no longer publishes images on
# Docker Hub. Sources: https://github.com/minio/minio and https://github.com/minio/mc
# (AGPLv3, used unmodified as a separate service).
#
# Pinned for a predictable deployment: base images by digest, the sources by COMMIT (a tag can
# be moved, a commit cannot), Alpine packages by exact version, Go modules by go.sum.
#   minio  RELEASE.2025-10-15T17-29-55Z
#   mc     RELEASE.2025-08-13T08-35-41Z
FROM golang:1.24-alpine@sha256:8bee1901f1e530bfb4a7850aa7a479d17ae3a18beb6e09064ed54cfd245b7191 AS build
ARG MINIO_COMMIT=9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a
ARG MC_COMMIT=7394ce0dd2a80935aded936b09fa12cbb3cb8096
RUN apk add --no-cache git=2.52.0-r0
WORKDIR /src
RUN git init -q minio && cd minio \
 && git fetch -q --depth 1 https://github.com/minio/minio.git ${MINIO_COMMIT} \
 && git checkout -q FETCH_HEAD && test "$(git rev-parse HEAD)" = "${MINIO_COMMIT}" \
 && CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o /out/minio .
RUN git init -q mc && cd mc \
 && git fetch -q --depth 1 https://github.com/minio/mc.git ${MC_COMMIT} \
 && git checkout -q FETCH_HEAD && test "$(git rev-parse HEAD)" = "${MC_COMMIT}" \
 && CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o /out/mc .

FROM alpine:3.20@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc
RUN apk add --no-cache ca-certificates=20261020-r0 curl=8.14.1-r2 bash=5.2.26-r0 \
 && adduser -D -u 10002 minio && mkdir /data && chown minio /data
COPY --from=build /out/minio /out/mc /usr/local/bin/
COPY init.sh app-policy.json /opt/objectstore/
USER minio
EXPOSE 9000
VOLUME /data
ENTRYPOINT ["minio"]
