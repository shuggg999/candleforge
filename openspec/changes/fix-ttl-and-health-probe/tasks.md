# Tasks: fix-ttl-and-health-probe

## 1. Pre-flight Checks

- [x] 1.1 Grep `src/` 树确认没有任何代码引用 `routes.health_check`（防止删除后断引用）— ZERO 引用，只在 routes.py:359 定义
- [x] 1.2 Grep `src/` 和 `tests/` 确认没有断言 `/api/v1/health` 返回 `{status, timestamp}` 这种简化结构（防止删除后破坏测试）— 只有 routes.py:367 自己返回这个 shape，无测试断言它
- [x] 1.3 实测当前 `curl http://192.168.110.51:8100/api/v1/health` 确认返回简化版（建立 baseline 对比）— HTTP 200 + `{"status":"healthy","timestamp":"2026-05-11T17:38:51.162482+00:00"}`
- [x] 1.4 在 jarvis 上 `clickhouse-client -q "SHOW CREATE TABLE crypto_data.ohlcv_futures"` 抓当前 TTL 表达式存证 — 确认 `WHERE timeframe IN ('15m', '1h')` bug 存在（1h 当前 90 天）

## 2. Write Failing Tests (TDD)

实际放置：跟随现有项目 layout，所有新测试放在 `tests/unit/` 下。

- [x] 2.1 写 `tests/unit/test_health_endpoint.py::test_health_returns_module_subprobes`：断言 `/api/v1/health` 200 时 body.details 至少含 `database` / `classification` / `detection` / `alerts` / `ws_collectors` / `recovery` 六个 key
- [x] 2.2 写 `tests/unit/test_health_endpoint.py::test_health_503_when_classification_failed`：mock classifier.health() 返回 status=failed → 整体应该 503 + body.status=degraded 或 unhealthy
- [x] 2.3 写 `tests/unit/test_health_endpoint.py::test_health_ws_collectors_subprobe_shape`：断言 ws_collectors sub-probe body 含 `expected_count` / `connected_count` / `disconnected_client_keys`
- [x] 2.4 写 `tests/unit/test_health_endpoint.py::test_health_recovery_subprobe_shape`：断言 recovery sub-probe 含 `is_running` / `last_cycle_age_seconds`
- [x] 2.5 写 `tests/unit/test_ttl_init_sql.py::test_init_sql_has_independent_ttl_per_timeframe`：解析 `config/clickhouse/init.sql`，断言 TTL 表达式里没有 `IN ('15m', '1h')` 这种合并形式，每个 timeframe 一条独立子句
- [x] 2.6 写 `tests/unit/test_ttl_init_sql.py::test_init_sql_1h_ttl_is_365_days`：断言 1h 对应 365 天
- [x] 2.7 跑 `pytest tests/unit/test_health_endpoint.py tests/unit/test_ttl_init_sql.py` 全部 FAIL（红） — 验证测试有效（结果：**9 failed, 4 passed**；4 passed 是 1m/5m/4h/1d 这些已经独立 clause 的；唯一额外补强测试 `test_only_one_health_route_registered` 直接抓到 routes.py 重复注册 bug）

## 3. Fix init.sql TTL Expression

- [x] 3.1 编辑 `config/clickhouse/init.sql`，把 `TTL ... timestamp + INTERVAL 90 DAY WHERE timeframe IN ('15m', '1h'), ...` 拆为 6 条独立 WHERE 子句（1m=7, 5m=30, 15m=90, 1h=365, 4h=365, 1d=1825）。注：init.sql 已用 `INTERVAL X DAY` 语法，本次保留该语法不切换 `toIntervalDay()`，与线上表的 `toIntervalDay()` 渲染等价。
- [x] 3.2 跑 `pytest tests/unit/test_ttl_init_sql.py --no-cov` → 8/8 PASSED（含 1m/5m/15m/1h/4h/1d 6 个 parametrized + 1 个独立性 + 1 个 1h-365 回归保护）
- [x] 3.3 写 `scripts/migrations/2026-05-12-fix-1h-ttl.sql` 记录线上 ALTER TABLE 语句，便于贾维斯部署时跑。已包含 usage / verification 注释。

## 4. Apply Production Migration to jarvis

> **暂停：等代码改完测试全绿后再跑。** 用户已被告知本步在 Phase 7 完成后回头确认。

- [ ] 4.1 ssh jarvis 执行 `docker exec -i clickhouse-db clickhouse-client < scripts/migrations/2026-05-12-fix-1h-ttl.sql`（或同步 push gitea → jarvis pull 后跑 file）
- [ ] 4.2 验证：`docker exec clickhouse-db clickhouse-client -q "SHOW CREATE TABLE crypto_data.ohlcv_futures"` 显示 6 条独立 WHERE 子句，1h 对应 365 天
- [ ] 4.3 验证：`docker exec clickhouse-db clickhouse-client -q "SELECT engine_full FROM system.tables WHERE database='crypto_data' AND name='ohlcv_futures' FORMAT Vertical"` 含 toIntervalDay(365) WHERE timeframe = '1h'

