"""Pure functions that turn raw portal payloads into the public models.

Kept free of I/O so they can be tested directly against recorded portal responses.
Rules applied everywhere:
  * empty strings from the portal become ``None`` (never invented values);
  * numbers that arrive as strings are converted, unparseable ones become ``None``;
  * anything inconsistent is reported in ``data_quality_notes`` rather than "fixed".
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date, datetime
from typing import Any, Optional

from app.errors import UnexpectedPortalResponseError
from app.models.meter import (
    ConsumptionReading,
    ConsumptionSummary,
    Coordinates,
    Meter,
    MeterConsumption,
    MeterDetail,
    NetworkNode,
    NetworkPosition,
    Transformer,
)

# Order matters: top of the network to the bottom.
NETWORK_LEVELS = ["zone", "circle", "division", "subdivision", "substation", "feeder", "distribution_transformer"]
_EXPORT_LEVEL_KEYS = dict(zip(NETWORK_LEVELS, ["zone", "circle", "division", "subdivision", "substation", "feeder", "dt"]))
_PAGE_LEVEL_LABELS = dict(
    zip(NETWORK_LEVELS, ["Zone", "Circle", "Division", "Subdivision", "Sub Station", "Feeder", "DT"])
)
_PORTAL_TIMESTAMP = "%d/%m/%Y %H:%M"
_NAME_CODE = re.compile(r"^(?P<name>.*?)\s*\((?P<code>[^()]+)\)\s*$")


# ----------------------------------------------------------------- primitives


def clean_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def to_float(value: Any) -> Optional[float]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None  # drop NaN


def parse_portal_timestamp(value: str) -> datetime:
    return datetime.strptime(value.strip(), _PORTAL_TIMESTAMP)


def parse_name_code(label: Any) -> NetworkNode:
    """'Feeder 1 (F-001)' -> name/code. 'Circle 6' -> name only. '' -> both None."""
    text = clean_str(label)
    if text is None:
        return NetworkNode()
    match = _NAME_CODE.match(text)
    if match:
        return NetworkNode(name=clean_str(match["name"]), code=clean_str(match["code"]))
    return NetworkNode(name=text)


# ----------------------------------------------------------------- meters


def meter_from_search(row: dict[str, Any]) -> Meter:
    """Row from ``/portal/meters/search`` (no installation type in this view)."""
    return Meter(
        meter_id=_require_id(row.get("meterId")),
        serial_number=clean_str(row.get("serialNo")),
        make=clean_str(row.get("make")),
        phase_type=clean_str(row.get("phaseType")),
        installation_status=clean_str(row.get("installStatus")),
        dt_code=clean_str(row.get("dtCode")),
    )


def detail_from_export(rec: dict[str, Any]) -> MeterDetail:
    """Record from the signed bulk export (richest source: structured hierarchy + geo)."""
    hierarchy = rec.get("hierarchy") if isinstance(rec.get("hierarchy"), dict) else {}
    network = NetworkPosition(
        **{
            level: _node_from_export(hierarchy.get(key))
            for level, key in _EXPORT_LEVEL_KEYS.items()
        }
    )
    geo = rec.get("geo") if isinstance(rec.get("geo"), dict) else {}
    meter = MeterDetail(
        meter_id=_require_id(rec.get("meterId")),
        serial_number=clean_str(rec.get("serialNo")),
        make=clean_str(rec.get("make")),
        phase_type=clean_str(rec.get("phaseType")),
        installation_status=clean_str(rec.get("installStatus")),
        installation_type=clean_str(rec.get("installType")),
        dt_code=clean_str(rec.get("dtCode")),
        network=network,
        coordinates=make_coordinates(geo.get("lat"), geo.get("lng")),
    )
    return meter.model_copy(update={"data_quality_notes": network_notes(meter)})


def detail_from_meter_page(page: dict[str, Any], geo: Optional[dict[str, Any]] = None) -> MeterDetail:
    """Decoded ``/meters/{id}/__data.json`` (+ optional ``/geo``).

    The page has two record formats: a ``data`` list of name/value parameters
    ("legacy") or a ``classData`` JSON string with an ``installed_meter`` object ("v2").
    """
    detail = page.get("detail")
    if not isinstance(detail, dict):
        raise UnexpectedPortalResponseError("meter page: 'detail' is not an object.")
    if "classData" in detail:
        try:
            raw = json.loads(detail["classData"]).get("installed_meter") or {}
        except (TypeError, ValueError, AttributeError) as exc:
            raise UnexpectedPortalResponseError("meter page: 'classData' is not valid JSON.") from exc
        attrs = {
            "meter_id": raw.get("MeterId"),
            "serial_number": raw.get("SerialNo"),
            "make": raw.get("Make"),
            "phase_type": raw.get("PhaseType"),
            "installation_status": raw.get("InstallationStatus"),
            "installation_type": raw.get("InstallationType"),
        }
    elif isinstance(detail.get("data"), list):
        params = {
            p.get("parameterName"): p.get("parameterValue") for p in detail["data"] if isinstance(p, dict)
        }
        attrs = {
            "meter_id": params.get("Meter ID"),
            "serial_number": params.get("Serial No"),
            "make": params.get("Make"),
            "phase_type": params.get("Phase Type"),
            "installation_status": params.get("Installation Status"),
            "installation_type": params.get("Installation Type"),
        }
    else:
        raise UnexpectedPortalResponseError("meter page: unknown detail format.")

    hierarchy = page.get("hierarchy") if isinstance(page.get("hierarchy"), dict) else {}
    network = NetworkPosition(
        **{level: parse_name_code(hierarchy.get(label)) for level, label in _PAGE_LEVEL_LABELS.items()}
    )
    meter_id = attrs["meter_id"] or page.get("meterId")
    meter = MeterDetail(
        meter_id=_require_id(meter_id),
        serial_number=clean_str(attrs["serial_number"]),
        make=clean_str(attrs["make"]),
        phase_type=clean_str(attrs["phase_type"]),
        installation_status=clean_str(attrs["installation_status"]),
        installation_type=clean_str(attrs["installation_type"]),
        dt_code=network.distribution_transformer.code,
        network=network,
        coordinates=make_coordinates((geo or {}).get("latitude"), (geo or {}).get("longitude")),
    )
    return meter.model_copy(update={"data_quality_notes": network_notes(meter)})


def as_summary(detail: MeterDetail) -> Meter:
    return Meter(**{name: getattr(detail, name) for name in Meter.model_fields})


def make_coordinates(lat: Any, lng: Any) -> Optional[Coordinates]:
    lat_f, lng_f = to_float(lat), to_float(lng)
    if lat_f is None or lng_f is None or not (-90 <= lat_f <= 90 and -180 <= lng_f <= 180):
        return None
    return Coordinates(latitude=lat_f, longitude=lng_f)


def transformer_from_portal(row: dict[str, Any]) -> Optional[Transformer]:
    code = clean_str(row.get("code"))
    if code is None:
        return None
    return Transformer(
        code=code,
        name=clean_str(row.get("name")),
        feeder_code=clean_str(row.get("feederCode")),
        capacity_kva=to_float(row.get("capacityKva")),
    )


def network_notes(meter: MeterDetail) -> list[str]:
    notes = []
    for level in NETWORK_LEVELS:
        node: NetworkNode = getattr(meter.network, level)
        if node.code is None and node.name is None:
            notes.append(f"Portal has no {level.replace('_', ' ')} for this meter.")
        elif node.code is None:
            notes.append(f"Portal gives no code for {level.replace('_', ' ')} '{node.name}'.")
        elif node.name is None:
            notes.append(f"Portal gives no name for {level.replace('_', ' ')} {node.code}.")
    dt = meter.network.distribution_transformer.code
    if meter.dt_code and dt and meter.dt_code != dt:
        notes.append(f"Meter lists transformer {meter.dt_code} but its network path ends at {dt}.")
    if meter.coordinates is None:
        notes.append("Portal has no usable coordinates for this meter.")
    return notes


def transformer_notes(meter: MeterDetail, registry: Optional[Transformer]) -> list[str]:
    if not meter.dt_code:
        return []
    if registry is None:
        return [f"Transformer {meter.dt_code} is not in the portal's transformer registry."]
    notes = []
    meter_name = meter.network.distribution_transformer.name
    if meter_name and registry.name and meter_name != registry.name:
        notes.append(
            f"Transformer {registry.code} is named '{meter_name}' on the meter record "
            f"but '{registry.name}' in the transformer registry."
        )
    feeder = meter.network.feeder.code
    if feeder and registry.feeder_code and feeder != registry.feeder_code:
        notes.append(
            f"Meter's feeder is {feeder} but the registry places {registry.code} on {registry.feeder_code}."
        )
    return notes


# ----------------------------------------------------------------- consumption


def build_consumption(
    meter_id: str,
    rows: list[dict[str, Any]],
    requested_from: Optional[date] = None,
    requested_to: Optional[date] = None,
) -> MeterConsumption:
    notes: list[str] = []
    merged: dict[datetime, dict[str, Optional[float]]] = {}
    unparseable = 0
    duplicates = 0
    for row in rows:
        if not isinstance(row, dict):
            unparseable += 1
            continue
        try:
            ts = parse_portal_timestamp(str(row.get("timestamp", "")))
        except ValueError:
            unparseable += 1
            continue
        values = {
            "cumulative_kwh": to_float(row.get("kwh")),
            "cumulative_kvah": to_float(row.get("kvah")),
            "voltage_r_v": to_float(row.get("voltR")),
        }
        if ts in merged:
            duplicates += 1
            # Keep the first non-empty value for each field.
            merged[ts] = {k: merged[ts][k] if merged[ts][k] is not None else v for k, v in values.items()}
        else:
            merged[ts] = values
    if duplicates:
        notes.append(f"Merged {duplicates} duplicate reading(s) sharing a timestamp; blanks were ignored.")
    if unparseable:
        notes.append(f"Skipped {unparseable} reading(s) with an unrecognised timestamp.")

    readings: list[ConsumptionReading] = []
    previous_kwh: Optional[float] = None
    for ts in sorted(merged):
        values = merged[ts]
        kwh = values["cumulative_kwh"]
        delta = round(kwh - previous_kwh, 4) if kwh is not None and previous_kwh is not None else None
        if kwh is not None:
            previous_kwh = kwh
        readings.append(ConsumptionReading(timestamp=ts, interval_kwh=delta, **values))

    missing = sum(1 for r in readings if r.cumulative_kwh is None)
    if missing:
        notes.append(f"{missing} reading(s) have no kWh value.")
    if any(r.interval_kwh is not None and r.interval_kwh < 0 for r in readings):
        notes.append("Cumulative kWh decreases at least once (meter reset or bad reading).")

    return MeterConsumption(
        meter_id=meter_id,
        requested_from=requested_from,
        requested_to=requested_to,
        first_reading_at=readings[0].timestamp if readings else None,
        last_reading_at=readings[-1].timestamp if readings else None,
        interval_minutes=_dominant_interval(readings),
        reading_count=len(readings),
        summary=_summary(readings),
        readings=readings,
        data_quality_notes=notes,
    )


def _dominant_interval(readings: list[ConsumptionReading]) -> Optional[int]:
    gaps = Counter(
        int((b.timestamp - a.timestamp).total_seconds() // 60) for a, b in zip(readings, readings[1:])
    )
    return gaps.most_common(1)[0][0] if gaps else None


def _summary(readings: list[ConsumptionReading]) -> ConsumptionSummary:
    def span(field: str) -> Optional[float]:
        values = [getattr(r, field) for r in readings if getattr(r, field) is not None]
        return round(values[-1] - values[0], 4) if len(values) >= 2 else None

    volts = [r.voltage_r_v for r in readings if r.voltage_r_v is not None]
    return ConsumptionSummary(
        kwh_consumed=span("cumulative_kwh"),
        kvah_consumed=span("cumulative_kvah"),
        average_voltage_r_v=round(sum(volts) / len(volts), 2) if volts else None,
    )


# ----------------------------------------------------------------- helpers


def _node_from_export(node: Any) -> NetworkNode:
    if not isinstance(node, dict):
        return NetworkNode()
    return NetworkNode(code=clean_str(node.get("code")), name=clean_str(node.get("name")))


def _require_id(value: Any) -> str:
    meter_id = clean_str(value)
    if meter_id is None:
        raise UnexpectedPortalResponseError("Portal record has no meter ID.")
    return meter_id
