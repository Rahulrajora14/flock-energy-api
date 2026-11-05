"""FastAPI dependencies: service access and optional API-key protection."""

from __future__ import annotations

import secrets
from typing import Optional

from fastapi import Request, Security
from fastapi.security import APIKeyHeader

from app.config import Settings
from app.errors import ServiceError
from app.services.meter_service import MeterService

api_key_header = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,
    description="Required only when the server is started with SERVICE_API_KEY set.",
)


class InvalidApiKeyError(ServiceError):
    status_code = 401
    code = "invalid_api_key"


def get_meter_service(request: Request) -> MeterService:
    return request.app.state.meter_service


def require_api_key(request: Request, key: Optional[str] = Security(api_key_header)) -> None:
    settings: Settings = request.app.state.settings
    expected = settings.service_api_key
    if expected and not (key and secrets.compare_digest(key, expected)):
        raise InvalidApiKeyError("Missing or invalid X-API-Key header.")
