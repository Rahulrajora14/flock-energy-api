# Urja Meter API

A FastAPI service that puts a clean REST API in front of **Urja Meter Ops**, a browser-only smart-meter
portal used by an electricity distribution utility. Other applications and data teams can now get
meter, network, location and consumption data as consistent JSON without scraping the portal or logging into it.

- **[PROTOCOL.md](PROTOCOL.md)**: how the portal actually works (verified findings vs assumptions)
- **[REFLECTION.md](REFLECTION.md)**: what went well, what didn't, what's next
- **[openapi.json](openapi.json)**: OpenAPI 3.1 spec (generated from the code and tested to match it)

---

## Problem statement

Utility staff use Urja Meter Ops in a browser to look up smart meters, see where they sit in the supply
network and check recent readings. The portal has no API. Its data comes from undocumented
SvelteKit endpoints, a session cookie, an HMAC-signed export, mixed record formats and a rate limit.
Anyone who wants the data programmatically has to reverse-engineer all of that.

## Objective

Hide all of that behind a small, predictable, documented API:

1. Sign in to the portal and keep the session alive.
2. Read meters, meter details, network position, coordinates and consumption, using only normal
   read-only user functionality.
3. Return consistent, typed JSON with clear errors.

## Features

- `GET /api/meters`: paginated list with filters (ID/serial search, status, make, phase, installation type, transformer, feeder, zone)
- `GET /api/meters/{id}`: nameplate attributes, full network path and coordinates
- `GET /api/meters/{id}/location`: coordinates, zone-to-transformer path, transformer registry entry (capacity, feeder)
- `GET /api/meters/{id}/consumption`: register readings with derived interval kWh and a summary, for a date range
- `GET /api/transformers`, `GET /api/network/hierarchy`: optional network views
- `GET /api/health`, `GET /api/health/portal`: liveness, and portal connectivity/authentication
- Automatic sign-in and re-sign-in when the session expires; retry with back-off on 429, 5xx and network errors
- One signed bulk-export request loads all meters, cached with a TTL, and the last good copy is served if the portal is down
- **Data-quality notes**: inconsistencies in the portal data are reported, never silently "fixed"
- Optional API-key protection for the service itself
- 89 mocked tests replaying **real recorded portal responses**, plus opt-in live integration tests

## Technology stack

Python 3.10+ · FastAPI · Pydantic v2 · HTTPX (async) · Uvicorn · Pytest (+ pytest-asyncio, openapi-spec-validator).
No other runtime dependencies.

## Architecture

```
            ┌───────────── this service ────────────────────────────────────────┐
 client ──► │ routes/        validation, HTTP mapping, OpenAPI                  │
            │   │                                                               │
            │ services/      MeterService: sources, snapshot cache, filters     │
            │   │            transform.py: pure portal-JSON → model functions   │
            │ clients/       PortalClient: retries, 429/5xx back-off,           │
            │   │            session-expiry detection, export signing           │
            │   │            AuthClient: SvelteKit form-action sign-in          │
            │   │            devalue.py: decoder for __data.json                │
            └───┼───────────────────────────────────────────────────────────────┘
                ▼
     Urja Meter Ops portal (SvelteKit):  POST /login · /portal/export (+ /portal/keys)
     /portal/dts · /portal/meters/{id}/energy · /portal/meters/{id}/geo · /meters/{id}/__data.json
```

Where each endpoint gets its data:

| API endpoint | Primary portal source | Fallback |
|---|---|---|
| `/api/meters` | cached bulk export | last good snapshot |
| `/api/meters/{id}` | cached bulk export | meter page `__data.json` + `/geo` |
| `/api/meters/{id}/location` | cached export + transformer registry | meter page + `/geo` (no registry check) |
| `/api/meters/{id}/consumption` | `/portal/meters/{id}/energy` (live, every call) | none |
| `/api/transformers`, `/api/network/hierarchy` | cached export + registry | last good snapshot |

## Folder structure

```
flock-energy-api/
├── app/
│   ├── main.py               # app factory, lifespan, error handlers
│   ├── config.py             # env/.env settings
│   ├── errors.py             # domain errors → HTTP status + error code
│   ├── dependencies.py       # service injection, optional API key
│   ├── routes/               # health.py, meters.py, examples.py (OpenAPI examples)
│   ├── services/             # meter_service.py, transform.py
│   ├── clients/              # portal_client.py, auth_client.py, devalue.py
│   └── models/               # meter.py, responses.py
├── tests/
│   ├── fixtures/portal_responses.json   # real portal responses (see _comment inside)
│   ├── conftest.py           # FakePortal: replays fixtures, simulates sessions/429/5xx
│   ├── test_health.py  test_meters.py  test_portal_client.py  test_transform.py  test_openapi.py
│   └── test_live_portal.py   # LIVE, opt-in
├── scripts/export_openapi.py
├── README.md  PROTOCOL.md  REFLECTION.md  openapi.json
├── requirements.txt  pytest.ini  .env.example  .gitignore
```

