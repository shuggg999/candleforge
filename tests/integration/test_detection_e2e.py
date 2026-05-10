"""End-to-end test for detection: testcontainers ClickHouse + real SQL + Detector flow."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
import pytest_asyncio
from aiochclient import ChClient
from testcontainers.clickhouse import ClickHouseContainer

from src.classification.classifier import adapt_aiochclient
from src.detection.detector import Detector


@pytest.fixture(scope="session")
def clickhouse_container():
    with ClickHouseContainer() as ch:
        yield ch


@pytest_asyncio.fixture
async def ch_client(clickhouse_container):
    host = clickhouse_container.get_container_host_ip()
    port = clickhouse_container.get_exposed_port(8123)
    url = f"http://{host}:{port}"

    async with aiohttp.ClientSession() as session:
        client = ChClient(
            session,
            url=url,
            user=clickhouse_container.username,
            password=clickhouse_container.password,
            database=clickhouse_container.dbname,
        )

        await client.execute("DROP TABLE IF EXISTS ohlcv_futures")
        await client.execute(
            "CREATE TABLE ohlcv_futures ("
            "exchange String, symbol String, timeframe String, "
            "timestamp DateTime64(3), "
            "open Decimal64(8), high Decimal64(8), low Decimal64(8), close Decimal64(8), "
            "volume Decimal128(4), turnover Decimal128(4)"
            ") ENGINE = ReplacingMergeTree() "
            "ORDER BY (exchange, symbol, timeframe, timestamp)"
        )
        yield client


def _make_klines(symbol: str, baseline_volume: float, current_volume: float):
    """Generate 1440 1m klines (24h): first 1435 at baseline, last 5 at current."""
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    rows = []
    for i in range(1440):
        # 1m intervals from 24h ago to now
        ts = now - timedelta(minutes=1440 - i)
        vol = current_volume if i >= 1435 else baseline_volume
        rows.append((
            "binance", symbol, "1m", ts,
            100.0, 100.0, 100.0, 100.0,
            vol, vol,
        ))
    return rows


@pytest.mark.integration
@pytest.mark.asyncio
async def test_detector_e2e_real_sql(ch_client):
    """Insert 24h of mock klines for 3 symbols → detect_once → verify ratio + level."""
    rows = []
    rows.extend(_make_klines("BTC/USDT", baseline_volume=1000.0, current_volume=4000.0))   # ratio 4.0
    rows.extend(_make_klines("ETH/USDT", baseline_volume=1000.0, current_volume=12000.0))  # ratio 12.0
    rows.extend(_make_klines("SOL/USDT", baseline_volume=1000.0, current_volume=1100.0))   # ratio 1.1 → normal

    BATCH = 200
    for i in range(0, len(rows), BATCH):
        await ch_client.execute(
            "INSERT INTO ohlcv_futures "
            "(exchange, symbol, timeframe, timestamp, open, high, low, close, volume, turnover) VALUES",
            *rows[i:i + BATCH],
        )

    classifier = MagicMock()
    classifier.get_tier.side_effect = lambda s: {
        "BTC/USDT": "mega",
        "ETH/USDT": "mega",
        "SOL/USDT": "mega",
    }.get(s)

    publisher = AsyncMock()
    detector = Detector(
        ch_client=adapt_aiochclient(ch_client),
        classifier=classifier,
        publisher=publisher,
        baseline_hours=24,
        current_minutes=5,
        min_samples=1200,
        interval_minutes=5,
        exchange="binance",
    )

    events = await detector.detect_once()

    by_symbol = {e.symbol: e for e in events}
    # BTC: ratio 4.0 (mega.warn=3, .strong=5) → WARN
    assert "BTC/USDT" in by_symbol
    assert by_symbol["BTC/USDT"].level.name == "WARN"
    # ETH: ratio 12.0 (mega.extreme=10) → EXTREME
    assert "ETH/USDT" in by_symbol
    assert by_symbol["ETH/USDT"].level.name == "EXTREME"
    # SOL: ratio 1.1 → NORMAL → no event
    assert "SOL/USDT" not in by_symbol

    publisher.publish.assert_awaited_once()
