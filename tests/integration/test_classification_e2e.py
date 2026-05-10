"""End-to-end test: testcontainers ClickHouse → mock klines → classifier full flow."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import aiohttp
import pytest
import pytest_asyncio
from aiochclient import ChClient
from testcontainers.clickhouse import ClickHouseContainer

from src.classification.classifier import Classifier, adapt_aiochclient


SYMBOL_VOLUMES_24H_USDT = {
    "BTC/USDT": 4_000_000_000,
    "ETH/USDT": 2_000_000_000,
    "BNB/USDT": 800_000_000,
    "SOL/USDT": 400_000_000,
    "DOGE/USDT": 80_000_000,
    "ADA/USDT": 40_000_000,
    "XRP/USDT": 8_000_000,
    "DOT/USDT": 4_000_000,
    "TRX/USDT": 800_000,
    "LINK/USDT": 400_000,
    "AVAX/USDT": 80_000,
    "ATOM/USDT": 40_000,
}


@pytest.fixture(scope="session")
def clickhouse_container():
    with ClickHouseContainer() as ch:
        yield ch


@pytest_asyncio.fixture
async def ch_client(clickhouse_container):
    """Async aiochclient connected to testcontainers ClickHouse + recreate schema."""
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
        await client.execute("DROP TABLE IF EXISTS symbol_tiers")

        await client.execute(
            "CREATE TABLE ohlcv_futures ("
            "exchange String, symbol String, timeframe String, "
            "timestamp DateTime64(3), "
            "open Decimal64(8), high Decimal64(8), low Decimal64(8), close Decimal64(8), "
            "volume Decimal128(4), turnover Decimal128(4)"
            ") ENGINE = ReplacingMergeTree() "
            "ORDER BY (exchange, symbol, timeframe, timestamp)"
        )
        await client.execute(
            "CREATE TABLE symbol_tiers ("
            "exchange String, symbol String, refreshed_at DateTime64(3), "
            "tier LowCardinality(String), quote_volume_24h Decimal128(4), "
            "p25 Decimal128(4), p50 Decimal128(4), p75 Decimal128(4), "
            "symbol_count UInt32"
            ") ENGINE = ReplacingMergeTree() "
            "ORDER BY (exchange, symbol, refreshed_at)"
        )
        yield client


@pytest.mark.integration
@pytest.mark.asyncio
async def test_classification_full_pipeline(ch_client):
    """Insert mock klines → refresh_tiers → verify table + cache + health."""
    one_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(tzinfo=None)
    rows = [
        (
            "binance",
            symbol,
            "1m",
            one_hour_ago,
            100.0,
            100.0,
            100.0,
            100.0,
            float(total),
            float(total),
        )
        for symbol, total in SYMBOL_VOLUMES_24H_USDT.items()
    ]
    await ch_client.execute(
        "INSERT INTO ohlcv_futures "
        "(exchange, symbol, timeframe, timestamp, open, high, low, close, volume, turnover) VALUES",
        *rows,
    )

    classifier = Classifier(ch_client=adapt_aiochclient(ch_client), refresh_hours=12, exchange="binance")
    stats = await classifier.refresh_tiers()

    assert stats.symbol_count == len(SYMBOL_VOLUMES_24H_USDT)

    persisted_dict: dict[str, str] = {}
    async for r in ch_client.iterate("SELECT symbol, tier FROM symbol_tiers ORDER BY symbol"):
        persisted_dict[r["symbol"]] = r["tier"]
    assert len(persisted_dict) == len(SYMBOL_VOLUMES_24H_USDT)

    for symbol in SYMBOL_VOLUMES_24H_USDT:
        assert classifier.get_tier(symbol) is not None
        assert classifier.get_tier(symbol) in {"mega", "large", "mid", "small"}

    counts = stats.count_per_tier()
    assert counts["mega"] >= 1
    assert counts["small"] >= 1

    h = classifier.health()
    assert h["status"] == "ok"
    assert h["last_refresh"] is not None
    assert h["next_refresh"] is not None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tiers_endpoint_returns_full_mapping(ch_client):
    """7.2: GET /api/v1/classification/tiers returns the populated cache."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.classification.api import router, set_classifier

    one_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(tzinfo=None)
    rows = [
        ("binance", sym, "1m", one_hour_ago, 100.0, 100.0, 100.0, 100.0, float(v), float(v))
        for sym, v in SYMBOL_VOLUMES_24H_USDT.items()
    ]
    await ch_client.execute(
        "INSERT INTO ohlcv_futures "
        "(exchange, symbol, timeframe, timestamp, open, high, low, close, volume, turnover) VALUES",
        *rows,
    )

    classifier = Classifier(ch_client=adapt_aiochclient(ch_client), refresh_hours=12, exchange="binance")
    await classifier.refresh_tiers()
    set_classifier(classifier)

    app = FastAPI()
    app.include_router(router)

    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/classification/tiers")
            assert response.status_code == 200
            body = response.json()
            assert len(body) == len(SYMBOL_VOLUMES_24H_USDT)
            for symbol in SYMBOL_VOLUMES_24H_USDT:
                assert symbol in body
                assert body[symbol] in {"mega", "large", "mid", "small"}
    finally:
        set_classifier(None)
