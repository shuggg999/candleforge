"""Tests for ClickHouseManager → NatsPublisher wiring.

Spec: After every successful insert into ohlcv_futures, the publisher's
`publish_kline(row)` MUST be awaited exactly once. Publisher exceptions
MUST NOT propagate into the insert path.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.events.nats_publisher import NatsPublisher
from src.storage.clickhouse import ClickHouseManager


def _row() -> dict:
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


@pytest.mark.asyncio
async def test_set_publisher_attaches_to_manager():
    """ClickHouseManager.set_publisher() stashes the reference for use by insert path."""
    mgr = ClickHouseManager.__new__(ClickHouseManager)  # bypass __init__ network call
    mgr._publisher = None
    pub = NatsPublisher(url="nats://nope:4222", enabled=False)
    mgr.set_publisher(pub)
    assert mgr._publisher is pub


@pytest.mark.asyncio
async def test_publish_after_insert_calls_publisher_once():
    """The hook ClickHouseManager._publish_after_insert(row) awaits publisher.publish_kline once."""
    mgr = ClickHouseManager.__new__(ClickHouseManager)
    mock_pub = AsyncMock()
    mgr._publisher = mock_pub

    await mgr._publish_after_insert(_row())

    mock_pub.publish_kline.assert_awaited_once_with(_row())


@pytest.mark.asyncio
async def test_publish_after_insert_swallows_publisher_errors():
    """If publisher.publish_kline raises, ClickHouseManager hook MUST NOT propagate."""
    mgr = ClickHouseManager.__new__(ClickHouseManager)
    mock_pub = AsyncMock()
    mock_pub.publish_kline.side_effect = ConnectionError("nats down")
    mgr._publisher = mock_pub

    # Must not raise
    await mgr._publish_after_insert(_row())


@pytest.mark.asyncio
async def test_publish_after_insert_no_op_when_publisher_unset():
    """If no publisher attached, the hook is a no-op (don't crash)."""
    mgr = ClickHouseManager.__new__(ClickHouseManager)
    mgr._publisher = None
    # Must not raise
    await mgr._publish_after_insert(_row())
