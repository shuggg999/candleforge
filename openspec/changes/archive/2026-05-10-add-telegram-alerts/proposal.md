## Why

成交量异常监控的最后一公里：把 detection 模块产出的 `AlertEvent` 推送到 Telegram 单聊（个人监控用），同时在 ClickHouse `volume_alerts` 表持久化所有事件用于审计与回放。本 change 引入 `telegram-alerts` capability —— 通过实现 detection change 定义的 `AlertPublisher` Protocol，作为可插拔 publisher 接入，**不修改 detection 模块代码**。

Telegram 推送格式、去重策略（"首次进档 + 升级"在 detection 已处理）、失败处理、不限流策略详见 `docs/superpowers/specs/2026-05-10-volume-anomaly-monitor-design.md` 第 3 节 Alerts 部分及第 10 节决策追溯。

本 change 也包含**跨 3 个 change 的端到端集成测试**（设计 doc 第 8 节约定）：用 testcontainers 起 ClickHouse + Telegram fake，验证 collector → backfill → classification → detection → alerts → Telegram 全链路。

## What Changes

- **新增** `src/alerts/` 模块：`Notifier` 类（实现 `AlertPublisher` Protocol）、`telegram_client.py`（薄包装 `python-telegram-bot` 或直接 httpx 调用 Telegram Bot API）、`formatter.py`（AlertEvent → Markdown 字符串）、`api.py` 端点
- **新增** ClickHouse 表 `volume_alerts`（MergeTree 按月分区，TTL 365 天）—— 加到 `config/clickhouse/init.sql`
- **修改** `src/main.py` lifespan：把 detection change 注入的 `LoggingPublisher` 替换成 `Notifier`（依赖反转：alerts 模块在 startup 实例化 Notifier 并注入给 detector）
- **新增** `/api/v1/health` 子键 `alerts`：telegram_configured / dry_run / last_send_at / stats_24h
- **新增** endpoints：`POST /api/v1/alerts/test`（发测试消息）、`GET /api/v1/alerts/recent?limit=N`（查最近告警）、`GET /api/v1/alerts/stats?since=N`（统计）
- **新增** `.env.example` 项：`TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`、`TELEGRAM_PARSE_MODE=Markdown`、`ALERTS_DRY_RUN=false`
- **新增** 跨 change 端到端集成测试 `tests/integration/test_full_pipeline.py`：覆盖 backfill → classification refresh → detection cycle 触发 escalation → alerts 落库 + 推 Telegram fake
- **依赖** `add-volume-detection` change 已 archive 入主 specs：本 change 注入 `Notifier` 替换 `LoggingPublisher`
- **NOT IN SCOPE**：多渠道（Discord / 邮件等）；告警聚合摘要（"今日 top 10"等）；持久化重试队列；告警接收人路由

## Capabilities

### New Capabilities

- `telegram-alerts`: 把 detection 输出的 AlertEvent 全量落 ClickHouse 审计表 + 全量推 Telegram 单聊，含 Markdown 格式渲染、tenacity 重试、失败状态追踪、dry-run 模式。

### Modified Capabilities

- `volume-detection`: 不修改 requirement，但**替换默认 publisher**：lifespan 不再注入 `LoggingPublisher` 而注入 `Notifier`。detection 模块代码 0 改动，验证 Pluggable Publisher requirement 设计正确。

## Impact

**Affected code**:
- 新增 `src/alerts/{__init__, notifier, telegram_client, formatter, api, models}.py`
- 修改 `src/main.py` lifespan：替换 publisher 注入
- 修改 `src/api/routes.py`：include alerts router、聚合 /health
- 修改 `config/clickhouse/init.sql`：加 `volume_alerts` 表 DDL
- 修改 `src/config.py`：加 4 个 telegram 相关 env vars
- 修改 `.env.example`：加 telegram + dry-run 配置
- 修改 `requirements.txt`：加 `httpx>=0.27`（如果还没有）+ 可选 `python-telegram-bot==21.0`（决策见 design.md）
- 新增 `tests/unit/test_notifier.py`、`tests/unit/test_formatter.py`、`tests/unit/test_telegram_client.py`、`tests/integration/test_alerts_e2e.py`、`tests/integration/test_full_pipeline.py`

**Affected APIs**: 新增 3 个 alerts endpoints；扩展 `GET /api/v1/health`

**Affected dependencies**: 可能新增 `httpx` 或 `python-telegram-bot`

**Risk**: 中。这是首个真正发出"用户可见外部消息"的 change。错的告警 / 误报 / 刷屏会直接打扰用户。Mitigation：默认配 `ALERTS_DRY_RUN=true`，第一次部署时手动设 `false` 才启用；alerts 失败不影响 detection 继续跑（fail-open）。
