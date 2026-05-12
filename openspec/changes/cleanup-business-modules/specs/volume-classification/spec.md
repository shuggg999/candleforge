## REMOVED Requirements

整 capability `volume-classification` 从 `data-service` 仓库下线。所有 requirement 均移除，对应代码 `src/classification/` 整目录删除。

业务能力 SHALL 由独立 repo `volume-monitor` 提供，详见 `volume-monitor/openspec/changes/bootstrap-volume-monitor/specs/volume-classification/spec.md`。

### Requirement: Symbol Tier Classification (REMOVED)

**Reason**: 拆分到 `volume-monitor` 仓 — 该 repo 已实现等价能力，订阅 NATS K-line 事件并维护 24h 滚动 quote-volume 计算 + 12h 周期分级刷新。

**Migration**: data-service 不再暴露 `/api/v1/tiers` 端点；下游消费者 SHALL 直接从 `volume-monitor` 服务（jarvis:8200）查询分级结果，或订阅 NATS subject `tier.refresh.>` 接收变更事件。

### Requirement: Tier Refresh Schedule (REMOVED)

**Reason**: 由 `volume-monitor` 内 APScheduler 12h cron job 接管。

### Requirement: Classification Health Sub-Probe (REMOVED)

**Reason**: data-service 的 `/api/v1/health` body 不再包含 `classification` 字段；`volume-monitor:8200/api/v1/health` 暴露等价 `classifier` sub-probe。
