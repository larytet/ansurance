# MinIO built from source (upstream no longer publishes images on Docker Hub).
# Source: https://github.com/minio/minio  (AGPLv3; used unmodified as a separate service).
FROM golang:1.24-alpine AS build
ARG MINIO_VERSION=RELEASE.2025-10-15T17-29-55Z
RUN apk add --no-cache git
WORKDIR /src
RUN git clone --depth 1 --branch ${MINIO_VERSION} https://github.com/minio/minio.git .
RUN CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o /out/minio .

FROM alpine:3.20
RUN apk add --no-cache ca-certificates curl && adduser -D -u 10002 minio \
 && mkdir /data && chown minio /data
COPY --from=build /out/minio /usr/local/bin/minio
USER minio
EXPOSE 9000
VOLUME /data
HEALTHCHECK NONE
ENTRYPOINT ["minio"]
