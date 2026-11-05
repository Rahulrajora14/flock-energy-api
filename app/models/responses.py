"""Response envelopes shared by the routes."""

from __future__ import annotations

from datetime import datetime
from typing import Generic, Optional, TypeVar

from pydantic import BaseModel, Field

from app.models.meter import HierarchyNode, Meter, Transformer

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    page: int
    page_size: int
    total: int
    total_pages: int


class MeterPage(Page[Meter]):
    data_as_of: Optional[datetime] = Field(None, description="When the meter snapshot was fetched from the portal.")


class TransformerList(BaseModel):
    items: list[Transformer]
    total: int


class NetworkHierarchy(BaseModel):
    levels: list[str]
    meter_count: int
    roots: list[HierarchyNode]
    note: str


class HealthResponse(BaseModel):
    status: str = Field(examples=["healthy"])
    service: str = Field(examples=["urja-meter-api"])
    version: str = Field(examples=["1.0.0"])


class PortalHealthResponse(BaseModel):
    status: str = Field(examples=["ok", "unavailable"])
    portal_url: str
    authenticated: bool
    detail: Optional[str] = None


class ErrorBody(BaseModel):
    code: str = Field(examples=["meter_not_found"])
    message: str = Field(examples=["Meter 'J999999' was not found."])


class ErrorResponse(BaseModel):
    error: ErrorBody
