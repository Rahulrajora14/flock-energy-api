"""[mocked] The committed openapi.json must match what the running app serves."""

import json
from pathlib import Path

from openapi_spec_validator import validate

from app.main import create_app
from tests.conftest import make_settings

SPEC = Path(__file__).resolve().parents[1] / "openapi.json"


def test_openapi_file_matches_app():
    generated = create_app(settings=make_settings()).openapi()
    committed = json.loads(SPEC.read_text(encoding="utf-8"))
    assert committed == generated, "openapi.json is stale: run `python scripts/export_openapi.py`"


def test_openapi_file_is_valid_openapi_3():
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    assert spec["openapi"].startswith("3.")
    validate(spec)


def test_every_route_is_documented():
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    assert set(spec["paths"]) == {
        "/api/health",
        "/api/health/portal",
        "/api/meters",
        "/api/meters/{meter_id}",
        "/api/meters/{meter_id}/location",
        "/api/meters/{meter_id}/consumption",
        "/api/transformers",
        "/api/network/hierarchy",
    }
