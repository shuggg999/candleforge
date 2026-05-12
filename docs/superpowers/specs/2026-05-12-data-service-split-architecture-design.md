# 架构重新定位：data-service 转为纯中转数据库 + 业务层拆出

**日期**: 2026-05-12
**作者**: project maintainer + Claude
**状态**: Draft — 待用户 review

---

## 1. 起源

2026-05-10 / 11 用三个 OpenSpec change 把 volume classification + detection + telegram alerts 全部塞进 `freqtrade-data-service` 里实现并上线。功能跑通后用户复盘提出：

> "我想把它定义为一个中转的数据库的服务。后续比如说警报、监控成交量、发送警报这些东西都可以用中间组件来进行组装，而不是写在一起。"

也就是说，**当前实现违反了用户脑子里的服务定位**。需要做架构重新对齐 + 重构 + 拆分。

---

## 2. 新定位

| 角色 | 职责 | 不做 |
|---|---|---|
| **data-service** (本仓库) | 多交易所 WS 实时采集 + REST gap recovery + ClickHouse 持久化 + 查询 API + MQ event publish | 业务规则计算、告警决策、通知投递 |
| **volume-monitor** (新 repo) | 订阅 MQ event → 触发 5min 检测 cycle → SQL 查 ClickHouse 算 baseline → 产生 alert event | 数据采集、数据存储、消息投递 |
| **telegram-bot** (新 repo) | HTTP webhook 接收上游 alert event → 调 Telegram API 发消息 → 处理 429 / 退避 | 业务决策、数据查询 |

**生效约束**：未来任何"基于这些数据做某件事"的需求（其他策略服务、行情面板、回测分析、新告警维度），都**不允许往 data-service 里塞**，必须作为下游消费者新起服务。

---

## 3. 数据契约（双轨）

```
┌─────────────────────┐
│   data-service      │
│  (this repo)        │
│                     │
│  WS:                │
│   binance fstream   │──┐  (实时 K 线)
│   okx WS (future)   │  │
│   bybit WS (future) │  │
│                     │  ▼
│       insert ClickHouse.ohlcv_futures
│                                │
│  ┌─────────────────────────────┤  通知层 (实时事件)
│  │ publish event to            │
│  │ NATS: ohlcv.{ex}.{sym}.{tf} │──────► NATS broker ─────► volume-monitor (subscribe)
│  └─────────────────────────────┤                                  │
│                                │                                  │
│  REST API:                     │                                  │
│   GET /ohlcv/...               │                                  │
│   GET /multi-timeframe/...     │                                  │
│                                │                                  │  查询层 (历史)
│  ClickHouse:                   │ ◄────────── SQL ─────────────────┘
│   ohlcv_futures (TTL 自动清理)  │  (volume-monitor 读 baseline + alerts 写自己的库)
│   symbol_tiers                 │
└─────────────────────┘                                              │
                                                                      ▼
                                                              volume-monitor 产生 alert event
                                                                      │
                                                                      ▼ HTTP webhook
                                                              telegram-bot ─► Telegram API
```

**两层职责**：
- **MQ 通知层（NATS）**：data-service 入库一条 → publish 一次。下游决定何时反应。
- **SQL 查询层（ClickHouse）**：下游算 baseline / 复杂聚合时直查表。schema 由 data-service 公开作为契约。

**为什么不只用 MQ？** detection 需要 24h baseline，纯 MQ 推送不存历史，下游会被迫自己再做一份历史副本，浪费。

**为什么不只用 SQL？** 失去事件驱动 / 实时性 / 多消费者解耦。"未来扩"的扩展点失守。

---

## 4. NATS event 契约

**Subject**: `ohlcv.{exchange}.{symbol_normalized}.{timeframe}`

例：
- `ohlcv.binance.BTCUSDT.1m`
- `ohlcv.okx.ETHUSDT.5m`

**Payload (JSON)**:
```json
{
  "exchange": "binance",
  "symbol": "BTC/USDT",
  "symbol_normalized": "BTCUSDT",
  "timeframe": "1m",
  "timestamp": "2026-05-12T00:00:00+00:00",
  "open": "81000.00",
  "high": "81200.50",
  "low": "80950.00",
  "close": "81100.00",
  "volume": "123.456",
  "turnover": "10005000.00",
  "trades_count": 1234,
  "is_closed": true,
  "data_quality": "websocket",
  "ingested_at": "2026-05-12T00:00:00.123+00:00"
}
```

- `is_closed=true` 表示 K 线已收盘（WS final frame）；recovery 补的也是 closed
- `ingested_at` data-service 写入时间，便于下游计算延迟

**保留策略**：JetStream 持久化 24h（足以支持下游短暂离线后追平）

---

## 5. 仓库边界

| Repo | 现状 | 内容 |
|---|---|---|
| `freqtrade-data-service` (当前) | 现存 | collectors, storage, recovery, API, NATS publisher。**未来不允许新增业务逻辑** |
| `volume-monitor` (新建) | TBD | classifier, detector, alert event producer。订阅 NATS + SQL 查 ClickHouse |
| `telegram-bot` (新建，可选合并到 monitor) | TBD | HTTP webhook → Telegram API。轻量中间件，未来其他服务也可以调 |

