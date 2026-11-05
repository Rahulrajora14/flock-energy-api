"""Business logic: where each answer comes from, caching, filtering and pagination.

Data sources (see README "Design decisions"):
  * Meter list / detail / location are served from a cached snapshot of the portal's
    signed bulk export (one request returns all meters with hierarchy and coordinates).
  * If the snapshot cannot be refreshed, the last good snapshot keeps being served.
  * If there is no snapshot at all, or a meter is missing from it, single-meter lookups
    fall back to the meter's own portal page (``__data.json`` + ``/geo``).
  * Consumption is always fetched live per request; it is never cached.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

from app.clients.portal_client import PortalClient
from app.errors import InvalidRequestError, MeterNotFoundError, ServiceError
from app.models.meter import (
    HierarchyNode,
    Meter,
    MeterConsumption,
    MeterDetail,
    MeterLocation,
    Transformer,
)
from app.models.responses import MeterPage, NetworkHierarchy
from app.services import transform

log = logging.getLogger(__name__)

METER_ID_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9_-]{0,31}$")


@dataclass
class MeterFilters:
    q: Optional[str] = None
    installation_status: Optional[str] = None
    make: Optional[str] = None
    phase_type: Optional[str] = None
    installation_type: Optional[str] = None
    dt_code: Optional[str] = None
    feeder_code: Optional[str] = None
    zone_code: Optional[str] = None

    def matches(self, m: MeterDetail) -> bool:
        if self.q:
            needle = self.q.strip().lower()
            if needle not in m.meter_id.lower() and needle not in (m.serial_number or "").lower():
                return False
        checks = [
            (self.installation_status, m.installation_status),
            (self.make, m.make),
            (self.phase_type, m.phase_type),
            (self.installation_type, m.installation_type),
            (self.dt_code, m.dt_code),
            (self.feeder_code, m.network.feeder.code),
            (self.zone_code, m.network.zone.code),
        ]
        return all(want is None or (have or "").lower() == want.strip().lower() for want, have in checks)


@dataclass
class Snapshot:
    meters: dict[str, MeterDetail]
    transformers: dict[str, Transformer]
    fetched_at: datetime
    monotonic: float = field(default_factory=time.monotonic)


class MeterService:
    def __init__(self, portal: PortalClient, cache_ttl_seconds: int = 300):
        self._portal = portal
        self._ttl = cache_ttl_seconds
        self._snapshot: Optional[Snapshot] = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------ snapshot cache

    def _fresh(self) -> bool:
        return self._snapshot is not None and (time.monotonic() - self._snapshot.monotonic) < self._ttl

    async def _get_snapshot(self) -> Snapshot:
        if self._fresh():
            return self._snapshot  # type: ignore[return-value]
        async with self._lock:
            if self._fresh():
                return self._snapshot  # type: ignore[return-value]
            try:
                self._snapshot = await self._load_snapshot()
            except ServiceError as exc:
                if self._snapshot is None:
                    raise
                log.warning("Snapshot refresh failed (%s); serving data from %s", exc.code, self._snapshot.fetched_at)
            return self._snapshot

    async def _load_snapshot(self) -> Snapshot:
        records = await self._portal.export_meters()
        transformers_raw = await self._portal.list_all_transformers()
        transformers = {t.code: t for t in map(transform.transformer_from_portal, transformers_raw) if t}
        meters: dict[str, MeterDetail] = {}
        for rec in records:
            if not isinstance(rec, dict):
                continue
            meter = transform.detail_from_export(rec)
            meters[meter.meter_id] = meter
        log.info("Loaded snapshot: %d meters, %d transformers", len(meters), len(transformers))
        return Snapshot(meters=meters, transformers=transformers, fetched_at=datetime.now(timezone.utc))

    async def refresh(self) -> None:
        async with self._lock:
            self._snapshot = await self._load_snapshot()

    # ------------------------------------------------------------ queries

    async def list_meters(self, filters: MeterFilters, page: int, page_size: int) -> MeterPage:
        snap = await self._get_snapshot()
        matching = [m for m in snap.meters.values() if filters.matches(m)]
        matching.sort(key=lambda m: m.meter_id)
        total = len(matching)
        start = (page - 1) * page_size
        items = [transform.as_summary(m) for m in matching[start : start + page_size]]
        return MeterPage(
            items=items,
            page=page,
            page_size=page_size,
            total=total,
            total_pages=max(1, math.ceil(total / page_size)),
            data_as_of=snap.fetched_at,
        )

    async def get_meter(self, meter_id: str) -> MeterDetail:
        meter_id = normalise_meter_id(meter_id)
        snap = await self._snapshot_or_none()
        if snap and meter_id in snap.meters:
            return snap.meters[meter_id]
        return await self._meter_from_portal_page(meter_id)

    async def get_location(self, meter_id: str) -> MeterLocation:
        meter = await self.get_meter(meter_id)
        snap = await self._snapshot_or_none()
        registry = snap.transformers.get(meter.dt_code or "") if snap else None
        notes = list(meter.data_quality_notes)
        if snap is not None:
            notes += transform.transformer_notes(meter, registry)
        return MeterLocation(
            meter_id=meter.meter_id,
            coordinates=meter.coordinates,
            network=meter.network,
            transformer=registry,
            data_quality_notes=notes,
        )

    async def get_consumption(
        self, meter_id: str, date_from: Optional[date], date_to: Optional[date]
    ) -> MeterConsumption:
        meter_id = normalise_meter_id(meter_id)
        if date_from and date_to and date_from > date_to:
            raise InvalidRequestError("'from' must be on or before 'to'.")
        rows = await self._portal.get_meter_energy(meter_id, date_from, date_to)
        return transform.build_consumption(meter_id, rows, date_from, date_to)

    async def list_transformers(self) -> list[Transformer]:
        snap = await self._get_snapshot()
        return sorted(snap.transformers.values(), key=lambda t: t.code)

    async def network_hierarchy(self) -> NetworkHierarchy:
        """Tree built from each meter's own path. Codes repeat under different parents in
        the portal data, so a node is identified by its full path, not by its code alone."""
        snap = await self._get_snapshot()
        root: dict = {}
        for meter in snap.meters.values():
            level_map = root
            for level in transform.NETWORK_LEVELS:
                node = getattr(meter.network, level)
                key = (node.code, node.name)
                entry = level_map.setdefault(key, {"level": level, "count": 0, "children": {}})
                entry["count"] += 1
                level_map = entry["children"]

        def build(level_map: dict) -> list[HierarchyNode]:
            nodes = [
                HierarchyNode(
                    level=v["level"], code=k[0], name=k[1], meter_count=v["count"], children=build(v["children"])
                )
                for k, v in level_map.items()
            ]
            return sorted(nodes, key=lambda n: (n.code or "~", n.name or ""))

        return NetworkHierarchy(
            levels=transform.NETWORK_LEVELS,
            meter_count=len(snap.meters),
            roots=build(root),
            note=(
                "Built from each meter's network path as reported by the portal. The portal's paths are not a "
                "strict tree (the same code can appear under different parents), so nodes are grouped by path."
            ),
        )

    # ------------------------------------------------------------ internals

    async def _snapshot_or_none(self) -> Optional[Snapshot]:
        try:
            return await self._get_snapshot()
        except ServiceError as exc:
            if isinstance(exc, MeterNotFoundError):
                raise
            log.warning("Bulk snapshot unavailable (%s); using per-meter portal pages", exc.code)
            return None

    async def _meter_from_portal_page(self, meter_id: str) -> MeterDetail:
        page = await self._portal.get_meter_page(meter_id)  # raises MeterNotFoundError on 404
        try:
            geo = await self._portal.get_meter_geo(meter_id)
        except MeterNotFoundError:
            geo = None
        return transform.detail_from_meter_page(page, geo)


def normalise_meter_id(meter_id: str) -> str:
    """Meter IDs are upper-case on the portal (e.g. J100000) and its lookup is case-sensitive."""
    value = (meter_id or "").strip().upper()
    if not METER_ID_PATTERN.match(value):
        raise InvalidRequestError(
            "Invalid meter ID: use 1-32 letters, digits, '-' or '_' (for example J100000)."
        )
    return value
