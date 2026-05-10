## Why

成交量异常监控的核心检测逻辑：每 5 分钟扫描一遍所有币安 USDT 永续合约，找出"`5min 当前均量 / 24h 中位数`倍数超过当前流动性档位阈值"且"比上一轮级别更高（首次进档或升级）"的事件。本 change 引入 `volume-detection` capability —— 实现检测算法 + 内存状态比对 + 可热替换的阈值表，并把检测产出的 `AlertEvent` 列表通过依赖注入的 publisher 传给下游（alerts 模块在独立 change 实现）。

本 change 还顺带引入两块跨模块基础设施：**APScheduler 调度器**（首个有周期任务的 change）+ **冷启动 backfill**（detection 需要 24h 历史数据才能算中位数基线）。

算法选型与"为什么是中位数 + 倍数 + 档位调阈值"详见 `docs/superpowers/specs/2026-05-10-volume-anomaly-monitor-design.md` 第 3 节 Detection 部分及第 10 节决策追溯。

## What Changes

- **新增** `src/detection/` 模块：`Detector` 类、`AlertEvent` dataclass、`Level` enum、`thresholds.py` 阈值表、`detection/api.py` 暴露 endpoints
- **新增** `src/detection/backfill.py`：启动时检测 ClickHouse 是否有过去 24h 完整数据，不够则按批 (10 req/sec) 从币安 `/fapi/v1/klines` REST 拉，~5-10 分钟完成
- **新增** `src/scheduler.py`：APScheduler 封装，在 `main.py` lifespan 里启动，注册两个 job：
  - `detector.detect_and_publish` interval=5min（对齐时钟整点，max_instances=1 防重入）
  - `classifier.refresh_tiers` interval=12h（替代 classification change 里的"仅 startup 跑一次"做法）
- **新增** `AlertPublisher` Protocol + 默认 `LoggingPublisher`：让 detector 不直接依赖 alerts 模块；alerts change 实现 `NotifierPublisher` 注入
- **接入** `src/main.py` lifespan：startup 顺序为 collector → backfill → 首次 classifier.refresh → scheduler.start；shutdown 顺序为 scheduler.shutdown → 其他模块清理
- **新增** `/api/v1/health` 子键 `detection`：last_run / duration / active_alerts 各级数 / skipped_low_data
- **新增** endpoints：`POST /api/v1/detect/run`（手动触发，调试）、`GET /api/v1/detect/state`（看当前 last_levels）、`GET /api/v1/detect/thresholds`（看当前生效阈值）
- **新增** `.env.example` 项：`DETECTION_INTERVAL_MINUTES=5`、`DETECTION_THRESHOLDS=...`（覆盖默认阈值表的可选 string）、`DETECTION_BASELINE_HOURS=24`、`DETECTION_CURRENT_MINUTES=5`、`DETECTION_MIN_SAMPLES=1200`、`BACKFILL_BATCH_RPS=10`
- **依赖** `add-volume-classification` change 已 archive 入主 specs：本 change 注入 `Classifier` 实例消费 tier dict
- **NOT IN SCOPE**：alerts 模块（独立 change，本 change 默认用 `LoggingPublisher` 只把 events 输出到 log）；告警去重的"跨进程持久化"（保持内存）

## Capabilities

### New Capabilities

- `volume-detection`: 每 5 分钟周期性扫描所有 symbol，按"近期均量/历史中位数"倍数定级，跟上一轮状态比对仅输出"首次进档/升级"事件，含可插拔的发布契约。

### Modified Capabilities

- `volume-classification`: 不修改 requirement，但本 change **替换 classifier 的触发方式**：从"lifespan startup 跑一次"改为"通过 APScheduler 周期触发 + bootstrap 后跑一次"。classifier 模块代码不变，只是在 main.py 的 wiring 层面切换 trigger source。

## Impact

**Affected code**:
- 新增 `src/detection/{__init__, detector, models, thresholds, backfill, api}.py`
- 新增 `src/scheduler.py`（首次引入 APScheduler）
- 修改 `src/main.py`：扩展 lifespan 顺序、加 backfill 调用、注册 scheduler、注入 LoggingPublisher
- 修改 `src/api/routes.py`：include detection router、扩展 /health 聚合
- 修改 `src/config.py`：加 detection 相关配置项 + scheduler interval 配置
- 修改 `.env.example`：加 6 个 detection 相关 env vars
- 修改 `requirements.txt`：加 `apscheduler==3.10.4`（或最新稳定版）
- 修改 `environment.yml`：同步新增依赖
- 新增 `tests/unit/test_detector.py`、`tests/unit/test_backfill.py`、`tests/unit/test_scheduler.py`、`tests/integration/test_detection_e2e.py`

**Affected APIs**: 新增 `POST /api/v1/detect/run`、`GET /api/v1/detect/state`、`GET /api/v1/detect/thresholds`；扩展 `GET /api/v1/health`

**Affected dependencies**: 新增 `apscheduler` 依赖

**Risk**: 中。引入 APScheduler 新调度组件需要注意"重启 / shutdown 行为"。Backfill 拉外部 API 有限流风险（已设 batch_rps=10 远低于币安 20/sec 限制）。Detection 算法有错的话会污染 alerts 输出，但本 change 默认 publisher 是 LoggingPublisher 只 log 不发外部消息，可以安全观察一段时间。