## Installation

```bash
git clone https://github.com/Rahulrajora14/flock-energy-api.git
cd flock-energy-api
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Environment configuration

```bash
cp .env.example .env             # Windows: copy .env.example .env
```

Then set at least the two credentials in `.env`. The file is git-ignored.

| Variable | Default | Meaning |
|---|---|---|
| `URJA_PORTAL_URL` | `https://urja-ops.flockenergy.tech` | Portal base URL |
| `URJA_PORTAL_EMAIL` / `URJA_PORTAL_PASSWORD` | (none) | **Required.** Portal account |
| `URJA_PORTAL_TIMEOUT_SECONDS` | `20` | Per-request timeout |
| `URJA_PORTAL_MAX_RETRIES` | `3` | Retries for 429 / 5xx / network errors |
| `URJA_PORTAL_RETRY_BACKOFF_SECONDS` | `2` | Base for exponential back-off (capped at 30 s) |
| `CACHE_TTL_SECONDS` | `300` | How long the bulk meter snapshot is reused |
| `SERVICE_API_KEY` | (empty) | If set, `/api/*` (except `/api/health`) requires `X-API-Key` |
| `LOG_LEVEL` | `INFO` | Logging level. The password is never logged |
| `URJA_LIVE_TESTS` | `0` | `1` enables the live portal tests |

Real environment variables take precedence over `.env`.

## Running

```bash
uvicorn app.main:app --reload --port 8000
```

- Swagger UI: http://localhost:8000/docs · ReDoc: http://localhost:8000/redoc
- Spec at runtime: http://localhost:8000/openapi.json

### Tests

```bash
pytest                                  # mocked suite, no network (live tests are skipped)
URJA_LIVE_TESTS=1 pytest -m live -v     # live tests against the real portal (needs .env credentials)
python scripts/export_openapi.py        # regenerate openapi.json after changing the API
```

