# Every external input is pinned so a rebuild gives the same image: the base image by digest,
# uv at an exact version, Python packages from uv.lock (with hashes). No parser of untrusted
# files and no Tesseract is installed here: they are in the sandbox image (sandbox/Dockerfile). To update, see docs/OPERATIONS.md "Updating pinned versions".
FROM python:3.12-slim@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016 AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1
RUN pip install --no-cache-dir uv==0.5.31 \
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
