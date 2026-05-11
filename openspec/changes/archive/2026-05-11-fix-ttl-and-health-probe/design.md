# Design: fix-ttl-and-health-probe

## Context

2026-05-12 复盘当前 jarvis 部署，发现 baseline-infrastructure spec 已经写好的两条规约**在实现层面被违反**或**配置不一致**。本 change 不引入新能力，只把"实现"与"已有 spec + 已有 .env 配置"对齐。

## Goals

1. 修复 1h K 线 TTL 与 .env 声明一致（90 → 365 天）
2. 让 `/api/v1/health` 返回 baseline-infrastructure spec 要求的 module sub-keys
3. 删 stale `/dashboard` 文档描述

## Non-Goals

- 不重新设计 health endpoint 返回 schema（沿用 main.py 已有结构）
- 不引入新的监控指标（如 Prometheus exporter）
- 不修复其他已知小问题（如 dashboard 真要实现、stream_name 命名一致性等）—— 不在本 change scope

## Decision 1: 用 ALTER TABLE 修线上表 TTL，而不是重建表

**选项 A（采纳）**：`ALTER TABLE crypto_data.ohlcv_futures MODIFY TTL <new_expr>`
- 优点：秒级完成，只改元数据，零数据丢失风险
- 缺点：必须手动跑一次（在 tasks.md 写入操作步骤）

**选项 B（弃用）**：drop + recreate
- 优点：表结构完全跟 init.sql 一致
- 缺点：丢失现有 1.1M 行数据 + 需要 recovery 重新拉

**选项 C（弃用）**：让 data-service 启动时自动检测 TTL 不一致 + 自动 ALTER
- 优点：自愈
- 缺点：超出本次 scope，引入额外启动副作用。留作未来增强

## Decision 2: 删 routes.py 简化版 /health，保留 main.py 完整版

**当前路径冲突**：
- `src/api/routes.py:358` — `@router.get("/health")`，router prefix `/api/v1` → 实际 `/api/v1/health`
- `src/main.py:424` — `@app.get("/api/v1/health")` 直接挂 app

FastAPI 注册顺序：`main.py` 里 `app.include_router(router, prefix="/api/v1")` 在前，`@app.get(...)` 在后。**实测**返回简化版 → routes.py 的版本赢了。

**选项 A（采纳）**：删 routes.py:358-388，让 main.py 的 sub-probe 版接管
- main.py 版本已经写好 classification / detection / alerts sub-probe，扩展两个新 sub-probe 即可
- 一处实现，无歧义

**选项 B（弃用）**：把 main.py 的 sub-probe 逻辑搬到 routes.py，删 main.py:424
- 多此一举，main.py 的 lifespan 已经能拿到 service 实例，搬到 routes.py 还要再走 dependency injection

## Decision 3: 新增的 sub-probe 不引入新轮询，只读 in-memory 状态

`ws_collectors` 和 `recovery` 的 health 必须**非阻塞**：

| Sub-probe | 数据来源 | 判定逻辑 |
|---|---|---|
| `ws_collectors` | `collector_manager.collectors[*].websocket_clients` (dict) + `.connection_health` (in-memory dict) | `connected_count == expected_count` → `ok`；否则 `degraded`，body 列出 disconnected client_keys |
| `recovery` | `recovery_service.is_running` + `recovery_service.last_cycle_ts`（in-memory，需要新增字段） | `is_running and (now - last_cycle_ts) < 5min` → `ok`；超时 → `degraded` |

**为什么不查 ClickHouse**：health endpoint 必须毫秒级响应，不能每次都跑 SQL。in-memory 检查足够。

## Decision 4: Per-Timeframe TTL Requirement 加进 baseline-infrastructure spec

防止下一个开发者再把多个 timeframe 绑成 `IN (...)` 引起 1h 那种 bug。spec 用 SHALL 措辞要求 init.sql 的 TTL 子句**每个 timeframe 一条独立表达式**。

## Risks & Mitigations

| 风险 | 缓解 |
|---|---|
| ALTER TABLE 失败 / 锁表 | `ALTER ... MODIFY TTL` 在 ClickHouse 是元数据 op，不锁数据；如果失败，回滚 = 不动现有表（init.sql 修复保证下次部署正确） |
| 新 sub-probe 抛异常导致整个 /health 503 | 每个 sub-probe 单独 try/except，失败时返回 `{status: "failed", reason: ...}` 而非 raise |
| routes.py 删 /health 影响其他内部调用 | grep 确认没有其他地方调 `routes.health_check`（**已确认 zero match**） |
| dashboard 文档删除影响外部读者 | CLAUDE.md 是项目说明，明确标注"未实现"或直接删行更诚实 |

## Open Questions

无 — 4 个决策点全部由当前信息可定。
