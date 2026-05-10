# Volume Anomaly Monitor — 总体设计

**日期**：2026-05-10
**作者**：Brainstorming session（用户 + Claude）
**状态**：Draft，待 user review
**关联 OpenSpec changes**（即将创建）：`add-volume-classification`、`add-volume-detection`、`add-telegram-alerts`
**依赖 spec**：`baseline-infrastructure`（已 archive 入主 specs）

---

## 1. 目标与非目标

### 目标

实时监控约 475 个币安 USDT 永续合约的"短时间成交量异常拉升"。

具体而言：每 5 分钟扫描一遍所有 symbol，对每个 symbol 计算"最近 5 分钟的 1 分钟均量"相对"过去 24 小时的 1 分钟成交量中位数"的倍数。如果倍数超过该 symbol 所属流动性档位的预设阈值，触发多级告警（warn / strong / extreme），推送到 Telegram 单聊并落 ClickHouse 审计表。

### 非目标

- 不做高频检测（5 分钟粒度即可，不追求秒级延迟）
- 不做交易决策（仅监控告警，是否操作由人或下游主项目 HUANMU-WRU-BOT 自行决定）
- 不做跨交易所（先做币安永续，OKX/Bybit 留给未来 change）
- 不做现货市场（仅 USDT 永续）
- 不引入 ML/统计假设（如 z-score 假设正态分布），坚持"中位数 + 倍数"的鲁棒、可解释路线
- 不做去重以外的告警聚合（如"今日 top 10 异常"摘要，留给未来 change）

---

## 2. 架构概览

系统由三个职责独立的业务模块组成，挂在已有的 FastAPI / ClickHouse / 币安 WebSocket collector 底座之上（底座契约见已合并的 `baseline-infrastructure` spec）：

- **classification**（`src/classification/`）：每 12 小时按所有 symbol 过去 24h 的 quote_volume 总量算出 P25/P50/P75 分位数切档线，把 475 个 symbol 划入 mega / large / mid / small 四档。结果同时落 ClickHouse `symbol_tiers` 表（带历史版本）和进程内存 dict（detection 高频读用）
- **detection**（`src/detection/`）：每 5 分钟跑一次。一条 ClickHouse SQL 同时算出 475 个 symbol 的"近 5min 均量"与"过去 24h 1m 中位数"的比值，按所属档位查阈值表定级（warn / strong / extreme），跟内存中"上一轮 level"比对，**仅在"首次进档"或"级别升高"时**输出 `AlertEvent`
- **alerts**（`src/alerts/`）：接收 detection 输出的事件，全量落 `volume_alerts` 表（审计），全量推 Telegram，记录推送状态。Telegram 失败不阻塞检测继续进行

**数据流向**：`binance WebSocket → ClickHouse ohlcv_futures → detection 查 → events → alerts 落库 + Telegram`

**调度由 APScheduler 在 main.py lifespan 里统一管理**：detection job (interval=5min) + classification job (cron=*/12h)。这两个 job 跟 FastAPI 主进程同生命周期。

**部署拓扑**：本服务跑在贾维斯（保加利亚 VPS）上 docker compose；本地 Mac 只跑业务代码 + pytest 调试，通过 SSH 隧道读贾维斯 ClickHouse 调真实数据。

---

## 3. 三模块边界与契约

### Classification 模块

**职责单句**：把 475 个 symbol 按流动性体量分到 4 档，每 12 小时刷新。

**输入**：ClickHouse `ohlcv_futures` 表过去 24 小时所有 1m 数据。

**输出**：
- 内存：`dict[symbol → tier]`，detection 模块通过依赖注入消费
- ClickHouse：`symbol_tiers` 表（ReplacingMergeTree，按 `(exchange, symbol, refreshed_at)` 主键，TTL 90 天）

**算法**：
1. SQL 算每个 symbol 过去 24h `SUM(quote_volume)`
2. 把所有 symbol 的总量排序，取 P25 / P50 / P75 三个分位数
3. >P75 → mega，P50-P75 → large，P25-P50 → mid，<P25 → small
4. 内存 dict 更新 + 批量 insert 历史快照

**为什么用 24h 而不是 7d / 30d**：
- 与 detection 的"24h 中位数基线"对齐，概念一致
- 24h 总量是"现在的市场体量"，比 7d 平均更跟得上市场冷热变化
- 短期窗口 + 12h refresh 频率，足以平滑掉单小时炒作的偏差

