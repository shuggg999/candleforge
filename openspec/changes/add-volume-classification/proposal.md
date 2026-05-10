## Why

成交量异常监控的下游模块（detection / alerts）需要按"流动性档位"配置不同的告警阈值（小盘币 5 倍可能日常、大盘币 2 倍就值得关注）。本 change 引入 `volume-classification` capability —— 把所有币安 USDT 永续合约按过去 24 小时的 `quote_volume` 总量分到 4 档（mega / large / mid / small），每 12 小时刷新，结果通过内存 dict 暴露给 detection 消费、通过 ClickHouse `symbol_tiers` 表保留历史快照供回溯。

算法选型与档位定义详见 `docs/superpowers/specs/2026-05-10-volume-anomaly-monitor-design.md` 第 3 节 Classification 部分及第 10 节决策追溯。

## What Changes

- **新增** `src/classification/` 模块，包含 `Classifier` 类和 module-level factory，对外只暴露 `classifier.get_tier(symbol) -> str | None` 与 `classifier.refresh_tiers() -> dict[str, str]`
- **新增** ClickHouse 表 `symbol_tiers`（ReplacingMergeTree，TTL 90 天）—— 由 `config/clickhouse/init.sql` 创建
- **接入** `src/main.py` 的 FastAPI lifespan：startup 注册 classifier instance、首次刷新；shutdown 优雅停止
- **新增** `/api/v1/health` 子键 `classification`：暴露 last_refresh / next_refresh / 各档 symbol 数量 / 当时 P25/P50/P75
- **新增** `/api/v1/classification/tiers` GET 端点：返回当前 symbol → tier 字典（运维/调试用）
- **新增** `.env.example` 项 `CLASSIFICATION_REFRESH_HOURS=12`（可调节刷新频率）
- **NOT IN SCOPE**：detection / alerts 模块的实现（独立 change）；APScheduler 集成（独立 change，但本 change 暴露 `refresh_tiers` 协程让 scheduler 调用）；从 OKX/Bybit 等其他交易所扩展

## Capabilities

### New Capabilities

- `volume-classification`: 把币安 USDT 永续合约按 24h quote_volume 总量分到 4 个流动性档位，定期刷新，让下游模块按档配阈值。

### Modified Capabilities

- `baseline-infrastructure`: 不修改 requirement，但本 change 是 baseline-infrastructure 第 7 条 Module Wiring Convention 的首个具体实现，验证该约定可执行

## Impact

**Affected code**:
- 新增 `src/classification/__init__.py`、`src/classification/classifier.py`、`src/classification/api.py`
- 修改 `src/main.py` lifespan 注册 classifier
- 修改 `src/api/routes.py` 聚合 classification 子健康 + 挂 tiers endpoint
- 修改 `config/clickhouse/init.sql` 加 `symbol_tiers` 表 DDL
- 修改 `.env.example` 加 `CLASSIFICATION_REFRESH_HOURS`
- 新增 `tests/unit/test_classifier.py` + `tests/integration/test_classification_e2e.py`

**Affected APIs**: 新增 `GET /api/v1/classification/tiers`；扩展 `GET /api/v1/health` 响应

**Affected dependencies**: 无（依赖 baseline 已引入的 aiochclient / pydantic-settings / loguru）

**Risk**: 低。本 change 不改交易、不发外部消息、不动现有 collector 逻辑。最坏情况是 `symbol_tiers` 表里数据不准，detection 还没接入，无业务影响。
