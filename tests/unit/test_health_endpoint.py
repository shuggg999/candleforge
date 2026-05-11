"""Tests for /api/v1/health endpoint sub-probes.

Spec (baseline-infrastructure / Module Wiring Convention):
- /api/v1/health MUST be the single endpoint defined in src/main.py.
- src/api/routes.py MUST NOT register a duplicate /health route.
- body.details MUST include sub-probe keys for every wired module:
  database, classification, detection, alerts, ws_collectors, recovery.
- Sub-probe failure MUST yield 503 (not 500) and surface the failing sub-key's
  status, while keeping the JSON body intact.
"""
from __future__ import annotations

import importlib
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

    # get_status — base dict the health_check function starts from
    base_status: Dict[str, Any] = {
        "running": mock_service.is_running,
        "database": overrides.get("database", "healthy"),
        "collectors": overrides.get("collectors_status", {}),
        "recovery": "running",
    }
    mock_service.get_status = AsyncMock(return_value=base_status)

    # Classification sub-probe component
    if "classifier" in overrides:
        mock_service.classifier = overrides["classifier"]
    else:
        mock_classifier = MagicMock()
        mock_classifier.health.return_value = {"status": "ok", "symbol_count": 100}
        mock_service.classifier = mock_classifier

    # Detection sub-probe component
    if "detector" in overrides:
        mock_service.detector = overrides["detector"]
    else:
        mock_detector = MagicMock()
        mock_detector.health.return_value = {"status": "ok", "last_run_ts": None}
        mock_service.detector = mock_detector

    # Alerts sub-probe component
    if "notifier" in overrides:
        mock_service.notifier = overrides["notifier"]
    else:
        mock_notifier = MagicMock()
        mock_notifier.health.return_value = {"status": "ok", "dry_run": False}
        mock_service.notifier = mock_notifier

    # collector_manager — for ws_collectors sub-probe
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

    # recovery_service — for recovery sub-probe
    if "recovery_service" in overrides:
        mock_service.recovery_service = overrides["recovery_service"]
    else:
        mock_rs = MagicMock()
        mock_rs.is_running = True
        mock_rs.last_cycle_ts = datetime.now(timezone.utc)
        mock_service.recovery_service = mock_rs

    # db_manager — keep simple
    mock_service.db_manager = MagicMock()

    orig = main_module.service
    main_module.service = mock_service
    try:
        yield main_module.app, mock_service
    finally:
        main_module.service = orig


# --- 2.1 ----------------------------------------------------------------------


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
    required = {"database", "classification", "detection", "alerts", "ws_collectors", "recovery"}
    missing = required - set(details.keys())
    assert not missing, (
        f"health details missing sub-probes: {sorted(missing)}. "
        f"Got keys: {sorted(details.keys())}"
    )


# --- 2.2 ----------------------------------------------------------------------


def test_health_503_when_classification_failed():
    """Spec: sub-probe failure SHALL surface as 503 with status field reflecting failure."""
    failing_classifier = MagicMock()
    failing_classifier.health.return_value = {
        "status": "failed",
        "reason": "ClickHouse query timed out",
    }
    with patched_service(classifier=failing_classifier) as (app, _):
        client = TestClient(app)
        r = client.get("/api/v1/health")

    assert r.status_code == 503, (
        f"expected 503 when classification sub-probe failed, got {r.status_code}: {r.text}"
    )
    body = r.json()
    # overall status should reflect degraded/unhealthy
    assert body.get("status") in ("degraded", "unhealthy"), (
        f"expected status=degraded|unhealthy, got {body.get('status')!r}"
    )
    # the failing sub-probe should be reachable in the body
    classification = body.get("details", {}).get("classification", {})
    assert classification.get("status") == "failed", (
        f"failing sub-probe must be surfaced in body, got {classification}"
    )


# --- 2.3 ----------------------------------------------------------------------