**为什么用分位数硬切而不是 k-means 聚类**：
- 可解释：能直接说"P75 是多少 USDT"
- 可审计：`symbol_tiers` 历史表可以重现"为什么当时这个币是 mid 档"
- 可调试：聚类的 0/1/2/3 编号没业务含义，告警文案、文档、运维都难写

**跨档迁移**：tier 变化不触发任何告警（这只是分档调整不是异常事件），但下次 detection 算 ratio 时会用新档的阈值。

---

### Detection 模块

**职责单句**：每 5 分钟扫一遍 475 个 symbol，找出"成交量倍数超阈值且比上一轮升级"的事件。

**输入**：
- ClickHouse `ohlcv_futures` 表过去 24 小时所有 1m 数据（用 LEFT JOIN 拉 tier）
- classification 模块的内存 dict（symbol → tier）
- 阈值表（Python dict，按档配 warn/strong/extreme 三档倍数）

**输出**：`list[AlertEvent]`，传给 alerts 模块。每个 event 包含 symbol / tier / level / ratio / 上下文（curr_5min_avg / baseline_median / threshold_used / prev_level）。

**核心算法**：
1. 一条 SQL 同时算所有 symbol 的 `(5min 均量) / (24h 中位数) = ratio` + `count() >= 1200` 过滤数据不足的（约等于近 20h）
2. 按 tier 查阈值表，定 level
3. 跟内存 `last_levels: dict[symbol → Level]` 比对，仅"normal → warn/strong/extreme"或"warn → strong/extreme"或"strong → extreme"算 escalation
4. 更新 last_levels（**所有 symbol 都更新**，不只 escalation 的）
5. 仅 escalation 的事件入 events 列表

**默认阈值表**（写在 `detection/thresholds.py`，可被 `.env` 的 `DETECTION_THRESHOLDS` 覆盖，便于不重启调参）：

| 档位 | warn | strong | extreme |
|---|---|---|---|
| mega（超大盘 BTC/ETH 类） | 3× | 5× | 10× |
| large（大盘） | 5× | 10× | 20× |
| mid（中盘） | 10× | 20× | 50× |
| small（小盘 / meme） | 20× | 50× | 100× |

**为什么档位调阈值**：小盘币天然波动大，5 倍可能是日常；大盘币 2-3 倍就值得关注。统一阈值会导致"小盘刷屏 + 大盘漏报"。

**为什么只发"首次进档 + 升级"**（与"每次扫到都发"对比）：
- 你是单人监控，不需要"持续提醒"，需要"事件发生时知道"
- 重启后 last_levels 清空 → 第一轮所有当时处于异常的 symbol 重发一次。**这是合理的**：重启后用户应该重新看到当前异常状态

**为什么 last_levels 在内存而不是 ClickHouse**：
- 单进程部署，475 entries 内存够
- query 加几毫秒延迟没必要
- 重启后行为可预期（如上）

---

### Alerts 模块

**职责单句**：把 detection 输出的事件全量落库 + 全量推 Telegram，失败兜底但不阻塞下一条。

**输入**：detection 输出的 `list[AlertEvent]`。

**输出**：
- ClickHouse：`volume_alerts` 表（MergeTree，按月分区，TTL 365 天）
- Telegram：单聊消息（Markdown 格式）

**流程**：
1. 全量 batch insert 到 `volume_alerts`（status=`pending`）
2. 并发 (`asyncio.gather`) 发 Telegram，每条用 tenacity 退避重试 3 次
3. 异步 update `volume_alerts.telegram_status` 为 `sent` 或 `failed` + `sent_at`

**消息格式**：Markdown，含 emoji（🟡 warn / 🟠 strong / 🔴 extreme）、symbol、档位、倍数、上一轮级别（升级时）、阈值、近 5min 均量、24h 中位数、UTC 时间戳。升级用 `⬆ from STRONG` 这种标记直观。

**Telegram 配置**：`.env` 加两个变量：`TELEGRAM_BOT_TOKEN`（从 BotFather 拿）+ `TELEGRAM_CHAT_ID`（你跟 bot 1 对 1 聊出来）。可选 `ALERTS_DRY_RUN=true` 让 alerts 只落库不发 Telegram（调试用）。

**限流策略**：**不限流**（用户决策）。Telegram 自然限流 ~30 msg/sec，asyncio.gather 并发会自然 spread。如果未来真刷屏可以加 daily cap 兜底。

