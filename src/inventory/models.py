"""Inventory data model (local SQLite / JSON)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class AssetStatus(str, Enum):
    ORDERED = "Ordered"
    RECEIVED = "Received"
    IN_STOREROOM = "In Storeroom"
    ISSUED_DEDICATED = "Issued-Dedicated"
    ISSUED_LOAN = "Issued-Loan"
    RETURNED = "Returned"
    QUARANTINE = "Quarantine"
    RETIRED = "Retired"


@dataclass
class OrderLine:
    asset_type: str
    model: str = ""
    qty_ordered: int = 0
    qty_received: int = 0
    notes: str = ""


@dataclass
class Order:
    order_id: str
    status: str = "Pending Delivery"  # Pending Delivery | Partially Received | Complete | Closed
    source: str = "quote"
    lines: list[OrderLine] = field(default_factory=list)
    created_by: str = ""
    created_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Asset:
    asset_id: str
    barcode: str = ""
    serial_number: str = ""
    asset_type: str = ""
    model: str = ""
    status: AssetStatus = AssetStatus.ORDERED
    location: str = ""
    assigned_to: str = ""
    order_id: str = ""
    return_due_at: datetime | None = None
    condition_notes: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Movement:
    asset_id: str
    from_status: str
    to_status: str
    actor: str
    at: datetime
    note: str = ""
    photo_ref: str = ""


@dataclass
class ExceptionRecord:
    exception_id: str
    type: str  # short | over | type_mismatch | damage | process_skip | other
    status: str = "Open"  # Open | Resolved
    order_id: str = ""
    asset_id: str = ""
    raised_by: str = ""
    detail: str = ""
    slack_thread: str = ""
    created_at: datetime | None = None
