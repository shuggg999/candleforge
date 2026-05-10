"""Unit tests for src.classification.classifier.Classifier."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from src.classification.classifier import Classifier


@pytest.fixture
def classifier() -> Classifier:
    return Classifier(ch_client=None, refresh_hours=12)


def test_assigns_tier_per_quantile_cut(classifier: Classifier) -> None:
    """Spec: every symbol gets exactly one tier per P25/P50/P75 cuts.

    Construct 16 symbols with volumes evenly distributed across 4 bands
    so numpy.percentile (linear interpolation) puts P25 between band 1/2,
    P50 between band 2/3, P75 between band 3/4 — yielding 4 symbols per tier.
    """
    raw = [
        1_000_000, 2_000_000, 3_000_000, 4_000_000,           # small (≤P25)
        10_000_000, 20_000_000, 30_000_000, 40_000_000,       # mid   (P25..P50]
        100_000_000, 200_000_000, 300_000_000, 400_000_000,   # large (P50..P75]
        1_000_000_000, 2_000_000_000, 3_000_000_000, 4_000_000_000,  # mega (>P75)
    ]
    volumes = {f"S{i:02d}/USDT": Decimal(v) for i, v in enumerate(raw)}

    stats = classifier._compute_tiers(volumes)

    assert stats.symbol_count == 16
    counts = stats.count_per_tier()
    assert counts == {"mega": 4, "large": 4, "mid": 4, "small": 4}, (
        f"expected even 4/4/4/4, got {counts}"
    )


def test_zero_volume_symbol_excluded(classifier: Classifier) -> None:
    """Zero-volume symbols neither appear in tiers nor pollute P quantile calc."""
    volumes = {
        "ACTIVE/USDT": Decimal(100_000_000),
        "DELISTED/USDT": Decimal(0),
        "BIG/USDT": Decimal(1_000_000_000),
    }

    stats = classifier._compute_tiers(volumes)

    assert "DELISTED/USDT" not in stats.tiers
    assert stats.symbol_count == 2
    assert "ACTIVE/USDT" in stats.tiers
    assert "BIG/USDT" in stats.tiers


def test_p_calculation_n_equals_4(classifier: Classifier) -> None:
    """n=4 boundary: numpy.percentile linear interpolation gives expected values."""
    volumes = {
        "A/USDT": Decimal(10_000_000),
        "B/USDT": Decimal(20_000_000),
        "C/USDT": Decimal(30_000_000),
        "D/USDT": Decimal(40_000_000),
    }

    stats = classifier._compute_tiers(volumes)

    assert stats.symbol_count == 4
    assert float(stats.p25) == pytest.approx(17_500_000.0)
    assert float(stats.p50) == pytest.approx(25_000_000.0)
    assert float(stats.p75) == pytest.approx(32_500_000.0)
    assert stats.tiers["A/USDT"] == "small"
    assert stats.tiers["B/USDT"] == "mid"
    assert stats.tiers["C/USDT"] == "large"
    assert stats.tiers["D/USDT"] == "mega"


def test_p_calculation_n_equals_0(classifier: Classifier) -> None:
    """n=0 boundary: empty / all-zero input returns zero stats without raising."""
    stats_empty = classifier._compute_tiers({})
    assert stats_empty.symbol_count == 0
    assert stats_empty.tiers == {}
    assert stats_empty.p25 == Decimal(0)
    assert stats_empty.p50 == Decimal(0)
    assert stats_empty.p75 == Decimal(0)

    stats_all_zero = classifier._compute_tiers(
        {"X/USDT": Decimal(0), "Y/USDT": Decimal(0)}
    )
    assert stats_all_zero.symbol_count == 0
    assert stats_all_zero.tiers == {}


def test_p_calculation_all_equal(classifier: Classifier) -> None:
    """All-equal volumes: P25=P50=P75=that value; per spec '≤P25 → small' all symbols → small."""
    volumes = {f"S{i}/USDT": Decimal(100_000_000) for i in range(4)}

    stats = classifier._compute_tiers(volumes)

    assert float(stats.p25) == pytest.approx(100_000_000.0)
    assert float(stats.p50) == pytest.approx(100_000_000.0)
    assert float(stats.p75) == pytest.approx(100_000_000.0)
    assert stats.count_per_tier() == {"mega": 0, "large": 0, "mid": 0, "small": 4}


@pytest.mark.asyncio
async def test_refresh_queries_24h_window() -> None:
    """refresh_tiers SQL targets 24h window of 1m klines from ohlcv_futures."""
    mock_ch = AsyncMock()
    mock_ch.fetchall.return_value = [
        {"symbol": "BTC/USDT", "quote_volume_24h": Decimal("1000000000")},
        {"symbol": "ETH/USDT", "quote_volume_24h": Decimal("500000000")},
    ]
    classifier = Classifier(ch_client=mock_ch, refresh_hours=12)

    await classifier.refresh_tiers()

    sql = mock_ch.fetchall.await_args.args[0]
    assert "INTERVAL 24 HOUR" in sql
    assert "timeframe = '1m'" in sql
    assert "ohlcv_futures" in sql
    assert "binance" in sql.lower()


@pytest.mark.asyncio
async def test_refresh_populates_cache_atomically() -> None:
    """After successful refresh, tier_cache contains every active symbol with its tier."""
    mock_ch = AsyncMock()
    mock_ch.fetchall.return_value = [
        {"symbol": "BTC/USDT", "quote_volume_24h": Decimal("4000000000")},
        {"symbol": "ETH/USDT", "quote_volume_24h": Decimal("400000000")},
        {"symbol": "DOGE/USDT", "quote_volume_24h": Decimal("40000000")},
        {"symbol": "RANDOM/USDT", "quote_volume_24h": Decimal("4000000")},
    ]
    classifier = Classifier(ch_client=mock_ch, refresh_hours=12)

    await classifier.refresh_tiers()

    assert classifier.get_tier("BTC/USDT") is not None
    assert classifier.get_tier("ETH/USDT") is not None
    assert classifier.get_tier("DOGE/USDT") is not None
    assert classifier.get_tier("RANDOM/USDT") is not None
    assert classifier.get_tier("NOTLISTED/USDT") is None


@pytest.mark.asyncio
async def test_refresh_failure_preserves_cache() -> None:
    """Second refresh raising mid-flight MUST NOT empty or corrupt prior cache."""
    mock_ch = AsyncMock()
    # First call succeeds with 4 symbols
    mock_ch.fetchall.return_value = [
        {"symbol": "BTC/USDT", "quote_volume_24h": Decimal("4000000000")},
        {"symbol": "ETH/USDT", "quote_volume_24h": Decimal("400000000")},
        {"symbol": "DOGE/USDT", "quote_volume_24h": Decimal("40000000")},
        {"symbol": "RANDOM/USDT", "quote_volume_24h": Decimal("4000000")},
    ]
    classifier = Classifier(ch_client=mock_ch, refresh_hours=12)
    await classifier.refresh_tiers()

    snapshot = dict(classifier._tier_cache)
    assert len(snapshot) == 4

    # Second call raises ConnectionError
    mock_ch.fetchall.side_effect = ConnectionError("clickhouse down")
    with pytest.raises(ConnectionError):
        await classifier.refresh_tiers()

    assert classifier._tier_cache == snapshot, "cache must remain intact after failure"
    assert classifier._last_error is not None


@pytest.mark.asyncio
async def test_health_reports_failed_before_first_refresh() -> None:
    """No successful refresh yet → status='failed', empty symbol_count, no thresholds."""
    classifier = Classifier(ch_client=AsyncMock(), refresh_hours=12)
    h = classifier.health()

    assert h["status"] == "failed"
    assert h["last_refresh"] is None
    assert h["symbol_count"] == {"mega": 0, "large": 0, "mid": 0, "small": 0}


@pytest.mark.asyncio
async def test_health_reports_ok_after_fresh_refresh() -> None:
    """Successful refresh within interval → status='ok' with fresh thresholds."""
    mock_ch = AsyncMock()
    mock_ch.fetchall.return_value = [
        {"symbol": "BTC/USDT", "quote_volume_24h": Decimal("4000000000")},
        {"symbol": "ETH/USDT", "quote_volume_24h": Decimal("400000000")},
        {"symbol": "DOGE/USDT", "quote_volume_24h": Decimal("40000000")},
        {"symbol": "RANDOM/USDT", "quote_volume_24h": Decimal("4000000")},
    ]
    classifier = Classifier(ch_client=mock_ch, refresh_hours=12)
    await classifier.refresh_tiers()

    h = classifier.health()
    assert h["status"] == "ok"
    assert h["last_refresh"] is not None
    assert h["next_refresh"] is not None
    assert h["symbol_count"]["mega"] + h["symbol_count"]["large"] + h["symbol_count"]["mid"] + h["symbol_count"]["small"] == 4
    assert h["p25"] is not None and h["p50"] is not None and h["p75"] is not None


@pytest.mark.asyncio
async def test_health_reports_degraded_when_stale() -> None:
    """last_refresh older than 2 × CLASSIFICATION_REFRESH_HOURS → status='degraded'."""
    mock_ch = AsyncMock()
    mock_ch.fetchall.return_value = [
        {"symbol": "BTC/USDT", "quote_volume_24h": Decimal("4000000000")},
        {"symbol": "ETH/USDT", "quote_volume_24h": Decimal("400000000")},
    ]
    classifier = Classifier(ch_client=mock_ch, refresh_hours=12)
    await classifier.refresh_tiers()

    # Manually rewind last_refresh to 25 hours ago (>2×12h)
    assert classifier._last_refresh is not None
    classifier._last_refresh = classifier._last_refresh - timedelta(hours=25)

    h = classifier.health()
    assert h["status"] == "degraded"
