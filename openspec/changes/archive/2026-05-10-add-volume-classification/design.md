## Context

本 change 是"成交量异常监控"系统的第一个业务模块，下游 detection 与 alerts 都依赖它输出的"symbol → tier"映射来选择阈值。算法层面的所有"为什么"（中位数 vs 均值、24h vs 7d、quoteVolume vs OI vs 市值、4 档 vs 5 档 vs k-means）已经在 `docs/superpowers/specs/2026-05-10-volume-anomaly-monitor-design.md` 第 3 节 + 第 10 节决策追溯里详细论证。

本 design.md 只覆盖**模块内部的工程决策**：文件结构、类设计、错误恢复、ClickHouse 表设计的工程取舍。

## Goals / Non-Goals

**Goals**：
- 让 detection 模块通过 O(1) 内存查询拿 tier，不在热路径触发 ClickHouse query
- 历史 tier 可回溯（解释"为什么这条告警当时是 strong 而不是 extreme"）
- 模块完全可独立部署 / 可独立 disable（未来可能加 feature flag）
- 严格遵守 baseline-infrastructure 第 7 条：import 时不触发副作用、startup 才连接、shutdown 优雅关闭

**Non-Goals**：
- 不实现 detection / alerts（独立 change）
- 不实现 APScheduler 集成（独立 change，但 classifier 暴露 `refresh_tiers` 协程供 scheduler 直接调用）
- 不支持运行时改 tier 数（始终 4 档）
- 不实现 tier 变化的事件通知（detection 直接每次读 cache，无需事件机制）

## Decisions

### Decision 1：模块内部文件布局

**选**：`src/classification/{__init__.py, classifier.py, models.py, api.py}`

- `__init__.py`：暴露 `Classifier`、`build_classifier()` 工厂函数，外部统一从这里 import
- `classifier.py`：`Classifier` 类（持 ClickHouse client + tier_cache + last_refresh 状态）
- `models.py`：`TierName` Literal、`TierStats` dataclass（封装 P25/P50/P75 + 各档 count）
- `api.py`：FastAPI router，挂 `/api/v1/classification/*`

**不选**：单文件 `classification.py`、或更细粒度（`refresher.py` / `cache.py` / `repository.py`）

**理由**：4 个文件够用，单文件容易膨胀、过细分割对一个 ~300 行的模块 over-engineering。models 拆出来是因为 detection 模块也要 import `TierName`，避免 circular import。

### Decision 2：tier_cache 数据结构与并发

**选**：`dict[str, str]`，在 `refresh_tiers()` 末尾用 `self._cache = new_cache` 整体替换。

**不选**：`asyncio.Lock` 保护的 dict、或 `dict.update()` 增量更新

**理由**：
- Python 字典赋值是原子的（GIL 保证），整体替换不需要锁
- 增量更新会有"中间状态"问题（部分 symbol 已更新、部分还没）
- detection 读 cache 走 `dict.get()`，多线程场景也是线程安全的

### Decision 3：ClickHouse `symbol_tiers` 表的 ENGINE 选择

**选**：`ReplacingMergeTree(refreshed_at)` PRIMARY KEY `(exchange, symbol, refreshed_at)`

**不选**：
- A. `MergeTree`：每次 refresh 都全表 INSERT，没去重，30 天后表可能膨胀到 1700 万行
- B. `CollapsingMergeTree`：需要每行带 sign=+1/-1，业务上不需要"撤回"语义，过度复杂

**理由**：ReplacingMergeTree 的 `refreshed_at` 作为 version 字段，相同 `(exchange, symbol)` 但 `refreshed_at` 不同的行被合并（保留最大 refreshed_at）。但因为 `refreshed_at` 在 PRIMARY KEY 里，所以"同一 symbol 的不同 refresh 版本"不被合并 —— 完美匹配"想保留每次 refresh 历史 + 同时点查最新值"的诉求。

### Decision 4：refresh 失败的恢复策略

