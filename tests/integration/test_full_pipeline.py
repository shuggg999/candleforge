"""End-to-end pipeline: classifier → detector → notifier → Telegram fake.

Spans all three changes (classification + detection + alerts) on a real
ClickHouse via testcontainers + a httpx.MockTransport-based Telegram fake.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import aiohttp
import httpx
import pytest
import pytest_asyncio
from aiochclient import ChClient
from testcontainers.clickhouse import ClickHouseContainer

from src.alerts import Notifier, TelegramClient
from src.classification.classifier import Classifier, adapt_aiochclient
from src.detection import Detector


SYMBOL_VOLUMES = {
    # symbol: (baseline_per_minute, current_per_minute)
    "BTC/USDT":  (1000.0, 4000.0),    # ratio 4.0 → mega.warn
    "ETH/USDT":  (500.0, 500.0),      # stable
    "BNB/USDT":  (200.0, 200.0),
    "SOL/USDT":  (100.0, 100.0),
    "DOGE/USDT": (50.0, 50.0),
    "ADA/USDT":  (20.0, 20.0),
    "XRP/USDT":  (10.0, 10.0),
    "DOT/USDT":  (5.0, 5.0),
}


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

        for tbl in ("ohlcv_futures", "symbol_tiers", "volume_alerts"):
            await client.execute(f"DROP TABLE IF EXISTS {tbl}")

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
        await client.execute(
            "CREATE TABLE volume_alerts ("
            "detected_at DateTime64(3), exchange LowCardinality(String), "
            "symbol String, tier LowCardinality(String), "
            "level LowCardinality(String), prev_level LowCardinality(String), "
            "ratio Float64, curr_5min_avg Float64, baseline_median Float64, "
            "threshold_used Float64, "
            "telegram_status LowCardinality(String) DEFAULT 'pending', "
            "telegram_error String DEFAULT '', "
            "sent_at Nullable(DateTime64(3))"
            ") ENGINE = MergeTree() "
            "ORDER BY (detected_at, exchange, symbol)"
        )
        yield client


@pytest.fixture
def telegram_fake():
    """httpx.MockTransport that records every sendMessage request body."""
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "/sendMessage" in str(request.url):
            try:
                body = json.loads(request.content.decode("utf-8"))
            except Exception:
                body = {}
            captured.append(body)
            return httpx.Response(200, json={"ok": True, "result": {"message_id": len(captured)}})
        return httpx.Response(404, json={"ok": False})

    return httpx.MockTransport(handler), captured


@pytest.mark.integration
@pytest.mark.asyncio
async def test_full_pipeline_warn_event_flows_end_to_end(ch_client, telegram_fake):
    """Insert klines → classifier → detector → notifier → fake Telegram receives message."""
    transport, captured = telegram_fake

    # Step 1: insert 24h of mock 1m klines
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    rows = []
    for symbol, (baseline, current) in SYMBOL_VOLUMES.items():
        for i in range(1440):
            ts = now - timedelta(minutes=1440 - i)
            v = current if i >= 1435 else baseline
            rows.append((
                "binance", symbol, "1m", ts,
                100.0, 100.0, 100.0, 100.0,
                v, v,
            ))

    BATCH = 200
    for i in range(0, len(rows), BATCH):
        await ch_client.execute(
            "INSERT INTO ohlcv_futures "
            "(exchange, symbol, timeframe, timestamp, open, high, low, close, volume, turnover) VALUES",
            *rows[i:i + BATCH],
        )

    # Step 2: classifier — assigns tiers based on 24h quote_volume
    classifier = Classifier(
        ch_client=adapt_aiochclient(ch_client),
        refresh_hours=12,
        exchange="binance",
    )
    await classifier.refresh_tiers()
    assert classifier.get_tier("BTC/USDT") is not None

    # Step 3: notifier with fake Telegram transport
    tg_client = TelegramClient(
        token="fake-token",
        parse_mode="Markdown",
        transport=transport,
        max_retries=2,
        base_backoff=0.01,
    )
    notifier = Notifier(
        ch_client=adapt_aiochclient(ch_client),
        telegram_client=tg_client,
        chat_id="test_chat",
        dry_run=False,
        exchange="binance",
    )

    # Step 4: detector wired with notifier as publisher
    detector = Detector(
        ch_client=adapt_aiochclient(ch_client),
        classifier=classifier,
        publisher=notifier,
        baseline_hours=24,
        current_minutes=5,
        min_samples=1200,
        interval_minutes=5,
        exchange="binance",
    )

    events = await detector.detect_once()

    # Step 5: verify pipeline
    btc_events = [e for e in events if e.symbol == "BTC/USDT"]
    assert len(btc_events) == 1, f"expected 1 BTC event, got {len(btc_events)}: {events}"
    assert btc_events[0].level.name == "WARN"

    # Telegram fake should have received exactly 1 message
    assert len(captured) == 1, f"expected 1 sendMessage, got {len(captured)}"
    assert captured[0]["chat_id"] == "test_chat"
    msg_text = captured[0]["text"]
    assert "BTC/USDT" in msg_text
    assert "WARN" in msg_text
    assert "🟡" in msg_text

    # volume_alerts table should have 1 row corresponding to the BTC event
    alerts_rows = []
    async for r in ch_client.iterate("SELECT symbol, level, telegram_status FROM volume_alerts"):
        alerts_rows.append(dict(r))
    btc_audits = [r for r in alerts_rows if r["symbol"] == "BTC/USDT"]
    assert len(btc_audits) == 1
    assert btc_audits[0]["level"] == "warn"
    # NOTE: telegram_status update via ALTER...UPDATE mutation is async in ClickHouse.
    # Status may still be 'pending' at read time — accept either pending or sent.
    assert btc_audits[0]["telegram_status"] in {"pending", "sent"}
