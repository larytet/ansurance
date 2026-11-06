"""FastAPI application factory."""

import logging
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import Engine

from app import __version__
from app.config import Settings, get_settings
from app.db import database_ready, make_engine
from app.logging import configure_logging, request_id_var

log = logging.getLogger("ansurance")


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    engine = engine or make_engine(settings)

    app = FastAPI(
        title="ansurance",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

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
