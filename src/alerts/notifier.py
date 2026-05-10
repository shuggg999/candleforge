"""Notifier: AlertPublisher impl that audits events to ClickHouse + sends to Telegram."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from src.detection.detector import AlertPublisher
from src.detection.models import AlertEvent

from .formatter import format_message
from .models import AlertStatus, TelegramSendResult
from .telegram_client import TelegramClient


class Notifier(AlertPublisher):
    """Implements AlertPublisher: insert pending → send to Telegram → update status."""

    def __init__(
        self,
        ch_client,
        telegram_client: Optional[TelegramClient],
        chat_id: Optional[str],
        dry_run: bool = False,
        exchange: str = "binance",
    ) -> None:
        self._ch = ch_client
        self._tg = telegram_client
        self._chat_id = chat_id
        self._exchange = exchange
        self._dry_run = bool(dry_run or telegram_client is None or not chat_id)
        self._last_send_at: Optional[datetime] = None
        self._consecutive_failures: int = 0
        if self._dry_run:
            logger.warning(
                "Notifier in DRY-RUN mode — no Telegram messages will be sent "
                "(token or chat_id missing, or ALERTS_DRY_RUN=true)"
            )

    async def publish(self, events: list[AlertEvent]) -> None:
        """Insert all events as pending → send (or skip in dry-run) → update statuses.

        Errors are caught and logged; this method MUST NOT raise.
        """
        if not events:
            return

        # Step 1: insert pending audit rows
        try:
            await self._insert_pending(events)
        except Exception as exc:
            logger.error("notifier insert pending failed (audit may be incomplete): {}", exc)
            # Continue: still try to send so user gets the message

        # Step 2: dry-run shortcut
        if self._dry_run:
            for ev in events:
                try:
                    await self._update_status(ev, AlertStatus.SKIPPED, error="dry-run")
                except Exception as exc:
                    logger.warning("notifier dry-run update failed for {}: {}", ev.symbol, exc)
            return

        # Step 3: concurrent send
        send_tasks = [self._send_one(ev) for ev in events]
        results = await asyncio.gather(*send_tasks, return_exceptions=True)

        # Step 4: per-event status update
        now = datetime.now(timezone.utc)
        for ev, res in zip(events, results):
            try:
                if isinstance(res, Exception):
                    await self._update_status(ev, AlertStatus.FAILED, error=str(res))
                    self._consecutive_failures += 1
                elif res.success:
                    await self._update_status(ev, AlertStatus.SENT, sent_at=now)
                    self._last_send_at = now
                    self._consecutive_failures = 0
                else:
                    await self._update_status(ev, AlertStatus.FAILED, error=res.error or "")
                    self._consecutive_failures += 1
            except Exception as exc:
                logger.warning("notifier status update failed for {}: {}", ev.symbol, exc)

    async def _send_one(self, event: AlertEvent) -> TelegramSendResult:
        text = format_message(event)
        return await self._tg.send_message(self._chat_id, text)

    async def _insert_pending(self, events: list[AlertEvent]) -> None:
        rows = []
        for ev in events:
            ts = ev.detected_at.replace(tzinfo=None) if ev.detected_at.tzinfo else ev.detected_at
            rows.append(
                (
                    ts,
                    self._exchange,
                    ev.symbol,
                    ev.tier,
                    ev.level.name.lower(),
                    ev.prev_level.name.lower() if ev.prev_level else "none",
                    float(ev.ratio),
                    float(ev.current_avg),
                    float(ev.baseline_median),
                    float(ev.threshold_used),
                )
            )
        await self._ch.execute(
            "INSERT INTO volume_alerts "
            "(detected_at, exchange, symbol, tier, level, prev_level, ratio, "
            "curr_5min_avg, baseline_median, threshold_used) VALUES",
            *rows,
        )

    async def _update_status(
        self,
        event: AlertEvent,
        status: AlertStatus,
        error: Optional[str] = None,
        sent_at: Optional[datetime] = None,
    ) -> None:
        ts = event.detected_at.replace(tzinfo=None) if event.detected_at.tzinfo else event.detected_at
        ts_str = ts.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]  # millisecond precision
        sent_at_clause = "NULL"
        if sent_at is not None:
            sent_at_naive = sent_at.replace(tzinfo=None) if sent_at.tzinfo else sent_at
            sent_at_str = sent_at_naive.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            sent_at_clause = f"toDateTime64('{sent_at_str}', 3)"
        # Escape single quotes in error message
        error_escaped = (error or "").replace("'", "''")
        symbol_escaped = event.symbol.replace("'", "''")
        query = (
            f"ALTER TABLE volume_alerts UPDATE "
            f"telegram_status = '{status.value}', "
            f"telegram_error = '{error_escaped}', "
            f"sent_at = {sent_at_clause} "
            f"WHERE detected_at = toDateTime64('{ts_str}', 3) "
            f"AND symbol = '{symbol_escaped}'"
        )
        await self._ch.execute(query)

    def health(self) -> dict:
        """Sub-probe per spec Health Sub-probe requirement.

        Note: stats_24h (per-level sent/failed counts) is exposed via
        GET /api/v1/alerts/stats which performs an async ClickHouse query;
        keeping health() sync to match classifier/detector contracts.
        """
        FAILURE_THRESHOLD = 5

        configured = self._tg is not None and bool(self._chat_id)
        if self._consecutive_failures >= FAILURE_THRESHOLD:
            status = "degraded"
        elif self._dry_run:
            status = "degraded"
        else:
            status = "ok"

        return {
            "status": status,
            "telegram_configured": configured,
            "telegram_dry_run": self._dry_run,
            "last_send_at": self._last_send_at.isoformat() if self._last_send_at else None,
            "consecutive_failures": self._consecutive_failures,
        }

    async def fetch_stats_24h(self) -> dict:
        """Aggregate stats over the last 24h of volume_alerts (async; called by /alerts/stats)."""
        try:
            rows = await self._ch.fetchall(
                "SELECT level, telegram_status, count() AS c "
                "FROM volume_alerts "
                "WHERE detected_at >= now() - INTERVAL 24 HOUR "
                "GROUP BY level, telegram_status"
            )
        except Exception as exc:
            logger.warning("fetch_stats_24h failed: {}", exc)
            return {"sent": 0, "failed": 0, "by_level": {}}

        sent = 0
        failed = 0
        by_level: dict[str, dict[str, int]] = {}
        for r in rows:
            lvl = r["level"]
            st = r["telegram_status"]
            cnt = int(r["c"])
            by_level.setdefault(lvl, {"sent": 0, "failed": 0, "skipped": 0, "pending": 0})
            by_level[lvl][st] = by_level[lvl].get(st, 0) + cnt
            if st == "sent":
                sent += cnt
            elif st == "failed":
                failed += cnt
        return {"sent": sent, "failed": failed, "by_level": by_level}

    async def send_test_message(self) -> dict:
        """For POST /api/v1/alerts/test — sends a hard-coded test message."""
        if self._dry_run or self._tg is None:
            return {"status": "skipped", "reason": "dry-run mode"}
        text = (
            "🟡 *VOLUME WARN* (test message)\n"
            "*TEST/USDT*  ·  mega (超大盘)\n"
            "ratio: *3.0×*  ·  threshold: warn=3×\n"
            "current: 30.0M USDT  vs baseline: 10.0M USDT\n"
            "detected: <test>"
        )
        try:
            res = await self._tg.send_message(self._chat_id, text)
            if res.success:
                return {"status": "sent", "message_id": res.message_id}
            return {"status": "failed", "error": res.error}
        except Exception as exc:
            return {"status": "failed", "error": str(exc)}
