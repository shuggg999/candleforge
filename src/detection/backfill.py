"""Cold-start backfill: fetch missing 24h baseline klines from Binance REST."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Iterable

from loguru import logger


async def ensure_baseline_data(
    ch_client,
    binance_rest_client,
    symbols: Iterable[str],
    baseline_hours: int = 24,
    rps: float = 10.0,
    exchange: str = "binance",
) -> dict:
    """Verify ClickHouse has `baseline_hours` of 1m klines per symbol; backfill via REST otherwise.

    Returns a stats dict:
      {symbols_checked, symbols_backfilled, klines_inserted, duration_ms}

    The throttling is a sequential token bucket (single-flight): consecutive
    `binance_rest_client.fetch_klines` calls are spaced ≥ 1/rps seconds apart.
    Failures on individual symbols are logged and counted; the function never
    raises but reports `errors` count in the result.
    """
    started = time.monotonic()
    symbols_list = list(symbols)
    expected_rows = baseline_hours * 60  # 1m klines

    # Step 1: query existing row counts per symbol
    counts_query = (
        "SELECT symbol, count() AS row_count "
        "FROM ohlcv_futures "
        f"WHERE exchange = '{exchange}' AND timeframe = '1m' "
        f"AND timestamp >= now() - INTERVAL {baseline_hours} HOUR "
        "GROUP BY symbol"
    )
    rows = await ch_client.fetchall(counts_query)
    counts: dict[str, int] = {r["symbol"]: int(r["row_count"]) for r in rows}

    missing = [s for s in symbols_list if counts.get(s, 0) < expected_rows]

    stats = {
        "symbols_checked": len(symbols_list),
        "symbols_backfilled": 0,
        "klines_inserted": 0,
        "errors": 0,
    }

    if not missing:
        stats["duration_ms"] = int((time.monotonic() - started) * 1000)
        return stats

    min_interval = 1.0 / rps
    last_call = 0.0

    for symbol in missing:
        # Throttle: enforce min interval between consecutive Binance calls
        elapsed = time.monotonic() - last_call
        if elapsed < min_interval:
            await asyncio.sleep(min_interval - elapsed)
        last_call = time.monotonic()

        try:
            klines = await binance_rest_client.fetch_klines(symbol, interval="1m", limit=1500)
        except Exception as exc:
            logger.warning("backfill fetch failed for {}: {}", symbol, exc)
            stats["errors"] += 1
            continue

        if not klines:
            continue

        insert_rows = []
        for k in klines:
            # Binance kline format: [open_time(ms), open, high, low, close, volume, close_time, quote_volume, ...]
            ts_ms = int(k[0])
            insert_rows.append(
                (
                    exchange,
                    symbol,
                    "1m",
                    datetime.utcfromtimestamp(ts_ms / 1000),
                    float(k[1]),
                    float(k[2]),
                    float(k[3]),
                    float(k[4]),
                    float(k[5]),
                    float(k[7]),
                )
            )

        try:
            await ch_client.execute(
                "INSERT INTO ohlcv_futures "
                "(exchange, symbol, timeframe, timestamp, open, high, low, close, volume, turnover) VALUES",
                *insert_rows,
            )
            stats["symbols_backfilled"] += 1
            stats["klines_inserted"] += len(insert_rows)
        except Exception as exc:
            logger.warning("backfill insert failed for {}: {}", symbol, exc)
            stats["errors"] += 1

    stats["duration_ms"] = int((time.monotonic() - started) * 1000)
    logger.info("backfill complete: {}", stats)
    return stats
