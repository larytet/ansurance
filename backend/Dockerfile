# Every external input is pinned so a rebuild gives the same image: the base image by digest,
# Debian packages from a dated snapshot at exact versions, uv at an exact version, Python
# packages from uv.lock (with hashes). To update, see docs/OPERATIONS.md "Updating pinned versions".
FROM python:3.12-slim@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016 AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1
RUN printf 'Types: deb\nURIs: http://snapshot.debian.org/archive/debian/20261105T000000Z\nSuites: trixie trixie-updates\nComponents: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.pgp\n\nTypes: deb\nURIs: http://snapshot.debian.org/archive/debian-security/20261105T000000Z\nSuites: trixie-security\nComponents: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.pgp\n' > /etc/apt/sources.list.d/debian.sources \
 && printf 'Acquire::Check-Valid-Until "false";\n' > /etc/apt/apt.conf.d/99snapshot \
 && apt-get update && apt-get install -y --no-install-recommends \
      tesseract-ocr=5.5.0-1+b1 \
      tesseract-ocr-heb=1:4.1.0-2 tesseract-ocr-ara=1:4.1.0-2 tesseract-ocr-rus=1:4.1.0-2 \
      tesseract-ocr-fra=1:4.1.0-2 tesseract-ocr-eng=1:4.1.0-2 \
      libglib2.0-0t64=2.84.4-3~deb13u5 \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir uv==0.5.31 \
 && useradd --system --uid 10001 --create-home app
WORKDIR /srv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
ARG BUILD_ID=dev
LABEL org.ansurance.build=$BUILD_ID
USER app
ENV PATH="/srv/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
