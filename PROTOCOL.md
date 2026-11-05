# Urja Meter Ops — Portal Protocol

How the existing portal at `https://urja-ops.flockenergy.tech` actually works, as observed on
**2026-11-05** while signed in as the development operator account.

**How this was investigated.** The portal was reached through a real browser session. After the
operator signed in, I read the portal's own JavaScript bundles to find every request the UI makes,
then repeated those requests from the page (`fetch` with the session cookie) to record
responses, status codes and edge cases. Every portal request was a `GET`, apart from the sign-in
`POST` the operator made in the browser. Nothing on the portal was changed.
All 403 meters were checked against more than one source (see §6).

Each statement below is labelled:

- **[Verified]**: observed directly in a response, network log or the portal's JavaScript.
- **[Inferred]**: follows from verified facts but wasn't tested in isolation.
- **[Assumption]**: not observed; the service is built to tolerate it being wrong.

---

## 1. Portal overview

| | |
|---|---|
| Framework | **[Verified]** SvelteKit (bundles under `/_app/immutable/`, `__sveltekit_*` bootstrap, `__data.json` loaders). |
| UI routes | **[Verified]** `/login`, `/meters` (list), `/meters/[id]` (detail), `/transformers`. `/` redirects to `/meters`. |
| Data access | **[Verified]** Two mechanisms: (a) JSON endpoints under `/portal/*` called by page JavaScript; (b) SvelteKit server-load data at `/<route>/__data.json`. |
| Signed-in user | **[Verified]** Layout data exposes `{"user": {"name": "Ops Desk", "email": "<operator-email>"}}`. |

## 2. Authentication

**[Verified]** Sign-in is a SvelteKit *form action* on `POST /login`. The login page's JavaScript
(`nodes/7.*.js`, SvelteKit `enhance`) sends:

```http
POST /login
accept: application/json
x-sveltekit-action: true
content-type: application/x-www-form-urlencoded

email=<email>&password=<password>
```

**[Verified]** Successful response (HTTP 200, captured from the browser network log):

```json
{"type":"redirect","status":303,"location":"/meters"}
```

…and the browser then loads `/meters`. The session is a cookie that is **HttpOnly** (`document.cookie`
is empty after sign-in) **[Verified]**. Its name was not visible to page scripts **[Verified]**, so the
service does not depend on it and keeps whatever cookie the server sets.

- **[Inferred]** Failed sign-in returns a SvelteKit `{"type":"failure", ...}` result; the login
  component renders `form.error`. I did not submit wrong credentials.
- **[Assumption]** SvelteKit's default CSRF check rejects form POSTs whose `Origin` doesn't match the
  site. The service sends `Origin: <portal URL>` to satisfy it.
- **[Verified]** Sign-out is `POST /api/auth/sign-out` (seen in `nodes/2.*.js`). The service never calls it.

## 3. Session management

| Situation | Behaviour |
|---|---|
| `/portal/*` without a valid session | **[Verified]** `401 {"error":"unauthorized","message":"A valid session is required."}` |
| `/<route>/__data.json` without a session | **[Verified]** `200 {"type":"redirect","location":"/login"}` (not a 401!) |
| HTML page without a session | **[Verified]** HTTP redirect (opaque redirect to `/login`) |
| `__data.json` caching | **[Verified]** `cache-control: private, no-store` |
| Session lifetime | **[Unknown]** Not observed to expire during the ~30-minute investigation. The service re-authenticates when it sees either of the two "no session" signals above. |

## 4. Data endpoints

All of these are what the portal's own UI calls. Every response is JSON.

### 4.1 Meter search: `GET /portal/meters/search?q=<text>&page=<n>`

Used by the Meters list page (`nodes/5.*.js`).

```json
{"data":[{"meterId":"J100000","serialNo":"SE33962","make":"HPL","phaseType":"single",
          "installStatus":"Decommissioned","dtCode":"DT-001"}, ...],
 "total":403,"page":1,"pageSize":20}
```

