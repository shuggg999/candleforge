"""Volume-based symbol tier classifier."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

import numpy as np
from loguru import logger

from .models import TierName, TierStats


class AiochClientAdapter:
    """Adapter exposing fetchall/execute on top of aiochclient.ChClient."""

    def __init__(self, ch_client) -> None:
        self._ch = ch_client

    async def fetchall(self, query: str) -> list[dict]:
        rows: list[dict] = []
        async for record in self._ch.iterate(query):
            # aiochclient Record is dict-like; coerce to plain dict
            rows.append(dict(record))
        return rows

    async def execute(self, query: str, *args, **kwargs) -> None:
        await self._ch.execute(query, *args, **kwargs)


def adapt_aiochclient(ch_client) -> AiochClientAdapter:
    """Wrap an aiochclient.ChClient into the Classifier's fetchall/execute contract."""
    return AiochClientAdapter(ch_client)


class Classifier:
    """Classify exchange symbols into 4 tiers by 24h quote volume."""

    def __init__(self, ch_client, refresh_hours: int = 12, exchange: str = "binance"):
        self._ch = ch_client
        self._refresh_hours = refresh_hours
        self._exchange = exchange
        self._tier_cache: dict[str, TierName] = {}
        self._last_stats: Optional[TierStats] = None
        self._last_refresh: Optional[datetime] = None
        self._last_error: Optional[str] = None
        self._lock = asyncio.Lock()

    def get_tier(self, symbol: str) -> Optional[TierName]:
        """O(1) tier lookup; returns None if symbol not classified."""
        return self._tier_cache.get(symbol)

    async def refresh_tiers(self) -> TierStats:
        """Fetch 24h volumes → compute tiers → persist snapshot → atomic cache swap.

        Errors propagate; the in-memory cache is preserved on any failure
        (fetch or insert) per the Refresh Resilience requirement.
        """
        query = self._build_volumes_query()
        try:
            rows = await self._ch.fetchall(query)
        except Exception as exc:
            self._last_error = f"fetch failed: {type(exc).__name__}: {exc}"
            logger.error("classifier refresh fetch failed: {}", exc)
            raise

        volumes: dict[str, Decimal] = {
            row["symbol"]: Decimal(str(row["quote_volume_24h"])) for row in rows
        }
        stats = self._compute_tiers(volumes)

        if stats.symbol_count > 0:
            try:
                await self._insert_snapshot(stats)
            except Exception as exc:
                self._last_error = f"insert failed: {type(exc).__name__}: {exc}"
                logger.error("classifier refresh insert failed: {}", exc)
                raise

        # atomic swap: only reached when fetch + insert both succeeded
        self._tier_cache = dict(stats.tiers)
        self._last_stats = stats
        self._last_refresh = stats.refreshed_at
        self._last_error = None
        logger.info(
            "classifier refresh ok: symbols={}, tiers={}",
            stats.symbol_count,
            stats.count_per_tier(),
        )
        return stats

    def _build_volumes_query(self) -> str:
        return (
            "SELECT symbol, sum(turnover) AS quote_volume_24h "
            "FROM ohlcv_futures "
            f"WHERE exchange = '{self._exchange}' "
            "AND timeframe = '1m' "
            "AND timestamp >= now() - INTERVAL 24 HOUR "
            "GROUP BY symbol "
            "HAVING quote_volume_24h > 0"
        )

    async def _insert_snapshot(self, stats: TierStats) -> None:
        """Batch insert one row per symbol into symbol_tiers."""
        if not stats.tiers:
            return
        # ClickHouse DateTime64 doesn't accept tz-aware ISO with offset suffix; strip tz.
        ts_naive = stats.refreshed_at.replace(tzinfo=None) if stats.refreshed_at.tzinfo else stats.refreshed_at
        rows = [
            (
                self._exchange,
                sym,
                ts_naive,
                tier,
                float(stats.volumes.get(sym, Decimal(0))),
                float(stats.p25),
                float(stats.p50),
                float(stats.p75),
                stats.symbol_count,
            )
            for sym, tier in stats.tiers.items()
        ]
        await self._ch.execute(
            "INSERT INTO symbol_tiers "
            "(exchange, symbol, refreshed_at, tier, quote_volume_24h, p25, p50, p75, symbol_count) VALUES",
            *rows,
        )

    async def shutdown(self) -> None:
        self._tier_cache = {}

    def health(self) -> dict:
        """Return health subprobe dict per spec Health Sub-probe requirement."""
        from .models import VALID_TIERS

        empty_counts = {t: 0 for t in VALID_TIERS}

        if self._last_refresh is None:
            return {
                "status": "failed",
                "last_refresh": None,
                "next_refresh": None,
                "symbol_count": empty_counts,
                "p25": None,
                "p50": None,
                "p75": None,
                "last_error": self._last_error,
            }

        next_refresh = self._last_refresh + timedelta(hours=self._refresh_hours)
        age = self._utcnow() - self._last_refresh
        stale_threshold = timedelta(hours=self._refresh_hours * 2)

        if age > stale_threshold:
            status = "degraded"
        elif self._last_error:
            status = "degraded"
        else:
            status = "ok"

        stats = self._last_stats
        counts = stats.count_per_tier() if stats else empty_counts
        return {
            "status": status,
            "last_refresh": self._last_refresh.isoformat(),
            "next_refresh": next_refresh.isoformat(),
            "symbol_count": counts,
            "p25": float(stats.p25) if stats else None,
            "p50": float(stats.p50) if stats else None,
            "p75": float(stats.p75) if stats else None,
            "last_error": self._last_error,
        }

    def _compute_tiers(self, volumes: dict[str, Decimal]) -> TierStats:
        """Compute P25/P50/P75 from non-zero volumes; assign each active symbol a tier.

        Zero-volume symbols (newly listed / delisted / halted) are excluded from
        both the quantile computation and the resulting tier dictionary.
        """
        refreshed_at = self._utcnow()
        active = {sym: vol for sym, vol in volumes.items() if vol > 0}

        if not active:
            return TierStats(
                refreshed_at=refreshed_at,
                p25=Decimal(0),
                p50=Decimal(0),
                p75=Decimal(0),
                symbol_count=0,
                tiers={},
            )

        vals = np.array([float(v) for v in active.values()], dtype=np.float64)
        p25, p50, p75 = (float(x) for x in np.percentile(vals, [25, 50, 75]))

        tiers: dict[str, TierName] = {}
        for sym, vol in active.items():
            v = float(vol)
            if v > p75:
                tiers[sym] = "mega"
            elif v > p50:
                tiers[sym] = "large"
            elif v > p25:
                tiers[sym] = "mid"
            else:
                tiers[sym] = "small"

        return TierStats(
            refreshed_at=refreshed_at,
            p25=Decimal(str(p25)),
            p50=Decimal(str(p50)),
            p75=Decimal(str(p75)),
            symbol_count=len(active),
            tiers=tiers,
            volumes=dict(active),
        )

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)
