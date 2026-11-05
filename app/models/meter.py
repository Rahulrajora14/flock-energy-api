"""Public data models for meters, network position, location and consumption."""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True)


class Meter(_Model):
    meter_id: str = Field(examples=["J100000"])
    serial_number: Optional[str] = Field(None, examples=["SE33962"])
    make: Optional[str] = Field(None, examples=["HPL"])
    phase_type: Optional[str] = Field(None, description="'single' or 'three' as reported by the portal.")
    installation_status: Optional[str] = Field(None, examples=["Installed", "Faulty", "Decommissioned"])
    installation_type: Optional[str] = Field(None, examples=["Whole Current", "CT Operated"])
    dt_code: Optional[str] = Field(None, description="Distribution transformer the meter is fed from.")


class NetworkNode(_Model):
    code: Optional[str] = Field(None, description="Portal code, e.g. 'F-001'. null when the portal has none.")
    name: Optional[str] = Field(None, description="Display name. null when the portal has none.")


class Transformer(_Model):
    code: str = Field(examples=["DT-001"])
    name: Optional[str] = Field(None, examples=["Malviya Nagar DT 1"])
    feeder_code: Optional[str] = Field(None, examples=["F-001"])
    capacity_kva: Optional[float] = Field(None, examples=[100])


class NetworkPosition(_Model):
    """The meter's path through the supply network, top (zone) to bottom (transformer)."""

    zone: NetworkNode
    circle: NetworkNode
    division: NetworkNode
    subdivision: NetworkNode
    substation: NetworkNode
    feeder: NetworkNode
    distribution_transformer: NetworkNode


class Coordinates(_Model):
    latitude: float = Field(examples=[26.938961002479868])
    longitude: float = Field(examples=[75.83095696146852])


class MeterDetail(Meter):
    network: NetworkPosition
    coordinates: Optional[Coordinates] = None
    data_quality_notes: list[str] = Field(
        default_factory=list,
        description="Inconsistencies found in the portal data for this meter (nothing is silently corrected).",
    )


class MeterLocation(_Model):
    meter_id: str
    coordinates: Optional[Coordinates]
    network: NetworkPosition
    transformer: Optional[Transformer] = Field(
        None, description="Registry entry for the meter's transformer (capacity, feeder), when it exists."
    )
    data_quality_notes: list[str] = Field(default_factory=list)


class ConsumptionReading(_Model):
    timestamp: datetime = Field(description="Local portal time; the portal gives no timezone (see README).")
    cumulative_kwh: Optional[float] = Field(None, description="Active-energy register reading (kWh).")
    cumulative_kvah: Optional[float] = Field(None, description="Apparent-energy register reading (kVAh).")
    voltage_r_v: Optional[float] = Field(None, description="R-phase voltage (V).")
    interval_kwh: Optional[float] = Field(
        None, description="Derived: change in cumulative_kwh since the previous reading."
    )


class ConsumptionSummary(_Model):
    kwh_consumed: Optional[float] = Field(None, description="Derived: last minus first cumulative_kwh.")
    kvah_consumed: Optional[float] = Field(None, description="Derived: last minus first cumulative_kvah.")
    average_voltage_r_v: Optional[float] = None


class MeterConsumption(_Model):
    meter_id: str
    requested_from: Optional[date] = None
    requested_to: Optional[date] = None
    first_reading_at: Optional[datetime] = None
    last_reading_at: Optional[datetime] = None
    interval_minutes: Optional[int] = Field(None, description="Most common spacing between readings.")
    reading_count: int
    summary: ConsumptionSummary
    readings: list[ConsumptionReading]
    data_quality_notes: list[str] = Field(default_factory=list)


class HierarchyNode(_Model):
    level: str
    code: Optional[str]
    name: Optional[str]
    meter_count: int
    children: list["HierarchyNode"] = Field(default_factory=list)
