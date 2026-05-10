## 1. Dependencies

- [x] 1.1 加 `apscheduler==3.10.4`（或最新稳定版）到 `requirements.txt`
- [x] 1.2 同步加到 `environment.yml` pip section
- [x] 1.3 重建 conda env：`conda env update -f environment.yml --prune`
- [x] 1.4 验证 `python -c "from apscheduler.schedulers.asyncio import AsyncIOScheduler; print('ok')"`

## 2. Module Structure (Detection)

- [x] 2.1 创建 `src/detection/__init__.py`、`src/detection/models.py`（Level Enum、AlertEvent dataclass）
- [x] 2.2 创建 `src/detection/thresholds.py`：默认 dict + `parse_thresholds_env(s: str) -> dict` 解析 `DETECTION_THRESHOLDS` env
- [x] 2.3 创建 `src/detection/detector.py`：`Detector` 类骨架 + `AlertPublisher` Protocol + `LoggingPublisher` 实现
- [x] 2.4 创建 `src/detection/backfill.py`：`ensure_baseline_data` 函数骨架
- [x] 2.5 创建 `src/detection/api.py`：FastAPI router 骨架挂 `/api/v1/detect`

## 3. Scheduler Module

- [x] 3.1 创建 `src/scheduler.py`：`build_scheduler(classifier, detector) -> AsyncIOScheduler`，注册两个 job
- [x] 3.2 实现 `next_5min_boundary() -> datetime` 工具函数
- [x] 3.3 写 `tests/unit/test_scheduler.py`：测 boundary 计算正确、job 注册正确

## 4. Detection Algorithm (TDD)

- [x] 4.1 写 `tests/unit/test_detector.py::test_ratio_computation`：mock SQL 返回，验证 ratio = current/baseline
- [x] 4.2 写 `tests/unit/test_detector.py::test_classify_by_tier`：4 档 × 4 级别 = 16 个 case 矩阵
- [x] 4.3 写 `tests/unit/test_detector.py::test_escalation_detection`：normal→warn 发、warn→strong 发、warn→warn 不发、extreme→warn 不发
- [x] 4.4 写 `tests/unit/test_detector.py::test_skip_low_samples`：sample_count<1200 不出现在结果
- [x] 4.5 写 `tests/unit/test_detector.py::test_skip_no_tier`：classifier.get_tier() 返回 None 跳过
- [x] 4.6 实现 `Detector.detect_once()` 让所有测试通过

## 5. Publisher Protocol

- [x] 5.1 写 `tests/unit/test_detector.py::test_publisher_called_per_cycle`：mock publisher，断言 publish 被调用一次（可空 list）
- [x] 5.2 写 `tests/unit/test_detector.py::test_logging_publisher_logs_events`：用 caplog 验证 LoggingPublisher 输出 JSON
- [x] 5.3 实现 LoggingPublisher

## 6. Backfill (TDD)

- [x] 6.1 写 `tests/unit/test_backfill.py::test_skips_if_data_complete`：mock ClickHouse 返回完整 24h，断言不调用 binance API
- [x] 6.2 写 `tests/unit/test_backfill.py::test_fetches_missing_symbols`：mock 缺数据的 symbol，断言对应 binance 调用
- [x] 6.3 写 `tests/unit/test_backfill.py::test_respects_rate_limit`：用 time.monotonic 测速率 ≤ 10 req/sec
- [x] 6.4 实现 `ensure_baseline_data` 全流程

## 7. SQL & ClickHouse Integration

- [x] 7.1 在 `detector.py` 顶部定义 `DETECTION_SQL` 字符串常量
- [x] 7.2 写 `tests/integration/test_detection_e2e.py::test_sql_executes_correctly`：用 testcontainers 起 ClickHouse + 灌入 mock ohlcv_futures + 跑 SQL + 断言聚合结果
- [x] 7.3 跑 SQL 性能基准：mock 475 symbols × 1440 行，验证 detect_once() < 1 秒

## 8. Lifespan Wiring & Bootstrap

- [x] 8.1 修改 `src/main.py` lifespan：startup 顺序 = ClickHouse 连接 → collector 启动 → backfill 跑完 → classifier.refresh_tiers 首次 → scheduler.start → "ready" 状态
- [x] 8.2 修改 lifespan shutdown：scheduler.shutdown(wait=True) → classifier 清理 → collector 停止 → ClickHouse 连接释放
- [x] 8.3 注入 `LoggingPublisher` 实例给 detector（alerts change 会替换）
- [x] 8.4 验证 `python -c "from src.main import app"` 不抛异常

## 9. Health Sub-probe

- [x] 9.1 在 `Detector` 类暴露 `health()` 方法
- [x] 9.2 修改 `/api/v1/health` 聚合 detection + bootstrap_progress 字段
- [x] 9.3 写测试：startup 期 / running 期 / stale 期的健康响应

## 10. Inspection Endpoints

- [x] 10.1 实现 `POST /api/v1/detect/run` 手动触发
- [x] 10.2 实现 `GET /api/v1/detect/state`
- [x] 10.3 实现 `GET /api/v1/detect/thresholds`
- [x] 10.4 写集成测试 `test_endpoints_e2e`

## 11. Configuration

- [x] 11.1 在 `src/config.py` 加 6 个 env vars（DETECTION_INTERVAL_MINUTES / DETECTION_THRESHOLDS / DETECTION_BASELINE_HOURS / DETECTION_CURRENT_MINUTES / DETECTION_MIN_SAMPLES / BACKFILL_BATCH_RPS）
- [x] 11.2 在 `.env.example` 加对应项 + 注释
- [x] 11.3 写测试：env override 后阈值表正确改变

## 12. Validate + Commit + Archive

- [x] 12.1 跑全套 `pytest tests/`，必须 PASS
- [x] 12.2 跑 `openspec validate add-volume-detection --strict`
- [x] 12.3 commit message：`feat(detection): add volume anomaly detector + scheduler + backfill`
- [x] 12.4 跑 `openspec archive add-volume-detection -y`
- [x] 12.5 二次 commit + git status 干净