**Mocked vs live.** The mocked tests use `httpx.MockTransport` to replay responses that were recorded
from the real portal (the 21 data fixtures were checked against fresh live responses by SHA-256 before the
operator's email was redacted; error and sign-in bodies were copied from observed responses). The fake
portal also simulates the observed sessions, signed export, 404s, 401 expiry, 429 limits and transport
failures. `tests/test_live_portal.py` drives the real service against the real portal and is opt-in.

## API endpoints

All error responses look like `{"error": {"code": "<machine code>", "message": "<readable text>"}}`.

| Method & path | Purpose | Notable params | Errors |
|---|---|---|---|
| `GET /api/health` | Liveness (no portal call) | none | none |
| `GET /api/health/portal` | Can we sign in and read? | none | 401 |
| `GET /api/meters` | List / filter / paginate | `page`, `page_size`≤100, `q`, `installation_status`, `make`, `phase_type`, `installation_type`, `dt_code`, `feeder_code`, `zone_code` | 401, 422, 502, 503, 504 |
| `GET /api/meters/{meter_id}` | Meter details | case-insensitive ID | 400, 401, 404, 502, 503, 504 |
| `GET /api/meters/{meter_id}/location` | Coordinates + network | none | 400, 401, 404, 502, 503, 504 |
| `GET /api/meters/{meter_id}/consumption` | Readings | `from`, `to` (`YYYY-MM-DD`, inclusive) | 400, 401, 404, 422, 502, 503, 504 |
| `GET /api/transformers` | DT registry | none | 401, 502, 503, 504 |
| `GET /api/network/hierarchy` | Zone → DT tree with meter counts | none | 401, 502, 503, 504 |

| Status | `error.code` | When |
|---|---|---|
| 400 | `invalid_request` | Malformed meter ID, or `from` after `to` |
| 401 | `invalid_api_key` | `SERVICE_API_KEY` set and header missing or wrong |
| 404 | `meter_not_found` / `not_found` | Unknown meter (confirmed with the portal) / unknown route |
| 422 | `validation_error` | Bad query parameter (type, range, date format) |
| 500 | `internal_error` | Bug in this service (details only in the log) |
| 502 | `portal_authentication_failed` | Portal rejected the configured credentials, or none are set |
| 502 | `portal_unavailable` | Network failure or repeated 5xx from the portal |
| 502 | `unexpected_portal_response` | Portal answered in a shape we don't understand |
| 503 | `portal_rate_limited` | Portal still returning 429 after retries (`Retry-After: 30`) |
| 504 | `portal_timeout` | Portal didn't answer in time |

## Sample requests

```bash
curl "http://localhost:8000/api/meters?page=1&page_size=2"
curl "http://localhost:8000/api/meters?installation_status=Faulty&zone_code=Z-01"
curl  http://localhost:8000/api/meters/J100000
curl  http://localhost:8000/api/meters/J100400/location
curl "http://localhost:8000/api/meters/J100000/consumption?from=2026-06-01&to=2026-06-01"
curl -H "X-API-Key: $SERVICE_API_KEY" http://localhost:8000/api/meters   # when an API key is configured
```

## Sample responses

All values below are real portal data.

`GET /api/meters?page_size=2`

```json
{
  "items": [
    {"meter_id": "J100000", "serial_number": "SE33962", "make": "HPL", "phase_type": "single",
     "installation_status": "Decommissioned", "installation_type": "Whole Current", "dt_code": "DT-001"},
    {"meter_id": "J100001", "serial_number": "GE84132", "make": "L&T", "phase_type": "single",
     "installation_status": "Installed", "installation_type": "CT Operated", "dt_code": "DT-002"}
  ],
  "page": 1, "page_size": 2, "total": 403, "total_pages": 202,
  "data_as_of": "2026-11-05T09:20:00Z"
}
```

`GET /api/meters/J100000`

```json
{
  "meter_id": "J100000", "serial_number": "SE33962", "make": "HPL", "phase_type": "single",
  "installation_status": "Decommissioned", "installation_type": "Whole Current", "dt_code": "DT-001",
  "network": {
    "zone": {"code": "Z-01", "name": "Jaipur Zone 1"},
    "circle": {"code": "C-01", "name": "Circle 1"},
    "division": {"code": "D-01", "name": "Division 1"},
    "subdivision": {"code": "SD-01", "name": "Subdivision 1"},
    "substation": {"code": "SS-01", "name": "Substation 1"},
    "feeder": {"code": "F-001", "name": "Feeder 1"},
    "distribution_transformer": {"code": "DT-001", "name": "Malviya Nagar DT 1"}
  },
  "coordinates": {"latitude": 26.938961002479868, "longitude": 75.83095696146852},
  "data_quality_notes": []
}
```

`GET /api/meters/J100400/location` (shows a real data conflict being reported)

```json
{
  "meter_id": "J100400",
  "coordinates": {"latitude": 26.882703003792653, "longitude": 75.74015781362327},
  "network": {"zone": {"code": "Z-01", "name": "Jaipur Zone 1"}, "...": "...",
              "feeder": {"code": "F-007", "name": "Feeder 7"},
              "distribution_transformer": {"code": "DT-007", "name": "Old Malviya Nagar Xfmr"}},
  "transformer": {"code": "DT-007", "name": "Sanganer DT 7", "feeder_code": "F-007", "capacity_kva": 63.0},
  "data_quality_notes": [
    "Transformer DT-007 is named 'Old Malviya Nagar Xfmr' on the meter record but 'Sanganer DT 7' in the transformer registry."
  ]
}
```

`GET /api/meters/J100000/consumption?from=2026-06-01&to=2026-06-01` (readings truncated)

```json
{
  "meter_id": "J100000", "requested_from": "2026-06-01", "requested_to": "2026-06-01",
  "first_reading_at": "2026-06-01T00:00:00", "last_reading_at": "2026-06-01T23:30:00",
  "interval_minutes": 30, "reading_count": 48,
  "summary": {"kwh_consumed": 19.87, "kvah_consumed": 21.46, "average_voltage_r_v": 229.69},
  "readings": [
    {"timestamp": "2026-06-01T00:00:00", "cumulative_kwh": 47972.42, "cumulative_kvah": 51810.22, "voltage_r_v": 237.0, "interval_kwh": null},
    {"timestamp": "2026-06-01T00:30:00", "cumulative_kwh": 47972.85, "cumulative_kvah": 51810.67, "voltage_r_v": 222.0, "interval_kwh": 0.43}
  ],
  "data_quality_notes": []
}
```

`GET /api/meters/J999999`

```json
{"error": {"code": "meter_not_found", "message": "Meter 'J999999' was not found."}}
```

## AI usage

I used an AI assistant (Claude) throughout this assignment, as the brief allows:

- **Planning:** breaking the task down and planning the overall flow (investigate the portal → document the protocol → design the API → tests → docs).
- **Concepts:** explaining things I needed along the way, such as session-based auth, the SvelteKit `__data.json`/devalue format, and OpenAPI.
- **Code:** helping write and structure the code, and debugging errors.
- **Review:** reviewing the code, tests and documentation.

I explored the portal myself, verified the findings in PROTOCOL.md against the live portal, and made the final design decisions. I can explain every part of the code.

## Assumptions

- Energy timestamps are portal-local time, probably IST. The portal states no timezone, so the API returns
  ISO timestamps **without an offset** instead of inventing one.
- `kwh`/`kvah` are cumulative registers (verified as never decreasing for all 403 meters). `interval_kwh` and
  `summary.*_consumed` are therefore **derived** differences and are labelled as derived.
- With no dates, "recent consumption" means the portal's own default: the last 7 days *of available
  data*. Right now that ends on 30 June 2026, because the portal only holds June 2026.
- Meter IDs are upper case. The portal's lookup is case-sensitive, so the API upper-cases input for convenience.
- The bulk export is legitimate "normal user functionality": it's a button on the portal's Transformers page.

## Design decisions

- **Bulk export as the main source.** One signed request returns every meter with *structured*
  hierarchy codes and numeric coordinates. The alternative is about 400 requests against a ~120-per-30 s
  rate limit, using page strings that lose codes (PROTOCOL §4.2, §6).
- **Snapshot cache with stale-on-error.** List and detail reads are fast and cheap on the portal. If a refresh fails, the
  last good snapshot is served and the failure is logged.
- **Per-meter fallback.** If the export is broken, or a meter isn't in the snapshot, the service asks the
  meter's own page. A 404 is only returned after the portal itself confirms the meter doesn't exist.
- **Consumption is never cached.** It's the most time-sensitive data, and it's a single request per call.
- **Report, don't repair.** Blank codes become `null`, conflicting names are both shown, and duplicate blank rows are
  merged. Every one of these leaves a `data_quality_notes` entry.
- **Pure transform layer.** All parsing lives in `services/transform.py` with no I/O. It's tested directly
  against recorded payloads, including a test that checks the export and the meter page agree.
- **Stable error contract.** One error envelope, machine-readable codes, and upstream failures mapped to 502/503/504
  so callers can tell "your request is wrong" from "the portal is struggling".
- **App factory and injectable transport/sleep.** Tests run the real stack with a fake portal, without
  monkeypatching, and retries don't actually sleep.

## Engineering trade-offs

- **Freshness vs load.** Meter attributes can be up to `CACHE_TTL_SECONDS` (default 5 min) old. Every list
  response says how old in `data_as_of`. Set the TTL to 0 to always refetch.
- **Reliance on an undocumented signed endpoint.** If the portal changes the export, list/detail still
  work for single meters through the fallback, but listing fails with a clear 502.
- **Reactive, not proactive, rate limiting.** The client backs off when it gets 429 instead of pacing itself.
  That's simpler, and because of the snapshot the service rarely gets close to the limit.
- **Filtering in memory.** Fine for hundreds or thousands of meters. A real database would be needed at a much larger scale.

## Intentionally excluded

- Anything that writes to the portal. The service only reads.
- Calling the portal's sign-out endpoint.
- Persisting portal data (database, files). The only storage is an in-memory cache.
- The optional frontend dashboard. The backend was the priority.
- Guessing a timezone, or "repairing" bad portal data.

## Known limitations

- The portal only has consumption data for **June 2026**, so ranges outside it return zero readings.
- The live tests (`tests/test_live_portal.py`) were written but **not executed from the build environment**.
  Its network allowlist blocked the portal's domain, so the portal was explored through an
  authenticated browser instead. Every request the client makes was replayed live there, and the recorded responses
  drive the mocked suite. Run `URJA_LIVE_TESTS=1 pytest -m live` from a machine that can reach the portal.
- The format of a failed portal sign-in, the session lifetime and the exact rate-limit window were inferred,
  not measured (PROTOCOL §9). The client treats any non-success sign-in as an auth failure.
- The service's own API key is a single shared secret. There are no per-client keys or rate limits.
- In-memory cache: each worker process keeps its own snapshot and portal session.

## Future improvements

- Run and record the live test suite in CI (a nightly job with secrets) to catch portal drift.
- Proactive token-bucket pacing below the portal's rate limit, and a background snapshot refresher.
- A short-TTL cache for consumption keyed by meter and range.
- Prometheus metrics (portal latency, 429s, re-logins, cache age) and structured JSON logs.
- Anomaly endpoints: zero or flat consumption, voltage outside limits, meters marked Faulty but still reporting.
- Dockerfile and docker-compose for deployment.
- The optional dashboard (consumption charts, map, hierarchy explorer) on top of this API.
