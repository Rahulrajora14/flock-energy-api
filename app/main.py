"""FastAPI application: wiring, lifecycle and error handling."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.clients.portal_client import PortalClient
from app.config import Settings, get_settings
from app.errors import ServiceError
from app.routes import health, meters
from app.services.meter_service import MeterService

log = logging.getLogger("app")

DESCRIPTION = """
REST API over the **Urja Meter Ops** portal. The service signs in to the portal with
server-side credentials, reads smart-meter data the same way the portal's own web UI
does, and returns it as consistent JSON. It never changes anything on the portal.

All errors use one shape: `{"error": {"code": "...", "message": "..."}}`.
"""


def _error(status: int, code: str, message: str, headers: Optional[dict] = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}}, headers=headers)


def create_app(
    settings: Optional[Settings] = None,
    transport: Optional[httpx.AsyncBaseTransport] = None,
    portal_client: Optional[PortalClient] = None,
) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        client = portal_client or PortalClient(settings, transport=transport)
        app.state.settings = settings
        app.state.portal_client = client
        app.state.meter_service = MeterService(client, settings.cache_ttl_seconds)
        if not settings.has_credentials:
            log.warning("URJA_PORTAL_EMAIL / URJA_PORTAL_PASSWORD are not set; portal calls will fail.")
        try:
            yield
        finally:
            await client.aclose()

    app = FastAPI(
        title="Urja Meter API",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        openapi_tags=[
            {"name": "health", "description": "Service and portal status."},
            {"name": "meters", "description": "Smart-meter data."},
            {"name": "network", "description": "Supply network: transformers and hierarchy."},
        ],
    )

    @app.exception_handler(ServiceError)
    async def service_error(_: Request, exc: ServiceError) -> JSONResponse:
        if exc.status_code >= 500:
            log.warning("%s: %s", exc.code, exc.message)
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
        return _error(exc.status_code, exc.code, exc.message, headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        parts = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", []) if p not in ("query", "path"))
            parts.append(f"{loc}: {err.get('msg')}")
        return _error(422, "validation_error", "; ".join(parts) or "Invalid request.")

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return _error(exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("Unhandled error")
        return _error(500, "internal_error", "An unexpected error occurred.")

    app.include_router(health.router)
    app.include_router(meters.router)
    return app


app = create_app()