- **[Verified]** Fixed page size of 20. `pageSize=100` is ignored.
- **[Verified]** `page=0` and `page=abc` act like `page=1`. Pages past the end return `data: []` and keep the real `total`.
- **[Verified]** `q` is a **case-insensitive substring** match on `meterId` and `serialNo` only (`q=339` → 2 hits; `q=Genus`, `q=DT-001` → 0). It is not trimmed (`q=" J100000 "` → 0).
- **[Verified]** No installation type, hierarchy or coordinates in this view.

### 4.2 Meter page data: `GET /meters/<id>/__data.json`

The server-load data behind the meter detail page. It uses SvelteKit's **devalue** encoding: the last
node's `data` is a flat array, index 0 is the root object, and every object or array holds *indices* into
that array rather than values. Decoded:

```json
{"meterId":"J100000",
 "detail":{"data":[{"parameterName":"Meter ID","parameterValue":"J100000"},
                   {"parameterName":"Serial No","parameterValue":"SE33962"}, "...Make, Phase Type,
                    Installation Status, Installation Type..."]},
 "hierarchy":{"Meter ID":"J100000","Installation Status":"Decommissioned","Installation Type":"Whole Current",
              "Zone":"Jaipur Zone 1 (Z-01)","Circle":"Circle 1 (C-01)","Division":"Division 1 (D-01)",
              "Subdivision":"Subdivision 1 (SD-01)","Sub Station":"Substation 1 (SS-01)",
              "Feeder":"Feeder 1 (F-001)","DT":"Malviya Nagar DT 1 (DT-001)"}}
```

**[Verified] Two record formats** for `detail`. The UI handles both (`classData` in `e` …):

| Format | Count | Shape |
|---|---|---|
| "legacy" | 238 | `detail.data` = list of `{parameterName, parameterValue}` |
| "v2" | 165 | `detail.classData` = a **JSON string**: `{"installed_meter":{"MeterId","SerialNo","Make","PhaseType","InstallationStatus","InstallationType"}}` |

- **[Verified]** An unknown ID still returns HTTP 200, with the error inside the node: `{"type":"error","error":{"message":"Meter not found"},"status":404}`.
- **[Verified]** ID lookup is **case-sensitive** (`j100000` → not found).
- **[Verified]** Hierarchy values are `"Name (CODE)"` strings, but if the portal has no code it's just `"Name"`. If it has a code but **no name**, the string is `""` and the code is lost. That's why the export (§4.6) is the better source.
- **[Verified]** This endpoint is **not** rate limited (§7).

### 4.3 Coordinates: `GET /portal/meters/<id>/geo`

```json
{"data":{"latitude":"26.938961002479868","longitude":"75.83095696146852"}}
```

- **[Verified]** Coordinates are **strings**. Unknown ID → `404 {"error":"not_found","message":"Meter not found"}`.
- **[Verified]** All 403 meters have coordinates, all in the Jaipur area.

### 4.4 Consumption: `GET /portal/meters/<id>/energy[?from=YYYY-MM-DD&to=YYYY-MM-DD]`

```json
{"data":[{"timestamp":"23/06/2026 23:30","kwh":"48438.74","kvah":"52313.84","voltR":"226"}, ...]}
```

- **[Verified]** Every value is a **string**. Timestamps are `DD/MM/YYYY HH:mm` with **no timezone**. Rows come oldest first.
- **[Verified]** `kwh`/`kvah` are **cumulative register readings**: they never decrease across all 403 meters for June 2026. `voltR` (R-phase voltage) ranges 220–240.
- **[Verified] Window semantics** (tested on J100000):