def test_health_ws_collectors_subprobe_shape():
    """Spec: ws_collectors sub-probe MUST contain expected_count, connected_count,
    disconnected_client_keys.

    Models the realistic state where a client was once connected (so it has a
    `connection_health` entry) but has since been removed from `websocket_clients`
    (e.g. reconnect failed). The sub-probe treats the union of both dicts as
    "expected" and `websocket_clients` as "currently connected".
    """
    now = datetime.now(timezone.utc)
    c1 = MagicMock()
    c1.websocket_clients = {
        "BTCUSDT@kline_1m": MagicMock(),
        "ETHUSDT@kline_1m": MagicMock(),
    }
    c1.connection_health = {
        "BTCUSDT@kline_1m": {"status": "healthy", "last_message_time": now, "messages_count": 100, "symbols": set()},
        "ETHUSDT@kline_1m": {"status": "healthy", "last_message_time": now, "messages_count": 50, "symbols": set()},
        # Was once connected, now gone — should show up as disconnected
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
    assert isinstance(ws["disconnected_client_keys"], list), (
        f"disconnected_client_keys must be a list, got {type(ws['disconnected_client_keys']).__name__}"
    )
    # 3 clients ever seen, 2 currently in websocket_clients dict → 1 missing
    assert ws["expected_count"] == 3, f"expected_count should be 3 (union of both dicts), got {ws['expected_count']}"
    assert ws["connected_count"] == 2, f"connected_count should be 2 (websocket_clients), got {ws['connected_count']}"
    assert len(ws["disconnected_client_keys"]) == 1, (
        f"disconnected_client_keys should have 1 entry (BNBUSDT), got {ws['disconnected_client_keys']}"
    )
    assert "BNBUSDT" in ws["disconnected_client_keys"][0], (
        f"disconnected entry should reference BNBUSDT, got {ws['disconnected_client_keys']}"
    )
    assert ws["status"] == "degraded", f"status should be degraded when connected < expected, got {ws['status']}"


# --- 2.4 ----------------------------------------------------------------------


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
    # NB: existing main.py returns recovery as a string ("running"/"stopped").
    # The new spec requires recovery be a dict with sub-keys.
    assert isinstance(rec, dict), (
        f"recovery sub-probe must be a dict, got {type(rec).__name__}: {rec!r}. "
        f"This test fails until main.py:424 health_check is extended."
    )
    for key in ("status", "is_running", "last_cycle_age_seconds"):
        assert key in rec, (
            f"recovery sub-probe missing key {key!r}; got keys: {sorted(rec.keys())}"
        )
    assert rec["is_running"] is True
    # last_cycle_age_seconds should be a number close to 42
    age = rec["last_cycle_age_seconds"]
    assert isinstance(age, (int, float)), (
        f"last_cycle_age_seconds must be numeric, got {type(age).__name__}"
    )
    assert 30 <= age <= 60, f"expected age ~42s, got {age}"


# --- additional regression: no duplicate /health route -----------------------


def test_health_returns_200_when_only_degraded():
    """Spec: HTTP 503 means *unhealthy* (service unusable). HTTP 200 with
    `status: "degraded"` means service is up but a sub-probe reports a
    non-critical issue (e.g. recovery hasn't completed first cycle yet,
    one WS client out of many is disconnected).

    Without this distinction, docker's `curl -f` healthcheck flips the
    container to unhealthy permanently even when the service is functional
    — which was the symptom on jarvis 2026-05-12.
    """
    # ws_collectors reports "degraded" (one expected client missing)
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
    assert body["details"]["ws_collectors"]["status"] == "degraded", (
        f"sanity: ws_collectors should be degraded, got {body['details']['ws_collectors']}"
    )
    assert r.status_code == 200, (
        f"degraded-only should return HTTP 200 (service usable), got {r.status_code}. "
        f"This regression breaks docker healthcheck on jarvis."
    )
    assert body["status"] == "degraded", f"body.status should be 'degraded', got {body['status']!r}"


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
                    "last_message_time": now,  # <-- raw datetime, must serialize
                    "stats": {"started_at": now, "messages_received": 1000},
                },
            },
            "recovery": "running",
        }

    with patched_service() as (app, svc):
        svc.get_status = fake_get_status_with_datetime
        client = TestClient(app)
        r = client.get("/api/v1/health")

    # Whatever the verdict, the response MUST be valid JSON (not a 500 from
    # serialization). 200 or 503 — both fine. 500 means we regressed.
    assert r.status_code != 500, (
        f"datetime in collectors.last_message_time crashed serialization; "
        f"body={r.text[:300]}"
    )
    body = r.json()
    assert "details" in body
    # The datetime value should now be an ISO string after jsonable_encoder
    nested = body["details"]["collectors"]["binance_primary"]["last_message_time"]
    assert isinstance(nested, str) and "T" in nested, (
        f"datetime should serialize to ISO string, got {nested!r}"
    )


def test_only_one_health_route_registered():
    """Spec (Module Wiring Convention scenario): exactly one handler for /api/v1/health,
    defined in src/main.py — routes.py MUST NOT register its own /health.
    """
    import src.main as main_module

    health_routes = [
        r for r in main_module.app.routes
        if getattr(r, "path", None) == "/api/v1/health"
    ]
    assert len(health_routes) == 1, (
        f"expected exactly 1 /api/v1/health route, got {len(health_routes)}. "
        f"This usually means src/api/routes.py also registers /health and "
        f"FastAPI silently overrides main.py's full version."
    )

    # The single registered handler must come from src.main module (not routes.py)
    endpoint = getattr(health_routes[0], "endpoint", None)
    module_name = getattr(endpoint, "__module__", "")
    assert module_name == "src.main", (
        f"/api/v1/health handler must live in src.main, got {module_name!r}"
    )
