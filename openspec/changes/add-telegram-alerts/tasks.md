## 1. ClickHouse Schema

- [ ] 1.1 在 `config/clickhouse/init.sql` 追加 `volume_alerts` 表 DDL（MergeTree, PARTITION BY toYYYYMM(detected_at), ORDER BY (detected_at, exchange, symbol), TTL 365 day）
- [ ] 1.2 重建测试 ClickHouse 容器，验证表结构（`SHOW CREATE TABLE volume_alerts`）

## 2. Module Structure

- [ ] 2.1 创建 `src/alerts/__init__.py`、`src/alerts/models.py`（TelegramSendResult dataclass、AlertStatus Enum）
- [ ] 2.2 创建 `src/alerts/formatter.py`：纯函数 `format_message(event: AlertEvent) -> str`
- [ ] 2.3 创建 `src/alerts/telegram_client.py`：`TelegramClient` 类，httpx-based 薄包装
- [ ] 2.4 创建 `src/alerts/notifier.py`：`Notifier` 类（实现 `AlertPublisher` Protocol）
- [ ] 2.5 创建 `src/alerts/api.py`：FastAPI router 挂 `/api/v1/alerts/`

## 3. Formatter (TDD)

- [ ] 3.1 写 `tests/unit/test_formatter.py::test_warn_message_format`：mock event → 期望 Markdown 字符串完全匹配
- [ ] 3.2 写 `test_strong_message_includes_orange_emoji` / `test_extreme_message_includes_red_emoji`
- [ ] 3.3 写 `test_escalation_marker_present`：prev_level=STRONG, level=EXTREME → "⬆ from STRONG"
- [ ] 3.4 写 `test_first_entry_no_escalation_marker`：prev_level=None → 不出现 ⬆ marker
- [ ] 3.5 写 `test_volume_formatted_with_si_suffix`：28500000 → "28.5M USDT"
- [ ] 3.6 实现 `format_message()` 让以上测试通过

## 4. Telegram Client (TDD)

- [ ] 4.1 写 `tests/unit/test_telegram_client.py::test_send_message_calls_correct_url`：mock httpx，验证 POST 到 `https://api.telegram.org/bot<TOKEN>/sendMessage`
- [ ] 4.2 写 `test_send_includes_chat_id_and_text`：验证 request body 含 chat_id + text + parse_mode
- [ ] 4.3 写 `test_429_retry_after_honored`：mock 第一次返回 429 + Retry-After=2，断言下次调用至少 2 秒后
- [ ] 4.4 写 `test_500_retry_with_backoff`：mock 502 → 502 → 200，断言 3 次调用 + 退避
- [ ] 4.5 写 `test_all_retries_failed_returns_error`：5 次都 502，返回失败结果（不 raise）
- [ ] 4.6 实现 `TelegramClient.send_message` + tenacity 装饰

## 5. Notifier (TDD)

- [ ] 5.1 写 `tests/unit/test_notifier.py::test_empty_events_no_op`：publish([]) 不调用任何下游
- [ ] 5.2 写 `test_batch_insert_before_send`：mock ch.insert + tg.send，验证 insert 在 send 之前
- [ ] 5.3 写 `test_status_updated_after_send`：3 events，2 sent + 1 failed，验证最终状态正确
- [ ] 5.4 写 `test_dry_run_writes_db_no_telegram`：ALERTS_DRY_RUN=true，验证 db 写但 tg 不调
- [ ] 5.5 写 `test_auto_dry_run_when_no_token`：TELEGRAM_BOT_TOKEN=""，构造时自动 dry_run=true
- [ ] 5.6 写 `test_publish_does_not_raise`：mock 所有下游全失败，publish 仍返回 None 不 raise
- [ ] 5.7 实现 `Notifier.publish()` 完整流程

## 6. API Endpoints

- [ ] 6.1 实现 `POST /api/v1/alerts/test` 发测试消息
- [ ] 6.2 实现 `GET /api/v1/alerts/recent`
- [ ] 6.3 实现 `GET /api/v1/alerts/stats`
- [ ] 6.4 写 `tests/integration/test_alerts_e2e.py` 跑这 3 个 endpoint（用 testcontainers ClickHouse + Telegram fake）

## 7. Health Sub-probe

- [ ] 7.1 在 Notifier 暴露 `health()` 方法
- [ ] 7.2 修改 `/api/v1/health` 聚合 alerts 子键
- [ ] 7.3 写测试覆盖 ok / degraded（dry_run）/ degraded（连续失败）三种状态

## 8. Lifespan Replacement

- [ ] 8.1 修改 `src/main.py`：在 lifespan startup 实例化 Notifier，替换 detector 的 publisher（之前是 LoggingPublisher）
- [ ] 8.2 验证：`grep -rIn LoggingPublisher src/` 在 lifespan 中应该不再用作 detector 注入（保留作为 fallback / test 用）
- [ ] 8.3 验证 `python -c "from src.main import app"` 不抛

## 9. Configuration

- [ ] 9.1 在 `src/config.py` 加 4 个 env vars: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID / TELEGRAM_PARSE_MODE / ALERTS_DRY_RUN
- [ ] 9.2 在 `.env.example` 加对应项 + 说明从 BotFather 拿 token / 跟 bot 1 对 1 拿 chat_id 的步骤注释

## 10. Telegram Fake Server (test infra)

- [ ] 10.1 在 `tests/conftest.py` 加 `telegram_fake` fixture：用 aiohttp 起一个本地 server，接收 sendMessage 请求，记录到 list 供测试 assert
- [ ] 10.2 fixture 配 `TELEGRAM_BOT_TOKEN=fake-token` + 重定向 base URL 到 fake server
- [ ] 10.3 写一个 sanity test 验证 fixture 工作

## 11. End-to-End Pipeline Test (跨 3 个 change)

- [ ] 11.1 写 `tests/integration/test_full_pipeline.py::test_warn_event_flows_end_to_end`：
  - testcontainers ClickHouse + telegram_fake fixture
  - 灌入 mock ohlcv_futures：BTC/USDT 24h 平稳 + 最后 5min 突然 4× 跳升
  - 调 backfill (mock binance REST) → classifier.refresh → detector.detect_once → notifier.publish
  - 断言：volume_alerts 表 1 行 status=sent；telegram_fake 收到 1 个 sendMessage 请求；body 含 BTC/USDT, *4×*, mega
- [ ] 11.2 加 second test：跨 cycle escalation（warn → strong）
- [ ] 11.3 跑 `pytest tests/integration/ -m integration`

## 12. Validate + Commit + Archive

- [ ] 12.1 跑 `pytest tests/`，必须 PASS
- [ ] 12.2 `openspec validate add-telegram-alerts --strict`
- [ ] 12.3 commit message: `feat(alerts): add telegram-alerts pluggable publisher + e2e pipeline test`
- [ ] 12.4 `openspec archive add-telegram-alerts -y`
- [ ] 12.5 二次 commit + git status 干净
