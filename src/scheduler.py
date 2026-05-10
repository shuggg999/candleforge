"""Cross-cutting APScheduler factory: registers detection + classification refresh jobs."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger


def next_n_min_boundary(now: datetime, n: int) -> datetime:
    """Return the next wall-clock multiple-of-`n` minute boundary strictly after `now`.

    Used to align detection cycles to 00, 05, 10, ... minute marks.
    """
    if n <= 0:
        raise ValueError("n must be positive")
    base = now.replace(second=0, microsecond=0)
    minute = (base.minute // n + 1) * n
    if minute >= 60:
        return (base + timedelta(hours=1)).replace(minute=0)
    return base.replace(minute=minute)


def build_scheduler(
    classifier,
    detector,
    detection_interval_minutes: int = 5,
    classification_refresh_hours: int = 12,
) -> AsyncIOScheduler:
    """Build an AsyncIOScheduler with detection_cycle + classification_refresh jobs.

    Caller must `start()` the returned scheduler at startup and `shutdown()` at teardown.
    """
    scheduler = AsyncIOScheduler(timezone=timezone.utc)

    async def _run_detection():
        try:
            events = await detector.detect_once()
            await detector._publisher.publish(events)
        except Exception as exc:
            logger.exception("detection cycle failed: {}", exc)

    async def _run_classification_refresh():
        try:
            await classifier.refresh_tiers()
        except Exception as exc:
            logger.warning("classification refresh failed (preserving prior cache): {}", exc)

    # Detection: every N minutes, aligned to wall-clock boundary, max 1 instance
    scheduler.add_job(
        _run_detection,
        CronTrigger(minute=f"*/{detection_interval_minutes}", second=0, timezone=timezone.utc),
        id="detection_cycle",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    # Classification refresh: every N hours
    scheduler.add_job(
        _run_classification_refresh,
        IntervalTrigger(hours=classification_refresh_hours, timezone=timezone.utc),
        id="classification_refresh",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    return scheduler
