"""Unit tests for src.detection.api endpoints."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.detection.api import router, set_detector
from src.detection.detector import Detector
from src.detection.models import Level


@pytest.fixture
def client_with_detector():
    mock_ch = AsyncMock()
    mock_ch.fetchall.return_value = [
        {"symbol": "BTC/USDT", "sample_count": 1440, "current_avg": 4000.0, "baseline_median": 1000.0},
    ]
    mock_classifier = MagicMock()
    mock_classifier.get_tier.return_value = "mega"
    publisher = AsyncMock()

    detector = Detector(
        ch_client=mock_ch,
        classifier=mock_classifier,
        publisher=publisher,
    )
    set_detector(detector)

    app = FastAPI()
    app.include_router(router)
    yield TestClient(app), detector
    set_detector(None)


def test_run_endpoint_returns_events(client_with_detector):
    client, _ = client_with_detector
    r = client.post("/api/v1/detect/run")
    assert r.status_code == 200
    body = r.json()
    assert "events" in body
    assert len(body["events"]) == 1
    assert body["events"][0]["symbol"] == "BTC/USDT"
    assert body["events"][0]["level"] == "WARN"


def test_state_endpoint_returns_last_levels(client_with_detector):
    client, detector = client_with_detector
    client.post("/api/v1/detect/run")  # populate state
    r = client.get("/api/v1/detect/state")
    assert r.status_code == 200
    body = r.json()
    assert "BTC/USDT" in body["last_levels"]
    assert body["last_levels"]["BTC/USDT"] == "WARN"
    assert body["active_by_level"]["warn"] == 1


def test_thresholds_endpoint_returns_current_table(client_with_detector):
    client, _ = client_with_detector
    r = client.get("/api/v1/detect/thresholds")
    assert r.status_code == 200
    body = r.json()
    assert "mega" in body
    assert body["mega"]["warn"] == 3.0
    assert body["small"]["extreme"] == 100.0


def test_run_endpoint_503_when_no_detector():
    set_detector(None)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    r = client.post("/api/v1/detect/run")
    assert r.status_code == 503
