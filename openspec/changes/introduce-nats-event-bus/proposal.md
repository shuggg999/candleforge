## Why

`data-service` 即将拆分（见 `docs/superpowers/specs/2026-05-12-data-service-split-architecture-design.md`）。拆分后 `volume-monitor` 和 `telegram-bot` 是两个**独立部署的下游消费者**，需要一个机制让它们：

1. **被动等数据，不轮询** —— 比 SQL polling 低延迟、低 ClickHouse 压力
2. **解耦** —— monitor / bot 重启或离线不影响 data-service 写入 ClickHouse
3. **多消费者** —— 未来同一份 K 线流可能被多个 monitor、多个 bot、回测分析、行情面板等服务订阅，不能让每个新订阅者都自己 SQL 轮询 ClickHouse

当前缺这个机制 —— 所有"基于 K 线做事"的逻辑（classifier / detector / notifier）必须住进 data-service 进程才能拿到数据。这就是为什么三个业务模块塞进 `src/{classification,detection,alerts}/` 的根本原因。

本次 change 不拆模块，只**在 data-service 内增加 NATS event publish 能力**。拆模块在后续 `bootstrap-volume-monitor` + `bootstrap-telegram-bot` 两个外部 repo 的 OpenSpec changes 里做。

## What Changes

### 新增能力 `nats-event-bus` (NEW capability spec)
- 定义 NATS subject 命名规约：`ohlcv.{exchange}.{symbol_normalized}.{timeframe}`
- 定义 event payload 字段 schema（与 `ohlcv_futures` 行对齐 + `ingested_at` 字段）
- 定义 publish 时机：ClickHouse insert 成功后 publish；publish 失败不阻塞写入
- 定义 JetStream 持久化策略：stream `OHLCV`、保留 24 小时

### 修改能力 `baseline-infrastructure`
- **MODIFY** Requirement: Service Topology — 新增 `nats` 服务到 `docker-compose.yml`
- **MODIFY** Requirement: Required Environment Variables — 新增 `NATS_URL` / `NATS_ENABLE`
- **MODIFY** Requirement: Module Wiring Convention — `nats` sub-probe 加入 `/health` 模块列表

### 代码改动
- 新增 `src/events/` 模块：`NatsPublisher` 类 + `event_schema.py` 字段定义
- 修改 `src/storage/clickhouse.py`：成功 insert OHLCV 后 await `publisher.publish_kline(row)`，publish 失败只 log warn 不 raise
- 修改 `docker-compose.yml`：加 `nats` 服务（`nats:2.10-alpine` 镜像、4222 端口、`-js` flag 启 JetStream）
- 修改 `src/main.py`：startup 注入 `NatsPublisher` 到 ClickHouseManager + 给 `/api/v1/health` 加 `nats` sub-probe

### 不动
- volume-monitor / telegram-bot repo 本次不建（下次 session 在新 repo 里走 OpenSpec）
- src/{classification,detection,alerts}/ 业务模块继续工作不动（同进程消费内存对象，不需要订阅 NATS）
- 不删除任何东西

## Impact

**Affected code (data-service)**:
- `docker-compose.yml`（加 nats 服务）
- `src/events/`（新模块）
- `src/storage/clickhouse.py`（insert 后 publish）
- `src/main.py`（注入 publisher + health sub-probe）
- `.env.example`（加 NATS 配置）
- `requirements.txt` / `environment.yml`（加 `nats-py` 依赖）

**Affected production state**:
- jarvis docker compose 多一个 `nats` 容器（~10 MB 镜像，~10 MB RAM 占用，4222 端口）
- ClickHouse 写入路径多一个 `await publisher.publish_kline()` 调用（局部网络 publish 实测 < 1 ms）

**Affected external**:
- 暂无外部消费者（monitor / bot 未建）— 本次只是把"发布者一侧"准备好
- 未来下游消费者 connect `nats://jarvis:4222` 即可订阅 `ohlcv.>` 全部 events

**Risk**: 低。
- NATS publish 失败不阻塞写入（fire-and-forget + warn log）
- NATS 容器死掉 ClickHouse 写入仍然正常
- 回滚 = 把 `NATS_ENABLE=false` 设上，相当于 publisher no-op
- 镜像小、容器轻，跟 jarvis 上其他 11 个容器和谐共处
