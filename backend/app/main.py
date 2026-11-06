"""FastAPI application factory."""

import logging
import uuid
from collections.abc import Awaitable, Callable

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app import __version__
from app.adapters.antivirus import Antivirus, ClamdAntivirus, FakeAntivirus
from app.audit.readbuffer import ReadAuditBuffer
from app.auth.router import router as auth_router
from app.blob.base import BlobStore
from app.blob.fake import FakeBlobStore
from app.blob.s3 import S3BlobStore
from app.clients.router import router as clients_router
from app.config import Profile, Settings, get_settings
from app.consent.router import router as consent_router
from app.db import database_ready, make_engine
from app.documents.router import router as documents_router
from app.inbox.router import router as inbox_router
from app.intake.context import IntakeContext
from app.intake.router import router as intake_router
from app.logging import configure_logging, request_id_var
from app.messaging.router import router as messaging_router
from app.processes.router import router as processes_router

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
    intake: IntakeContext | None = None,
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
    app.state.read_audit = ReadAuditBuffer(
        sessionmaker(engine),
        settings.read_audit_flush_seconds,
        settings.read_audit_batch,
    )
    app.router.add_event_handler("shutdown", app.state.read_audit.stop)
    # the real client address (not the proxy's) feeds the per-IP login limit and the audit log
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=settings.trusted_proxies)
    app.state.sessionmaker = sessionmaker(engine, expire_on_commit=False)
    app.state.http = http or httpx.Client(timeout=10)
    app.include_router(auth_router)
    app.state.blobs = blobs or make_blob_store(settings)
    app.state.antivirus = antivirus or make_antivirus(settings)
    app.state.intake = intake or IntakeContext(settings, app.state.blobs, app.state.antivirus, None)
    app.include_router(clients_router)
    app.include_router(documents_router)
    app.include_router(inbox_router)
    app.include_router(consent_router)
    app.include_router(messaging_router)
    app.include_router(processes_router)
    app.include_router(intake_router)

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
