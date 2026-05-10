## 1. ClickHouse Schema

- [x] 1.1 在 `config/clickhouse/init.sql` 追加 `symbol_tiers` 表 DDL（ReplacingMergeTree, PRIMARY KEY (exchange, symbol, refreshed_at), TTL 90 day）
- [x] 1.2 在 docker-compose 测试环境重建 ClickHouse 容器，验证表能创建（`SHOW CREATE TABLE symbol_tiers`）

## 2. Module Structure

- [x] 2.1 创建 `src/classification/__init__.py`、`src/classification/models.py`（TierName Literal + TierStats dataclass）
- [x] 2.2 创建 `src/classification/classifier.py` 框架：`Classifier` 类骨架 + `__init__` 持 ClickHouse client + 空 cache + 状态字段
- [x] 2.3 创建 `src/classification/api.py`：FastAPI APIRouter 骨架，挂 `/api/v1/classification` prefix

## 3. Core Algorithm (TDD)

- [x] 3.1 写 `tests/unit/test_classifier.py::test_assigns_tier_per_quantile_cut`：mock 输入 → 期望 4 档归属正确
- [x] 3.2 写 `tests/unit/test_classifier.py::test_zero_volume_symbol_excluded`：零成交 symbol 不进 cache
- [x] 3.3 实现 `Classifier._compute_tiers(volumes_dict)` 让上述测试通过
- [x] 3.4 写测试 `test_p25_p50_p75_calculation`：边界（symbols 数量=4 / =0 / 全相等）
- [x] 3.5 实现分位数计算（用 `numpy.percentile` 或 `statistics.quantiles`，二选一更轻量者）

## 4. ClickHouse Integration

- [x] 4.1 写 `tests/unit/test_classifier.py::test_refresh_queries_24h_window`：mock ClickHouse 返回，断言 SQL 包含 `INTERVAL 24 HOUR` + `timeframe = '1m'`
- [x] 4.2 实现 `Classifier.refresh_tiers()` 完整流程：fetch → compute → batch insert → atomic cache swap
- [x] 4.3 写 `tests/unit/test_classifier.py::test_refresh_failure_preserves_cache`：第二次 refresh 抛异常，cache 仍是第一次的值
- [x] 4.4 实现错误恢复：try/except 包 SQL，失败时 raise + 不动 cache + log error

## 5. Lifespan Wiring

- [x] 5.1 在 `src/main.py` 加 `build_classifier()` 调用 + 在 lifespan startup 跑首次 `await classifier.refresh_tiers()`（失败时 log warning，不阻塞 startup）
- [x] 5.2 在 lifespan shutdown 调用 `await classifier.shutdown()`（清空 cache，关闭独占资源）
- [x] 5.3 在 `src/api/routes.py` include classification router
- [x] 5.4 验证 `python -c "from src.main import app"` 不抛异常（baseline-infrastructure 第 7 条）

## 6. Health Sub-probe

- [x] 6.1 在 `Classifier` 类暴露 `health()` 方法返回 dict（status / last_refresh / next_refresh / symbol_count / p25 / p50 / p75）
- [x] 6.2 修改 `/api/v1/health` endpoint 响应聚合 `classification` 子键
- [x] 6.3 写测试 `tests/unit/test_classifier.py::test_health_reports_degraded_when_stale`：last_refresh > 2 × interval 时 status='degraded'

## 7. Inspection Endpoint

- [x] 7.1 在 `src/classification/api.py` 实现 `GET /api/v1/classification/tiers` 返回 `dict[symbol, tier]`
- [x] 7.2 写测试 `tests/integration/test_classification_e2e.py::test_tiers_endpoint_returns_full_mapping`

## 8. Configuration

- [x] 8.1 在 `src/config.py` 加 `CLASSIFICATION_REFRESH_HOURS: int = Field(default=12, env="CLASSIFICATION_REFRESH_HOURS")`
- [x] 8.2 在 `.env.example` 加 `CLASSIFICATION_REFRESH_HOURS=12` 一项 + 注释

## 9. Integration Test

- [x] 9.1 写 `tests/integration/test_classification_e2e.py`：用 testcontainers 起 ClickHouse → 灌入 mock ohlcv_futures 数据 → 跑 `refresh_tiers()` → 断言 `symbol_tiers` 表内容 + cache dict 内容 + `/health` 响应
- [x] 9.2 跑全套 `pytest tests/`，必须 PASS

## 10. Validate + Commit + Archive

- [x] 10.1 跑 `openspec validate add-volume-classification --strict`，必须 PASS
- [ ] 10.2 git add + commit message 含 `feat(classification): add volume tier classifier (add-volume-classification)`
- [ ] 10.3 跑 `openspec archive add-volume-classification -y`，spec delta 合入 `openspec/specs/volume-classification/`
- [ ] 10.4 二次 commit 包含归档移动
- [ ] 10.5 `git status` 确认工作树彻底干净
