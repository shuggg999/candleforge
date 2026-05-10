"""Unit tests for src.detection.detector.Detector."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.detection.detector import Detector, LoggingPublisher
from src.detection.models import AlertEvent, Level


def _make_detector(rows, classifier_tiers, publisher=None, min_samples=1200):
    """Helper: wire up Detector with mocked ch_client + classifier + publisher."""
    mock_ch = AsyncMock()
    mock_ch.fetchall.return_value = rows

    mock_classifier = MagicMock()
    mock_classifier.get_tier.side_effect = lambda s: classifier_tiers.get(s)

    pub = publisher or AsyncMock()
    detector = Detector(
        ch_client=mock_ch,
        classifier=mock_classifier,
        publisher=pub,
        min_samples=min_samples,
    )
    return detector, mock_ch, pub


@pytest.mark.asyncio
async def test_ratio_computation():
    """ratio = current_avg / baseline_median per spec."""
    rows = [
        {"symbol": "BTC/USDT", "sample_count": 1440, "current_avg": 28_000_000.0, "baseline_median": 8_000_000.0},
    ]
    detector, _, _ = _make_detector(rows, {"BTC/USDT": "mega"})
    events = await detector.detect_once()
    assert len(events) == 1
    assert events[0].ratio == pytest.approx(3.5)
    assert events[0].symbol == "BTC/USDT"


@pytest.mark.asyncio
async def test_classify_by_tier_matrix():
    """4 tiers × 4 ratio bands = 16 cases mapped to expected levels."""
    # tier, ratio, expected_level
    cases = [
        ("mega", 2.0, Level.NORMAL),
        ("mega", 4.0, Level.WARN),
        ("mega", 7.0, Level.STRONG),
        ("mega", 12.0, Level.EXTREME),
        ("large", 4.0, Level.NORMAL),
        ("large", 7.0, Level.WARN),
        ("large", 15.0, Level.STRONG),
        ("large", 25.0, Level.EXTREME),
        ("mid", 9.0, Level.NORMAL),
        ("mid", 15.0, Level.WARN),
        ("mid", 30.0, Level.STRONG),
        ("mid", 60.0, Level.EXTREME),
        ("small", 19.0, Level.NORMAL),
        ("small", 30.0, Level.WARN),
        ("small", 70.0, Level.STRONG),
        ("small", 110.0, Level.EXTREME),
    ]

    rows = []
    classifier_tiers = {}
    for i, (tier, ratio, _) in enumerate(cases):
        sym = f"S{i:02d}/USDT"
        rows.append({
            "symbol": sym,
            "sample_count": 1440,
            "current_avg": ratio * 1000.0,
            "baseline_median": 1000.0,
        })
        classifier_tiers[sym] = tier

    detector, _, _ = _make_detector(rows, classifier_tiers)
    events = await detector.detect_once()
    events_by_symbol = {e.symbol: e.level for e in events}

    for i, (tier, ratio, expected) in enumerate(cases):
        sym = f"S{i:02d}/USDT"
        if expected == Level.NORMAL:
            assert sym not in events_by_symbol, f"{tier} ratio={ratio} should be NORMAL (no event)"
        else:
            assert events_by_symbol[sym] == expected, (
                f"{tier} ratio={ratio} expected {expected.name}, got {events_by_symbol.get(sym)}"
            )


@pytest.mark.asyncio
async def test_escalation_detection():
    """First entry / escalation emits; same-level / downgrade does not."""
    base_rows = [
        {"symbol": "X/USDT", "sample_count": 1440, "current_avg": 4000.0, "baseline_median": 1000.0},
    ]
    detector, mock_ch, _ = _make_detector(base_rows, {"X/USDT": "mega"})

    # Cycle 1: warn (4× exceeds mega.warn=3 but not mega.strong=5)
    events1 = await detector.detect_once()
    assert len(events1) == 1
    assert events1[0].level == Level.WARN
    assert events1[0].is_escalation is True
    assert events1[0].prev_level is None

    # Cycle 2: warn again (same level, no event)
    events2 = await detector.detect_once()
    assert len(events2) == 0

    # Cycle 3: extreme (escalate from warn → extreme)
    mock_ch.fetchall.return_value = [
        {"symbol": "X/USDT", "sample_count": 1440, "current_avg": 12000.0, "baseline_median": 1000.0},
    ]
    events3 = await detector.detect_once()
    assert len(events3) == 1
    assert events3[0].level == Level.EXTREME
    assert events3[0].prev_level == Level.WARN

    # Cycle 4: warn (downgrade — no event but state updated)
    mock_ch.fetchall.return_value = [
        {"symbol": "X/USDT", "sample_count": 1440, "current_avg": 4000.0, "baseline_median": 1000.0},
    ]
    events4 = await detector.detect_once()
    assert len(events4) == 0
    assert detector._last_levels["X/USDT"] == Level.WARN


@pytest.mark.asyncio
async def test_skip_low_samples():
    """Symbols with sample_count < min_samples are excluded entirely."""
    rows = [
        {"symbol": "ENOUGH/USDT", "sample_count": 1440, "current_avg": 4000.0, "baseline_median": 1000.0},
        {"symbol": "TOOFEW/USDT", "sample_count": 800, "current_avg": 4000.0, "baseline_median": 1000.0},
    ]
    detector, _, _ = _make_detector(
        rows, {"ENOUGH/USDT": "mega", "TOOFEW/USDT": "mega"}, min_samples=1200
    )
    events = await detector.detect_once()
    assert {e.symbol for e in events} == {"ENOUGH/USDT"}
    assert detector._symbols_skipped_low_data == 1


@pytest.mark.asyncio
async def test_skip_no_tier():
    """Symbols where classifier.get_tier returns None are skipped silently."""
    rows = [
        {"symbol": "WITH_TIER/USDT", "sample_count": 1440, "current_avg": 4000.0, "baseline_median": 1000.0},
        {"symbol": "NO_TIER/USDT", "sample_count": 1440, "current_avg": 4000.0, "baseline_median": 1000.0},
    ]
    detector, _, _ = _make_detector(rows, {"WITH_TIER/USDT": "mega"})
    events = await detector.detect_once()
    assert {e.symbol for e in events} == {"WITH_TIER/USDT"}
    assert "NO_TIER/USDT" not in detector._last_levels


@pytest.mark.asyncio
async def test_publisher_called_per_cycle_even_empty():
    """publisher.publish() is invoked once per cycle, even with empty event list."""
    rows = [
        {"symbol": "QUIET/USDT", "sample_count": 1440, "current_avg": 1000.0, "baseline_median": 1000.0},
    ]
    pub = AsyncMock()
    detector, _, _ = _make_detector(rows, {"QUIET/USDT": "mega"}, publisher=pub)
    events = await detector.detect_once()
    assert len(events) == 0
    pub.publish.assert_awaited_once()
    pub.publish.assert_awaited_with([])


@pytest.mark.asyncio
async def test_health_failed_before_first_run():
    detector, _, _ = _make_detector([], {})
    h = detector.health()
    assert h["status"] == "failed"
    assert h["last_run"] is None
    assert h["active_alerts"] == {"warn": 0, "strong": 0, "extreme": 0}


@pytest.mark.asyncio
async def test_health_ok_after_recent_run():
    rows = [
        {"symbol": "X/USDT", "sample_count": 1440, "current_avg": 4000.0, "baseline_median": 1000.0},
    ]
    detector, _, _ = _make_detector(rows, {"X/USDT": "mega"})
    await detector.detect_once()
    h = detector.health()
    assert h["status"] == "ok"
    assert h["last_run"] is not None
    assert h["last_run_duration_ms"] is not None
    assert h["active_alerts"]["warn"] == 1


@pytest.mark.asyncio
async def test_health_degraded_when_stale():
    rows = [
        {"symbol": "X/USDT", "sample_count": 1440, "current_avg": 4000.0, "baseline_median": 1000.0},
    ]
    detector, _, _ = _make_detector(rows, {"X/USDT": "mega"})
    await detector.detect_once()
    assert detector._last_run is not None
    detector._last_run = detector._last_run - timedelta(minutes=11)  # 5×2=10 min threshold

    h = detector.health()
    assert h["status"] == "degraded"


@pytest.mark.asyncio
async def test_logging_publisher_emits_per_event(caplog):
    """LoggingPublisher logs each AlertEvent at INFO level."""
    pub = LoggingPublisher()
    ev = AlertEvent(
        symbol="BTC/USDT",
        tier="mega",
        level=Level.WARN,
        ratio=3.5,
        current_avg=28_000_000.0,
        baseline_median=8_000_000.0,
        threshold_used=3.0,
        detected_at=datetime.now(timezone.utc),
        is_escalation=True,
        prev_level=None,
    )
    await pub.publish([ev])
    await pub.publish([])  # empty must not raise
