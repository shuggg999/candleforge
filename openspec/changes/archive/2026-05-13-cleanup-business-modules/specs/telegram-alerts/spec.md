## REMOVED Requirements

整 capability `telegram-alerts` 从 `data-service` 仓库下线 — 等价能力由独立 repo `telegram-bot` 提供，遵守"采集进程不接触业务凭据"拆分原则。`TELEGRAM_BOT_TOKEN` 等敏感配置 SHALL ONLY 由 `telegram-bot/.env` 持有。

### Requirement: AlertPublisher Implementation

**Reason**: 拆分到 `telegram-bot` 仓 — 该 repo 已实现等价 webhook 接收 + Telegram Bot API 推送，详见 `telegram-bot/openspec/changes/bootstrap-telegram-bot/specs/telegram-alerts/`。

### Requirement: Telegram Message Format

**Reason**: HTML / Markdown 渲染逻辑由 `telegram-bot/src/formatter/volume_alert.py` 接管，使用 `html.escape` 防注入。

### Requirement: Persistent Audit Table

**Reason**: ClickHouse 表 `telegram_audit` 物理保留（telegram-bot 仍在写入），但 data-service 进程不再读写。审计查询走 `telegram-bot:8300`。

### Requirement: Retry on Telegram Failure

**Reason**: 重试策略（429 / 5xx / 4xx 区分）由 `telegram-bot/src/telegram/client.py` 接管，使用 tenacity + retry_after 头解析。

### Requirement: Dry-Run Mode

**Reason**: dry-run 配置移到 `telegram-bot/.env`；`ALERTS_DRY_RUN` 字段从 data-service 删除。

### Requirement: Health Sub-probe

**Reason**: data-service 的 `/api/v1/health` body 不再包含 `alerts` 字段；`telegram-bot:8300/api/v1/health` 暴露等价 `telegram_api` + `clickhouse_audit` + `chat_rate_limiter` sub-probes。

### Requirement: Inspection & Test Endpoints

**Reason**: `/api/v1/alerts/*` 等检查端点从 data-service 下线（返回 404）；`telegram-bot:8300/alerts` + `telegram-bot:8300/audit` 提供等价端点。

### Requirement: End-to-End Pipeline Verification

**Reason**: e2e 验证流程（detection → notifier → Telegram → audit）由 `volume-monitor` + `telegram-bot` 两仓的集成测试接管；data-service 不再持有相关验证逻辑。
