# MinIO server and client (mc) built from source; upstream no longer publishes images on
# Docker Hub. Sources: https://github.com/minio/minio and https://github.com/minio/mc
# (AGPLv3, used unmodified as a separate service).
FROM golang:1.24-alpine AS build
ARG MINIO_VERSION=RELEASE.2025-10-15T17-29-55Z
ARG MC_VERSION=RELEASE.2025-08-13T08-35-41Z
RUN apk add --no-cache git
WORKDIR /src
RUN git clone --depth 1 --branch ${MINIO_VERSION} https://github.com/minio/minio.git minio \
 && cd minio && CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o /out/minio .
RUN git clone --depth 1 --branch ${MC_VERSION} https://github.com/minio/mc.git mc \
 && cd mc && CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o /out/mc .

FROM alpine:3.20
RUN apk add --no-cache ca-certificates curl bash && adduser -D -u 10002 minio \
 && mkdir /data && chown minio /data
COPY --from=build /out/minio /out/mc /usr/local/bin/
COPY init.sh app-policy.json /opt/objectstore/
USER minio
EXPOSE 9000
VOLUME /data
ENTRYPOINT ["minio"]
