"""Tests for /api/v1/health endpoint sub-probes.

Spec (baseline-infrastructure / Module Wiring Convention, post cleanup-business-modules):
- /api/v1/health MUST be the single endpoint defined in src/main.py.
- src/api/routes.py MUST NOT register a duplicate /health route.
- body.details MUST include sub-probe keys for every wired module:
  database, ws_collectors, recovery, nats.
- Sub-probe failure MUST yield 503 (not 500) and surface the failing sub-key's
  status, while keeping the JSON body intact.
- After cleanup-business-modules: classification/detection/alerts sub-probes
  MUST NOT appear (those moved to volume-monitor and telegram-bot repos).
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient


@contextmanager
def patched_service(**overrides: Any):
    """Temporarily replace `src.main.service` with a mocked DataService.

    Tests do NOT enter the FastAPI lifespan — `TestClient(app)` (no `with`)
    skips startup/shutdown, so importing main.py does not trigger ClickHouse
    or WebSocket connection attempts.
    """
    import src.main as main_module

    mock_service = MagicMock()
    mock_service.is_running = overrides.get("is_running", True)

    base_status: Dict[str, Any] = {
        "running": mock_service.is_running,
        "database": overrides.get("database", "healthy"),
        "collectors": overrides.get("collectors_status", {}),
        "recovery": "running",
    }
    mock_service.get_status = AsyncMock(return_value=base_status)

    if "collector_manager" in overrides:
        mock_service.collector_manager = overrides["collector_manager"]
    else:
        now = datetime.now(timezone.utc)
        c1 = MagicMock()
        c1.websocket_clients = {"BTCUSDT@kline_1m": MagicMock()}
        c1.connection_health = {
            "BTCUSDT@kline_1m": {
                "status": "healthy",
                "last_message_time": now,
                "messages_count": 1,
                "symbols": set(),
            }
        }
        mock_cm = MagicMock()
        mock_cm.collectors = {"binance_primary": c1}
        mock_service.collector_manager = mock_cm

    if "recovery_service" in overrides:
        mock_service.recovery_service = overrides["recovery_service"]
    else:
        mock_rs = MagicMock()
        mock_rs.is_running = True
        mock_rs.last_cycle_ts = datetime.now(timezone.utc)
        mock_service.recovery_service = mock_rs

    mock_service.db_manager = MagicMock()

    # NATS publisher — default healthy
    if "publisher" in overrides:
        mock_service.publisher = overrides["publisher"]
    else:
        mock_pub = MagicMock()
        mock_pub.health.return_value = {
            "status": "ok",
            "connected": True,
            "publish_failure_count": 0,
            "last_publish_age_seconds": 1.0,
            "disabled": False,
        }
        mock_service.publisher = mock_pub

    orig = main_module.service
    main_module.service = mock_service
    try:
        yield main_module.app, mock_service
    finally:
        main_module.service = orig


def test_health_returns_module_subprobes():
    """Spec: body.details MUST include keys for every wired module."""
    with patched_service() as (app, _svc):
        client = TestClient(app)
        r = client.get("/api/v1/health")

    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert "details" in body, (
        f"health response missing 'details' key — got {list(body.keys())}. "
        f"Likely routes.py's simplified /health is overriding main.py's full version."
    )
    details = body["details"]
    required = {"database", "ws_collectors", "recovery", "nats"}
    missing = required - set(details.keys())
    assert not missing, (
        f"health details missing sub-probes: {sorted(missing)}. "
        f"Got keys: {sorted(details.keys())}"
    )


def test_health_no_business_module_subprobes():
    """Spec (cleanup-business-modules): classification/detection/alerts MUST NOT
    appear in body.details — they live in volume-monitor and telegram-bot now."""
    with patched_service() as (app, _svc):
        client = TestClient(app)
        r = client.get("/api/v1/health")

    body = r.json()
    details = body.get("details", {})
    leaked = set(details.keys()) & {"classification", "detection", "alerts"}
    assert not leaked, (
        f"candleforge /health MUST NOT expose business module sub-probes after "
        f"cleanup-business-modules; leaked: {sorted(leaked)}"
    )


def test_health_ws_collectors_subprobe_shape():
    """Spec: ws_collectors sub-probe MUST contain expected_count, connected_count,
    disconnected_client_keys."""
    now = datetime.now(timezone.utc)
    c1 = MagicMock()
    c1.websocket_clients = {
        "BTCUSDT@kline_1m": MagicMock(),
        "ETHUSDT@kline_1m": MagicMock(),
    }
    c1.connection_health = {
        "BTCUSDT@kline_1m": {"status": "healthy", "last_message_time": now, "messages_count": 100, "symbols": set()},
        "ETHUSDT@kline_1m": {"status": "healthy", "last_message_time": now, "messages_count": 50, "symbols": set()},
        "BNBUSDT@kline_1m": {"status": "unhealthy", "last_message_time": None, "messages_count": 0, "symbols": set()},
    }
    mock_cm = MagicMock()
    mock_cm.collectors = {"binance_primary": c1}

    with patched_service(collector_manager=mock_cm) as (app, _):
        client = TestClient(app)
        r = client.get("/api/v1/health")

    body = r.json()
    ws = body.get("details", {}).get("ws_collectors", {})
    for key in ("status", "expected_count", "connected_count", "disconnected_client_keys"):
        assert key in ws, (
            f"ws_collectors sub-probe missing key {key!r}; got keys: {sorted(ws.keys())}"
        )
    assert isinstance(ws["disconnected_client_keys"], list)
    assert ws["expected_count"] == 3
    assert ws["connected_count"] == 2
    assert len(ws["disconnected_client_keys"]) == 1
    assert "BNBUSDT" in ws["disconnected_client_keys"][0]
    assert ws["status"] == "degraded"


def test_health_recovery_subprobe_shape():
    """Spec: recovery sub-probe MUST contain is_running, last_cycle_age_seconds."""
    mock_rs = MagicMock()
    mock_rs.is_running = True
    mock_rs.last_cycle_ts = datetime.now(timezone.utc) - timedelta(seconds=42)
    with patched_service(recovery_service=mock_rs) as (app, _):
        client = TestClient(app)
        r = client.get("/api/v1/health")

    body = r.json()
    rec = body.get("details", {}).get("recovery", {})
    assert isinstance(rec, dict), f"recovery sub-probe must be a dict, got {type(rec).__name__}"
    for key in ("status", "is_running", "last_cycle_age_seconds"):
        assert key in rec, f"recovery sub-probe missing key {key!r}; got keys: {sorted(rec.keys())}"
    assert rec["is_running"] is True
    age = rec["last_cycle_age_seconds"]
    assert isinstance(age, (int, float))
    assert 30 <= age <= 60


def test_health_returns_200_when_only_degraded():
    """Spec: HTTP 503 means *unhealthy* (service unusable). HTTP 200 with
    `status: "degraded"` means service is up but a sub-probe reports a
    non-critical issue. Without this distinction, docker's `curl -f` healthcheck
    flips the container to unhealthy permanently even when the service is
    functional — symptom observed on production host on 2026-05-12.
    """
    now = datetime.now(timezone.utc)
    c1 = MagicMock()
    c1.websocket_clients = {"BTCUSDT@kline_1m": MagicMock()}
    c1.connection_health = {
        "BTCUSDT@kline_1m": {"status": "healthy", "last_message_time": now, "messages_count": 1, "symbols": set()},
        "stale_client_key": {"status": "unhealthy", "last_message_time": None, "messages_count": 0, "symbols": set()},
    }
    mock_cm = MagicMock()
    mock_cm.collectors = {"binance_primary": c1}

    with patched_service(collector_manager=mock_cm) as (app, _):
        client = TestClient(app)
        r = client.get("/api/v1/health")

    body = r.json()
    assert body["details"]["ws_collectors"]["status"] == "degraded"
    assert r.status_code == 200, (
        f"degraded-only should return HTTP 200 (service usable), got {r.status_code}."
    )
    assert body["status"] == "degraded"


def test_health_serializes_datetime_in_collectors_status():
    """Regression: 2026-05-12 deploy crashed with `Object of type datetime is not
    JSON serializable` because `collector_manager.get_status()` returns nested
    dicts containing `last_message_time: datetime` values. JSONResponse does NOT
    apply FastAPI's encoder automatically — the handler MUST `jsonable_encoder()`
    its content before constructing the response.
    """
    now = datetime.now(timezone.utc)

    async def fake_get_status_with_datetime() -> Dict[str, Any]:
        return {
            "running": True,
            "database": "healthy",
            "collectors": {
                "binance_primary": {
                    "is_running": True,
                    "last_message_time": now,
                    "stats": {"started_at": now, "messages_received": 1000},
                },
            },
            "recovery": "running",
        }

    with patched_service() as (app, svc):
        svc.get_status = fake_get_status_with_datetime
        client = TestClient(app)
        r = client.get("/api/v1/health")

    assert r.status_code != 500
    body = r.json()
    assert "details" in body
    nested = body["details"]["collectors"]["binance_primary"]["last_message_time"]
    assert isinstance(nested, str) and "T" in nested


def test_health_nats_subprobe_shape():
    """Spec (introduce-nats-event-bus): body.details MUST include 'nats' key."""
    mock_pub = MagicMock()
    mock_pub.health.return_value = {
        "status": "ok",
        "connected": True,
        "publish_failure_count": 0,
        "last_publish_age_seconds": 1.2,
        "disabled": False,
    }
    with patched_service(publisher=mock_pub) as (app, _svc):
        client = TestClient(app)
        r = client.get("/api/v1/health")

    body = r.json()
    nats = body.get("details", {}).get("nats")
    assert isinstance(nats, dict), f"body.details.nats missing or not dict: {nats!r}"
    for k in ("status", "connected", "publish_failure_count", "last_publish_age_seconds", "disabled"):
        assert k in nats, f"nats sub-probe missing {k!r}; got keys {sorted(nats.keys())}"
    assert nats["status"] == "ok"
    assert nats["connected"] is True


def test_health_nats_subprobe_failed_when_no_publisher():
    """When service.publisher is None, nats sub-probe reports failed."""
    with patched_service(publisher=None) as (app, _svc):
        client = TestClient(app)
        r = client.get("/api/v1/health")

    body = r.json()
    nats = body.get("details", {}).get("nats")
    assert nats["status"] == "failed"
    assert "not initialized" in nats.get("reason", "").lower()


def test_only_one_health_route_registered():
    """Spec: exactly one handler for /api/v1/health, defined in src/main.py."""
    import src.main as main_module

    health_routes = [
        r for r in main_module.app.routes
        if getattr(r, "path", None) == "/api/v1/health"
    ]
    assert len(health_routes) == 1
    endpoint = getattr(health_routes[0], "endpoint", None)
    module_name = getattr(endpoint, "__module__", "")
    assert module_name == "src.main"
