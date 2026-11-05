"""Example payloads for the OpenAPI document. Values are real portal data (see PROTOCOL.md)."""

_NETWORK = {
    "zone": {"code": "Z-01", "name": "Jaipur Zone 1"},
    "circle": {"code": "C-01", "name": "Circle 1"},
    "division": {"code": "D-01", "name": "Division 1"},
    "subdivision": {"code": "SD-01", "name": "Subdivision 1"},
    "substation": {"code": "SS-01", "name": "Substation 1"},
    "feeder": {"code": "F-001", "name": "Feeder 1"},
    "distribution_transformer": {"code": "DT-001", "name": "Malviya Nagar DT 1"},
}
_COORDS = {"latitude": 26.938961002479868, "longitude": 75.83095696146852}
_METER = {
    "meter_id": "J100000",
    "serial_number": "SE33962",
    "make": "HPL",
    "phase_type": "single",
    "installation_status": "Decommissioned",
    "installation_type": "Whole Current",
    "dt_code": "DT-001",
}

METER_LIST = {
    "items": [
        _METER,
        {
            "meter_id": "J100001",
            "serial_number": "GE84132",
            "make": "L&T",
            "phase_type": "single",
            "installation_status": "Installed",
            "installation_type": "CT Operated",
            "dt_code": "DT-002",
        },
    ],
    "page": 1,
    "page_size": 2,
    "total": 403,
    "total_pages": 202,
    "data_as_of": "2026-11-05T09:20:00Z",
}

METER_DETAIL = {**_METER, "network": _NETWORK, "coordinates": _COORDS, "data_quality_notes": []}

METER_LOCATION = {
    "meter_id": "J100400",
    "coordinates": {"latitude": 26.882703003792653, "longitude": 75.74015781362327},
    "network": {
        "zone": {"code": "Z-01", "name": "Jaipur Zone 1"},
        "circle": {"code": "C-01", "name": "Circle 1"},
        "division": {"code": "D-07", "name": "Division 7"},
        "subdivision": {"code": "SD-07", "name": "Subdivision 7"},
        "substation": {"code": "SS-07", "name": "Substation 7"},
        "feeder": {"code": "F-007", "name": "Feeder 7"},
        "distribution_transformer": {"code": "DT-007", "name": "Old Malviya Nagar Xfmr"},
    },
    "transformer": {"code": "DT-007", "name": "Sanganer DT 7", "feeder_code": "F-007", "capacity_kva": 63.0},
    "data_quality_notes": [
        "Transformer DT-007 is named 'Old Malviya Nagar Xfmr' on the meter record "
        "but 'Sanganer DT 7' in the transformer registry."
    ],
}

METER_CONSUMPTION = {
    "meter_id": "J100000",
    "requested_from": "2026-06-01",
    "requested_to": "2026-06-01",
    "first_reading_at": "2026-06-01T00:00:00",
    "last_reading_at": "2026-06-01T23:30:00",
    "interval_minutes": 30,
    "reading_count": 48,
    "summary": {"kwh_consumed": 19.87, "kvah_consumed": 21.46, "average_voltage_r_v": 229.69},
    "readings": [
        {
            "timestamp": "2026-06-01T00:00:00",
            "cumulative_kwh": 47972.42,
            "cumulative_kvah": 51810.22,
            "voltage_r_v": 237.0,
            "interval_kwh": None,
        },
        {
            "timestamp": "2026-06-01T00:30:00",
            "cumulative_kwh": 47972.85,
            "cumulative_kvah": 51810.67,
            "voltage_r_v": 222.0,
            "interval_kwh": 0.43,
        },
    ],
    "data_quality_notes": [],
}


def error(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


ERRORS = {
    400: error("invalid_request", "Invalid meter ID: use 1-32 letters, digits, '-' or '_' (for example J100000)."),
    401: error("invalid_api_key", "Missing or invalid X-API-Key header."),
    404: error("meter_not_found", "Meter 'J999999' was not found."),
    422: error("validation_error", "page_size: Input should be less than or equal to 100"),
    502: error("portal_unavailable", "Portal returned HTTP 502 for /portal/meters/J100000/energy."),
    503: error("portal_rate_limited", "The portal is rate limiting requests; try again shortly."),
    504: error("portal_timeout", "Portal did not answer in time (/portal/meters/J100000/energy)."),
}


def ok(example: dict) -> dict:
    return {200: {"content": {"application/json": {"example": example}}}}
