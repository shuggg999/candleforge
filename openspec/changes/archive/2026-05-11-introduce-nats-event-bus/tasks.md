# Tasks: introduce-nats-event-bus

## 1. Pre-flight Checks

- [x] 1.1 确认 `requirements.txt` 没有 `nats-py` —— 若已存在则只做版本检查
- [x] 1.2 Grep `src/` 没有任何 NATS 引用（防止重复实现）
- [x] 1.3 验证 jarvis 端口 4222 / 8222 没被占用：`ssh jarvis "lsof -i :4222 -i :8222 2>&1 | head"`
- [x] 1.4 确认 docker-compose volume `nats-data` 命名跟现有 `clickhouse-data` 命名风格一致

## 2. Write Failing Tests (TDD)

> 测试放 `tests/unit/test_nats_publisher.py` 跟现有 layout 对齐。

- [x] 2.1 写 `test_publisher_subject_format`：`NatsPublisher._build_subject(exchange, symbol, tf)` 必须返回 `ohlcv.<ex>.<sym_normalized>.<tf>`；输入 `("binance", "BTC/USDT", "1m")` → `"ohlcv.binance.BTCUSDT.1m"`
- [x] 2.2 写 `test_publisher_payload_schema`：`_build_payload(row)` 必须包含 13 个字段：exchange/symbol/symbol_normalized/timeframe/timestamp/open/high/low/close/volume/turnover/trades_count/data_quality/is_closed/ingested_at/schema_version；价格字段必须 str 类型而非 float
- [x] 2.3 写 `test_publisher_publishes_on_insert`：mock nats client，调用 `publisher.publish_kline(row)` 后 mock 应该收到 `nc.publish(subject, data)` 调用一次；data 反序列化后 schema_version==1
- [x] 2.4 写 `test_publisher_swallows_errors_logs_warning`：mock `nc.publish` raise 一个 Exception，`publish_kline` 不应该 raise，应该 warn log + `_publish_failure_count` 计数+1
- [x] 2.5 写 `test_publisher_no_op_when_disabled`：`NATS_ENABLE=false` 时 `publish_kline` 直接 return，nc.publish 0 调用
- [x] 2.6 写 `test_clickhouse_insert_calls_publisher`：mock publisher，`ClickHouseManager.insert_ohlcv(row)` 成功后必须 `await publisher.publish_kline(row)` 一次
- [x] 2.7 写 `test_clickhouse_insert_succeeds_when_publisher_raises`：mock publisher.publish_kline raise 异常，insert_ohlcv 必须返回 success（不能传播）
- [x] 2.8 写 `test_health_nats_subprobe_shape`：扩展 `tests/unit/test_health_endpoint.py`，断言 `/api/v1/health` body.details 含 `nats` sub-key，shape: `{status, connected, publish_failure_count, last_publish_age_seconds}`
- [x] 2.9 跑全套测试 FAIL（红） — 验证测试有效

## 3. Add Dependencies

- [x] 3.1 在 `requirements.txt` 加 `nats-py>=2.6.0`
- [x] 3.2 在 `environment.yml` 加 `nats-py>=2.6.0`（pip 段）
- [x] 3.3 `pip install -r requirements.txt` 本地装上确认能 `import nats`

## 4. Implement `src/events/` Module

- [x] 4.1 建目录 `src/events/__init__.py`
- [x] 4.2 写 `src/events/event_schema.py`：定义 `KLineEvent` dataclass + `SCHEMA_VERSION = 1` 常量
- [x] 4.3 写 `src/events/nats_publisher.py`：
  - `class NatsPublisher`: `__init__(url, enabled)` / `async connect()` / `async close()` / `async publish_kline(row)`
  - `_build_subject(exchange, symbol, timeframe) -> str`
  - `_build_payload(row) -> dict`
  - `health() -> dict` 返回 `{status, connected, publish_failure_count, last_publish_age_seconds}`
  - 内部维护 `self._nc`（nats client）+ `self._publish_failure_count` + `self._last_publish_ts`
- [x] 4.4 跑 `pytest tests/unit/test_nats_publisher.py` → 测试 2.1-2.5 转绿

## 5. Wire Publisher Into ClickHouse Insert Path

