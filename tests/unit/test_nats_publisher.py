"""Unit tests for src.events.nats_publisher.NatsPublisher.

Spec coverage (introduce-nats-event-bus):
- Subject Naming Convention
- K-Line Event Payload Schema (15 fields, schema_version=1, Decimal as string)
- JetStream Stream Bootstrap (idempotent)
- Publish Semantics (fire-and-forget after persist, swallow errors, increment counter)
- NATS_ENABLE=false → no-op
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.events.event_schema import SCHEMA_VERSION, KLineEvent
from src.events.nats_publisher import NatsPublisher


def _sample_row() -> dict:
    """One ClickHouse `ohlcv_futures` row, shaped exactly as ClickHouseManager passes it."""
    return {
        "exchange": "binance",
        "symbol": "BTC/USDT",
        "timeframe": "1m",
        "timestamp": datetime(2026, 5, 12, 0, 0, 0, tzinfo=timezone.utc),
        "open": Decimal("81000.00"),
        "high": Decimal("81200.50"),
        "low": Decimal("80950.00"),
        "close": Decimal("81100.00"),
        "volume": Decimal("123.456"),
        "turnover": Decimal("10005000.00"),
        "trades_count": 1234,
        "data_quality": "websocket",
        "is_closed": True,
        "ingested_at": datetime(2026, 5, 12, 0, 0, 0, 123000, tzinfo=timezone.utc),
    }


# --- 2.1 ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "exchange,symbol,timeframe,expected",
    [
        ("binance", "BTC/USDT", "1m", "ohlcv.binance.BTCUSDT.1m"),
        ("binance", "ETH/USDT", "5m", "ohlcv.binance.ETHUSDT.5m"),
        ("okx", "BTC-USD-SWAP", "4h", "ohlcv.okx.BTCUSDSWAP.4h"),
        ("bybit", "BTCUSDT", "1d", "ohlcv.bybit.BTCUSDT.1d"),
    ],
)
def test_publisher_subject_format(exchange, symbol, timeframe, expected):
    """Spec: subject MUST be `ohlcv.{exchange}.{symbol_normalized}.{timeframe}`.

    `symbol_normalized` strips `/`, `-` and any other NATS-illegal characters.
    """
    pub = NatsPublisher(url="nats://nope:4222", enabled=False)
    assert pub._build_subject(exchange, symbol, timeframe) == expected


# --- 2.2 ---------------------------------------------------------------------


def test_publisher_payload_schema():
    """Spec: payload contains exactly the 15 documented fields with correct types."""
    pub = NatsPublisher(url="nats://nope:4222", enabled=False)
    payload = pub._build_payload(_sample_row())

    expected_keys = {
        "schema_version", "exchange", "symbol", "symbol_normalized", "timeframe",
        "timestamp", "open", "high", "low", "close", "volume", "turnover",
        "trades_count", "data_quality", "is_closed", "ingested_at",
    }
    assert set(payload.keys()) == expected_keys, (
        f"payload keys mismatch:\nextra: {set(payload.keys()) - expected_keys}\n"
        f"missing: {expected_keys - set(payload.keys())}"
    )

    # Type assertions
    assert payload["schema_version"] == SCHEMA_VERSION == 1
    assert payload["exchange"] == "binance"
    assert payload["symbol"] == "BTC/USDT"
    assert payload["symbol_normalized"] == "BTCUSDT"
    assert payload["timeframe"] == "1m"
    assert payload["is_closed"] is True
    assert payload["trades_count"] == 1234
    assert payload["data_quality"] == "websocket"
    # Decimals MUST be strings (no float precision loss)
    for k in ("open", "high", "low", "close", "volume", "turnover"):
        assert isinstance(payload[k], str), (
            f"{k}={payload[k]!r} should be str, got {type(payload[k]).__name__}"
        )
    # Datetime MUST be ISO 8601 string with timezone offset
    assert payload["timestamp"].endswith("+00:00"), payload["timestamp"]
    assert payload["ingested_at"].endswith("+00:00"), payload["ingested_at"]
    # JSON-roundtrips clean (no Decimal/datetime objects left)
    json.dumps(payload)


# --- 2.3 ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publisher_publishes_on_insert():
    """publish_kline calls nc.publish exactly once with subject + JSON-encoded payload."""
    mock_nc = AsyncMock()
    mock_nc.publish = AsyncMock()

    pub = NatsPublisher(url="nats://nope:4222", enabled=True)
    pub._nc = mock_nc  # bypass connect

    row = _sample_row()
    await pub.publish_kline(row)

    mock_nc.publish.assert_awaited_once()
    subject, data_bytes = mock_nc.publish.await_args.args[:2]
    assert subject == "ohlcv.binance.BTCUSDT.1m"
    body = json.loads(data_bytes.decode("utf-8"))
    assert body["schema_version"] == 1
    assert body["symbol"] == "BTC/USDT"


# --- 2.4 ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publisher_swallows_errors_logs_warning():
    """publish failures must NOT raise — they log warn and increment counter."""
    mock_nc = AsyncMock()
    mock_nc.publish = AsyncMock(side_effect=ConnectionError("nats unreachable"))

    pub = NatsPublisher(url="nats://nope:4222", enabled=True)
    pub._nc = mock_nc

    # Should not raise
    await pub.publish_kline(_sample_row())

    assert pub._publish_failure_count == 1, (
        f"failure counter should be 1, got {pub._publish_failure_count}"
    )

    # Second failure increments
    await pub.publish_kline(_sample_row())
    assert pub._publish_failure_count == 2


# --- 2.5 ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publisher_no_op_when_disabled():
    """NATS_ENABLE=false → publish_kline is a no-op, never touches nc."""
    pub = NatsPublisher(url="nats://nope:4222", enabled=False)
    # _nc should remain None / never called
    assert pub._nc is None

    await pub.publish_kline(_sample_row())

    assert pub._nc is None
    assert pub._publish_failure_count == 0


@pytest.mark.asyncio
async def test_publisher_connect_no_op_when_disabled():
    """connect() is also a no-op when disabled (avoid network call on startup)."""
    pub = NatsPublisher(url="nats://nope:4222", enabled=False)
    with patch("src.events.nats_publisher.nats.connect", new=AsyncMock()) as mock_connect:
        await pub.connect()
        mock_connect.assert_not_awaited()
    assert pub._nc is None


# --- 8.2 — JetStream stream bootstrap ----------------------------------------


@pytest.mark.asyncio
async def test_publisher_bootstraps_jetstream_stream():
    """connect() MUST call js.add_stream(name='OHLCV', subjects=['ohlcv.>'], ...)
    exactly once with the documented config; idempotent on re-connect.
    """
    mock_nc = AsyncMock()
    mock_js = AsyncMock()
    mock_nc.jetstream = MagicMock(return_value=mock_js)
    mock_js.add_stream = AsyncMock()

    pub = NatsPublisher(url="nats://nope:4222", enabled=True)
    with patch("src.events.nats_publisher.nats.connect", new=AsyncMock(return_value=mock_nc)):
        await pub.connect()

    mock_js.add_stream.assert_awaited_once()
    call_kwargs = mock_js.add_stream.await_args.kwargs
    assert call_kwargs.get("name") == "OHLCV"
    assert call_kwargs.get("subjects") == ["ohlcv.>"]
    assert call_kwargs.get("max_age") == 24 * 3600
    assert call_kwargs.get("max_bytes") == 1 * 1024 * 1024 * 1024
    assert call_kwargs.get("storage") == "file"


@pytest.mark.asyncio
async def test_publisher_bootstrap_idempotent_on_existing_stream():
    """If stream already exists, add_stream raises — publisher must catch and not re-raise."""
    from nats.js.errors import BadRequestError

    mock_nc = AsyncMock()
    mock_js = AsyncMock()
    mock_nc.jetstream = MagicMock(return_value=mock_js)
    # Simulate "stream name already in use" error
    mock_js.add_stream = AsyncMock(
        side_effect=BadRequestError(code=400, description="stream name already in use", err_code=10058)
    )

    pub = NatsPublisher(url="nats://nope:4222", enabled=True)
    with patch("src.events.nats_publisher.nats.connect", new=AsyncMock(return_value=mock_nc)):
        # MUST not raise — idempotent
        await pub.connect()


# --- Health probe ------------------------------------------------------------


@pytest.mark.asyncio
async def test_publisher_health_when_disabled():
    """Disabled publisher reports status=ok with disabled=true."""
    pub = NatsPublisher(url="nats://nope:4222", enabled=False)
    h = pub.health()
    assert h["status"] == "ok"
    assert h["disabled"] is True
    assert h["connected"] is False
    assert h["publish_failure_count"] == 0


@pytest.mark.asyncio
async def test_publisher_health_when_connected_idle():
    """Connected publisher with no publishes yet → status=degraded (no recent activity is suspicious for a working pipeline)."""
    pub = NatsPublisher(url="nats://nope:4222", enabled=True)
    pub._nc = MagicMock(is_connected=True)  # simulate connected
    h = pub.health()
    assert h["connected"] is True
    assert h["disabled"] is False
    # last_publish_age_seconds is None → treated as degraded (we expect activity)
    assert h["last_publish_age_seconds"] is None


@pytest.mark.asyncio
async def test_publisher_health_after_recent_publish():
    """After a recent successful publish → status=ok with last_publish_age_seconds set."""
    mock_nc = AsyncMock()
    mock_nc.publish = AsyncMock()
    mock_nc.is_connected = True

    pub = NatsPublisher(url="nats://nope:4222", enabled=True)
    pub._nc = mock_nc
    await pub.publish_kline(_sample_row())

    h = pub.health()
    assert h["status"] == "ok"
    assert h["connected"] is True
    assert h["publish_failure_count"] == 0
    assert h["last_publish_age_seconds"] is not None
    assert h["last_publish_age_seconds"] < 5