| Query | Result |
|---|---|
| none | last 7 days of available data: 23/06 23:30 → 30/06 23:30 (337 rows) |
| `from=2026-06-01&to=2026-06-01` | whole day inclusive: 48 rows, 00:00 → 23:30 |
| `from` only | 7 days from `from`: 01/06 00:00 → 08/06 00:00 (337 rows) |
| `to` only | 7 days ending on `to`: 04/06 00:00 → 10/06 23:30 (336 rows) |
| `from` > `to` | `data: []` |
| range outside the data | `data: []` (HTTP 200) |
| very long range (2026-01-01 → 2026-12-31) | allowed: all 1440 rows, no cap observed |
| unparseable date, or a datetime like `2026-06-01T12:00` | **ignored without warning**, as if not sent |
| unknown meter | `404 {"error":"not_found",...}` |

- **[Verified] Data coverage** (all 403 meters, full range):
  - Every meter has data **only for June 2026** (first reading 01/06/2026 00:00).
  - **40 meters (J100000–J100039)** are half-hourly: 1440 readings, last 30/06 23:30.
  - **363 meters** are daily at 00:00: 30 readings, last 30/06 00:00.
  - **5 meters** (J100089, J100134, J100195, J100211, J100330) have a **duplicate final row** at 30/06/2026 00:00 with `kwh` and `voltR` set to `""` (`kvah` repeated).
  - No other blanks, no unparseable timestamps, no out-of-order rows.

### 4.5 Transformer registry: `GET /portal/dts?page=<n>`

Used by the Transformers page (`nodes/4.*.js`).

```json
{"data":[{"code":"DT-001","name":"Malviya Nagar DT 1","feederCode":"F-001","capacityKva":100}, ...],
 "total":40,"page":1,"pageSize":20}
```

- **[Verified]** 40 transformers over 2 pages. `capacityKva` is a number (63/100/160/250/400).
- **[Verified]** DT-029…DT-040 reuse feeders F-001…F-012.

### 4.6 Bulk export (signed): `GET /portal/export?page=1`

This is the Transformers page's **"Export all meters"** button. It's the most efficient and complete source.

1. **[Verified]** `GET /portal/keys` → `{"data":{"signingSecret":"<32 chars>"}}`. It returned the same secret on repeated calls in one session.
2. **[Verified]** The page computes `HMAC-SHA256(secret, "GET\n/portal/export\npage=1\n<unix-seconds>")` as lowercase hex.
3. **[Verified]** It then sends `GET /portal/export?page=1` with headers `x-timestamp: <unix-seconds>` and `x-signature: <hex>`.

```json
{"data":[{"meterId":"J100000","serialNo":"SE33962","make":"HPL","phaseType":"single",
          "installStatus":"Decommissioned","installType":"Whole Current","build":"legacy","dtCode":"DT-001",
          "hierarchy":{"zone":{"name":"Jaipur Zone 1","code":"Z-01"}, "circle":{...}, "division":{...},
                       "subdivision":{...}, "substation":{...}, "feeder":{...},
                       "dt":{"name":"Malviya Nagar DT 1","code":"DT-001"}},
          "geo":{"lat":26.938961002479868,"lng":75.83095696146852}}, ...],
 "total":403}
```

- **[Verified]** It returns **all 403 meters in one response**. `page` is ignored (`page=2` and no query both return all 403), but the signature must cover the exact query string sent.
- **[Verified]** Missing or wrong signature → `401 {"error":"signature_invalid","message":"Missing or invalid request signature."}`.
- **[Verified] Timestamp freshness:** signatures 30 s and 120 s old were accepted; 299 s, 301 s and 600 s old were rejected. The limit is somewhere between 120 s and 299 s. Future timestamps were not tested.
- **[Verified]** `build` (`legacy`/`v2`) matches the meter page's record format for all 403 meters.
- The service's HMAC function was checked against a value computed with `crypto.subtle` inside the portal page (`tests/test_portal_client.py::test_signature_matches_portal_javascript`).

## 5. Meter identification and attributes

- **[Verified]** 403 meters, IDs `J100000`–`J100402` (pattern `J` + 6 digits). IDs and serial numbers are unique.
- **[Verified]** Attribute values across all meters:
  - Status: Installed 238, Faulty 90, Decommissioned 75
  - Make: HPL 90, Genus 88, Secure 85, Allied 76, L&T 64
  - Phase: single 277, three 126
  - Installation type: Whole Current 205, CT Operated 198
