"""[mocked] Pure transformation functions against recorded portal payloads."""

from datetime import datetime

import pytest

from app.clients.devalue import unflatten
from app.services import transform
from tests.conftest import fixture


def page(name: str) -> dict:
    return unflatten(fixture(name)["nodes"][2]["data"])


@pytest.mark.parametrize(
    "label,expected",
    [
        ("Feeder 1 (F-001)", ("F-001", "Feeder 1")),
        ("Old Malviya Nagar Xfmr (DT-007)", ("DT-007", "Old Malviya Nagar Xfmr")),
        ("Circle 6", (None, "Circle 6")),
        ("", (None, None)),
        (None, (None, None)),
    ],
)
def test_parse_name_code(label, expected):
    node = transform.parse_name_code(label)
    assert (node.code, node.name) == expected


def test_legacy_and_v2_meter_pages_produce_same_shape():
    legacy = transform.detail_from_meter_page(page("meter_page_J100000"), fixture("geo_J100000")["data"])
    v2 = transform.detail_from_meter_page(page("meter_page_J100004"), fixture("geo_J100004")["data"])
    assert legacy.model_dump().keys() == v2.model_dump().keys()
    assert v2.make == "Genus" and v2.installation_status == "Faulty"


def test_meter_page_and_export_agree():
    """Two independent portal sources for the same meter must give the same answer."""
    export = {r["meterId"]: r for r in fixture("export_subset")["data"]}
    for meter_id in ["J100000", "J100004", "J100400"]:
        from_page = transform.detail_from_meter_page(page(f"meter_page_{meter_id}"), fixture(f"geo_{meter_id}")["data"])
        from_export = transform.detail_from_export(export[meter_id])
        assert from_page == from_export


def test_page_loses_code_when_name_is_blank():
    detail = transform.detail_from_meter_page(page("meter_page_J100162"))
    assert detail.network.feeder.code is None and detail.network.feeder.name is None
    assert "Portal has no feeder for this meter." in detail.data_quality_notes
    assert "Portal has no usable coordinates for this meter." in detail.data_quality_notes


def test_search_row_conversion():
    meter = transform.meter_from_search(fixture("search_page1")["data"][3])
    assert meter.serial_number == "L&84997"  # odd value kept as the portal reports it
    assert meter.installation_type is None  # not present in the search view


@pytest.mark.parametrize("value,expected", [("48438.74", 48438.74), ("", None), (None, None), ("abc", None), (63, 63.0)])
def test_to_float(value, expected):
    assert transform.to_float(value) == expected


def test_portal_timestamp_is_day_first():
    assert transform.parse_portal_timestamp("01/06/2026 23:30") == datetime(2026, 6, 1, 23, 30)


def test_coordinates_out_of_range_are_dropped():
    assert transform.make_coordinates("123", "75") is None
    assert transform.make_coordinates("26.9", "75.8").latitude == 26.9


def test_consumption_skips_bad_timestamps_and_flags_resets():
    rows = [
        {"timestamp": "01/06/2026 00:00", "kwh": "10", "kvah": "11", "voltR": "230"},
        {"timestamp": "2026-06-01T00:30", "kwh": "11", "kvah": "12", "voltR": "230"},
        {"timestamp": "01/06/2026 01:00", "kwh": "5", "kvah": "13", "voltR": ""},
    ]
    result = transform.build_consumption("J1", rows)
    assert result.reading_count == 2
    assert result.readings[1].interval_kwh == -5
    assert any("unrecognised timestamp" in n for n in result.data_quality_notes)
    assert any("decreases" in n for n in result.data_quality_notes)
    assert result.summary.average_voltage_r_v == 230