**失败处理总纲**：
- Telegram 失败 → `volume_alerts.telegram_status='failed'`、`telegram_error` 记原因，`/health` 累计 6 次失败标 degraded，但 detection 继续跑
- ClickHouse insert 失败 → tenacity 退避重试，都失败则本轮事件丢失（接受这个代价；alternative 是内存 retry queue 但增加复杂度）
- 配置缺失（无 token） → 启动时 log warning + 自动 `dry_run=true`，`/health` 标 degraded
- 不配 Telegram 也能用：detection + 落库照跑，告警可以从 `volume_alerts` 表查

---

## 4. 跨模块基础设施

### 调度（APScheduler）

`main.py` lifespan 里起 `AsyncIOScheduler`，注册：
- `detector.detect_and_publish` interval=5min, max_instances=1（防重入）
- `classifier.refresh_tiers` cron=`*/12 hours`
- 未来扩展：daily summary 发到 Telegram、weekly 阈值回放报告

shutdown 时 `scheduler.shutdown(wait=True)` 优雅停止。

**为什么 APScheduler 不用 asyncio loop**：用户偏好"专业感 + 未来加任务方便"。APScheduler 引入 1 个依赖换扩展性（cron 表达式、多任务管理、可暂停/恢复 jobs）。

### 启动顺序与冷启动 Backfill

1. FastAPI 启动 → ClickHouse / Telegram 连接初始化
2. **Bootstrap backfill**：检查 ClickHouse 是否有过去 24h 的连续数据。不够的话从币安 `/fapi/v1/klines` 按批次拉（10 req/sec，遵守 1200 weight/min 限制），约 5-10 分钟拉完 ~70 万条
3. backfill 完成后立即跑一次 `classifier.refresh_tiers()`（首次填充 tier dict）
4. 启动 APScheduler，detection 显式对齐到下一个 5min 时钟整点首次执行（00 / 05 / 10 / ...），之后每 5min 一次。这样多 symbol 的 detection 时间戳整齐，便于 `volume_alerts` 表按时段查询和回放
5. `/health` 在以上每步都更新子状态，运维能看到"当前进度"

### 健康检查聚合

`GET /api/v1/health` 返回三模块各自子状态 + 底座状态：

```json
{
  "status": "ok",
  "clickhouse": "ok",
  "collector":  "ok",
  "classification": { "status": "ok", "last_refresh": "...", "symbol_count": {...} },
  "detection":     { "status": "ok", "last_run": "...", "active_alerts": {...} },
  "alerts":        { "status": "ok", "telegram_configured": true, "stats_24h": {...} }
}
```

任一模块 degraded 时 `status=degraded`；任一模块 failed 时 `status=failed`。

### 模块独立 degrade

三个新模块都遵循"挂了不影响其他模块"原则：
- alerts 挂 → detection 继续跑 + 落库（你后续从表里查漏报）
- classification 挂 → detection 用上一轮 cache 的 tier，告警 `last_refresh` 偏老
- detection 挂 → collector 继续落数据，alerts 模块空转

这跟 `baseline-infrastructure` spec 的 Module Wiring Convention 完全对齐。

---

## 5. 数据库 Schema 总览

新增两张表：

**`symbol_tiers`**：分档结果，ReplacingMergeTree，主键 `(exchange, symbol, refreshed_at)`，每次 refresh 写新版本（旧版本被合并 keep latest）。字段：tier、quote_volume_24h、当时的 P25/P50/P75。TTL 90 天。

**`volume_alerts`**：告警审计，MergeTree 按月分区，主键 `(detected_at, exchange, symbol)`。字段：tier / level / prev_level / ratio / curr_5min_avg / baseline_median / threshold_used / telegram_status / telegram_error / sent_at。TTL 365 天。

**`ohlcv_futures`**：现有底座表，不动。

---

## 6. 测试策略

**单元测试**（每模块一个测试文件）：
- classification: 给定一组 mock quote_volume，验证分档结果正确；测边界（0 成交、相等 quote_volume、symbol 数 < 4）
- detection: 给定 mock SQL 结果 + tier dict + 上一轮 last_levels，验证返回的 events 列表正确；测各档阈值边界、escalation 判定
- alerts: 给定 events，mock Telegram 客户端，验证消息格式、batch insert 调用、失败时 status update

