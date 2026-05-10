"""FastAPI router for detection module: manual trigger / state inspect / thresholds."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException

from .detector import Detector
from .models import AlertEvent, Level

router = APIRouter(prefix="/api/v1/detect", tags=["detection"])

_detector: Optional[Detector] = None


def set_detector(d: Optional[Detector]) -> None:
    global _detector
    _detector = d


def get_detector() -> Detector:
    if _detector is None:
        raise HTTPException(status_code=503, detail="Detector not initialized")
    return _detector


def _event_to_dict(ev: AlertEvent) -> dict:
    return {
        "symbol": ev.symbol,
        "tier": ev.tier,
        "level": ev.level.name,
        "ratio": ev.ratio,
        "current_avg": ev.current_avg,
        "baseline_median": ev.baseline_median,
        "threshold_used": ev.threshold_used,
        "detected_at": ev.detected_at.isoformat(),
        "is_escalation": ev.is_escalation,
        "prev_level": ev.prev_level.name if ev.prev_level else None,
    }


@router.post("/run")
async def run_detection_once() -> dict:
    """Manually trigger one detection cycle (for debugging / ad-hoc inspection)."""
    detector = get_detector()
    events = await detector.detect_once()
    return {"events": [_event_to_dict(e) for e in events]}


@router.get("/state")
async def get_state() -> dict:
    """Return last_levels dict + per-level active counts."""
    detector = get_detector()
    last = detector.last_levels
    counts = {"normal": 0, "warn": 0, "strong": 0, "extreme": 0}
    for lvl in last.values():
        counts[lvl.name.lower()] += 1
    return {
        "last_levels": {sym: lvl.name for sym, lvl in last.items()},
        "active_by_level": counts,
    }


@router.get("/thresholds")
async def get_thresholds() -> dict:
    """Return the currently effective threshold table (after env override)."""
    detector = get_detector()
    return detector.thresholds