## 5. Remove Conflicting /health in routes.py

- [x] 5.1 编辑 `src/api/routes.py`，**删除** 358-388 行的 `@router.get("/health")` 函数和它的实现（连同 docstring + try/except 块），替换为一段 NOTE 注释解释为什么不要在这里加 /health（避坑信号）
- [x] 5.2 跑 `python -c "from src.api.routes import router; print([r.path for r in router.routes])"` 确认 `/health` 不再在 router.routes 里 — 结果：`/health in router.routes: False`，17 个其他 endpoint 完好
- [x] 5.3 跑 `pytest tests/unit/test_health_endpoint.py --no-cov` 看 sub-probe 测试是否开始通过 — 结果：**3 failed, 2 passed**；通过的是 `test_health_503_when_classification_failed` + `test_only_one_health_route_registered`（main.py classification sub-probe + 单 route），剩 3 个等 Phase 6 扩展 ws_collectors + recovery

## 6. Extend main.py /health with ws_collectors and recovery Sub-Probes

- [x] 6.1 在 `src/main.py` health_check 函数中加 ws_collectors + recovery 两个 sub-probe，并提取 `_compute_ws_collectors_health()` / `_compute_recovery_health()` / `_safe_subprobe()` 三个辅助函数（每个 sub-probe 自己处理 None 兜底）。返回 shape：`{status, expected_count, connected_count, disconnected_client_keys}` + `{status, is_running, last_cycle_age_seconds}`
- [x] 6.2 在 `src/services/recovery.py:__init__` 加 `self.last_cycle_ts: Optional[datetime] = None`，在 `start()` 主循环每轮 `_check_and_recover_gaps()` 后更新
- [x] 6.3 用 `_safe_subprobe(name, fn)` 包裹每个 sub-probe，失败返回 `{status: "failed", reason: str(exc)}`；同时 health_check 顶层 try/except 兜 get_status() 异常
- [x] 6.4 跑 `pytest tests/unit/test_health_endpoint.py --no-cov` 全部转绿 — 结果：5/5 PASS。全量回归 `pytest tests/ --no-cov` 65/65 PASS（52 旧 + 13 新，零破坏）

## 7. Update Documentation

- [x] 7.1 编辑 `CLAUDE.md`：删除 "### Main Endpoints" 里 `GET /dashboard` 行（注：本地 main.py:410 实际有该 endpoint + templates/dashboard.html 也存在，但 jarvis 部署 curl 返回 404 说明部署版本不同步；本次 change 不处理实现差异，只去掉 doc 上的明确承诺）+ 在 "### Common Issues" 加 #6 解释 health endpoint 被静默覆盖的避坑信号 + 把 #1 的 health endpoint shape 描述更新为 sub-probe body
- [x] 7.2 grep `CLAUDE.md` 和 `README.md` 没有其他 `/dashboard` 字样 — 结果：zero 匹配
- [x] 7.3 编辑 `.env.example`：确认 TTL_1H=365 注释跟 init.sql 一致 — `.env.example:54` 已经 TTL_1H=365，无需改

## 8. Validation

> **本地 docker 验证已删除**（用户决定 2026-05-12）：本地 Mac 网络访问不到 Binance，且 WS proxy 配置只在 jarvis 上才完整。整体验证只在 jarvis 上做。

- [ ] 8.2 推到 gitea → ssh jarvis pull + rebuild → curl `http://192.168.110.51:8100/api/v1/health` 返回新结构（含 ws_collectors + recovery sub-probe）
- [ ] 8.3 jarvis 上跑 `scripts/migrations/2026-05-12-fix-1h-ttl.sql` 的 ALTER TABLE → SHOW CREATE TABLE 验证 6 条独立 WHERE，1h=365 天
- [x] 8.4 跑全测试套 `pytest tests/ -v`，确认无回归（之前 52 个测试 + 本次新增 13 个）— 结果：**65/65 PASS**，零破坏
- [x] 8.5 跑 `openspec validate fix-ttl-and-health-probe --strict` — 结果：`Change 'fix-ttl-and-health-probe' is valid`

## 9. Archive

- [ ] 9.1 PR / commit 推到 gitea main ← **待用户授权 git push**（一并涵盖 push gitea + 8.2 jarvis deploy）
- [ ] 9.2 `openspec archive fix-ttl-and-health-probe --yes` 把 spec delta 合并到 `openspec/specs/baseline-infrastructure/spec.md`，move change 到 `archive/2026-05-12-fix-ttl-and-health-probe/` ← **待 8.1-8.3 全部验证完毕后**
