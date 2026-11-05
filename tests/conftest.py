"""Test helpers.

``FakePortal`` is an ``httpx.MockTransport`` handler that replays *real* portal responses
recorded in ``fixtures/portal_responses.json`` and simulates the portal behaviours we
observed (session cookie, signed export, 404s, 401 on expiry, 429 rate limiting).
Mocked tests never touch the network. Live tests live in ``test_live_portal.py``.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import re
from collections import Counter
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient

from app.clients.portal_client import PortalClient
from app.config import Settings
from app.main import create_app

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "portal_responses.json").read_text(encoding="utf-8"))
EMAIL = "operator@example.test"
PASSWORD = "not-the-real-password"
SESSION = "session=test-session-token"


KNOWN_METERS = {m["meterId"] for m in FIXTURES["export_subset"]["body"]["data"]}


def fixture(name: str) -> dict:
    return copy.deepcopy(FIXTURES[name]["body"])


class FakePortal:
    def __init__(self) -> None:
        self.calls: Counter[str] = Counter()
        self.valid_session = "test-session-token"
        self.reject_login = False
        # name -> list of (status, body) responses to return before normal behaviour
        self.queued: dict[str, list[tuple[int, object]]] = {}
        self.fail_export = False
        self.raise_on: Optional[Callable[[httpx.Request], Optional[Exception]]] = None

    def expire_session(self) -> None:
        self.valid_session = "rotated-" + self.valid_session

    def queue(self, path_key: str, *responses: tuple[int, object]) -> None:
        self.queued.setdefault(path_key, []).extend(responses)

    # -------------------------------------------------------------- handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        key = path if path == "/portal/meters/search" else re.sub(r"/meters/[^/]+", "/meters/{id}", path)
        self.calls[key] += 1
        if self.raise_on and (exc := self.raise_on(request)):
            raise exc
        if self.queued.get(key):
            status, body = self.queued[key].pop(0)
            return httpx.Response(status, json=body)

        if path == "/login" and request.method == "POST":
            return self._login(request)

        authed = f"session={self.valid_session}" in request.headers.get("cookie", "")
        if path.endswith("/__data.json"):
            if not authed:
                return httpx.Response(200, json=fixture("meter_page_unauthenticated"))
            meter_id = path.split("/")[2]
            name = f"meter_page_{meter_id}"
            return httpx.Response(200, json=fixture(name if name in FIXTURES else "meter_page_not_found"))

        if path.startswith("/portal/"):
            if not authed:
                return httpx.Response(401, json=fixture("portal_unauthorized"))
            return self._portal(request)
        return httpx.Response(404, text="Not Found")

    def _login(self, request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode())
        assert request.headers.get("x-sveltekit-action") == "true"
        assert request.headers.get("origin") == "https://portal.test"
        if self.reject_login or form.get("email") != [EMAIL] or form.get("password") != [PASSWORD]:
            return httpx.Response(
                400,
                json={"type": "failure", "status": 400, "data": '[{"error":1},"Invalid email or password"]'},
            )
        return httpx.Response(
            200,
            json=fixture("login_success"),
            headers={"set-cookie": f"session={self.valid_session}; Path=/; HttpOnly"},
        )

    def _portal(self, request: httpx.Request) -> httpx.Response:
        path, params = request.url.path, request.url.params
        if path == "/portal/meters/search":
            page = params.get("page", "1")
            q = params.get("q", "")
            name = {("", "1"): "search_page1", ("", "21"): "search_page21", ("SE339", "1"): "search_q_se339"}.get(
                (q, page), "search_page99"
            )
            return httpx.Response(200, json=fixture(name))
        m = re.fullmatch(r"/portal/meters/([^/]+)/(geo|energy)", path)
        if m:
            meter_id, kind = m.groups()
            if kind == "geo":
                name = f"geo_{meter_id}"
                return httpx.Response(200 if name in FIXTURES else 404, json=fixture(name if name in FIXTURES else "geo_not_found"))
            if meter_id not in KNOWN_METERS:
                return httpx.Response(404, json=fixture("geo_not_found"))
            name = {
                ("J100400", None, None): "energy_J100400_default",
                ("J100089", "2026-06-01", "2026-06-30"): "energy_J100089_june",
                ("J100000", "2026-06-01", "2026-06-01"): "energy_J100000_2026_06_01",
            }.get((meter_id, params.get("from"), params.get("to")), "energy_J100000_empty")
            return httpx.Response(200, json=fixture(name))
        if path == "/portal/dts":
            return httpx.Response(200, json=fixture("dts_page2" if params.get("page") == "2" else "dts_page1"))
        if path == "/portal/keys":
            return httpx.Response(200, json=fixture("keys"))
        if path == "/portal/export":
            return self._export(request)
        return httpx.Response(404, json={"error": "not_found", "message": "Not found"})

    def _export(self, request: httpx.Request) -> httpx.Response:
        secret = fixture("keys")["data"]["signingSecret"]
        ts = request.headers.get("x-timestamp", "")
        expected = hmac.new(
            secret.encode(), "\n".join(["GET", "/portal/export", request.url.query.decode(), ts]).encode(), hashlib.sha256
        ).hexdigest()
        if self.fail_export or request.headers.get("x-signature") != expected:
            return httpx.Response(401, json=fixture("export_signature_invalid"))
        return httpx.Response(200, json=fixture("export_subset"))


async def _no_sleep(_: float) -> None:
    return None


def make_settings(**overrides) -> Settings:
    base = dict(
        portal_base_url="https://portal.test",
        portal_email=EMAIL,
        portal_password=PASSWORD,
        portal_max_retries=2,
        portal_retry_backoff_seconds=0.01,
        cache_ttl_seconds=300,
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def portal() -> FakePortal:
    return FakePortal()


@pytest.fixture
def make_client(portal: FakePortal):
    clients = []

    def _make(**overrides) -> TestClient:
        settings = make_settings(**overrides)
        portal_client = PortalClient(settings, transport=httpx.MockTransport(portal), sleep=_no_sleep)
        client = TestClient(create_app(settings=settings, portal_client=portal_client))
        client.__enter__()
        clients.append(client)
        return client

    yield _make
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture
def client(make_client) -> TestClient:
    return make_client()


@pytest.fixture
def portal_client(portal: FakePortal) -> PortalClient:
    return PortalClient(make_settings(), transport=httpx.MockTransport(portal), sleep=_no_sleep)
