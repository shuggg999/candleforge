"""Render AlertEvent to a Markdown-formatted Telegram message."""
from __future__ import annotations

from src.detection.models import AlertEvent, Level


_LEVEL_EMOJI = {
    Level.WARN: "🟡",
    Level.STRONG: "🟠",
    Level.EXTREME: "🔴",
}

_TIER_HUMAN = {
    "mega": "mega (超大盘)",
    "large": "large (大盘)",
    "mid": "mid (中盘)",
    "small": "small (小盘)",
}


def _format_volume_si(v: float) -> str:
    if v >= 1_000_000_000:
        return f"{v / 1_000_000_000:.1f}B USDT"
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M USDT"
    if v >= 1_000:
        return f"{v / 1_000:.1f}K USDT"
    return f"{v:.0f} USDT"


def format_message(event: AlertEvent) -> str:
    """Render an AlertEvent to a Markdown-formatted Telegram message."""
    level_name = event.level.name  # WARN/STRONG/EXTREME
    emoji = _LEVEL_EMOJI.get(event.level, "⚪")
    tier_human = _TIER_HUMAN.get(event.tier, event.tier)

    lines = [
        f"{emoji} *VOLUME {level_name}*",
        f"*{event.symbol}*  ·  {tier_human}",
        f"ratio: *{event.ratio:.1f}×*  ·  threshold: {level_name.lower()}={event.threshold_used:.0f}×",
        f"current: {_format_volume_si(event.current_avg)}  vs baseline: {_format_volume_si(event.baseline_median)}",
    ]

    if event.prev_level is not None:
        lines.append(f"⬆ from {event.prev_level.name}")

    lines.append(f"detected: {event.detected_at.isoformat()}")
    return "\n".join(lines)
