"""NATS event bus publisher.

Publishes K-line events to subject `ohlcv.{exchange}.{symbol_norm}.{timeframe}`
after each successful ClickHouse insert. Failures are non-fatal — they log a
warning and increment `publish_failure_count` rather than disrupting the
ClickHouse write path.

Spec: openspec/changes/introduce-nats-event-bus/specs/nats-event-bus/spec.md
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

import nats
from loguru import logger
from nats.js.errors import BadRequestError

from src.events.event_schema import SCHEMA_VERSION

# Stream config (per design.md Decision 5)
_STREAM_NAME = "OHLCV"
_STREAM_SUBJECTS = ["ohlcv.>"]
_STREAM_MAX_AGE_SECONDS = 24 * 3600
_STREAM_MAX_BYTES = 1 * 1024 * 1024 * 1024  # 1 GiB

# Health probe thresholds
_HEALTH_FAILURE_DEGRADED_THRESHOLD = 100
_HEALTH_LAST_PUBLISH_DEGRADED_AGE_SECONDS = 300

# Subject component sanitization — strip everything that is not [A-Za-z0-9_].
# NATS technically allows `-` in subjects, but normalizing it out keeps the
# convention consistent with `BTC/USDT → BTCUSDT` (slashes removed) and
# `BTC-USD-SWAP → BTCUSDSWAP` (hyphens removed). Symbols become unambiguous
# concatenations, easier to grep / wildcard.
_SUBJECT_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_]")


class NatsPublisher:
    """Asynchronous NATS publisher with JetStream stream bootstrap.

    Lifecycle: `connect()` → many `publish_kline()` calls → `close()`.
    """

    def __init__(self, url: str, enabled: bool = True) -> None:
        self._url = url
        self._enabled = enabled
        self._nc: Optional[Any] = None  # nats.aio.client.Client
        self._publish_failure_count: int = 0
        self._last_publish_ts: Optional[datetime] = None

    # ------------------------------------------------------------------ lifecycle

    async def connect(self) -> None:
        """Connect to NATS and ensure JetStream stream `OHLCV` exists.

        No-op when `enabled=False`. Idempotent: re-connecting reuses an existing
        stream (BadRequestError "stream name already in use" is swallowed).
        """
        if not self._enabled:
            logger.info("NatsPublisher disabled (NATS_ENABLE=false); skipping connect")
            return

        logger.info("NatsPublisher connecting to {}", self._url)
        self._nc = await nats.connect(self._url)
        js = self._nc.jetstream()
        try:
            await js.add_stream(
                name=_STREAM_NAME,
                subjects=_STREAM_SUBJECTS,
                max_age=_STREAM_MAX_AGE_SECONDS,
                max_bytes=_STREAM_MAX_BYTES,
                storage="file",
            )
            logger.info("JetStream stream {!r} created", _STREAM_NAME)
        except BadRequestError as exc:
            # "stream name already in use" → idempotent, fine
            if "already in use" in str(exc).lower() or getattr(exc, "err_code", None) == 10058:
                logger.info("JetStream stream {!r} already exists; reusing", _STREAM_NAME)
            else:
                raise

    async def close(self) -> None:
        if self._nc is not None:
            try:
                await self._nc.drain()
            except Exception as exc:  # noqa: BLE001 — shutdown best-effort
                logger.warning("NatsPublisher drain failed (ignored): {}", exc)
            self._nc = None

    # ------------------------------------------------------------------ public API

    async def publish_kline(self, row: Dict[str, Any]) -> None:
        """Publish one ClickHouse `ohlcv_futures` row to NATS.

        Failures (broken pipe, JetStream rejection, serialization error) are
        logged at WARN and increment `_publish_failure_count`. They do NOT
        propagate; callers (ClickHouseManager) MUST treat publish as
        fire-and-forget.
        """
        if not self._enabled or self._nc is None:
            return

        try:
            subject = self._build_subject(
                row["exchange"], row["symbol"], row["timeframe"]
            )
            payload = self._build_payload(row)
            data = json.dumps(payload).encode("utf-8")
            await self._nc.publish(subject, data)
            self._last_publish_ts = datetime.now(timezone.utc)
        except Exception as exc:  # noqa: BLE001 — fire-and-forget contract
            self._publish_failure_count += 1
            logger.warning(
                "NATS publish failed (#{}): {}",
                self._publish_failure_count,
                exc,
            )

    def health(self) -> Dict[str, Any]:
        """Sub-probe payload for `/api/v1/health`.

        Verdict ladder per spec:
        - disabled → ok (intentional no-op)
        - connected + recent publish + low failure count → ok
        - disconnected OR high failure count OR stale last-publish → degraded
        - never connected since startup → failed
        """
        if not self._enabled:
            return {
                "status": "ok",
                "connected": False,
                "publish_failure_count": 0,
                "last_publish_age_seconds": None,
                "disabled": True,
            }

        connected = bool(self._nc is not None and getattr(self._nc, "is_connected", False))
        last_age: Optional[float] = None
        if self._last_publish_ts is not None:
            last_age = (datetime.now(timezone.utc) - self._last_publish_ts).total_seconds()

        if not connected and self._nc is None:
            status = "failed"
        elif not connected:
            status = "degraded"
        elif self._publish_failure_count > _HEALTH_FAILURE_DEGRADED_THRESHOLD:
            status = "degraded"
        elif last_age is not None and last_age > _HEALTH_LAST_PUBLISH_DEGRADED_AGE_SECONDS:
            status = "degraded"
        elif last_age is None:
            # Connected but no publish yet — common right after startup; report ok
            # (the `degraded` for stale-publish only kicks in after we've seen at
            # least one publish go through and then stall).
            status = "ok"
        else:
            status = "ok"

        return {
            "status": status,
            "connected": connected,
            "publish_failure_count": self._publish_failure_count,
            "last_publish_age_seconds": last_age,
            "disabled": False,
        }

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _build_subject(exchange: str, symbol: str, timeframe: str) -> str:
        """`ohlcv.{exchange}.{symbol_normalized}.{timeframe}` per spec."""
        symbol_normalized = _SUBJECT_SANITIZE_RE.sub("", symbol)
        return f"ohlcv.{exchange}.{symbol_normalized}.{timeframe}"

    @staticmethod
    def _build_payload(row: Dict[str, Any]) -> Dict[str, Any]:
        """Convert a ClickHouse row dict to the NATS-published JSON payload.

        Decimal → str (avoid float precision loss); datetime → ISO 8601 with tz.
        Returns a plain dict that is `json.dumps`-safe.
        """
        def _ser_decimal(v: Any) -> str:
            if isinstance(v, Decimal):
                return str(v)
            if isinstance(v, (int, float)):
                return str(Decimal(str(v)))
            return str(v)

        def _ser_dt(v: Any) -> str:
            if isinstance(v, datetime):
                # Force tz-aware, default UTC for naive (ClickHouse rows are tz-aware in practice)
                if v.tzinfo is None:
                    v = v.replace(tzinfo=timezone.utc)
                return v.isoformat()
            return str(v)

        symbol = str(row["symbol"])
        symbol_normalized = _SUBJECT_SANITIZE_RE.sub("", symbol)

        return {
            "schema_version": SCHEMA_VERSION,
            "exchange": str(row["exchange"]),
            "symbol": symbol,
            "symbol_normalized": symbol_normalized,
            "timeframe": str(row["timeframe"]),
            "timestamp": _ser_dt(row["timestamp"]),
            "open": _ser_decimal(row["open"]),
            "high": _ser_decimal(row["high"]),
            "low": _ser_decimal(row["low"]),
            "close": _ser_decimal(row["close"]),
            "volume": _ser_decimal(row["volume"]),
            "turnover": _ser_decimal(row["turnover"]),
            "trades_count": (
                int(row["trades_count"]) if row.get("trades_count") is not None else None
            ),
            "data_quality": str(row.get("data_quality", "unknown")),
            "is_closed": bool(row.get("is_closed", True)),
            "ingested_at": _ser_dt(
                row.get("ingested_at", datetime.now(timezone.utc))
            ),
        }
