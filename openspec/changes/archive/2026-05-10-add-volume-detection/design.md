## Context

本 change 是成交量异常监控的"大脑" —— 实现核心检测算法、跨周期状态比对、可插拔的发布契约。同时它是首个引入 APScheduler 的 change（classification 当前只在 startup 跑一次），并实现冷启动 backfill（detection 需要历史数据才能算中位数基线）。

算法层面所有"为什么"的论证都在 `docs/superpowers/specs/2026-05-10-volume-anomaly-monitor-design.md` 第 3 节 Detection 部分及第 10 节决策追溯里完成。本 design.md 只覆盖**模块内部 + 跨模块基础设施的工程决策**。

## Goals / Non-Goals

**Goals**：
- 一条 SQL 算 475 个 symbol 的 ratio，不在热路径触发 N 个 query
- detection ↔ alerts 通过 Protocol 解耦（detection 不知道 alerts 怎么发，alerts change 注入 publisher 即可接入）
- APScheduler 在本 change 完成首次集成，未来加新 job 直接 `scheduler.add_job()`
- backfill 能安全应对币安 REST 限流（IP weight 1200/min, klines = 1 weight）
- 重启友好：内存 last_levels 清空后第一轮重发当前异常（这是 feature 不是 bug）

**Non-Goals**：
- 不实现 alerts 模块（独立 change），本 change 用 LoggingPublisher 占位
- 不持久化 last_levels（接受重启重发当前异常）
- 不做 SQL 查询超时优化（先用裸 query，性能问题等真出再说）
- 不做 detection 内部的多并发或多线程（单 asyncio coroutine 串行跑足够）
- 不做"按 symbol 黑名单"过滤（未来需求，现阶段全量扫）

## Decisions

### Decision 1：APScheduler 放在 `src/scheduler.py` 而不是 detection 模块内

**选**：新增独立 `src/scheduler.py` 暴露 `build_scheduler(app, classifier, detector) -> AsyncIOScheduler`，在 `main.py` 的 lifespan 里启动 / 停止。

**不选**：把 APScheduler 集成代码塞进 `src/detection/__init__.py`

**理由**：
- scheduler 是横切关注点（detection / classification 都用），不属于任一模块
- alerts change 未来加新 job（如 daily summary）也注册到这个 scheduler
- detection 模块只暴露 `detect_and_publish()` 协程，不知道自己被怎么触发，便于单测

### Decision 2：detection ↔ alerts 用 Protocol 解耦

**选**：`AlertPublisher` Python `typing.Protocol` 定义 `async def publish(events: list[AlertEvent]) -> None`。detection 构造时注入。本 change 提供 `LoggingPublisher` 占位，alerts change 提供 `NotifierPublisher`。

**不选 A**：detection 直接调 `alerts.send_alerts(events)`
**不选 B**：用 asyncio.Queue 或 EventEmitter 模式

**理由**：
- 选 A 让 detection 硬依赖 alerts 模块，违反"模块独立 degrade"原则
- 选 B 引入异步队列的复杂度（背压、死锁、有界 vs 无界）但本场景 5min 一波事件量小，没必要
- Protocol 是 Pythonic 的接口契约，开销 0、可读性好、便于 mock 测试

### Decision 3：核心 SQL 写在 `detection/detector.py` 模块顶部常量

**选**：`DETECTION_SQL = """SELECT ... GROUP BY symbol HAVING ..."""` 作为模块顶层字符串常量，detector 方法引用。

**不选**：用 SQLAlchemy 或类似 ORM 抽象

**理由**：
- ClickHouse 不是关系型 OLTP 库，ORM 抽象不到位
- SQL 是 detection 的核心算法，写在显眼位置更便于 review 和调优
- 可注入参数（INTERVAL N HOUR）通过 f-string 或 query parameter 传递（注意 SQL injection — 因为参数都是从 settings 来的整数，安全）

### Decision 4：last_levels 内存数据结构

**选**：`dict[str, Level]`，在 `detect_once()` 末尾批量更新。Level 是 `Enum` 字符串值。

**不选 A**：dict[str, str]（少了类型安全）
**不选 B**：把 last_levels 持久化到 ClickHouse 或 Redis

**理由**：
- 重启后所有 abnormal 状态会重发一次，这正是希望的行为（"我重启完应该看到现在哪些 symbol 在异常"）
- 如果未来部署多副本，再考虑 Redis；当前单副本不需要
- Enum 让 `>=` 这种级别比较更直观（实现 `__ge__` 或者用 `IntEnum`）

### Decision 5：backfill 实现策略

**选**：`src/detection/backfill.py::ensure_baseline_data(min_hours, batch_rps)`，每次启动调一次。
- 先 SQL 查每个 symbol 在 `[now - min_hours, now]` 区间的数据点数
- 缺数据的 symbol 进 backfill 队列
- 用 `asyncio.Semaphore(batch_rps)` 限并发 + `asyncio.sleep(1/batch_rps)` 限频
- 每个请求拉单个 symbol 单段时间，写入 ClickHouse

**不选 A**：把 backfill 塞进 `src/collectors/binance.py`（混淆"实时采集"和"历史回填"职责）
**不选 B**：用 cryptofeed 库（增加依赖且不一定支持精确的回填模式）

