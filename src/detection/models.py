"""Domain models for the detection module."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum
from typing import Optional


class Level(IntEnum):
    """Anomaly intensity. IntEnum so we can compare with > / < across cycles."""

    NORMAL = 0
    WARN = 1
    STRONG = 2
    EXTREME = 3


@dataclass(frozen=True)
class AlertEvent:
    """One emitted detection event. Frozen to prevent downstream mutation."""

    symbol: str
    tier: str
    level: Level
    ratio: float
    current_avg: float
    baseline_median: float
    threshold_used: float
    detected_at: datetime
    is_escalation: bool
    prev_level: Optional[Level] = None
