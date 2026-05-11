"""K-line event payload schema published to NATS subject `ohlcv.>`.

Spec: introduce-nats-event-bus / K-Line Event Payload Schema (15 fields).
Bumping `SCHEMA_VERSION` is REQUIRED when removing or changing existing fields;
adding new optional fields does NOT require a bump.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

# Bump only on breaking change (field removal / type change).
SCHEMA_VERSION: int = 1


@dataclass(frozen=True)
class KLineEvent:
    """Typed view over the JSON payload published to NATS.

    Used by downstream consumers (volume-monitor, etc.) when deserializing.
    The publisher itself works directly on the row dict from ClickHouseManager
    and does not instantiate this dataclass.
    """

    schema_version: int
    exchange: str
    symbol: str
    symbol_normalized: str
    timeframe: str
    timestamp: datetime
    open: str  # decimal string
    high: str
    low: str
    close: str
    volume: str
    turnover: str
    trades_count: Optional[int]
    data_quality: str
    is_closed: bool
    ingested_at: datetime
