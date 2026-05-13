# nats-event-bus Specification

## Purpose
TBD - created by archiving change introduce-nats-event-bus. Update Purpose after archive.
## Requirements
### Requirement: NATS Event Bus Sidecar

The candleforge runtime SHALL include a `nats` container (image `nats:2.10-alpine` or newer compatible 2.10.x tag) running with JetStream enabled (`-js`), file storage (`-sd /data`), and an HTTP monitoring endpoint (`-m 8222`). The container SHALL be defined in the project's `docker-compose.yml` and SHALL persist its data volume across container restarts.

#### Scenario: NATS container starts alongside candleforge

- **WHEN** `docker compose up -d` runs on a fresh checkout with `.env` populated
- **THEN** a `nats` container reaches `Up (healthy)` within 30 seconds, listens on `4222` (client) and `8222` (monitoring), and `candleforge` connects to `nats://nats:4222` during startup

#### Scenario: NATS restart preserves stream history

- **WHEN** `docker compose restart nats` is run while candleforge keeps publishing
- **THEN** after the nats container is back up, prior JetStream stream `OHLCV` is still present, holding messages within the configured 24h `MaxAge` window

### Requirement: JetStream Stream Bootstrap

On candleforge startup, the `NatsPublisher` SHALL ensure a JetStream stream named `OHLCV` exists with the following config: subjects `["ohlcv.>"]`, storage `File`, retention `Limits`, `MaxAge` 24 hours, `MaxBytes` 1 GB, replicas 1. Creation MUST be idempotent (stream already exists is not an error).

#### Scenario: Stream re-created on first start

- **WHEN** candleforge starts on a host where NATS has no `OHLCV` stream
- **THEN** the publisher calls JetStream `add_stream` once with the documented config, and subsequent restarts find the stream and do not error

#### Scenario: Stream config drift detected

- **WHEN** an operator inspects the stream via `docker exec nats nats stream info OHLCV`
- **THEN** the output shows subjects `["ohlcv.>"]`, max_age `24h0m0s`, max_bytes `1.00 GiB`, storage `File`, retention `Limits`

### Requirement: Subject Naming Convention

The NatsPublisher SHALL build the publish subject as `ohlcv.{exchange}.{symbol_normalized}.{timeframe}` where `symbol_normalized` is the exchange-specific concatenated form (e.g. `BTC/USDT` → `BTCUSDT`, `BTC-USD-SWAP` → `BTCUSDSWAP`). Subjects MUST NOT contain `/`, `.`, ` `, or any NATS-illegal character; `symbol_normalized` SHALL be derived deterministically from the canonical symbol stored in `ohlcv_futures.symbol`.

#### Scenario: Standard binance USDT-M pair

- **WHEN** publisher is called with `(exchange="binance", symbol="BTC/USDT", timeframe="1m")`
- **THEN** the published subject is exactly `ohlcv.binance.BTCUSDT.1m`

#### Scenario: Wildcard subscription works as advertised

- **WHEN** a subscriber connects with subject pattern `ohlcv.binance.>` while candleforge publishes BTC/USDT 1m and ETH/USDT 5m events
- **THEN** the subscriber receives both messages within 1 second of publish

### Requirement: K-Line Event Payload Schema

Every published message body SHALL be a UTF-8 encoded JSON object containing exactly the following keys (no more, no fewer) with these types and constraints:

| Field | Type | Constraint |
|---|---|---|
| `schema_version` | integer | currently `1` |
| `exchange` | string | lowercase, e.g. `binance` |
| `symbol` | string | canonical form, e.g. `BTC/USDT` |
| `symbol_normalized` | string | matches subject component |
| `timeframe` | string | one of `1m/5m/15m/1h/4h/1d` |
| `timestamp` | string | ISO 8601 with timezone, K-line open time |
| `open` / `high` / `low` / `close` | string | decimal string, never float |
| `volume` / `turnover` | string | decimal string |
| `trades_count` | integer or null | count of trades in candle |
| `data_quality` | string | matches ClickHouse `data_quality` (websocket/rest_api/...) |
| `is_closed` | boolean | true = final/closed candle |
| `ingested_at` | string | ISO 8601 with timezone, candleforge write time |

#### Scenario: Subscriber deserializes with Decimal precision

- **WHEN** a downstream service receives a message and parses `open` with `Decimal(payload["open"])`
- **THEN** the resulting Decimal exactly matches the value written to ClickHouse without float rounding errors

#### Scenario: Schema version surfaced for compatibility

- **WHEN** a subscriber reads `payload["schema_version"]`
- **THEN** it returns integer `1` for all messages produced by candleforge in this iteration; subscribers can branch on this value when future versions add/remove fields

### Requirement: Publish Semantics — Fire-and-Forget After Persistence

The NatsPublisher's `publish_kline(row)` SHALL be invoked AFTER the corresponding ClickHouse `INSERT INTO ohlcv_futures` has been confirmed successful. Publish failures (broken connection, JetStream rejection, serialization error) MUST NOT raise, MUST NOT block subsequent inserts, MUST log at WARNING level, and MUST increment an internal `publish_failure_count` counter exposed via the `nats` health sub-probe.

#### Scenario: NATS container down during steady-state

- **WHEN** `docker compose stop nats` is invoked while candleforge keeps receiving WS frames
- **THEN** ClickHouse inserts continue to succeed without exception, the `nats` sub-probe in `/api/v1/health` reports `status: "degraded"` with `connected: false`, and `publish_failure_count` grows monotonically

#### Scenario: NATS recovers after outage

- **WHEN** `docker compose start nats` is invoked after a 60-second outage
- **THEN** within 30 seconds the publisher reconnects (handled by nats-py client retry), `/api/v1/health` flips `nats.status` back to `"ok"`, and new K-line events flow to JetStream; messages that occurred during the outage are NOT retroactively published (consumers use SQL queries against ClickHouse for backfill)

#### Scenario: Publish disabled via env

- **WHEN** operator sets `NATS_ENABLE=false` in `.env` and restarts candleforge
- **THEN** the publisher is a no-op: `nc.connect` is never called, `publish_kline` returns immediately, and the `nats` sub-probe reports `status: "ok"` with `connected: false`, `disabled: true`

### Requirement: NATS Sub-Probe on /api/v1/health

The candleforge `/api/v1/health` endpoint SHALL include a `nats` sub-probe in `body.details` with the following shape:

```json
{
  "status": "ok" | "degraded" | "failed",
  "connected": true | false,
  "publish_failure_count": <int>,
  "last_publish_age_seconds": <number or null>,
  "stream_messages": <int or null>,
  "disabled": true | false
}
```

`status` rules:
- `"ok"` — connected, no failures since startup OR `disabled: true`
- `"degraded"` — disconnected (auto-reconnect in progress) OR `publish_failure_count > 100` OR `last_publish_age_seconds > 300`
- `"failed"` — connect never succeeded since startup

#### Scenario: Stable operation reports ok

- **WHEN** candleforge has been publishing K-lines for 5 minutes with no errors
- **THEN** `body.details.nats.status` is `"ok"`, `connected: true`, `publish_failure_count: 0`, `last_publish_age_seconds < 60`

#### Scenario: Connection lost reports degraded

- **WHEN** the nats container has been stopped for 30 seconds while WS frames continue
- **THEN** `body.details.nats.status` is `"degraded"`, `connected: false`, `publish_failure_count > 0`

