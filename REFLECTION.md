# Reflection

Notes from building this service, based on what actually happened during development.

## 1. What assumptions were made?

- **Timezone.** Energy timestamps have no timezone. I assumed local Indian time (the network is in
  Jaipur), but I didn't encode that assumption. The API returns offset-free ISO timestamps and the
  README says so.
- **Cumulative registers.** I treated `kwh`/`kvah` as cumulative meter registers and derived interval
  consumption from them. I checked this before relying on it: across all 403 meters the values never decrease.
- **"Recent" consumption** = the portal's default window (the last 7 days of available data). The portal
  only holds June 2026, so "recent" is not "the last 7 calendar days". I kept the portal's meaning instead of
  silently returning empty results for today's date.
- **Sign-in failure and CSRF.** I could not observe a failed sign-in (§4), so I assumed the standard
  SvelteKit `{"type":"failure"}` result and the standard Origin check. The auth client treats anything except a
  redirect to `/meters` as a failure, so a slightly different shape still fails safely.
- **The export is fair game.** It's behind the portal's "Export all meters" button, so it counts as normal
  user functionality. The HMAC secret is handed to any signed-in user by `/portal/keys`.

## 2. Which part was the most difficult?

**Getting to the portal at all, then trusting what came back.**

The build sandbox's network allowlist blocked `urja-ops.flockenergy.tech`, and so did the shell on the
developer's machine. The only working path was an authenticated browser session. That shaped
everything: discovery had to happen through the portal's own JavaScript and `fetch` calls, and the
Python client could not be pointed at the live portal from where it was built.

The second difficulty was data consistency. The detail page, the search endpoint, `/geo` and the export each
describe meters differently, in two record formats and two encodings (devalue vs plain JSON), with
strings vs numbers. Working out which source was authoritative, and proving they agreed, took longer than
writing the API.

## 3. How were technical difficulties resolved?

- **No direct network path.** The operator signed in once in the browser. I'm not permitted to type passwords,
  so that step had to be theirs. I read the bundled SvelteKit route modules to list every request the UI makes
  (`/portal/meters/search`, `/geo`, `/energy`, `/dts`, `/keys`, `/export`, `__data.json`), then replayed each
  one from the page to record real responses and edge cases.
- **Making offline tests trustworthy.** The recorded responses became `tests/fixtures/portal_responses.json`.
  To make sure nothing was mistyped while moving them out of the browser, I hashed every data fixture
  (SHA-256 of the canonical JSON) both in the browser and locally: 21/21 matched. Only then was the operator's email redacted from them.
- **Signed export.** I reimplemented the portal's HMAC in Python and checked it against a value computed
  with `crypto.subtle` inside the portal page. That check is now a unit test.
- **Choosing a source.** A full cross-check of all 403 meters showed the export and the meter pages agree
  except where the page *loses* information (a blank name drops the code). That made the export the primary
  source and the page the fallback. The check also found the real data problems (22 hierarchy gaps, the DT-007
  name conflict, 5 duplicate blank energy rows), which became `data_quality_notes` and tests.
- **Rate limiting.** Measured (about 120 requests per ~30 s, no `Retry-After`), then handled with capped
  exponential back-off. Using the export means normal use barely touches the limit.

## 4. What mistakes occurred during implementation?

- **I tripped the portal's rate limit.** My first cross-check fired 403 parallel `/geo` requests and 296 got
  HTTP 429. Nothing broke, but it was careless against someone else's system. Later scans were throttled and
  retried, and the incident is documented as how the limit was found.
- **API-key dependency read the wrong settings.** `require_api_key` used the global `get_settings()` instead
  of the app's own settings, so a test app configured with a key still let requests through. The test
  `test_api_key_enforced_when_configured` caught it, and the dependency now reads `request.app.state.settings`.
- **Wrong test expectation.** I expected `q=J1000` to match two meters. It also matches `J100011` and
  `J100089` because it's a substring match, which is exactly how the portal behaves. The test was wrong, not the code.
- **OpenAPI 422 schema drift.** FastAPI auto-documents 422 with its own `HTTPValidationError` schema, but my
  handler returns the common error envelope. I caught it while reviewing the generated spec and fixed it by
  declaring 422 explicitly on every route. `test_openapi_file_matches_app` now guards the spec.
- **A first-load 502** from the portal right after sign-in briefly looked like a broken session. Repeating
  the request showed it was transient, which is why 5xx responses are retried.

## 5. What would be improved with another day?

1. Get the live suite (`tests/test_live_portal.py`) running somewhere with network access and record the
   result. Today it's written but **not executed**. This is the biggest gap.
2. Observe a failed sign-in and a real session expiry, and pin both down in fixtures.
3. Pace requests proactively (token bucket) and refresh the snapshot in the background, so no user request
   ever waits on the export.
4. Docker image, structured logs and basic metrics (re-logins, 429s, cache age).
5. Anomaly endpoints built on the consumption data (flat registers, voltage outside limits, "Faulty" meters
   still reporting).

## 6. What would you criticise about the implementation?

- **Live verification is indirect.** Every request shape was proven live in the browser and every parser is
  tested on real payloads, but the Python HTTP client has never talked to the real portal. The login step (form
  encoding, Origin header, cookie handling) is the part most likely to need a tweak.
- **Dependence on an undocumented signed endpoint.** The export is the best source today, but it's also the
  most likely thing to change. The fallback covers single meters, not listing.
- **Two parsers for one concept.** Keeping both export and page parsing adds code. I accepted it for
  resilience, and the agreement test keeps them honest.
- **In-memory state.** Cache and session are per process. Several Uvicorn workers would each sign in and each
  pull the export. That's fine at this scale, but it's a design limit.
- **The fake portal is fairly complex.** `tests/conftest.py` encodes several portal behaviours. It's realistic,
  but it's also code that can be wrong. It mirrors only what PROTOCOL.md says was observed.
- **Rate-limit retry can hold a request for up to ~14 s** before returning 503. It's configurable, but the
  default favours eventual success over fast failure.
