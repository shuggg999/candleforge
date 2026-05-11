# Design: introduce-nats-event-bus

## Context

2026-05-12 架构拆分 brainstorming（见 `docs/superpowers/specs/2026-05-12-data-service-split-architecture-design.md`）决定：data-service 是纯中转 DB 服务，业务逻辑要拆到 volume-monitor / telegram-bot 外部 repo。两个新 repo 需要拿到实时 K 线流，本 change 提供该机制。

## Goals

1. data-service 入库一条 → publish 一次 NATS event
2. 下游能用 1 行代码 `nc.subscribe("ohlcv.>", cb)` 拿到所有 K 线流
3. NATS 容器死掉不影响 ClickHouse 写入
4. publish 延迟 < 1 ms（贾维斯 docker 内网）

## Non-Goals

- 不实现 volume-monitor / telegram-bot 订阅端（下次 session 在新 repo 里做）
- 不替换 `src/{classification,detection,alerts}/` 现有内存对象通讯（同进程模块继续直接用对象引用，不走 NATS）
- 不引入 NATS 的 Request-Reply、Queue Groups、KV、Object Store 等高阶特性
- 不做认证（局域网内服务，account / NKey / TLS 全部跳过；后续上外网再加）

## Decision 1: 消息中间件选 NATS 而不是 Redis Streams / Kafka

| 维度 | NATS (JetStream) | Redis Streams | Kafka |
|---|---|---|---|
| 镜像大小 | ~10 MB | ~30 MB | ~600 MB + Zookeeper |
| RAM 占用 | ~10 MB idle | ~30 MB idle | ~500 MB |
| 多语言 client | Go/Python/Rust/JS/Java 一等公民 | 一般（需 XADD/XREAD 手写） | 重 client |
| 持久化 | JetStream stream + ack | Streams + consumer groups | Topic + offset |
| 运维复杂度 | 单二进制 | 单二进制（但常被当 cache） | 高（broker + ZK / KRaft） |
| 学习曲线 | 1 小时 | 半天 | 数天 |

**选 NATS**：本场景"小消息高频 publish + 多订阅者"是 NATS 的甜区，Redis Streams 也行但 NATS 的 client API 更干净（`nc.subscribe(subject, cb)`）。Kafka 对单机 jarvis 部署是 overkill。

## Decision 2: Subject 命名 `ohlcv.{exchange}.{symbol_normalized}.{timeframe}`

例：
- `ohlcv.binance.BTCUSDT.1m`
- `ohlcv.binance.ETHUSDT.5m`
- `ohlcv.okx.BTCUSDT.4h` （未来扩展）

**为什么 4 段**：NATS subject 是 dot-separated 层级，wildcard `>` 匹配剩余所有层级、`*` 匹配单层。这样下游可以按需精确订阅：

| 订阅模式 | 含义 |
|---|---|
| `ohlcv.>` | 所有 K 线流（monitor 默认） |
| `ohlcv.binance.>` | 只 binance |
| `ohlcv.*.BTCUSDT.*` | 任意交易所的 BTCUSDT 任意周期 |
| `ohlcv.binance.*.1m` | binance 所有 symbol 1m 周期 |

**为什么用 `symbol_normalized` 而不是 `BTC/USDT`**：NATS subject 不允许 `/`，必须用 `BTCUSDT` 这种 normalized 形式。Payload 里仍然保留 `symbol: "BTC/USDT"` 原值。

## Decision 3: Payload 字段固定，向后兼容

```json
{
  "exchange": "binance",
  "symbol": "BTC/USDT",
  "symbol_normalized": "BTCUSDT",
  "timeframe": "1m",
  "timestamp": "2026-05-12T00:00:00+00:00",
  "open":   "81000.00",
  "high":   "81200.50",
  "low":    "80950.00",
  "close":  "81100.00",
  "volume":   "123.456",
  "turnover": "10005000.00",
  "trades_count": 1234,
  "data_quality": "websocket",
  "is_closed": true,
  "ingested_at": "2026-05-12T00:00:00.123+00:00",
  "schema_version": 1
}
```

