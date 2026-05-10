"""Unit tests for src.scheduler."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest


def test_next_5min_boundary_aligns_correctly():
    from src.scheduler import next_n_min_boundary

    t = datetime(2026, 5, 10, 14, 23, 42, tzinfo=timezone.utc)
    assert next_n_min_boundary(t, 5) == datetime(2026, 5, 10, 14, 25, 0, tzinfo=timezone.utc)

    t2 = datetime(2026, 5, 10, 14, 25, 0, tzinfo=timezone.utc)
    assert next_n_min_boundary(t2, 5) == datetime(2026, 5, 10, 14, 30, 0, tzinfo=timezone.utc)

    t3 = datetime(2026, 5, 10, 14, 59, 30, tzinfo=timezone.utc)
    assert next_n_min_boundary(t3, 5) == datetime(2026, 5, 10, 15, 0, 0, tzinfo=timezone.utc)


def test_build_scheduler_registers_two_jobs():
    from src.scheduler import build_scheduler

    classifier = MagicMock()
    detector = MagicMock()

    scheduler = build_scheduler(
        classifier=classifier,
        detector=detector,
        detection_interval_minutes=5,
        classification_refresh_hours=12,
    )

    job_ids = {job.id for job in scheduler.get_jobs()}
    assert "detection_cycle" in job_ids
    assert "classification_refresh" in job_ids

    detection_job = scheduler.get_job("detection_cycle")
    assert detection_job is not None
    assert detection_job.max_instances == 1
