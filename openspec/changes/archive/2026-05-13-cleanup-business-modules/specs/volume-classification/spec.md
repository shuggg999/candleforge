## REMOVED Requirements

整 capability `volume-classification` 从 `data-service` 仓库下线 — 等价能力由独立 repo `volume-monitor` 提供（订阅 NATS K-line 事件 + 12h cron 周期刷新分级）。data-service 不再暴露 `/api/v1/tiers`，下游 SHALL 改 query `volume-monitor:8200`。

### Requirement: Tier Computation from Recent Trading Volume

**Reason**: 拆分到 `volume-monitor` 仓 — 该 repo 已实现等价能力，详见 `volume-monitor/openspec/changes/bootstrap-volume-monitor/specs/volume-classification/`。

**Migration**: data-service 不再计算分级；`volume-monitor` 直读 ClickHouse `ohlcv_futures` 计算 24h quote-volume 排序 + P25/P50/P75 分桶。

### Requirement: Refresh Cadence

**Reason**: 由 `volume-monitor` 内 APScheduler 12h cron job 接管，`CLASSIFICATION_REFRESH_HOURS` 配置移到 `volume-monitor/.env.example`。

### Requirement: Persistent History Snapshot

**Reason**: 历史快照表（如有）保留物理存在但 data-service 不再写入；`volume-monitor` 自行决定持久化策略。

### Requirement: In-Memory Tier Lookup

**Reason**: tier 查询 API 移到 `volume-monitor:8200` 暴露；data-service 进程内不再持有 classifier singleton。

### Requirement: Refresh Resilience

**Reason**: 由 `volume-monitor` 接管刷新流程的容错与重试。

### Requirement: Health Sub-probe

**Reason**: data-service 的 `/api/v1/health` body 不再包含 `classification` 字段；`volume-monitor:8200/api/v1/health` 暴露等价 `classifier` sub-probe。

### Requirement: Inspection Endpoint

**Reason**: `/api/v1/tiers` 等检查端点从 data-service 下线（返回 404）；`volume-monitor:8200/api/v1/tiers` 提供等价端点。
