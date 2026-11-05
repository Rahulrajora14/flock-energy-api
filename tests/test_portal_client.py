"""[mocked] Portal client: authentication, session expiry, signing, retries."""

import httpx
import pytest

from app.clients.portal_client import PortalClient, sign_request
from app.errors import MeterNotFoundError, PortalAuthError, PortalRateLimitedError, PortalUnavailableError
from tests.conftest import _no_sleep, make_settings


def test_signature_matches_portal_javascript():
    # Reference value computed in the portal's own page with crypto.subtle (same inputs).
    assert (
        sign_request("GET", "/portal/export", "page=1", "test-secret", 1700000000)
        == "b4b4d3dacaea949dfabeb63650cd8f500ebc3719cf8246de8306a2e31f18d4d6"
    )


async def test_login_happens_once_and_cookie_is_reused(portal_client, portal):
    await portal_client.search_meters()
    await portal_client.search_meters("SE339")
    assert portal.calls["/login"] == 1
    assert portal_client.auth.authenticated


async def test_missing_credentials_fail_without_network(portal):
    client = PortalClient(make_settings(portal_password=""), transport=httpx.MockTransport(portal), sleep=_no_sleep)
    with pytest.raises(PortalAuthError, match="not configured"):
        await client.search_meters()
    assert portal.calls["/login"] == 0


async def test_wrong_credentials_raise_auth_error(portal_client, portal):
    portal.reject_login = True
    with pytest.raises(PortalAuthError):
        await portal_client.search_meters()


async def test_expired_session_triggers_one_relogin(portal_client, portal):
    await portal_client.search_meters()
    portal.expire_session()  # server forgets our cookie -> 401 unauthorized
    payload = await portal_client.search_meters()
    assert payload["total"] == 403
    assert portal.calls["/login"] == 2


async def test_expired_session_on_sveltekit_page_triggers_relogin(portal_client, portal):
    await portal_client.search_meters()
    portal.expire_session()  # __data.json answers {"type":"redirect","location":"/login"}
    page = await portal_client.get_meter_page("J100000")
    assert page["meterId"] == "J100000"
    assert portal.calls["/login"] == 2


async def test_meter_page_decodes_devalue_payload(portal_client):
    page = await portal_client.get_meter_page("J100000")
    assert page["hierarchy"]["Feeder"] == "Feeder 1 (F-001)"
    assert page["detail"]["data"][1] == {"parameterName": "Serial No", "parameterValue": "SE33962"}


async def test_meter_page_not_found(portal_client):
    with pytest.raises(MeterNotFoundError):
        await portal_client.get_meter_page("J999999")


async def test_geo_not_found(portal_client):
    with pytest.raises(MeterNotFoundError):
        await portal_client.get_meter_geo("J999999")


async def test_rate_limit_is_retried(portal_client, portal):
    limited = (429, {"error": "rate_limited", "message": "Rate limit exceeded; slow down."})
    portal.queue("/portal/meters/search", limited, limited)
    payload = await portal_client.search_meters()
    assert payload["total"] == 403
    assert portal.calls["/portal/meters/search"] == 3


async def test_rate_limit_gives_up_after_retries(portal_client, portal):
    limited = (429, {"error": "rate_limited", "message": "Rate limit exceeded; slow down."})
    portal.queue("/portal/meters/search", limited, limited, limited)
    with pytest.raises(PortalRateLimitedError):
        await portal_client.search_meters()


async def test_transient_502_is_retried(portal_client, portal):
    # The portal answered one real request with 502 during exploration.
    portal.queue("/portal/meters/search", (502, {}))
    assert (await portal_client.search_meters())["total"] == 403


async def test_persistent_5xx_raises_unavailable(portal_client, portal):
    portal.queue("/portal/meters/search", (503, {}), (503, {}), (503, {}))
    with pytest.raises(PortalUnavailableError):
        await portal_client.search_meters()


async def test_export_signs_requests(portal_client, portal):
    rows = await portal_client.export_meters()
    assert len(rows) == 6
    assert portal.calls["/portal/keys"] == 1


async def test_export_refreshes_key_after_signature_rejection(portal_client, portal):
    await portal_client.export_meters()
    portal.queue("/portal/export", (401, {"error": "signature_invalid", "message": "Missing or invalid request signature."}))
    rows = await portal_client.export_meters()
    assert len(rows) == 6
    assert portal.calls["/portal/keys"] == 2


async def test_transformer_pages_are_all_read(portal_client):
    rows = await portal_client.list_all_transformers()
    assert [r["code"] for r in rows][:1] == ["DT-001"] and len(rows) == 40
