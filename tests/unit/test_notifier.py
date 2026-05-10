"""Unit tests for src.alerts.notifier.Notifier."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, call

import pytest

from src.alerts.models import TelegramSendResult
from src.alerts.notifier import Notifier
from src.detection.models import AlertEvent, Level


def _ev(symbol="BTC/USDT", level=Level.WARN, prev_level=None) -> AlertEvent:
    return AlertEvent(
        symbol=symbol,
        tier="mega",
        level=level,
        ratio=4.0,
        current_avg=4000.0,
        baseline_median=1000.0,
        threshold_used=3.0,
        detected_at=datetime(2026, 5, 10, 15, 0, 0, tzinfo=timezone.utc),
        is_escalation=True,
        prev_level=prev_level,
    )


@pytest.mark.asyncio
async def test_empty_events_no_op():
    ch = AsyncMock()
    tg = AsyncMock()
    n = Notifier(ch_client=ch, telegram_client=tg, chat_id="chat")
    await n.publish([])
    ch.execute.assert_not_called()
    tg.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_batch_insert_before_send():
    """publish must INSERT pending rows BEFORE attempting Telegram send."""
    ch = AsyncMock()
    tg = AsyncMock()
    tg.send_message = AsyncMock(return_value=TelegramSendResult(success=True, message_id=1))

    call_log = []

    async def _ch_execute(*args, **kwargs):
        # First call should be INSERT INTO volume_alerts
        sql = args[0]
        call_log.append(("ch_execute", sql.split()[0:3]))

    async def _tg_send(*args, **kwargs):
        call_log.append(("tg_send", None))
        return TelegramSendResult(success=True, message_id=1)

    ch.execute = AsyncMock(side_effect=_ch_execute)
    tg.send_message = AsyncMock(side_effect=_tg_send)

    n = Notifier(ch_client=ch, telegram_client=tg, chat_id="chat")
    await n.publish([_ev("X/USDT")])

    # First entry must be a ch_execute (the INSERT)
    assert call_log[0][0] == "ch_execute"
    assert call_log[0][1][:2] == ["INSERT", "INTO"]
    # Telegram send happens after insert
    tg_indices = [i for i, c in enumerate(call_log) if c[0] == "tg_send"]
    assert tg_indices and tg_indices[0] > 0


@pytest.mark.asyncio
async def test_status_updated_after_send():
    """3 events: 2 sent, 1 failed → 1 INSERT + 3 UPDATE statements."""
    ch = AsyncMock()
    tg = AsyncMock()

    send_results = [
        TelegramSendResult(success=True, message_id=1),
        TelegramSendResult(success=False, error="boom"),
        TelegramSendResult(success=True, message_id=3),
    ]
    tg.send_message = AsyncMock(side_effect=send_results)

    n = Notifier(ch_client=ch, telegram_client=tg, chat_id="chat")
    await n.publish([_ev(s) for s in ["A/USDT", "B/USDT", "C/USDT"]])

    sql_calls = [c.args[0] for c in ch.execute.call_args_list]
    insert_calls = [s for s in sql_calls if s.startswith("INSERT")]
    update_calls = [s for s in sql_calls if "ALTER TABLE" in s and "UPDATE" in s]
    assert len(insert_calls) == 1
    assert len(update_calls) == 3


@pytest.mark.asyncio
async def test_dry_run_writes_db_no_telegram():
    ch = AsyncMock()
    tg = AsyncMock()
    n = Notifier(ch_client=ch, telegram_client=tg, chat_id="chat", dry_run=True)
    await n.publish([_ev("X/USDT")])

    # INSERT should still happen
    sql_calls = [c.args[0] for c in ch.execute.call_args_list]
    assert any(s.startswith("INSERT") for s in sql_calls)
    # Telegram NOT called
    tg.send_message.assert_not_called()
    # All updated to 'skipped'
    update_sqls = [s for s in sql_calls if "ALTER TABLE" in s]
    assert any("'skipped'" in s for s in update_sqls)


@pytest.mark.asyncio
async def test_auto_dry_run_when_no_token():
    """telegram_client=None or chat_id missing → auto dry-run."""
    ch = AsyncMock()
    n = Notifier(ch_client=ch, telegram_client=None, chat_id=None)
    assert n._dry_run is True
    await n.publish([_ev("X/USDT")])
    sql_calls = [c.args[0] for c in ch.execute.call_args_list]
    assert any(s.startswith("INSERT") for s in sql_calls)


@pytest.mark.asyncio
async def test_publish_does_not_raise_on_failures():
    """All downstream failures swallowed; publish returns None."""
    ch = AsyncMock()
    ch.execute = AsyncMock(side_effect=Exception("clickhouse down"))
    tg = AsyncMock()
    tg.send_message = AsyncMock(side_effect=Exception("telegram down"))

    n = Notifier(ch_client=ch, telegram_client=tg, chat_id="chat")
    await n.publish([_ev("X/USDT")])  # MUST NOT raise