**集成测试**：
- 用 testcontainers 起临时 ClickHouse，灌入预制数据，跑 detection → alerts 端到端，断言 `volume_alerts` 表内容
- Telegram 用 fakes（不打真 API），验证消息内容

**阈值回放测试**（这个对持续调参很有价值）：
- 写一个 CLI 工具：`python -m scripts.replay --since 30d`
- 拉历史 30 天数据离线跑 detection 算法
- 输出"如果用当前阈值，这 30 天会触发哪些 symbol、什么时段、什么级别"
- 用来调阈值表的默认值

**覆盖率**：算法路径全测、错误处理路径选关键的测；不强求 80%。

---

## 7. 跟主项目 HUANMU-WRU-BOT 的接口（未来）

本次不实现，仅留口子：未来主项目通过 HTTP 调用本服务的两个端点：
- `GET /api/v1/alerts/recent?since=10m&min_level=strong` — 拿最近告警做交易决策
- `GET /api/v1/symbol-tier/{symbol}` — 查某 symbol 当前在哪档（决定仓位大小）

**不做的事**：不直接 push 给主项目（不引入 webhook 复杂度），主项目主动 pull 即可。

---

## 8. 切分为 3 个 OpenSpec changes 的理由

按用户决策"三个独立 change"：

1. **`add-volume-classification`** — 单独 propose，因为它不依赖 detection / alerts，可以独立交付（部署后 `symbol_tiers` 表会自动每 12h 填充，但没人消费）
2. **`add-volume-detection`** — 依赖 classification（消费 tier dict），propose 时 dependencies 引用 classification spec
3. **`add-telegram-alerts`** — 依赖 detection（消费 AlertEvent），propose 时 dependencies 引用 detection spec

**好处**：每个 change 测试范围小、回滚粒度细、code review 焦点集中。

**坏处**：要写 3 份 proposal/design/tasks/spec，是单一 change 工作量的 ~2x，但一次性投入换长期清晰。

**集成测试**：跨 change 的端到端集成测试放在最后一个 change（add-telegram-alerts）的 tasks 里。

---

## 9. 待办与开放问题（non-blocking）

- 阈值回放测试 CLI 工具是放本仓库还是主项目？倾向本仓库 `scripts/replay_detection.py`，等 add-volume-detection change 实现时再决定
- 未来加 Discord / 邮件等多渠道告警 → 重构 alerts 模块成 notifier 接口 + telegram_notifier 实现。本次不做，但 alerts 内部代码组织要给 future refactor 留余地（不把"telegram"硬编码到模块名以外的地方）
- ClickHouse 长期来看 detection 的 `median()` over 24h × 475 symbol × 每 5 分钟 一次 的负载，等真实部署后 benchmark 一下。如果有压力，可以加物化视图预聚合
- 阈值默认值是基于直觉拍的，等 backfill 完跑一次回放测试再校准

---

## 10. 决策追溯（"为什么不是这样"）

| 候选 | 否决理由 |
|---|---|
| z-score 算法 | 加密市场厚尾，z-score 失真严重；倍数对庄家拉盘场景更直观 |
| 7d 平均做基线 | 长尾窗口被前期爆量污染，第二波拉升漏报（用户洞察）|
| 7d 中位数做基线 | 24h 中位数对 4h 内异常仍鲁棒（污染 16.7% 不影响中位数），且更灵敏 |
| 不分档 | 小盘 5 倍 vs 大盘 2 倍 不可同日而语，统一阈值会刷屏或漏报 |
| 5 档 quintile | 每档 ~95 个 symbol 样本不够稳，档边界震荡频繁 |
| k-means 自适应分档 | 编号无业务含义、不可解释、调试难、对"持续调参"反而碍事 |
| 完整状态机告警 | Telegram 消息量 4-5x，单人监控不需要这种细粒度 |
| 固定 5min 去重 | 对"持续 30min 高警报"事件丢信息；进档+升级模型更合理 |
| 全局限流 | 用户明确选择"不限流，有多少发多少" |
| asyncio loop 调度 | 单一任务够用，但用户偏好"未来加任务方便"，APScheduler 一次投入 |
| ClickHouse 存 last_levels | 单进程内存够、零延迟，重启后行为可预期（重发当前异常）|
| 内存 retry queue 兜底 ClickHouse 失败 | 增加复杂度且 detection 5min 一轮，丢一轮可接受 |
