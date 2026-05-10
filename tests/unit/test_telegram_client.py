"""Unit tests for src.alerts.telegram_client.TelegramClient."""
from __future__ import annotations

import time

import httpx
import pytest

from src.alerts.telegram_client import TelegramClient


def _make_client(**kwargs):
    defaults = dict(token="fake-token", parse_mode="Markdown", max_retries=3, base_backoff=0.01)
    defaults.update(kwargs)
    return TelegramClient(**defaults)


def _success_handler(request: httpx.Request) -> httpx.Response:
    assert "/botfake-token/sendMessage" in str(request.url)
    body = httpx.Request("POST", request.url).read()  # not used
    return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})


@pytest.mark.asyncio
async def test_send_message_calls_correct_url():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    transport = httpx.MockTransport(handler)
    c = _make_client(transport=transport)
    res = await c.send_message("chat_id_123", "hello")
    assert res.success
    assert "/botfake-token/sendMessage" in captured["url"]


@pytest.mark.asyncio
async def test_send_includes_chat_id_and_text():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    transport = httpx.MockTransport(handler)
    c = _make_client(transport=transport)
    await c.send_message("chat_42", "hello *world*")
    assert captured["body"]["chat_id"] == "chat_42"
    assert captured["body"]["text"] == "hello *world*"
    assert captured["body"]["parse_mode"] == "Markdown"


@pytest.mark.asyncio
async def test_429_retry_after_honored():
    call_times = []
    state = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_times.append(time.monotonic())
        state["calls"] += 1
        if state["calls"] == 1:
            return httpx.Response(429, headers={"Retry-After": "1"}, json={"ok": False})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    transport = httpx.MockTransport(handler)
    c = _make_client(transport=transport)
    res = await c.send_message("chat", "msg")
    assert res.success
    assert state["calls"] == 2
    delta = call_times[1] - call_times[0]
    assert delta >= 0.95, f"expected ≥1s wait honoring Retry-After=1, got {delta:.3f}s"


@pytest.mark.asyncio
async def test_500_retry_with_backoff():
    state = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        if state["calls"] < 3:
            return httpx.Response(502, json={"ok": False})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    transport = httpx.MockTransport(handler)
    c = _make_client(transport=transport)
    res = await c.send_message("chat", "msg")
    assert res.success
    assert state["calls"] == 3


@pytest.mark.asyncio
async def test_all_retries_failed_returns_error():
    state = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        return httpx.Response(502, json={"ok": False})

    transport = httpx.MockTransport(handler)
    c = _make_client(max_retries=3, transport=transport)
    res = await c.send_message("chat", "msg")
    assert not res.success
    assert res.error is not None
    assert state["calls"] == 3
