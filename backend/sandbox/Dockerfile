# The sandbox image: the only place that reads an untrusted PDF, image or (later) Office file.
# It holds no application code, no credentials and no route out (see deploy/compose).
# Same pinning rules as the API image. Build context: backend/.
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
 && useradd --system --uid 10002 --no-create-home sandbox
WORKDIR /srv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --only-group sandbox --no-install-project
COPY sandbox ./sandbox
USER sandbox
ENV PATH="/srv/.venv/bin:$PATH" PYTHONPATH=/srv WORK=/work
EXPOSE 8080
CMD ["python", "-m", "sandbox.server", "--max-requests", "10"]
