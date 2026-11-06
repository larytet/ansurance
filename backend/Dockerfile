FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends \
      tesseract-ocr tesseract-ocr-heb tesseract-ocr-ara tesseract-ocr-rus tesseract-ocr-fra \
      tesseract-ocr-eng libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir uv==0.5.* \
 && useradd --system --uid 10001 --create-home app
WORKDIR /srv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
USER app
ENV PATH="/srv/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