- [x] 5.1 在 `src/storage/clickhouse.py` 的 `ClickHouseManager.__init__` 加 `self._publisher: Optional[NatsPublisher] = None`
- [x] 5.2 加 `set_publisher(publisher: NatsPublisher)` 方法（避免循环 import）
- [x] 5.3 在 `insert_ohlcv()` 成功 commit 后调 `await self._publisher.publish_kline(row)`（包 try/except 兜底）
- [x] 5.4 跑 `pytest tests/unit/test_nats_publisher.py::test_clickhouse_insert_calls_publisher tests/unit/test_nats_publisher.py::test_clickhouse_insert_succeeds_when_publisher_raises` → 转绿

## 6. Wire Publisher Into main.py Lifespan + /health

- [x] 6.1 `src/config.py` 加 `NATS_URL: str = "nats://nats:4222"` + `NATS_ENABLE: bool = True` settings
- [x] 6.2 `.env.example` 加对应行：`NATS_URL=nats://nats:4222` / `NATS_ENABLE=true`
- [x] 6.3 `src/main.py` startup：在 `db_manager.initialize()` 之后建 `NatsPublisher(url, enable)` → `await publisher.connect()` → `db_manager.set_publisher(publisher)`
- [x] 6.4 `src/main.py` shutdown：`await publisher.close()`
- [x] 6.5 `src/main.py` health_check：加 `nats` sub-probe，调 `service.publisher.health()`；包在 `_safe_subprobe()` 里
- [x] 6.6 跑 `pytest tests/unit/test_health_endpoint.py::test_health_nats_subprobe_shape` → 转绿

## 7. Docker-Compose: Add nats Service

- [x] 7.1 编辑 `docker-compose.yml`，加 `nats` 服务（image: `nats:2.10-alpine`，command: `-js -sd /data -m 8222`，ports 4222 + 8222，volume `nats-data:/data`，healthcheck via 8222/healthz，restart unless-stopped）
- [x] 7.2 加 `data-service.depends_on: [nats]`（同 clickhouse 一起）
- [x] 7.3 加 `volumes: nats-data:` 声明
- [x] 7.4 本地 `docker compose config` 验证 yaml 没语法错

## 8. JetStream Stream Bootstrap

- [x] 8.1 在 `NatsPublisher.connect()` 里调 `js.add_stream(name="OHLCV", subjects=["ohlcv.>"], max_age=24h, max_bytes=1GB, storage=File)`；用 `try/except StreamAlreadyExistsError` 兜底（幂等）
- [x] 8.2 写 `test_publisher_bootstraps_jetstream_stream`：mock nc.jetstream() → `.add_stream` 必须被调用一次，参数含 subjects=`["ohlcv.>"]`、max_age=24h

## 9. Full Test Sweep

- [x] 9.1 跑 `pytest tests/ --no-cov -q` 全套绿（67 旧 + ~9 新 = ~76）
- [x] 9.2 跑 `openspec validate introduce-nats-event-bus --strict`

## 10. Local Container Smoke Test (Optional)

> 用户本地 Mac 网络不通 Binance，但可以验证 NATS 容器 + publish 路径。需要起 docker compose 才能验。如果跳过则只靠 jarvis 部署验证。

- [x] 10.1 本地 `docker compose up -d nats` 起 NATS（不起 data-service 避免 WS 失败）
- [x] 10.2 `docker exec nats nats-cli stream info OHLCV` 确认 stream 创建成功

## 11. jarvis Deploy + Verify

- [x] 11.1 git push gitea main
- [x] 11.2 ssh jarvis pull + `docker compose up -d --build`（nats + data-service 一起 rebuild）
- [x] 11.3 等 data-service healthy（docker compose ps）
- [x] 11.4 验证 `curl http://192.168.110.51:8100/api/v1/health` 包含 `nats: {status: ok, connected: true, publish_failure_count: 0, last_publish_age_seconds: <小于 60>}`
- [x] 11.5 在 jarvis 上跑 `docker exec nats wget -qO- http://localhost:8222/jsz` 看 JetStream 状态
- [x] 11.6 在 jarvis 上 subscribe 验证：`docker run --rm --network freqtrade-data-service_default natsio/nats-box:latest nats sub -s nats://nats:4222 'ohlcv.binance.BTCUSDT.1m'` 应该几秒内收到 message
- [x] 11.7 观察 5 分钟，确认 publish_failure_count 仍为 0

## 12. Archive

- [x] 12.1 PR / commit 推到 gitea main（如果是分步 commit，本步是最后一次）
- [x] 12.2 `openspec archive introduce-nats-event-bus --yes`
