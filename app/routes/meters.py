from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Path, Query

from app.dependencies import get_meter_service, require_api_key
from app.routes import examples as ex
from app.models.meter import MeterConsumption, MeterDetail, MeterLocation
from app.models.responses import ErrorResponse, MeterPage, NetworkHierarchy, TransformerList
from app.services.meter_service import MeterFilters, MeterService

router = APIRouter(prefix="/api", dependencies=[Depends(require_api_key)])



def _err(status: int, description: str) -> dict:
    return {
        status: {
            "model": ErrorResponse,
            "description": description,
            "content": {"application/json": {"example": ex.ERRORS[status]}},
        }
    }


UPSTREAM_ERRORS = {
    **_err(502, "Portal unavailable, rejected our credentials, or answered unexpectedly."),
    **_err(503, "Portal is rate limiting; retry after the Retry-After header (seconds)."),
    **_err(504, "Portal did not answer in time."),
}
INVALID_ID = _err(400, "Malformed meter ID or invalid parameter combination.")
NOT_FOUND = _err(404, "No meter with this ID exists on the portal.")
VALIDATION = _err(422, "Query parameter failed validation.")
UNAUTHORIZED = _err(401, "SERVICE_API_KEY is set and X-API-Key is missing or wrong.")

MeterIdPath = Path(..., description="Portal meter number, e.g. J100000 (case-insensitive).", examples=["J100000"])


@router.get(
    "/meters",
    response_model=MeterPage,
    tags=["meters"],
    summary="List meters",
    responses={**ex.ok(ex.METER_LIST), **INVALID_ID, **UNAUTHORIZED, **VALIDATION, **UPSTREAM_ERRORS},
)
async def list_meters(
    page: int = Query(1, ge=1, description="1-based page number."),
    page_size: int = Query(20, ge=1, le=100, description="Items per page (max 100)."),
    q: Optional[str] = Query(None, max_length=64, description="Substring of meter ID or serial number."),
    installation_status: Optional[str] = Query(None, description="e.g. Installed, Faulty, Decommissioned."),
    make: Optional[str] = Query(None, description="Manufacturer, e.g. HPL, Genus, L&T."),
    phase_type: Optional[Literal["single", "three"]] = Query(None),
    installation_type: Optional[str] = Query(None, description="e.g. 'Whole Current', 'CT Operated'."),
    dt_code: Optional[str] = Query(None, description="Distribution transformer code, e.g. DT-001."),
    feeder_code: Optional[str] = Query(None, description="Feeder code, e.g. F-001."),
    zone_code: Optional[str] = Query(None, description="Zone code, e.g. Z-01."),
    service: MeterService = Depends(get_meter_service),
) -> MeterPage:
    """Meters sorted by ID. Filters are exact and case-insensitive (``q`` is a substring match)."""
    filters = MeterFilters(
        q=q,
        installation_status=installation_status,
        make=make,
        phase_type=phase_type,
        installation_type=installation_type,
        dt_code=dt_code,
        feeder_code=feeder_code,
        zone_code=zone_code,
    )
    return await service.list_meters(filters, page, page_size)


@router.get(
    "/meters/{meter_id}",
    response_model=MeterDetail,
    tags=["meters"],
    summary="Meter details",
    responses={**ex.ok(ex.METER_DETAIL), **INVALID_ID, **UNAUTHORIZED, **NOT_FOUND, **VALIDATION, **UPSTREAM_ERRORS},
)
async def get_meter(
    meter_id: str = MeterIdPath, service: MeterService = Depends(get_meter_service)
) -> MeterDetail:
    """Nameplate attributes, network position and coordinates of one meter."""
    return await service.get_meter(meter_id)


@router.get(
    "/meters/{meter_id}/location",
    response_model=MeterLocation,
    tags=["meters"],
    summary="Meter location and network position",
    responses={**ex.ok(ex.METER_LOCATION), **INVALID_ID, **UNAUTHORIZED, **NOT_FOUND, **VALIDATION, **UPSTREAM_ERRORS},
)
async def get_meter_location(
    meter_id: str = MeterIdPath, service: MeterService = Depends(get_meter_service)
) -> MeterLocation:
    """Coordinates, zone-to-transformer path, and the transformer's registry entry."""
    return await service.get_location(meter_id)


@router.get(
    "/meters/{meter_id}/consumption",
    response_model=MeterConsumption,
    tags=["meters"],
    summary="Meter consumption readings",
    responses={**ex.ok(ex.METER_CONSUMPTION), **INVALID_ID, **UNAUTHORIZED, **NOT_FOUND, **VALIDATION, **UPSTREAM_ERRORS},
)
async def get_meter_consumption(
    meter_id: str = MeterIdPath,
    date_from: Optional[date] = Query(None, alias="from", description="First day, YYYY-MM-DD (inclusive)."),
    date_to: Optional[date] = Query(None, alias="to", description="Last day, YYYY-MM-DD (inclusive)."),
    service: MeterService = Depends(get_meter_service),
) -> MeterConsumption:
    """Register readings from the portal, oldest first.

    Without dates the portal returns its default window: the last 7 days of available data.
    With only ``from`` it returns 7 days starting there; with only ``to``, 7 days ending there.
    """
    return await service.get_consumption(meter_id, date_from, date_to)


@router.get(
    "/transformers",
    response_model=TransformerList,
    tags=["network"],
    summary="Distribution transformer registry",
    responses={**UNAUTHORIZED, **UPSTREAM_ERRORS},
)
async def list_transformers(service: MeterService = Depends(get_meter_service)) -> TransformerList:
    items = await service.list_transformers()
    return TransformerList(items=items, total=len(items))


@router.get(
    "/network/hierarchy",
    response_model=NetworkHierarchy,
    tags=["network"],
    summary="Network hierarchy tree",
    responses={**UNAUTHORIZED, **UPSTREAM_ERRORS},
)
async def network_hierarchy(service: MeterService = Depends(get_meter_service)) -> NetworkHierarchy:
    """Zone → circle → division → subdivision → substation → feeder → transformer, with meter counts."""
    return await service.network_hierarchy()
