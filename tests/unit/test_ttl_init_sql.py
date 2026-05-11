"""Tests for config/clickhouse/init.sql TTL expressions.

Spec (baseline-infrastructure / Per-Timeframe TTL Configuration):
- Each timeframe MUST have an independent `WHERE timeframe = '<tf>'` clause.
- `WHERE timeframe IN (...)`合并多个 timeframe is forbidden.
- TTL day counts MUST match .env.example: 1m=7, 5m=30, 15m=90, 1h=365, 4h=365, 1d=1825.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
INIT_SQL_PATH = REPO_ROOT / "config" / "clickhouse" / "init.sql"


@pytest.fixture(scope="module")
def init_sql_text() -> str:
    assert INIT_SQL_PATH.exists(), f"init.sql not found at {INIT_SQL_PATH}"
    return INIT_SQL_PATH.read_text(encoding="utf-8")


def _extract_ohlcv_ttl_block(sql_text: str) -> str:
    """Return the TTL block of the `ohlcv_futures` CREATE TABLE statement.

    Slices from the first 'TTL ' after the `CREATE TABLE ... ohlcv_futures` keyword,
    up to the matching 'SETTINGS' keyword on subsequent lines.
    """
    create_idx = sql_text.find("CREATE TABLE IF NOT EXISTS ohlcv_futures")
    assert create_idx != -1, "ohlcv_futures CREATE TABLE not found"
    settings_idx = sql_text.find("SETTINGS index_granularity", create_idx)
    assert settings_idx != -1, "SETTINGS clause not found after ohlcv_futures"
    block = sql_text[create_idx:settings_idx]
    # extract from 'TTL ' onwards
    ttl_match = re.search(r"\bTTL\b", block)
    assert ttl_match, "TTL keyword not found in ohlcv_futures block"
    return block[ttl_match.start():]


def test_init_sql_has_independent_ttl_per_timeframe(init_sql_text: str) -> None:
    """Spec: TTL 子句必须为每个 timeframe 单独写 `WHERE timeframe = '<tf>'`，
    禁止 `WHERE timeframe IN (...)` 合并多个 timeframe。
    """
    ttl_block = _extract_ohlcv_ttl_block(init_sql_text)

    # 禁止 IN (...) 形式
    assert re.search(
        r"WHERE\s+timeframe\s+IN\s*\(",
        ttl_block,
        flags=re.IGNORECASE,
    ) is None, (
        f"ohlcv_futures TTL block must NOT use `WHERE timeframe IN (...)`. "
        f"Got:\n{ttl_block}"
    )

    # 每个 timeframe 必须有自己的 `WHERE timeframe = '<tf>'`
    required_tfs = ("1m", "5m", "15m", "1h", "4h", "1d")
    for tf in required_tfs:
        pattern = rf"WHERE\s+timeframe\s*=\s*'{re.escape(tf)}'"
        assert re.search(pattern, ttl_block, flags=re.IGNORECASE), (
            f"ohlcv_futures TTL block missing independent clause for timeframe={tf!r}. "
            f"Got:\n{ttl_block}"
        )


def _parse_ttl_day_count_for(ttl_block: str, timeframe: str) -> int:
    """Return TTL day count for a single timeframe, parsed from init.sql syntax.

    Supports both `INTERVAL <N> DAY` and `toIntervalDay(<N>)` forms.
    """
    interval_patterns = [
        rf"timestamp\s*\+\s*INTERVAL\s+(\d+)\s+DAY\s+WHERE\s+timeframe\s*=\s*'{re.escape(timeframe)}'",
        rf"timestamp\s*\+\s*toIntervalDay\(\s*(\d+)\s*\)\s+WHERE\s+timeframe\s*=\s*'{re.escape(timeframe)}'",
    ]
    for pat in interval_patterns:
        m = re.search(pat, ttl_block, flags=re.IGNORECASE)
        if m:
            return int(m.group(1))
    raise AssertionError(
        f"No TTL day count found for timeframe={timeframe!r} in:\n{ttl_block}"
    )


@pytest.mark.parametrize(
    "timeframe,expected_days",
    [
        ("1m", 7),
        ("5m", 30),
        ("15m", 90),
        ("1h", 365),
        ("4h", 365),
        ("1d", 1825),
    ],
)
def test_init_sql_ttl_days_per_timeframe(
    init_sql_text: str, timeframe: str, expected_days: int
) -> None:
    """Spec: 每个 timeframe 的 TTL 天数与 .env.example 声明对齐。

    特别地：1h MUST be 365 天 (bug fix — 历史值是 90 天)。
    """
    ttl_block = _extract_ohlcv_ttl_block(init_sql_text)
    actual = _parse_ttl_day_count_for(ttl_block, timeframe)
    assert actual == expected_days, (
        f"TTL for timeframe={timeframe!r} must be {expected_days} days, "
        f"got {actual}. Block:\n{ttl_block}"
    )


def test_init_sql_1h_ttl_is_365_days(init_sql_text: str) -> None:
    """Explicit regression guard for the 2026-05-12 bug: 1h was 90 days.

    Kept separately from parametrized test so it appears in failure summaries
    by name — operators grepping for "1h_ttl_is_365" find it immediately.
    """
    ttl_block = _extract_ohlcv_ttl_block(init_sql_text)
    days = _parse_ttl_day_count_for(ttl_block, "1h")
    assert days == 365, (
        f"1h TTL must be 365 days (per .env.example TTL_1H=365). Got {days}. "
        f"This is the regression that fix-ttl-and-health-probe addresses."
    )