- **`schema_version`**：第一版 1，未来加字段不改版本，删/改字段必须升版本。下游 monitor 用 `schema_version` 判断兼容性
- **Decimal as string**：价格/成交量保留字符串避免 JSON float 精度损失。下游用 `Decimal(s)` 反序列化
- **`ingested_at`**：data-service 写入 ClickHouse 那一刻的时间，下游可以算端到端延迟
- **`is_closed`**：true 表示 K 线已收盘（WS final frame 或 recovery 补的）；false 表示中间帧（暂时未使用，预留给"未收盘 K 线 push"功能）

## Decision 4: Publish 时机 = ClickHouse insert 成功后

```
WS frame → validate → ClickHouseManager.insert_ohlcv()
                          ├─ INSERT INTO ohlcv_futures VALUES (...)
                          ├─ commit
                          └─ await publisher.publish_kline(row)  ← 这里
```

**为什么不在 insert 前 publish**：
- 入库失败 → 历史就缺这条，但 NATS 已经推了 → 下游算 baseline 时 SQL 和 NATS 不一致
- 入库成功是"data-service 承诺保留这条"的语义边界

**Publish 错误处理**：
```python
try:
    await self._publisher.publish_kline(row)
except Exception as exc:
    logger.warning("NATS publish failed (non-fatal): {}", exc)
    self._publish_failure_count += 1
```

- 不 raise（不阻塞下一条写入）
- 计数器暴露到 `/health` 的 `nats` sub-probe，连续失败 > 100 时 `status: degraded`
- NATS 重连由 nats-py client 自动处理（默认指数退避，永远重试）

## Decision 5: JetStream 持久化 stream `OHLCV` 保留 24h

| 设置 | 值 | 理由 |
|---|---|---|
| Stream name | `OHLCV` | UPPER 区分系统级 stream |
| Subjects | `ohlcv.>` | 所有 K 线 |
| Storage | File | jarvis 上有 docker 卷，重启不丢 |
| Retention | Limits | 时间到了删，不要求 consumer ack |
| MaxAge | 24h | 下游短暂离线最多追平一天，超过就只能 SQL 查历史 |
| MaxBytes | 1 GB | 525 symbols × 6 tf × 1min × 24h × 500 bytes/msg ≈ 380 MB，留 2.5× buffer |
| Replicas | 1 | 单节点，无副本 |

**为什么不用 ephemeral（无持久化）**：
- monitor 重启 5 秒，ephemeral 模式这 5 秒数据就丢了 → 必须再 SQL 补
- JetStream pull-consumer 自己记 offset，重启后从断点续推

## Decision 6: 部署上 jarvis 用 sidecar 容器，不内嵌

`docker-compose.yml` 新加：
```yaml
nats:
  image: nats:2.10-alpine
  command: ["-js", "-sd", "/data", "-m", "8222"]  # JetStream + file storage at /data + monitoring at 8222
  ports:
    - "4222:4222"   # client
    - "8222:8222"   # monitoring HTTP
  volumes:
    - nats-data:/data
  healthcheck:
    test: ["CMD", "wget", "-qO-", "http://localhost:8222/healthz"]
    interval: 30s
    timeout: 5s
    retries: 3
  restart: unless-stopped
```

data-service 加 `depends_on: [nats]` 软依赖（NATS 没起来 data-service 也能起，publish 自动 retry）。

## Risks & Mitigations

| 风险 | 缓解 |
|---|---|
| NATS publish 同步等待拖慢 ClickHouse 写入吞吐 | publish 操作本地 docker 内网 < 1 ms；如果担心，用 `nc.publish(subject, data)` 不带 ack 的形式 |
| schema 改动破坏下游 | `schema_version` 字段 + 文档化迁移流程；新字段不升版本，删字段必升 |
| JetStream stream 满（1GB）| MaxAge 24h 自动 GC；监控 stream byte usage 通过 `/health` 的 `nats` sub-probe 暴露 |
| nats-py asyncio client 跟现有 asyncio loop 冲突 | nats-py 自带 asyncio support；在 startup lifespan 内 `await NATS().connect(...)` 共用 loop |
| 端到端验证难（需要订阅者）| 写测试时用 nats-py client 自己 subscribe 验证；jarvis 部署后用 `nats sub 'ohlcv.>'` CLI 直接抓 |

## Open Questions

无 — 6 个决策点全部 lock。下次 session 直接实施。