**理由**：
- 独立模块清晰
- Semaphore + sleep 双重限流抗 burst
- 复用现有 collector 的 ClickHouse client 写库逻辑（注入 `ClickHouseStorage`）

### Decision 6：scheduler 对齐时钟整点

**选**：`add_job(detect_and_publish, IntervalTrigger(minutes=5, start_date=next_5min_boundary()))` 让首次 trigger 落在下一个时钟整点。

**不选**：让 APScheduler 默认行为（从启动时刻起算每 5min）

**理由**：
- 多 symbol 的 detection 时间戳整齐（00, 05, 10, ...），便于按时间段查询 / 回放
- 跟用户对"5 分钟一次"的直觉一致（人脑想的是 09:00, 09:05, 09:10..., 不是 09:03:42, 09:08:42, ...）
- next_5min_boundary 计算简单（一行 datetime 运算）

### Decision 7：detection 失败的影响范围

**选**：detect_once() 内部不做重试。失败时 raise 让 APScheduler 记日志、`last_run_status` 标记 failed、health 标 degraded，下个 cycle 自动重试。

**不选**：tenacity 包 SQL 调用、内部重试 3 次

**理由**：
- 5min 一次的低频任务，下个 cycle 等 5min，容忍这个延迟
- 内部重试可能让一个 cycle 跑超过 5min，引发 max_instances 跳过下一次
- 失败的根因（ClickHouse 挂、网络分区）通常不是 retry 能救的
- 极端情况：连续 N 次失败 health=failed，等运维介入

### Decision 8：APScheduler 选 AsyncIOScheduler 而非 BackgroundScheduler

**选**：`AsyncIOScheduler` —— 跟 FastAPI 的 asyncio event loop 共享。

**不选**：`BackgroundScheduler`（独立线程池）

**理由**：detector 跑的是 async ClickHouse query；BackgroundScheduler 在线程里跑 async 函数需要 `asyncio.run()` 包裹，复杂且容易踩 event loop 重入。AsyncIOScheduler 直接 `await` 协程，零摩擦。

## Risks / Trade-offs

- **[Risk]** SQL 查询 475 symbols 24h × 1m = ~70 万行 + median 聚合，ClickHouse 性能未在贾维斯硬件上压测过 → **Mitigation**：本 change tasks 包含一项"在 testcontainers ClickHouse 灌入 mock 数据 + 跑 detect_once 测延迟"；如果 >5s 考虑加 PROJECTION 或 MATERIALIZED VIEW
- **[Risk]** backfill 拉 ~70 万条数据写 ClickHouse 需要 ~5-10 分钟，期间 health 会显示"backfill in progress"，用户可能误判服务挂了 → **Mitigation**：`/health` 加 `bootstrap_progress` 字段显示百分比；启动时 log 也明确写"backfill: X/Y symbols, ETA: Z minutes"
- **[Risk]** 重启时 last_levels 清空，首次 cycle 可能一次性发出几十条 events 给 LoggingPublisher（本 change 还没接 Telegram，影响有限）→ **Mitigation**：alerts change 处理"启动后第一次的 events 是否要节流"
- **[Risk]** APScheduler 的 `AsyncIOScheduler` 在 FastAPI shutdown 时的 graceful shutdown 顺序可能跟 worker 进程冲突（如果用 multiple workers）→ **Mitigation**：本服务单 worker 部署即可（uvicorn `--workers 1`），有需要扩展再改
- **[Trade-off]** classification refresh 改由 scheduler 触发后，classification change 之前的"lifespan startup 跑一次"逻辑不再起作用 → 可以选择保留（双触发，第二次刷的是同样的数据，无害）或移除（更干净）。本 change 倾向**保留**作为 fallback：scheduler 启动失败时 cache 仍有数据
- **[Trade-off]** 不持久化 last_levels → 接受重启重发当前异常的代价

## Migration Plan

1. 加 `apscheduler` 依赖到 `requirements.txt` + `environment.yml`
2. 实现 `src/scheduler.py`（先单测）
3. 实现 `src/detection/` 模块（TDD 顺序：models → thresholds → detector → backfill → api）
4. 修改 `src/main.py`：调整 lifespan 顺序（collector → backfill → 首次 classifier.refresh → scheduler.start → ready）
5. 更新 `/health` endpoint 聚合 detection 子健康
6. 更新 `.env.example` 加新 env vars
7. 跑集成测试（testcontainers ClickHouse + Binance REST mock）
8. 部署到贾维斯，观察 5min 间隔是否对齐 + backfill 是否正常完成
9. archive change

**Rollback**：`git revert` 即可。要注意：classification change 之前 startup 跑一次的逻辑被本 change 包在 scheduler 里，revert 时 lifespan 顺序要恢复。所以本 change 的 commit 要明确隔离 lifespan 修改。

## Open Questions

- backfill 失败的策略：单个 symbol 失败时跳过 / 全部失败时 abort startup？倾向"单个失败跳过 + 计数 > N% 失败时 abort"，具体 N 由实现时定（建议 20%）
- detection 跑超过 5min 时的告警：要不要在 `/health` 标 degraded？暂时不做（max_instances=1 已足够）
- APScheduler 持久化 jobstore（断电重启后任务能恢复）：当前用默认 MemoryJobStore，重启后任务重建。需要持久化等真出问题再加 SQLAlchemyJobStore