**为什么 telegram-bot 独立**：未来可能有"行情快讯""系统监控"等多个来源都需要发 Telegram，独立成中间件避免每个服务自带 token 配置。

---

## 6. 过渡期策略

不一次性 cut over，分四阶段：

### 阶段 1：data-service 内部修小 bug（不阻塞重构）
- 修 init.sql TTL bug（1h 90天 → 365天）+ ALTER TABLE 修现有表
- 补 `/api/v1/health` sub-probe（classifier/detector/notifier 状态）
- 删 CLAUDE.md 里 `/dashboard` stale 描述
- OpenSpec change: `fix-ttl-and-health-probe`

### 阶段 2：标记业务模块 deprecated（信号 + 心理准备）
- `src/classification/`, `src/detection/`, `src/alerts/` 入口加 `DeprecationWarning`
- CLAUDE.md 注明"这些模块即将搬到 volume-monitor / telegram-bot repo"
- 现有 5min 检测和 telegram alert **继续工作**，不影响生产
- OpenSpec change: `deprecate-business-modules`

### 阶段 3：data-service 加 NATS event bus
- docker-compose 加 nats 服务
- 入库后 publish event
- 公开 event schema 文档 + Python type stub
- OpenSpec change: `introduce-nats-event-bus`

### 阶段 4：外部新 repo（不在本仓库做）
- 新建 `volume-monitor` repo（订阅 NATS + SQL + 检测 + alert event）
- 新建 `telegram-bot` repo（HTTP webhook → Telegram）
- 部署：生产 host 的 docker compose 加这两个服务
- 验证：与 data-service 内部业务模块**双跑对比**几天，证明 monitor 行为一致

### 阶段 5：data-service 清理
- 外部 monitor + bot 稳定运行 ≥ 3 天后
- `git rm src/{classification,detection,alerts}/`
- 删 `src/main.py` 里的 wiring（classifier / detector / notifier / scheduler 任务）
- 关闭 data-service 的 5min cron
- OpenSpec change: `cleanup-business-modules`

---

## 7. TTL 策略

按用户 2026-05-12 决定，**维持当前 .env 设计 + 修 1h bug**：

| Timeframe | TTL | 稳态行数 (525 symbols) |
|---|---|---|
| 1m | 7 天 | 5.3M |
| 5m | 30 天 | 4.5M |
| 15m | 90 天 | 4.5M |
| 1h | **365 天 (修)** | 4.6M |
| 4h | 365 天 | 1.1M |
| 1d | 1825 天 | 1M |
| **合计** | | **~21M 行 → 压缩后 ~500MB-1GB** |

ClickHouse partition by month + TTL 自动 drop，无需运维干预。贾维斯 Mac mini 完全可承受。

**冷启动回填**（用户 Q1 提到的）**暂不做**，等下游 monitor 真要 24h baseline 时再说。WS 持续增量 7 天后 1m 即可达稳态。

---

## 8. 优先级派生的 OpenSpec changes

按"先修阻塞性 bug → 标 deprecated → 加 MQ → 外部 repo → 清理"顺序：

| # | change-id | 紧迫 | 复杂度 |
|---|---|---|---|
| 1 | `fix-ttl-and-health-probe` | 高（TTL bug 影响数据保留正确性） | 低 (1-2h) |
| 2 | `deprecate-business-modules` | 中（信号作用） | 低 (30m) |
| 3 | `introduce-nats-event-bus` | 中（下游消费者前置依赖） | 中 (4-6h) |
| 4 | 外部 `volume-monitor` repo | 中（业务功能搬家） | 高 (1-2 day) |
| 5 | 外部 `telegram-bot` repo | 低（可暂在 monitor 里 inline） | 中 (4h) |
| 6 | `cleanup-business-modules` | 低（外部稳定后才做） | 低 (1h) |

---

## 9. 风险 & 决策记录

| 风险 | 缓解 |
|---|---|
| 拆分期间业务功能中断 | 阶段 2 标 deprecated 不删；阶段 4 双跑对比 |
| MQ 引入运维复杂度 | NATS 单二进制 docker image，无 Zookeeper / 不需额外组件 |
| 跨 repo 协作慢 | 三个 repo 都用同一个 gitea 实例 + 同一份 docker-compose.yml 在部署 repo 里编排 |
| schema 改动协调 | event schema 写在 data-service repo 的 `docs/contracts/`，下游 import 时 vendor / 复制 |

**已决（不再讨论）**：
- ✅ 业务层严格分离（A 选项）
- ✅ 双轨数据契约（MQ + SQL）
- ✅ 三个独立 git repo
- ✅ 现有业务代码先 deprecated 后删
- ✅ TTL 当前设计 + 修 1h bug

**待决**（下次或实施时再说）：
- ❓ 选 NATS 还是 Redis Streams
- ❓ telegram-bot 独立 repo 还是 inline 在 monitor 里
- ❓ 阶段 4 完工标准（双跑多少天 / 监控哪些指标确认行为一致）
- ❓ 多交易所（okx / bybit）实际什么时候启用
