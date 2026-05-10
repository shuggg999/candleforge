"""Periodic volume anomaly detector (5min window vs 24h baseline median)."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Optional, Protocol

from loguru import logger

from .models import AlertEvent, Level
from .thresholds import DEFAULT_THRESHOLDS, ThresholdTable


class AlertPublisher(Protocol):
    """Pluggable sink for emitted AlertEvent batches."""

    async def publish(self, events: list[AlertEvent]) -> None: ...


class LoggingPublisher:
    """Default publisher: log each event as structured JSON (stdout via loguru)."""

    async def publish(self, events: list[AlertEvent]) -> None:
        if not events:
            return
        for ev in events:
            logger.info("alert_event {}", _event_to_dict(ev))


def _event_to_dict(ev: AlertEvent) -> dict:
    return {
        "symbol": ev.symbol,
        "tier": ev.tier,
        "level": ev.level.name,
        "ratio": ev.ratio,
        "current_avg": ev.current_avg,
        "baseline_median": ev.baseline_median,
        "threshold_used": ev.threshold_used,
        "detected_at": ev.detected_at.isoformat(),
        "is_escalation": ev.is_escalation,
        "prev_level": ev.prev_level.name if ev.prev_level else None,
    }


class Detector:
    """Periodic volume anomaly detector. One cycle = one ClickHouse query + per-symbol classify."""

    def __init__(
        self,
        ch_client,
        classifier,
        publisher: AlertPublisher,
        thresholds: Optional[ThresholdTable] = None,
        baseline_hours: int = 24,
        current_minutes: int = 5,
        min_samples: int = 1200,
        interval_minutes: int = 5,
        exchange: str = "binance",
    ) -> None:
        self._ch = ch_client
        self._classifier = classifier
        self._publisher = publisher
        self._thresholds: ThresholdTable = thresholds or DEFAULT_THRESHOLDS
        self._baseline_hours = baseline_hours
        self._current_minutes = current_minutes
        self._min_samples = min_samples
        self._interval_minutes = interval_minutes
        self._exchange = exchange

        self._last_levels: dict[str, Level] = {}
        self._last_run: Optional[datetime] = None
        self._last_run_duration_ms: Optional[int] = None
        self._symbols_skipped_low_data: int = 0
        self._lock = asyncio.Lock()

    @property
    def thresholds(self) -> ThresholdTable:
        return self._thresholds

    @property
    def last_levels(self) -> dict[str, Level]:
        return dict(self._last_levels)

    async def detect_once(self) -> list[AlertEvent]:
        """Run one detection cycle: SQL → per-symbol classify → escalation diff → events.

        Always invokes `publisher.publish(events)` exactly once before returning,
        even when events is empty. Returns the events list for callers that want it.
        """
        async with self._lock:
            started = time.monotonic()
            detected_at = self._utcnow()

            query = self._build_detection_sql()
            rows = await self._ch.fetchall(query)

            self._symbols_skipped_low_data = 0
            events: list[AlertEvent] = []
            new_levels: dict[str, Level] = {}

            for row in rows:
                symbol = row["symbol"]
                sample_count = int(row.get("sample_count", 0) or 0)
                if sample_count < self._min_samples:
                    self._symbols_skipped_low_data += 1
                    continue

                baseline = float(row.get("baseline_median") or 0.0)
                current = float(row.get("current_avg") or 0.0)
                if baseline <= 0:
                    continue

                tier = self._classifier.get_tier(symbol)
                if tier is None:
                    continue

                ratio = current / baseline
                level, threshold_used = self._classify_ratio(ratio, tier)

                new_levels[symbol] = level

                prior = self._last_levels.get(symbol)
                prior_int = int(prior) if prior is not None else 0
                if level > Level.NORMAL and int(level) > prior_int:
                    events.append(
                        AlertEvent(
                            symbol=symbol,
                            tier=tier,
                            level=level,
                            ratio=ratio,
                            current_avg=current,
                            baseline_median=baseline,
                            threshold_used=threshold_used,
                            detected_at=detected_at,
                            is_escalation=True,
                            prev_level=prior,
                        )
                    )

            # Atomic state swap (after all rows processed)
            self._last_levels = new_levels
            self._last_run = detected_at
            self._last_run_duration_ms = int((time.monotonic() - started) * 1000)

            await self._publisher.publish(events)

            logger.info(
                "detection cycle ok: events={}, skipped_low_data={}, duration_ms={}",
                len(events), self._symbols_skipped_low_data, self._last_run_duration_ms,
            )
            return events

    def _classify_ratio(self, ratio: float, tier: str) -> tuple[Level, float]:
        """Map (ratio, tier) to (Level, threshold_value_that_was_crossed)."""
        t = self._thresholds.get(tier, {})
        warn_t = float(t.get("warn", float("inf")))
        strong_t = float(t.get("strong", float("inf")))
        extreme_t = float(t.get("extreme", float("inf")))

        if ratio >= extreme_t:
            return Level.EXTREME, extreme_t
        if ratio >= strong_t:
            return Level.STRONG, strong_t
        if ratio >= warn_t:
            return Level.WARN, warn_t
        return Level.NORMAL, warn_t

    def _build_detection_sql(self) -> str:
        """Single ClickHouse query returning per-symbol baseline + current aggregates."""
        return (
            "SELECT "
            "symbol, "
            "count() AS sample_count, "
            "quantile(0.5)(volume) AS baseline_median, "
            f"avgIf(volume, timestamp >= now() - INTERVAL {self._current_minutes} MINUTE) AS current_avg "
            "FROM ohlcv_futures "
            f"WHERE exchange = '{self._exchange}' "
            "AND timeframe = '1m' "
            f"AND timestamp >= now() - INTERVAL {self._baseline_hours} HOUR "
            "GROUP BY symbol"
        )

    def health(self) -> dict:
        """Sub-probe per spec Health Sub-probe requirement."""
        counts = {"warn": 0, "strong": 0, "extreme": 0}
        for level in self._last_levels.values():
            if level == Level.WARN:
                counts["warn"] += 1
            elif level == Level.STRONG:
                counts["strong"] += 1
            elif level == Level.EXTREME:
                counts["extreme"] += 1

        if self._last_run is None:
            return {
                "status": "failed",
                "last_run": None,
                "last_run_duration_ms": None,
                "active_alerts": counts,
                "symbols_skipped_low_data": self._symbols_skipped_low_data,
            }

        age = self._utcnow() - self._last_run
        stale_threshold = timedelta(minutes=self._interval_minutes * 2)
        status = "degraded" if age > stale_threshold else "ok"

        return {
            "status": status,
            "last_run": self._last_run.isoformat(),
            "last_run_duration_ms": self._last_run_duration_ms,
            "active_alerts": counts,
            "symbols_skipped_low_data": self._symbols_skipped_low_data,
        }

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)