**选**：单次 refresh 内部不重试；失败时 raise，让调度器记日志，下个周期重试。tier_cache 保持上一轮值。

**不选**：tenacity 在 refresh_tiers 内部包 3 次重试

**理由**：
- 12 小时一次的低频任务，单次失败"等下个周期重试"的延迟（最坏 24h）可接受
- 把重试委托给调度层，模块内逻辑更纯粹
- 如果 ClickHouse 真挂了，重试 3 次也救不回来

### Decision 5：`/api/v1/classification/tiers` endpoint 是否分页

**选**：不分页，全量返回 ~475 entries 的 JSON object。

**理由**：单次请求 payload ~10 KB，不需要分页；这个 endpoint 是给运维和集成测试看的，不是高频用户接口。如果未来要做"前端分页 UI"再加 query string。

### Decision 6：startup 顺序 — 何时跑首次 refresh

**选**：lifespan startup 阶段，在 collector 启动**之后**、在 APScheduler 启动**之前**触发首次 `refresh_tiers()`。

**不选 A**：startup 立即跑（collector 还没启动，ClickHouse 可能没有 24h 数据）
**不选 B**：让 APScheduler 第一次 trigger 时才跑（首次延迟 12h，太久没法用）

**理由**：
- collector 启动后会自动用 backfill 补足 24h 数据（backfill 模块在 add-volume-detection change 里加，本 change 启动时如果数据不足、refresh 自然 SQL 返回少量 symbol，标 degraded 即可）
- 在 APScheduler 启动前显式跑一次，保证 detection 启动时 cache 是热的

## Risks / Trade-offs

- **[Risk]** ClickHouse `symbol_tiers` 表 90 天 TTL 后，回溯时间窗口受限 → **Mitigation**：90 天对正常调试 + 月度复盘够用；真要更长可改 `.env` / DDL，是配置问题不是设计问题
- **[Risk]** 首次部署 / backfill 期间 cache 是空的，detection 跳过所有 symbol → **Mitigation**：health 子探针 `degraded`，运维可见；backfill 完成后下一次 refresh 自动恢复
- **[Risk]** ReplacingMergeTree 合并是异步的，刚 INSERT 的数据可能查询时还没 dedup → **Mitigation**：所有查询都用 `argMax(tier, refreshed_at)` 显式取最新；不依赖隐式 dedup
- **[Trade-off]** 不缓存 `symbol_tiers` 历史数据到内存，每次 endpoint 调用都查 ClickHouse → 接受，这个 endpoint 不是热路径
- **[Trade-off]** 不实现"tier 变化时通知"机制 → detection 每 5 分钟自然轮询 cache 拿最新值，1 个 cycle 的延迟可接受

## Migration Plan

1. 加 ClickHouse 表 DDL 到 `config/clickhouse/init.sql`，在 docker compose 重启时自动 apply（已有的 init pattern）
2. 实现 `src/classification/` 模块代码（TDD：先 unit tests 后实现）
3. 修改 `src/main.py` 在 lifespan startup 注册 classifier；扩展 `/health` 聚合
4. 在 `src/api/routes.py` 挂 classification router
5. 跑集成测试（用 testcontainers ClickHouse）
6. 部署到贾维斯，观察 12h 内自动跑 1 次 refresh
7. archive change

**Rollback**：`git revert` 即可。`symbol_tiers` 表保留无害（无人查就空闲）；`/health` 字段缺失下游不会崩。

## Open Questions

- 是否需要让 classifier 支持"按需触发 refresh"的 admin endpoint（POST /api/v1/classification/refresh）？倾向延后到第一次发现"等不及 12h 重算"的实际需求时再加
- 阈值刷新失败累计多少次后从 degraded 升级到 failed？暂定 `2 × CLASSIFICATION_REFRESH_HOURS` 没有成功 refresh 即 degraded；连续 3 次失败 → failed。是否合适等部署后调
