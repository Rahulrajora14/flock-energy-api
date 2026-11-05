from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app import __version__
from app.dependencies import require_api_key
from app.errors import ServiceError
from app.models.responses import HealthResponse, PortalHealthResponse

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Service liveness")
async def health() -> HealthResponse:
    """Always cheap: does not contact the portal."""
    return HealthResponse(status="healthy", service="urja-meter-api", version=__version__)


@router.get(
    "/health/portal",
    response_model=PortalHealthResponse,
    summary="Portal connectivity and authentication",
    dependencies=[Depends(require_api_key)],
)
async def portal_health(request: Request) -> PortalHealthResponse:
    """Signs in to the portal if needed and runs one lightweight search request."""
    portal = request.app.state.portal_client
    settings = request.app.state.settings
    try:
        await portal.search_meters("", 1)
        return PortalHealthResponse(status="ok", portal_url=settings.portal_base_url, authenticated=True)
    except ServiceError as exc:
        return PortalHealthResponse(
            status="unavailable",
            portal_url=settings.portal_base_url,
            authenticated=portal.auth.authenticated,
            detail=f"{exc.code}: {exc.message}",
        )
