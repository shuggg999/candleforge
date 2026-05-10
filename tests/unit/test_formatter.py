"""Unit tests for src.alerts.formatter.format_message."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.alerts.formatter import format_message
from src.detection.models import AlertEvent, Level


def _ev(
    level: Level = Level.WARN,
    prev_level=None,
    ratio: float = 3.4,
    threshold_used: float = 3.0,
    current_avg: float = 28_500_000,
    baseline_median: float = 8_400_000,
    tier: str = "mega",
    symbol: str = "BTC/USDT",
):
    return AlertEvent(
        symbol=symbol,
        tier=tier,
        level=level,
        ratio=ratio,
        current_avg=current_avg,
        baseline_median=baseline_median,
        threshold_used=threshold_used,
        detected_at=datetime(2026, 5, 10, 15, 0, 0, tzinfo=timezone.utc),
        is_escalation=prev_level is not None or True,
        prev_level=prev_level,
    )


def test_warn_message_format():
    msg = format_message(_ev())
    assert "🟡" in msg
    assert "WARN" in msg
    assert "BTC/USDT" in msg
    assert "mega (超大盘)" in msg
    assert "3.4" in msg
    assert "28.5M USDT" in msg
    assert "8.4M USDT" in msg


def test_strong_message_includes_orange_emoji():
    msg = format_message(_ev(level=Level.STRONG, ratio=6.0, threshold_used=5.0))
    assert "🟠" in msg
    assert "STRONG" in msg


def test_extreme_message_includes_red_emoji():
    msg = format_message(_ev(level=Level.EXTREME, ratio=12.0, threshold_used=10.0))
    assert "🔴" in msg
    assert "EXTREME" in msg


def test_escalation_marker_present():
    msg = format_message(_ev(level=Level.EXTREME, prev_level=Level.STRONG))
    assert "⬆" in msg
    assert "STRONG" in msg


def test_first_entry_no_escalation_marker():
    msg = format_message(_ev(level=Level.WARN, prev_level=None))
    assert "⬆" not in msg


def test_volume_formatted_with_si_suffix():
    msg = format_message(_ev(current_avg=28_500_000, baseline_median=8_400_000))
    assert "28.5M USDT" in msg
    assert "8.4M USDT" in msg


def test_volume_formatted_with_billions():
    msg = format_message(_ev(current_avg=1_500_000_000, baseline_median=500_000_000))
    assert "1.5B USDT" in msg
    assert "500.0M USDT" in msg
