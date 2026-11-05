"""[mocked] Meter endpoints, end to end through FastAPI, replaying recorded portal responses."""

import pytest


# ------------------------------------------------------------------ list


def test_list_meters_from_export_snapshot(client, portal):
    resp = client.get("/api/meters")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 6 and body["page"] == 1 and body["page_size"] == 20 and body["total_pages"] == 1
    first = body["items"][0]
    assert first == {
        "meter_id": "J100000",
        "serial_number": "SE33962",
        "make": "HPL",
        "phase_type": "single",
        "installation_status": "Decommissioned",
        "installation_type": "Whole Current",
        "dt_code": "DT-001",
    }
    assert body["data_as_of"]
    assert portal.calls["/portal/export"] == 1


def test_list_meters_is_cached(client, portal):
    client.get("/api/meters")
    client.get("/api/meters?page=2&page_size=2")
    client.get("/api/meters/J100004")
    assert portal.calls["/portal/export"] == 1
    assert portal.calls["/login"] == 1


def test_list_meters_pagination(client):
    body = client.get("/api/meters?page=2&page_size=4").json()
    assert [m["meter_id"] for m in body["items"]] == ["J100162", "J100400"]
    assert body["total_pages"] == 2


def test_list_meters_page_past_end_is_empty(client):
    body = client.get("/api/meters?page=50").json()
    assert body["items"] == [] and body["total"] == 6


@pytest.mark.parametrize(
    "query,expected",
    [
        ("q=se339", ["J100000"]),
        ("q=j10000", ["J100000", "J100004"]),
        ("installation_status=faulty", ["J100004", "J100162"]),
        ("phase_type=three", ["J100011", "J100089", "J100400"]),
        ("dt_code=DT-007", ["J100400"]),
        ("zone_code=Z-03", ["J100011", "J100162"]),
        ("feeder_code=F-003", ["J100162"]),
        ("make=HPL&installation_type=Whole%20Current", ["J100000", "J100011", "J100162"]),
        ("q=nomatch", []),
    ],
)
def test_list_meters_filters(client, query, expected):
    body = client.get(f"/api/meters?{query}").json()
    assert [m["meter_id"] for m in body["items"]] == expected
    assert body["total"] == len(expected)


@pytest.mark.parametrize("query", ["page=0", "page_size=101", "page=abc", "phase_type=two"])
def test_list_meters_rejects_invalid_params(client, query):
    resp = client.get(f"/api/meters?{query}")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


# ------------------------------------------------------------------ detail


def test_get_meter_detail(client):
    body = client.get("/api/meters/J100000").json()
    assert body["meter_id"] == "J100000"
    assert body["installation_type"] == "Whole Current"
    assert body["network"]["zone"] == {"code": "Z-01", "name": "Jaipur Zone 1"}
    assert body["network"]["distribution_transformer"] == {"code": "DT-001", "name": "Malviya Nagar DT 1"}
    assert body["coordinates"] == {"latitude": 26.938961002479868, "longitude": 75.83095696146852}
    assert body["data_quality_notes"] == []


def test_meter_id_is_case_insensitive(client):
    assert client.get("/api/meters/j100004").json()["meter_id"] == "J100004"


def test_missing_hierarchy_values_become_null_with_note(client):
    body = client.get("/api/meters/J100011").json()
    assert body["network"]["circle"] == {"code": None, "name": "Circle 6"}
    assert any("no code for circle" in n for n in body["data_quality_notes"])
    body = client.get("/api/meters/J100162").json()
    assert body["network"]["feeder"] == {"code": "F-003", "name": None}


def test_unknown_meter_returns_404(client, portal):
    resp = client.get("/api/meters/J999999")
    assert resp.status_code == 404
    assert resp.json() == {"error": {"code": "meter_not_found", "message": "Meter 'J999999' was not found."}}
    # Not in the snapshot, so the portal's own meter page was asked before answering 404.
    assert portal.calls["/meters/{id}/__data.json"] == 1


@pytest.mark.parametrize("bad_id", ["J10%2F0", "J!00000", "%20", "A" * 40])
def test_malformed_meter_id_returns_400(client, bad_id):
    resp = client.get(f"/api/meters/{bad_id}")
    assert resp.status_code in (400, 404)
    if resp.status_code == 400:
        assert resp.json()["error"]["code"] == "invalid_request"


def test_detail_falls_back_to_meter_page_when_export_fails(client, portal):
    portal.fail_export = True
    resp = client.get("/api/meters/J100004")
    assert resp.status_code == 200
    body = resp.json()
    # v2 'classData' record format decoded from the meter page
    assert body["serial_number"] == "SE65293" and body["installation_type"] == "CT Operated"
    assert body["network"]["feeder"] == {"code": "F-005", "name": "Feeder 5"}
    assert body["coordinates"]["latitude"] == 26.907425164505934


def test_list_fails_cleanly_when_export_unavailable(client, portal):
    portal.fail_export = True
    resp = client.get("/api/meters")
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "unexpected_portal_response"


def test_stale_snapshot_served_when_refresh_fails(make_client, portal):
    c = make_client(cache_ttl_seconds=0)
    assert c.get("/api/meters").status_code == 200
    portal.fail_export = True
    resp = c.get("/api/meters")
    assert resp.status_code == 200 and resp.json()["total"] == 6


# ------------------------------------------------------------------ location


