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

from app import __version__, metrics
from app.adapters.antivirus import Antivirus, ClamdAntivirus, FakeAntivirus
from app.adapters.esign import ESign, FakeESign
from app.adapters.translate import FakeTranslator, Translator
from app.audit.readbuffer import ReadAuditBuffer
from app.auth.router import router as auth_router
from app.blob.base import BlobStore
from app.blob.fake import FakeBlobStore
from app.blob.s3 import S3BlobStore
from app.campaigns.router import router as campaigns_router
from app.clients.router import router as clients_router
from app.config import Profile, Settings, get_settings
from app.consent.router import router as consent_router
from app.db import database_ready, make_engine
from app.documents.router import router as documents_router
from app.flows.router import hook as esign_hook
from app.flows.router import router as flows_router
from app.inbox.router import router as inbox_router
from app.intake.context import IntakeContext, make_sandbox
from app.intake.router import router as intake_router
from app.integrations.calls import require_approved
from app.integrations.mailbox import ImapMailbox, Mailbox
from app.integrations.router import router as integrations_router
from app.integrations.smtp import FakeMailer, Mailer, SmtpMailer
from app.integrations.webhooks import router as webhooks_router
from app.integrations.whatsapp import FakeWhatsApp, MetaCloudWhatsApp, WhatsApp
from app.logging import configure_logging, request_id_var
from app.manager.router import router as manager_router
from app.messaging.router import router as messaging_router
from app.portal.router import router as portal_router
from app.processes.router import router as processes_router
from app.tasks.router import router as tasks_router
from app.tax.router import router as tax_router
from app.translation.router import router as translation_router

log = logging.getLogger("ansurance")


def make_blob_store(settings: Settings) -> BlobStore:
    if settings.profile is Profile.DEMO and not settings.s3_endpoint:
        return FakeBlobStore(settings.s3_bucket_originals)
    return S3BlobStore(settings)


def make_archive_store(settings: Settings) -> BlobStore:
    """The write-once bucket that holds sealed audit files."""
    if settings.profile is Profile.DEMO and not settings.s3_endpoint:
        return FakeBlobStore(settings.s3_bucket_audit_archive)
    return S3BlobStore(settings, bucket=settings.s3_bucket_audit_archive)


def make_whatsapp(settings: Settings) -> WhatsApp | None:
    if settings.profile is Profile.DEMO and settings.whatsapp_token is None:
        return FakeWhatsApp()
    if settings.whatsapp_token is None or not settings.whatsapp_phone_id:
        return None  # not configured: messages stay queued and the webhook answers 503
    require_approved(settings, "whatsapp")
    return MetaCloudWhatsApp(
        settings.whatsapp_phone_id,
        settings.whatsapp_token.get_secret_value(),
        settings.whatsapp_api_base,
    )


def make_esign(settings: Settings) -> ESign | None:
    if settings.profile is Profile.DEMO:
        return FakeESign()
    return None  # no provider adapter yet: Comsign or DocuSign needs a contract and credentials


def make_mailer(settings: Settings) -> Mailer | None:
    if settings.profile is Profile.DEMO and not settings.smtp_host:
        return FakeMailer()
    if not (settings.smtp_host and settings.smtp_from):
        return None  # not configured: e-mail stays queued
    require_approved(settings, "smtp")
    return SmtpMailer(
        settings.smtp_host,
        settings.smtp_port,
        settings.smtp_user,
        settings.smtp_password.get_secret_value() if settings.smtp_password else "",
        settings.smtp_from,
        settings.smtp_starttls,
    )


def make_mailbox(settings: Settings) -> Mailbox | None:
    if not (settings.imap_host and settings.imap_user and settings.imap_password):
        return None  # not configured
    require_approved(settings, "imap")
    return ImapMailbox(
        settings.imap_host,
        settings.imap_port,
        settings.imap_user,
        settings.imap_password.get_secret_value(),
        settings.imap_folder,
    )


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
    archive_blobs: BlobStore | None = None,
    whatsapp: WhatsApp | None = None,
    esign: ESign | None = None,
    translator: Translator | None = None,
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
    metrics.install(app, sessionmaker(engine), app.state.read_audit)
    # the real client address (not the proxy's) feeds the per-IP login limit and the audit log
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=settings.trusted_proxies)
    app.state.sessionmaker = sessionmaker(engine, expire_on_commit=False)
    app.state.http = http or httpx.Client(timeout=10)
    app.include_router(auth_router)
    app.state.blobs = blobs or make_blob_store(settings)
    app.state.archive_blobs = archive_blobs or make_archive_store(settings)
    app.state.whatsapp = whatsapp if whatsapp is not None else make_whatsapp(settings)
    app.state.translator = (
        translator
        if translator is not None
        else (FakeTranslator() if settings.profile is Profile.DEMO else None)
    )
    app.state.esign = esign if esign is not None else make_esign(settings)
    app.state.antivirus = antivirus or make_antivirus(settings)
    app.state.intake = intake or IntakeContext(
        settings, app.state.blobs, app.state.antivirus, None, make_sandbox(settings)
    )
    app.include_router(clients_router)
    app.include_router(documents_router)
    app.include_router(inbox_router)
    app.include_router(consent_router)
    app.include_router(messaging_router)
    app.include_router(processes_router)
    app.include_router(intake_router)
    app.include_router(webhooks_router)
    app.include_router(integrations_router)
    app.include_router(flows_router)
    app.include_router(campaigns_router)
    app.include_router(manager_router)
    app.include_router(tax_router)
    app.include_router(tasks_router)
    app.include_router(translation_router)
    app.include_router(portal_router)
    app.include_router(esign_hook)

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
