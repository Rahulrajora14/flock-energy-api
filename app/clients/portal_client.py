"""Low-level HTTP client for the Urja Meter Ops portal.

Responsibilities: transport, authentication refresh, retries, rate-limit back-off and
turning raw portal responses into plain dicts. It does *not* reshape data for the public
API; that lives in ``app.services``.

Every method here maps to a request the portal's own browser UI makes (PROTOCOL.md §4).
The client never calls anything that changes portal state.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any
from urllib.parse import quote

import httpx

from app.clients.auth_client import AuthClient
from app.clients.devalue import DevalueError, unflatten
from app.config import Settings
from app.errors import (
    MeterNotFoundError,
    PortalRateLimitedError,
    PortalTimeoutError,
    PortalUnavailableError,
    UnexpectedPortalResponseError,
)

log = logging.getLogger(__name__)

EXPORT_PATH = "/portal/export"
EXPORT_QUERY = "page=1"  # what the portal UI signs and sends; the server ignores it
RATE_LIMIT_WINDOW_SECONDS = 30  # observed, see PROTOCOL.md §7


def sign_request(method: str, path: str, query: str, secret: str, timestamp: int) -> str:
    """HMAC-SHA256 over ``METHOD\\nPATH\\nQUERY\\nTIMESTAMP`` (hex), as the portal's JS does."""
    message = "\n".join([method, path, query, str(timestamp)]).encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


class PortalClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self._settings = settings
        self._http = httpx.AsyncClient(
            base_url=settings.portal_base_url,
            timeout=settings.portal_timeout_seconds,
            transport=transport,
            headers={"user-agent": "flock-energy-urja-api/1.0"},
            follow_redirects=False,
        )
        self.auth = AuthClient(self._http, settings.portal_email, settings.portal_password)
        self._sleep = sleep
        self._signing_secret: str | None = None

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ transport

    async def _request(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        session_expired: Callable[[httpx.Response], bool] | None = None,
    ) -> httpx.Response:
        """GET with re-login on expired session and retries on 429 / 5xx / network errors."""
        session_expired = session_expired or _portal_unauthorized
        await self.auth.ensure_login()
        relogged = False
        attempt = 0
        while True:
            generation = self.auth.generation
            try:
                resp = await self._http.get(path, params=params, headers=headers)
            except httpx.TimeoutException as exc:
                if attempt < self._settings.portal_max_retries:
                    attempt += 1
                    await self._backoff(attempt, f"timeout on {path}")
                    continue
                raise PortalTimeoutError(f"Portal did not answer in time ({path}).") from exc
            except httpx.TransportError as exc:
                if attempt < self._settings.portal_max_retries:
                    attempt += 1
                    await self._backoff(attempt, f"{exc.__class__.__name__} on {path}")
                    continue
                raise PortalUnavailableError(f"Could not reach the portal: {exc.__class__.__name__}.") from exc

            if session_expired(resp):
                if relogged:
                    raise UnexpectedPortalResponseError("Portal still reports no session right after signing in.")
                log.info("Portal session expired; signing in again")
                await self.auth.login(seen_generation=generation)
                relogged = True
                continue

            if resp.status_code == 429:
                if attempt < self._settings.portal_max_retries:
                    attempt += 1
                    await self._backoff(attempt, f"rate limited on {path}", resp.headers.get("retry-after"))
                    continue
                raise PortalRateLimitedError(
                    "The portal is rate limiting requests; try again shortly.",
                    retry_after=RATE_LIMIT_WINDOW_SECONDS,
                )

            if resp.status_code >= 500:
                if attempt < self._settings.portal_max_retries:
                    attempt += 1
                    await self._backoff(attempt, f"HTTP {resp.status_code} on {path}")
                    continue
                raise PortalUnavailableError(f"Portal returned HTTP {resp.status_code} for {path}.")

            return resp

    async def _backoff(self, attempt: int, reason: str, retry_after: str | None = None) -> None:
        delay = self._settings.portal_retry_backoff_seconds * (2 ** (attempt - 1))
        if retry_after and retry_after.isdigit():
            delay = max(delay, float(retry_after))
        delay = min(delay, RATE_LIMIT_WINDOW_SECONDS)
        log.warning("%s; retry %d in %.1fs", reason, attempt, delay)
        await self._sleep(delay)

    @staticmethod
    def _json(resp: httpx.Response) -> Any:
        try:
            return resp.json()
        except ValueError as exc:
            raise UnexpectedPortalResponseError(
                f"Portal returned non-JSON content for {resp.request.url.path} (HTTP {resp.status_code})."
            ) from exc

    def _ok_json(self, resp: httpx.Response) -> Any:
        if resp.status_code != 200:
            raise UnexpectedPortalResponseError(
                f"Portal returned HTTP {resp.status_code} for {resp.request.url.path}."
            )
        return self._json(resp)

    @staticmethod
    def _require(payload: Any, key: str, kind: type, where: str) -> Any:
        if not isinstance(payload, dict) or not isinstance(payload.get(key), kind):
            raise UnexpectedPortalResponseError(f"{where}: expected '{key}' of type {kind.__name__}.")
        return payload[key]

    # ------------------------------------------------------------------ endpoints

    async def search_meters(self, query: str = "", page: int = 1) -> dict[str, Any]:
        """``GET /portal/meters/search`` - 20 meters per page, substring match on id/serial."""
        resp = await self._request("/portal/meters/search", params={"q": query, "page": page})
        payload = self._ok_json(resp)
        self._require(payload, "data", list, "meter search")
        return payload

    async def get_meter_page(self, meter_id: str) -> dict[str, Any]:
        """``GET /meters/{id}/__data.json`` - server data behind the meter detail page."""
        resp = await self._request(
            f"/meters/{quote(meter_id, safe='')}/__data.json",
            session_expired=_sveltekit_login_redirect,
        )
        payload = self._ok_json(resp)
        nodes = payload.get("nodes") if isinstance(payload, dict) else None
        if not isinstance(nodes, list) or len(nodes) < 3:
            raise UnexpectedPortalResponseError("meter page: unexpected __data.json structure.")
        node = nodes[-1] or {}
        if node.get("type") == "error":
            if node.get("status") == 404:
                raise MeterNotFoundError(f"Meter '{meter_id}' was not found.")
            raise UnexpectedPortalResponseError(f"meter page: portal error {node.get('status')}.")
        if node.get("type") != "data":
            raise UnexpectedPortalResponseError("meter page: missing data node.")
        try:
            data = unflatten(node["data"])
        except (DevalueError, KeyError, TypeError) as exc:
            raise UnexpectedPortalResponseError(f"meter page: cannot decode payload ({exc}).") from exc
        if not isinstance(data, dict) or "detail" not in data or "hierarchy" not in data:
            raise UnexpectedPortalResponseError("meter page: 'detail'/'hierarchy' missing.")
        return data

    async def get_meter_geo(self, meter_id: str) -> dict[str, Any]:
        """``GET /portal/meters/{id}/geo`` - latitude/longitude as strings."""
        resp = await self._request(f"/portal/meters/{quote(meter_id, safe='')}/geo")
        self._raise_not_found(resp, meter_id)
        return self._require(self._ok_json(resp), "data", dict, "meter geo")

    async def get_meter_energy(
        self, meter_id: str, date_from: date | None = None, date_to: date | None = None
    ) -> list[dict[str, Any]]:
        """``GET /portal/meters/{id}/energy[?from=YYYY-MM-DD&to=YYYY-MM-DD]``."""
        params: dict[str, str] = {}
        if date_from:
            params["from"] = date_from.isoformat()
        if date_to:
            params["to"] = date_to.isoformat()
        resp = await self._request(f"/portal/meters/{quote(meter_id, safe='')}/energy", params=params or None)
        self._raise_not_found(resp, meter_id)
        return self._require(self._ok_json(resp), "data", list, "meter energy")

    async def list_transformers_page(self, page: int = 1) -> dict[str, Any]:
        """``GET /portal/dts?page=N`` - distribution transformer registry, 20 per page."""
        resp = await self._request("/portal/dts", params={"page": page})
        payload = self._ok_json(resp)
        self._require(payload, "data", list, "transformers")
        return payload

    async def list_all_transformers(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            payload = await self.list_transformers_page(page)
            batch = payload["data"]
            items.extend(batch)
            total = payload.get("total")
            if not batch or not isinstance(total, int) or len(items) >= total or page >= 100:
                return items
            page += 1

    async def export_meters(self) -> list[dict[str, Any]]:
        """Bulk export of every meter (the 'Export all meters' button), HMAC-signed."""
        for attempt in range(2):
            secret = await self._get_signing_secret(refresh=attempt > 0)
            ts = int(time.time())
            headers = {
                "x-timestamp": str(ts),
                "x-signature": sign_request("GET", EXPORT_PATH, EXPORT_QUERY, secret, ts),
            }
            resp = await self._request(
                f"{EXPORT_PATH}?{EXPORT_QUERY}",
                headers=headers,
                session_expired=_portal_unauthorized,
            )
            if resp.status_code == 401 and _error_code(resp) == "signature_invalid":
                log.warning("Export signature rejected; refreshing signing key")
                continue
            payload = self._ok_json(resp)
            return self._require(payload, "data", list, "meter export")
        raise UnexpectedPortalResponseError("Portal rejected the export signature twice.")

    async def _get_signing_secret(self, refresh: bool = False) -> str:
        if self._signing_secret and not refresh:
            return self._signing_secret
        resp = await self._request("/portal/keys")
        data = self._require(self._ok_json(resp), "data", dict, "signing key")
        secret = data.get("signingSecret")
        if not isinstance(secret, str) or not secret:
            raise UnexpectedPortalResponseError("signing key: 'signingSecret' missing.")
        self._signing_secret = secret
        return secret

    @staticmethod
    def _raise_not_found(resp: httpx.Response, meter_id: str) -> None:
        if resp.status_code == 404:
            raise MeterNotFoundError(f"Meter '{meter_id}' was not found.")


def _error_code(resp: httpx.Response) -> str | None:
    try:
        body = resp.json()
    except ValueError:
        return None
    return body.get("error") if isinstance(body, dict) else None


def _portal_unauthorized(resp: httpx.Response) -> bool:
    return resp.status_code == 401 and _error_code(resp) == "unauthorized"


def _sveltekit_login_redirect(resp: httpx.Response) -> bool:
    """__data.json answers an anonymous request with 200 {"type":"redirect","location":"/login"}."""
    if resp.status_code in (301, 302, 303, 307, 308):
        return "/login" in resp.headers.get("location", "")
    if resp.status_code != 200:
        return False
    try:
        body = resp.json()
    except ValueError:
        return False
    return isinstance(body, dict) and body.get("type") == "redirect" and "/login" in str(body.get("location"))
