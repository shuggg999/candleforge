## REMOVED Requirements

整 capability `volume-detection` 从 `data-service` 仓库下线 — 等价 5 分钟 cron detection cycle 由独立 repo `volume-monitor` 接管（订阅 NATS K-line 事件 + ClickHouse baseline z-score 计算）。data-service 不再暴露 `/api/v1/detection/*`。

### Requirement: Periodic Detection Cycle

**Reason**: 拆分到 `volume-monitor` 仓 — 该 repo 已实现等价 5 分钟 cron detection cycle，详见 `volume-monitor/openspec/changes/bootstrap-volume-monitor/specs/volume-detection/`。

### Requirement: Ratio Computation

**Reason**: ratio / z-score / baseline 计算移到 `volume-monitor`；数学逻辑 1:1 移植。

### Requirement: Tier-Based Threshold Classification

**Reason**: 阈值表配置（`DETECTION_THRESHOLDS` 等）改由 `volume-monitor/.env.example` 持有，data-service 不再解析。

### Requirement: Cross-Cycle State Comparison (Escalation)

**Reason**: 升降级状态由 `volume-monitor` 内 `AlertStateStore` 持有（per-symbol per-tier）。

### Requirement: AlertEvent Contract

**Reason**: 旧的 process-internal `AlertEvent` Python 对象由 NATS-decoupled webhook JSON payload 替代，跨进程发到 `telegram-bot:8000/alerts`。

### Requirement: Pluggable Publisher

**Reason**: 旧 process-internal `AlertPublisher` 接口（detector → notifier）由 `volume-monitor` 内 `webhook_client` 替代（HTTP POST + tenacity 重试）。

### Requirement: Cold-Start Backfill

**Reason**: backfill 由 `volume-monitor` 启动时直读 ClickHouse `ohlcv_futures` 接管，不依赖 NATS history。

### Requirement: Health Sub-probe

**Reason**: data-service 的 `/api/v1/health` body 不再包含 `detection` 字段；`volume-monitor:8200/api/v1/health` 暴露等价 `detector` + `scheduler` sub-probe。

### Requirement: Inspection Endpoints

**Reason**: `/api/v1/detection/*` 等检查端点从 data-service 下线（返回 404）；`volume-monitor:8200/api/v1/detection/*` 提供等价端点。
