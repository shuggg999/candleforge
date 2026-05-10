"""Tier-based threshold tables for volume detection."""
from __future__ import annotations

from typing import Dict

ThresholdTable = Dict[str, Dict[str, float]]

DEFAULT_THRESHOLDS: ThresholdTable = {
    "mega":  {"warn": 3.0,  "strong": 5.0,  "extreme": 10.0},
    "large": {"warn": 5.0,  "strong": 10.0, "extreme": 20.0},
    "mid":   {"warn": 10.0, "strong": 20.0, "extreme": 50.0},
    "small": {"warn": 20.0, "strong": 50.0, "extreme": 100.0},
}


def parse_thresholds_env(spec: str) -> ThresholdTable:
    """Parse env-var form `tier:warn,strong,extreme;tier:warn,strong,extreme`.

    Empty/whitespace-only string returns an empty dict (no overrides).
    Malformed entries raise ValueError so misconfig is loud at startup.
    """
    if not spec or not spec.strip():
        return {}
    out: ThresholdTable = {}
    for part in spec.split(";"):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise ValueError(f"DETECTION_THRESHOLDS entry missing ':' — {part!r}")
        tier, vals = part.split(":", 1)
        triplet = vals.split(",")
        if len(triplet) != 3:
            raise ValueError(f"DETECTION_THRESHOLDS entry must have 3 values — {part!r}")
        out[tier.strip()] = {
            "warn": float(triplet[0]),
            "strong": float(triplet[1]),
            "extreme": float(triplet[2]),
        }
    return out


def merge_thresholds(default: ThresholdTable, override: ThresholdTable) -> ThresholdTable:
    """Per-tier replace: tiers in `override` fully replace those entries; others keep defaults."""
    out: ThresholdTable = {t: dict(v) for t, v in default.items()}
    for tier, vals in override.items():
        out[tier] = dict(vals)
    return out
