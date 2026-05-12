## REMOVED Requirements

整 capability `volume-detection` 从 `data-service` 仓库下线。所有 requirement 均移除，对应代码 `src/detection/` + `src/scheduler.py` 删除。

业务能力 SHALL 由独立 repo `volume-monitor` 提供，详见 `volume-monitor/openspec/changes/bootstrap-volume-monitor/specs/volume-detection/spec.md`。

### Requirement: Volume Anomaly Detection Cycle (REMOVED)

**Reason**: 拆分到 `volume-monitor` 仓 — 该 repo 已实现等价 5 分钟 cron detection cycle，从 NATS 订阅 K-line 事件 + 从 ClickHouse 读 baseline 计算 z-score。

**Migration**: data-service 不再暴露 `/api/v1/detection/*` 端点；下游消费者 SHALL 改 query `volume-monitor:8200/api/v1/detection/recent` 或订阅 telegram-bot 推送的告警。

### Requirement: Threshold Configuration (REMOVED)

**Reason**: `DETECTION_THRESHOLDS` 等环境变量改由 `volume-monitor/.env.example` 持有，data-service 不再解析。

### Requirement: Baseline Backfill Job (REMOVED)

**Reason**: 由 `volume-monitor` 启动时 backfill 接管（直读 ClickHouse `ohlcv_futures`，不依赖 NATS history）。

### Requirement: Detection Health Sub-Probe (REMOVED)

**Reason**: data-service 的 `/api/v1/health` body 不再包含 `detection` 字段；`volume-monitor:8200/api/v1/health` 暴露等价 `detector` + `scheduler` sub-probe。

### Requirement: Alert Publish Hook (REMOVED)

**Reason**: 旧的 process-internal `AlertPublisher` 接口（detector → notifier）由 NATS-decoupled `webhook_client` 替代，跨进程发到 telegram-bot:8000/alerts。
