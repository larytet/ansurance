"""FastAPI application factory."""

import logging
import uuid
from collections.abc import Awaitable, Callable

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from app import __version__
from app.adapters.antivirus import Antivirus, ClamdAntivirus, FakeAntivirus
from app.auth.router import router as auth_router
from app.blob.base import BlobStore
from app.blob.fake import FakeBlobStore
from app.blob.s3 import S3BlobStore
from app.clients.router import router as clients_router
from app.config import Profile, Settings, get_settings
from app.db import database_ready, make_engine
from app.documents.router import router as documents_router
from app.logging import configure_logging, request_id_var

log = logging.getLogger("ansurance")


def make_blob_store(settings: Settings) -> BlobStore:
    if settings.profile is Profile.DEMO and not settings.s3_endpoint:
        return FakeBlobStore(settings.s3_bucket_originals)
    return S3BlobStore(settings)


def make_antivirus(settings: Settings) -> Antivirus:
    if settings.clamd_host:
        return ClamdAntivirus(settings.clamd_host, settings.clamd_port)
    if settings.profile is Profile.DEMO:
        return FakeAntivirus()
    raise RuntimeError("ANS_CLAMD_HOST is required outside the demo profile")


def create_app(
    settings: Settings | None = None,
    engine: Engine | None = None,
    http: httpx.Client | None = None,
    blobs: BlobStore | None = None,
    antivirus: Antivirus | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    engine = engine or make_engine(settings)

    app = FastAPI(
        title="ansurance",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    app.state.settings = settings
    app.state.sessionmaker = sessionmaker(engine, expire_on_commit=False)
    app.state.http = http or httpx.Client(timeout=10)
    app.include_router(auth_router)
    app.state.blobs = blobs or make_blob_store(settings)
    app.state.antivirus = antivirus or make_antivirus(settings)
    app.include_router(clients_router)
    app.include_router(documents_router)

    @app.middleware("http")
    async def request_id_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["x-request-id"] = rid
        return response

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__, "profile": settings.profile.value}

    @app.get("/api/ready", response_model=None)
    def ready() -> Response | dict[str, str]:
        if database_ready(engine):
            return {"status": "ready"}
        log.warning("database not reachable")
        return JSONResponse({"status": "unavailable"}, status_code=503)

    return app
