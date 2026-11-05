"""Portal authentication.

The portal is a SvelteKit app. Signing in is a SvelteKit *form action* on ``POST /login``:
the browser sends the form fields URL-encoded with ``x-sveltekit-action: true`` and gets a
JSON "action result" back instead of an HTML page. On success the result is
``{"type": "redirect", "status": 303, "location": "/meters"}`` and the server sets an
HttpOnly session cookie, which httpx keeps in the client's cookie jar.

See PROTOCOL.md §2 for the observed exchange.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from app.errors import (
    PortalAuthError,
    PortalTimeoutError,
    PortalUnavailableError,
    UnexpectedPortalResponseError,
)

log = logging.getLogger(__name__)


class AuthClient:
    def __init__(self, http: httpx.AsyncClient, email: str, password: str):
        self._http = http
        self._email = email
        self._password = password
        self._lock = asyncio.Lock()
        self._generation = 0  # bumps on every successful login
        self.authenticated = False

    @property
    def generation(self) -> int:
        return self._generation

    async def ensure_login(self) -> None:
        if not self.authenticated:
            await self.login(seen_generation=self._generation)

    async def login(self, seen_generation: int | None = None) -> None:
        """Sign in. Concurrent callers that saw the same expired session share one login."""
        async with self._lock:
            if seen_generation is not None and seen_generation != self._generation and self.authenticated:
                return  # someone else already re-authenticated while we waited
            await self._do_login()

    async def _do_login(self) -> None:
        if not (self._email and self._password):
            raise PortalAuthError(
                "Portal credentials are not configured (set URJA_PORTAL_EMAIL and URJA_PORTAL_PASSWORD)."
            )
        self.authenticated = False
        self._http.cookies.clear()
        log.info("Signing in to the portal as %s", self._email)  # never log the password
        try:
            resp = await self._http.post(
                "/login",
                data={"email": self._email, "password": self._password},
                headers={
                    "accept": "application/json",
                    "x-sveltekit-action": "true",
                    # SvelteKit rejects cross-site form POSTs unless Origin matches the app.
                    "origin": str(self._http.base_url).rstrip("/"),
                },
            )
        except httpx.TimeoutException as exc:
            raise PortalTimeoutError("Timed out signing in to the portal.") from exc
        except httpx.TransportError as exc:
            raise PortalUnavailableError(f"Could not reach the portal: {exc.__class__.__name__}.") from exc

        if resp.status_code >= 500:
            raise PortalUnavailableError(f"Portal sign-in failed with HTTP {resp.status_code}.")
        try:
            result = resp.json()
        except ValueError as exc:
            raise UnexpectedPortalResponseError(
                f"Portal sign-in returned non-JSON (HTTP {resp.status_code})."
            ) from exc

        kind = result.get("type") if isinstance(result, dict) else None
        if kind == "redirect" and str(result.get("location", "")).startswith("/meters"):
            self.authenticated = True
            self._generation += 1
            log.info("Portal sign-in succeeded")
            return
        if kind in ("failure", "redirect"):
            # 'failure' is SvelteKit's fail(4xx, {...}); a redirect back to /login is also a rejection.
            raise PortalAuthError("The portal rejected the configured credentials.")
        raise UnexpectedPortalResponseError(f"Unexpected sign-in result type: {kind!r}.")