- **[Verified]** Serial prefixes: AL 90, SE 80, GE 78, L& 77, HP 75, GN 3. A prefix doesn't match the make (for example `L&84997` is a Genus meter). The `L&` serials look like a data-entry artefact but are returned as-is.

## 6. Network hierarchy and location

- **[Verified]** Each meter has a seven-level path: zone → circle → division → subdivision → substation → feeder → distribution transformer (DT).
- **[Verified]** Distinct codes: 3 zones, 6 circles, 10 divisions, 14 subdivisions, 18 substations, 28 feeders, 40 DTs.
- **[Verified] Not a strict tree.** 60 codes appear under more than one parent across meters (for example D-01 under C-01, C-03 and C-05). The service keeps each meter's own path and builds its hierarchy view by path, not by code.
- **[Verified] Gaps:** 22 path entries have a blank code or a blank name (circle 11, feeder 8, substation 3). Examples: J100011 circle `{"name":"Circle 6","code":""}`; J100162 feeder `{"name":"","code":"F-003"}`.
- **[Verified] Name conflict:** J100400–J100402 (the three `GN` serials, daily readings) name DT-007 **"Old Malviya Nagar Xfmr"**, but the registry and every other DT-007 meter say **"Sanganer DT 7"**. It looks like a stale or renamed transformer. The service reports both names and doesn't pick one.
- **[Verified] Cross-source agreement for all 403 meters.** Export vs meter page: identical attributes. Hierarchy is identical except where the page string lost information (the 22 gaps above). Export `geo` vs `/geo`: identical coordinates. `dtCode` always equals the hierarchy DT code.

## 7. Rate limiting and reliability

- **[Verified]** `/portal/*` endpoints share a rate limit. 119 back-to-back requests succeeded in about 4.6 s, then every `/portal/*` call (search, dts, geo) got `429 {"error":"rate_limited","message":"Rate limit exceeded; slow down."}`.
- **[Verified]** There is **no `Retry-After` header**. Access came back about 25–30 s after the limit was hit. **[Inferred]** It's a window of roughly 120 requests per ~30 s.
- **[Verified]** `__data.json` page loads were **not** limited while `/portal/*` was returning 429.
- **[Verified]** One transient **HTTP 502** (an empty body) came from `/meters/__data.json` just after sign-in. Repeating it gave 200.
- My first bulk check fired 403 parallel `/geo` requests: 296 got 429. That's how the limit was found. Later scans were throttled.

## 8. Technical quirks (summary)

1. Sign-in is a SvelteKit form action, not a JSON API.
2. "Not signed in" looks different depending on the endpoint: 401 JSON vs a 200 redirect body.
3. A missing meter on `__data.json` is HTTP 200 with the 404 inside the payload.
4. devalue-encoded page data, and two different meter record formats.
5. Numbers are sent as strings (`/geo`, `/energy`), but as numbers in the export and in `capacityKva`.
6. Day-first timestamps with no timezone.
7. Bad energy query parameters are ignored instead of rejected.
8. The export needs an HMAC signature using a secret the portal gives to any signed-in user. That's obfuscation, not security.
9. Search pagination ignores page-size requests. The export ignores paging entirely.
10. Data quality: blank hierarchy codes/names, a conflicting DT name, blank duplicate energy rows.

## 9. Limitations of this investigation

- I didn't submit a wrong password (§2), so the exact failure payload is inferred from the login JavaScript.
- Session lifetime and the cookie name weren't observed.
- The rate-limit window size is approximate.
- Consumption data exists only for June 2026, so "recent" means "the portal's latest 7 days", not the last 7 calendar days.
- The timezone of energy timestamps isn't stated anywhere. **[Assumption]** It's local Indian time (IST, UTC+05:30), given the Jaipur network. The API returns timestamps without an offset rather than guessing one.
