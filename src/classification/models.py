"""Domain models for the classification module."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal

TierName = Literal["mega", "large", "mid", "small"]

VALID_TIERS: tuple[TierName, ...] = ("mega", "large", "mid", "small")


@dataclass(frozen=True)
class TierStats:
    """Snapshot of a single tier-classification refresh cycle."""

    refreshed_at: datetime
    p25: Decimal
    p50: Decimal
    p75: Decimal
    symbol_count: int
    tiers: dict[str, TierName] = field(default_factory=dict)
    volumes: dict[str, Decimal] = field(default_factory=dict)

    def count_per_tier(self) -> dict[TierName, int]:
        counts: dict[TierName, int] = {t: 0 for t in VALID_TIERS}
        for tier in self.tiers.values():
            counts[tier] += 1
        return counts
