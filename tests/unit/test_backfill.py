"""Unit tests for src.detection.backfill.ensure_baseline_data."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest

from src.detection.backfill import ensure_baseline_data


def _mock_binance_kline(symbol: str, n: int = 1440) -> list[list]:
    """Generate mock Binance /fapi/v1/klines response: list of 12-element arrays."""
    base_ts = 1_700_000_000_000  # ms
    return [
        [
            base_ts + i * 60_000,           # open_time
            "100.0",                         # open
            "100.0",                         # high
            "100.0",                         # low
            "100.0",                         # close
            "100.0",                         # volume (contracts)
            base_ts + i * 60_000 + 59_999,  # close_time
            "10000.0",                       # quote_volume (USDT turnover)
            10,                              # trades
            "50.0",                          # taker_buy_base
            "5000.0",                        # taker_buy_quote
            "0",                             # ignore
        ]
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_skips_if_data_complete():
    """Symbols with ≥ baseline_hours×60 rows in past window → skip Binance call."""
    mock_ch = AsyncMock()
    mock_ch.fetchall.return_value = [
        {"symbol": "BTC/USDT", "row_count": 1440},
        {"symbol": "ETH/USDT", "row_count": 1440},
    ]
    mock_ch.execute = AsyncMock()

    mock_binance = AsyncMock()
    mock_binance.fetch_klines = AsyncMock(return_value=[])

    result = await ensure_baseline_data(
        ch_client=mock_ch,
        binance_rest_client=mock_binance,
        symbols=["BTC/USDT", "ETH/USDT"],
        baseline_hours=24,
        rps=10.0,
    )

    mock_binance.fetch_klines.assert_not_called()
    assert result["symbols_checked"] == 2
    assert result["symbols_backfilled"] == 0


@pytest.mark.asyncio
async def test_fetches_missing_symbols():
    """Symbols below threshold trigger Binance REST fetch + ClickHouse insert."""
    mock_ch = AsyncMock()
    mock_ch.fetchall.return_value = [
        {"symbol": "BTC/USDT", "row_count": 1440},
        {"symbol": "NEW/USDT", "row_count": 30},
    ]
    mock_ch.execute = AsyncMock()

    mock_binance = AsyncMock()
    mock_binance.fetch_klines = AsyncMock(return_value=_mock_binance_kline("NEW/USDT", 1440))

    result = await ensure_baseline_data(
        ch_client=mock_ch,
        binance_rest_client=mock_binance,
        symbols=["BTC/USDT", "NEW/USDT"],
        baseline_hours=24,
        rps=100.0,
    )

    mock_binance.fetch_klines.assert_called_once()
    call_kwargs = mock_binance.fetch_klines.call_args.kwargs
    call_args = mock_binance.fetch_klines.call_args.args
    sym_arg = call_args[0] if call_args else call_kwargs.get("symbol")
    assert sym_arg == "NEW/USDT"
    assert result["symbols_backfilled"] == 1
    assert result["klines_inserted"] >= 1440
    mock_ch.execute.assert_called()


@pytest.mark.asyncio
async def test_respects_rate_limit():
    """Sequential calls obey the configured RPS (10 req/sec → ≥0.1s between calls)."""
    mock_ch = AsyncMock()
    mock_ch.fetchall.return_value = [
        {"symbol": f"S{i:02d}/USDT", "row_count": 0} for i in range(5)
    ]
    mock_ch.execute = AsyncMock()

    call_times: list[float] = []

    async def _record_time(symbol, **kwargs):
        call_times.append(time.monotonic())
        return _mock_binance_kline(symbol, 1440)

    mock_binance = AsyncMock()
    mock_binance.fetch_klines = AsyncMock(side_effect=_record_time)

    await ensure_baseline_data(
        ch_client=mock_ch,
        binance_rest_client=mock_binance,
        symbols=[f"S{i:02d}/USDT" for i in range(5)],
        baseline_hours=24,
        rps=10.0,
    )

    assert len(call_times) == 5
    intervals = [call_times[i + 1] - call_times[i] for i in range(len(call_times) - 1)]
    for delta in intervals:
        # 10 req/sec → expect ≥ 0.09s between consecutive calls (10% slack)
        assert delta >= 0.09, f"rate-limit violated: only {delta:.4f}s between calls"
