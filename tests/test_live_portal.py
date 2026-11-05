"""[LIVE] Integration tests against the real Urja Meter Ops portal.

Skipped unless URJA_LIVE_TESTS=1 and credentials are present in the environment / .env.
They only read data. Run with:  URJA_LIVE_TESTS=1 pytest -m live -v
"""

import os

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app

settings = get_settings()

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("URJA_LIVE_TESTS") != "1" or not settings.has_credentials,
        reason="live tests disabled (set URJA_LIVE_TESTS=1 and portal credentials)",
    ),
]


@pytest.fixture(scope="module")
def live():
    with TestClient(create_app(settings=settings)) as c:
        yield c


def test_live_portal_authentication(live):
    body = live.get("/api/health/portal").json()
    assert body == {**body, "status": "ok", "authenticated": True}


def test_live_list_meters(live):
    body = live.get("/api/meters?page_size=5").json()
    assert body["total"] >= 1 and len(body["items"]) == 5
    assert all(m["meter_id"] for m in body["items"])


def test_live_meter_detail_matches_list(live):
    first = live.get("/api/meters?page_size=1").json()["items"][0]
    detail = live.get(f"/api/meters/{first['meter_id']}").json()
    assert {k: detail[k] for k in first} == first


def test_live_location(live):
    body = live.get("/api/meters/J100000/location").json()
    assert body["coordinates"]["latitude"] and body["network"]["zone"]["code"]


def test_live_consumption(live):
    body = live.get("/api/meters/J100000/consumption").json()
    assert body["reading_count"] > 0
    assert body["readings"][0]["timestamp"] <= body["readings"][-1]["timestamp"]


def test_live_unknown_meter(live):
    assert live.get("/api/meters/J999999").status_code == 404


def test_live_wrong_password_is_reported():
    bad = settings.__class__(**{**settings.__dict__, "portal_password": "definitely-wrong"})
    with TestClient(create_app(settings=bad)) as c:
        resp = c.get("/api/meters/J100000/consumption")
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "portal_authentication_failed"