def test_location_includes_network_and_transformer(client):
    body = client.get("/api/meters/J100000/location").json()
    assert body["coordinates"] == {"latitude": 26.938961002479868, "longitude": 75.83095696146852}
    assert body["network"]["feeder"] == {"code": "F-001", "name": "Feeder 1"}
    assert body["transformer"] == {
        "code": "DT-001",
        "name": "Malviya Nagar DT 1",
        "feeder_code": "F-001",
        "capacity_kva": 100.0,
    }
    assert body["data_quality_notes"] == []


def test_location_reports_transformer_name_conflict(client):
    body = client.get("/api/meters/J100400/location").json()
    assert body["network"]["distribution_transformer"]["name"] == "Old Malviya Nagar Xfmr"
    assert body["transformer"]["name"] == "Sanganer DT 7"
    assert any("Old Malviya Nagar Xfmr" in n and "Sanganer DT 7" in n for n in body["data_quality_notes"])


def test_location_unknown_meter_404(client):
    assert client.get("/api/meters/J999999/location").status_code == 404


# ------------------------------------------------------------------ consumption


def test_consumption_half_hourly_day(client):
    body = client.get("/api/meters/J100000/consumption?from=2026-06-01&to=2026-06-01").json()
    assert body["reading_count"] == 48
    assert body["interval_minutes"] == 30
    assert body["first_reading_at"] == "2026-06-01T00:00:00"
    assert body["last_reading_at"] == "2026-06-01T23:30:00"
    assert body["readings"][0] == {
        "timestamp": "2026-06-01T00:00:00",
        "cumulative_kwh": 47972.42,
        "cumulative_kvah": 51810.22,
        "voltage_r_v": 237.0,
        "interval_kwh": None,
    }
    assert body["readings"][1]["interval_kwh"] == pytest.approx(0.43)
    assert body["summary"]["kwh_consumed"] == pytest.approx(47992.29 - 47972.42)
    assert body["requested_from"] == "2026-06-01" and body["requested_to"] == "2026-06-01"


def test_consumption_default_window_daily_meter(client):
    body = client.get("/api/meters/J100400/consumption").json()
    assert body["reading_count"] == 8
    assert body["interval_minutes"] == 1440
    assert body["requested_from"] is None


def test_consumption_duplicate_blank_row_is_merged(client):
    body = client.get("/api/meters/J100089/consumption?from=2026-06-01&to=2026-06-30").json()
    assert body["reading_count"] == 30  # portal sent 31 rows; the last is a blank duplicate
    assert body["readings"][-1]["cumulative_kwh"] == 12732.34
    assert any("duplicate" in n for n in body["data_quality_notes"])


def test_consumption_empty_window(client):
    body = client.get("/api/meters/J100000/consumption?from=2026-07-01&to=2026-07-31").json()
    assert body["reading_count"] == 0 and body["readings"] == []
    assert body["summary"] == {"kwh_consumed": None, "kvah_consumed": None, "average_voltage_r_v": None}
    assert body["interval_minutes"] is None


def test_consumption_unknown_meter_404(client):
    resp = client.get("/api/meters/J999999/consumption")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "meter_not_found"


@pytest.mark.parametrize("query", ["from=01/06/2026", "to=2026-13-01", "from=yesterday"])
def test_consumption_rejects_bad_dates(client, query):
    resp = client.get(f"/api/meters/J100000/consumption?{query}")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_consumption_rejects_reversed_range(client, portal):
    resp = client.get("/api/meters/J100000/consumption?from=2026-06-10&to=2026-06-01")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_request"
    assert portal.calls["/portal/meters/{id}/energy"] == 0


# ------------------------------------------------------------------ network


def test_transformers_registry_is_complete(client, portal):
    body = client.get("/api/transformers").json()
    assert body["total"] == 40
    assert body["items"][0] == {"code": "DT-001", "name": "Malviya Nagar DT 1", "feeder_code": "F-001", "capacity_kva": 100.0}
    assert portal.calls["/portal/dts"] == 2


def test_network_hierarchy(client):
    body = client.get("/api/network/hierarchy").json()
    assert body["meter_count"] == 6
    assert body["levels"][0] == "zone" and body["levels"][-1] == "distribution_transformer"
    assert sum(z["meter_count"] for z in body["roots"]) == 6
    z1 = next(z for z in body["roots"] if z["code"] == "Z-01")
    assert z1["meter_count"] == 3


# ------------------------------------------------------------------ upstream failures


def test_bad_credentials_return_502(client, portal):
    portal.reject_login = True
    resp = client.get("/api/meters")
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "portal_authentication_failed"


def test_rate_limit_exhausted_returns_503_with_retry_after(client, portal):
    limited = (429, {"error": "rate_limited", "message": "Rate limit exceeded; slow down."})
    portal.queue("/portal/meters/{id}/energy", limited, limited, limited)
    resp = client.get("/api/meters/J100400/consumption")
    assert resp.status_code == 503
    assert resp.headers["retry-after"] == "30"
    assert resp.json()["error"]["code"] == "portal_rate_limited"


def test_portal_down_returns_502(client, portal):
    import httpx

    portal.raise_on = lambda req: httpx.ConnectError("boom")
    resp = client.get("/api/meters/J100000/consumption")
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "portal_unavailable"


def test_portal_timeout_returns_504(client, portal):
    import httpx

    portal.raise_on = lambda req: httpx.ReadTimeout("slow") if "energy" in req.url.path else None
    resp = client.get("/api/meters/J100000/consumption")
    assert resp.status_code == 504
    assert resp.json()["error"]["code"] == "portal_timeout"


def test_unexpected_portal_payload_returns_502(client, portal):
    portal.queue("/portal/meters/{id}/energy", (200, {"rows": []}))
    resp = client.get("/api/meters/J100000/consumption")
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "unexpected_portal_response"
