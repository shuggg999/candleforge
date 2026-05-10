"""FastAPI router for alerts module: test send / recent / stats."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from .notifier import Notifier

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])

_notifier: Optional[Notifier] = None


def set_notifier(n: Optional[Notifier]) -> None:
    global _notifier
    _notifier = n


def get_notifier() -> Notifier:
    if _notifier is None:
        raise HTTPException(status_code=503, detail="Notifier not initialized")
    return _notifier


@router.post("/test")
async def send_test_message() -> dict:
    """Send a hard-coded test message to the configured chat."""
    n = get_notifier()
    return await n.send_test_message()


@router.get("/recent")
async def recent_alerts(
    limit: int = Query(20, ge=1, le=200),
    min_level: Optional[str] = Query(None, regex="^(warn|strong|extreme)$"),
) -> dict:
    """Return recent rows from volume_alerts (latest first)."""
    n = get_notifier()
    where_extra = ""
    if min_level:
        order = ["warn", "strong", "extreme"]
        keep = order[order.index(min_level):]
        keep_csv = ", ".join(f"'{k}'" for k in keep)
        where_extra = f"AND level IN ({keep_csv}) "
    query = (
        "SELECT detected_at, exchange, symbol, tier, level, prev_level, ratio, "
        "curr_5min_avg, baseline_median, threshold_used, telegram_status, "
        "telegram_error, sent_at "
        "FROM volume_alerts "
        f"WHERE 1=1 {where_extra}"
        f"ORDER BY detected_at DESC LIMIT {limit}"
    )
    try:
        rows = await n._ch.fetchall(query)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"query failed: {exc}")
    return {"alerts": [_serialize_row(r) for r in rows]}


@router.get("/stats")
async def alert_stats() -> dict:
    """Aggregate stats over the last 24h."""
    n = get_notifier()
    return await n.fetch_stats_24h()


def _serialize_row(r) -> dict:
    out = dict(r)
    # Coerce datetime fields to ISO strings (aiochclient returns datetime objects)
    for k in ("detected_at", "sent_at"):
        if out.get(k) is not None and hasattr(out[k], "isoformat"):
            out[k] = out[k].isoformat()
    return out
