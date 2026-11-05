"""Domain exceptions. Each maps to one HTTP status and a stable machine-readable error code."""

from __future__ import annotations


class ServiceError(Exception):
    status_code = 500
    code = "internal_error"

    def __init__(self, message: str, *, retry_after: int | None = None):
        super().__init__(message)
        self.message = message
        self.retry_after = retry_after


class MeterNotFoundError(ServiceError):
    status_code = 404
    code = "meter_not_found"


class InvalidRequestError(ServiceError):
    status_code = 400
    code = "invalid_request"


class PortalAuthError(ServiceError):
    """The portal rejected our credentials (or none are configured)."""

    status_code = 502
    code = "portal_authentication_failed"


class PortalRateLimitedError(ServiceError):
    status_code = 503
    code = "portal_rate_limited"


class PortalUnavailableError(ServiceError):
    """Connection failure or repeated 5xx from the portal."""

    status_code = 502
    code = "portal_unavailable"


class PortalTimeoutError(ServiceError):
    status_code = 504
    code = "portal_timeout"


class UnexpectedPortalResponseError(ServiceError):
    """The portal answered, but not in a shape we understand."""

    status_code = 502
    code = "unexpected_portal_response"
