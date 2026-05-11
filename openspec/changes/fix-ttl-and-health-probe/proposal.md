## Why

2026-05-12 实战部署后发现两个底座缺陷：

1. **`ohlcv_futures` 表 1h TTL 跟 15m 一起设了 90 天**（init.sql 写法 `WHERE timeframe IN ('15m', '1h')`），但 `.env.example` / `src/config.py` 一直声明 `TTL_1H=365`。线上行为与文档/配置不一致 —— 1h K 线只能保留 90 天而不是预期的 365 天，影响后续业务消费方做长周期 baseline。

2. **`GET /api/v1/health` 只返回 `{status, timestamp}`，缺所有 sub-probe**。`src/main.py:424` 已经写好了完整的 classification / detection / alerts sub-probe 实现，但 `src/api/routes.py:358` 一个简化版 router 同名 endpoint 先注册，**FastAPI 默认先注册者优先 → main.py 的完整实现被静默覆盖**。结果是：违反了 `baseline-infrastructure` spec 已有的 **Module Wiring Convention** 要求（health endpoint 必须含每模块 sub-key），运维无法从 /health 得到子系统状态。

3. **`CLAUDE.md` 提到 `/dashboard` 是 Web UI**，但 `src/api/routes.py` 和 `src/main.py` 都不包含该路由 —— 是历史遗留 stale 描述，误导未来 onboarding。

这三个问题都是当前 baseline 的"实现违反 spec"或"文档与实现不一致"类型的小修复，不引入新业务能力。本次 change 把它们合并为单个独立小通道，与正在 brainstorm 中的"data-service 拆分"重构**并行不阻塞**。

## What Changes

- **修** `config/clickhouse/init.sql`：把 TTL 表达式里 `WHERE timeframe IN ('15m', '1h')` 拆成两条 —— `WHERE timeframe = '15m'` 保留 90 天 + `WHERE timeframe = '1h'` 改为 365 天，与 `.env.example` 声明对齐
- **修** 线上 ClickHouse `ohlcv_futures` 表：通过 `ALTER TABLE ... MODIFY TTL ...` 修复现有部署的 TTL（init.sql 只影响新部署，存量表不重建）
- **修** `src/api/routes.py:358-388`：**删除**简化版 `/health` endpoint，让 `src/main.py:424` 的完整 sub-probe 版本接管路由
- **扩展** `src/main.py:424` health endpoint：在已有 `classification` / `detection` / `alerts` sub-probe 基础上补充 `ws_collectors` sub-probe（暴露每个 WS connection 的活跃数 + 最近一次消息时间）和 `recovery` sub-probe（暴露 recovery service 是否在跑 + 最近 cycle 时间）
- **删** `CLAUDE.md` 里 `/dashboard` 字样（API Endpoints 段 + Troubleshooting 段都有），改为标注"未实现"或直接删除该行
- **不动**：业务逻辑、collector 行为、recovery 算法、detection 阈值 —— 严格只修 spec 违规 + 文档 stale

## Capabilities

### Modified Capabilities

- `baseline-infrastructure`:
  - **新增** Requirement: Per-Timeframe TTL Configuration（明确每个 timeframe 的 TTL 必须独立可配置，避免 init.sql 把多个 timeframe 绑在同一行 `IN (...)` 导致改一个误伤一个）
  - **强化** Requirement: Module Wiring Convention（已有，但加入 Scenario：当业务模块已 wire 进 main.py lifespan 但 health endpoint 被 router 简化版覆盖时，视为违反此 requirement）

## Impact

**Affected code**:
- `config/clickhouse/init.sql`（拆 TTL 表达式）
- `src/api/routes.py`（删除 358-388 简化 health endpoint）
- `src/main.py`（扩展 424-489 health endpoint，加 ws_collectors / recovery 两个 sub-probe）
- `CLAUDE.md`（删 stale /dashboard 描述）

**Affected production data**:
- 线上 jarvis 部署 `ohlcv_futures` 表需要跑一次 `ALTER TABLE crypto_data.ohlcv_futures MODIFY TTL ...` 语句修正存量表的 TTL（只影响 TTL 元数据，不重写数据，秒级完成）

**Affected APIs**:
- `GET /api/v1/health` 返回 body 结构变化：从 `{status, timestamp}` → `{status, details: {database, classification, detection, alerts, ws_collectors, recovery, ...}}`。如果有外部 monitoring（如 Prometheus exporter / uptime-kuma）依赖旧 body，需要同步更新（**当前无已知外部依赖**）

**Affected systems**:
- jarvis 上 docker compose 的 `data-service` healthcheck（`curl -f http://localhost:8000/api/v1/health`）仍然只看 HTTP status code，**新 body 完全向后兼容**

**Risk**: 低。
- TTL 改动：纯元数据修改，不动数据。回滚 = 再跑一次 ALTER 改回去
- /health 改动：返回的 sub-probe 是新增字段，HTTP status 行为不变（200/503）。如果新增 sub-probe 出 bug，最多让 health 报 degraded，不会让服务 crash
- 文档改动：零生产影响
