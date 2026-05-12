## REMOVED Requirements

整 capability `telegram-alerts` 从 `data-service` 仓库下线。所有 requirement 均移除，对应代码 `src/alerts/` 整目录删除。

业务能力 SHALL 由独立 repo `telegram-bot` 提供，详见 `telegram-bot/openspec/changes/bootstrap-telegram-bot/specs/telegram-alerts/spec.md`。

### Requirement: Telegram Bot API Client (REMOVED)

**Reason**: 拆分到 `telegram-bot` 仓 — `TELEGRAM_BOT_TOKEN` 等敏感配置 SHALL ONLY 由 `telegram-bot/.env` 持有。data-service 不再持有 token，遵守"采集进程不接触业务凭据"拆分原则。

**Security Migration**: 任何残留的 `TELEGRAM_*` 配置项从 data-service 的 `.env.example` 和 `src/config.py` 移除。轮换 token 时只动 `telegram-bot/.env`。

### Requirement: Alert Notifier (REMOVED)

**Reason**: 推送由 `telegram-bot` 仓的 `TelegramClient` + `ChatRateLimiter` + `AuditWriter` 接管，跨进程接收 webhook（POST /alerts）。

### Requirement: Alert API Routes (REMOVED)

**Reason**: data-service 不再暴露 `/api/v1/alerts/*`；下游可调 `telegram-bot:8300/alerts` 直接推送，或调 telegram-bot 的查询接口取审计记录。

### Requirement: Alert Audit Persistence (REMOVED from data-service)

**Reason**: ClickHouse 表 `telegram_audit` 物理保留（telegram-bot 仍在写入），但 data-service 进程不再读写。审计查询走 `telegram-bot:8300`。

### Requirement: Alerts Health Sub-Probe (REMOVED)

**Reason**: data-service 的 `/api/v1/health` body 不再包含 `alerts` 字段；`telegram-bot:8300/api/v1/health` 暴露等价 `telegram_api` + `clickhouse_audit` + `chat_rate_limiter` sub-probes。
