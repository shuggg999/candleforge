# candleforge

> Pure data relay for cryptocurrency perpetual-futures K-line data.
> WebSocket collect → ClickHouse persist → NATS publish.

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

## What this is

This service is the **data plane** of a small distributed trading-data platform. It does exactly three things:

1. **Subscribe** to exchange WebSocket streams (Binance USDT-perpetual contracts, ~475 symbols × 6 timeframes)
2. **Persist** every K-line to a partitioned ClickHouse table (`ohlcv_futures`)
3. **Publish** each successful insert to NATS JetStream as a typed event

It deliberately holds **no business logic** — no detection, no alerting, no thresholding. Downstream services subscribe to the event bus to do their own work. See the [Sibling Services](#sibling-services) section.

## Architecture

```
        ┌─────────────────────────────────────┐
        │     Exchange WebSocket Streams      │
        │   (Binance fstream, OKX, Bybit)     │
        └────────────────┬────────────────────┘
                         │
                         ▼
        ┌─────────────────────────────────────┐
        │     candleforge          │  ← this repo
        │   ┌──────────────────────────────┐  │
        │   │  WS collector (asyncio)      │  │
        │   │  REST recovery (gap fill)    │  │
        │   │  Health endpoint /api/v1/*   │  │
        │   └──────────────────────────────┘  │
        └──────┬─────────────────────────┬────┘
               │ insert                  │ publish
               ▼                         ▼
        ┌───────────────┐         ┌─────────────┐
        │   ClickHouse  │         │     NATS    │
        │ ohlcv_futures │         │  JetStream  │
        │  (per-tf TTL) │         │   OHLCV     │
        └───────────────┘         └─────────────┘
                                         │
                                         │ subject: ohlcv.{exchange}.{symbol}.{tf}
                                         ▼
                         ┌─────────────────────────────────┐
                         │       downstream consumers      │
                         │  (see Sibling Services below)   │
                         └─────────────────────────────────┘
```

## Sibling Services

These run as separate processes, subscribe to the same NATS bus, and own all business logic. Pick the ones you need or build your own.

| Service | Role |
|---|---|
| [volume-monitor](https://github.com/shuggg999/volume-monitor) | Symbol-tier classification + 5-minute volume anomaly detection, publishes alert webhooks |
| [telegram-bot](https://github.com/shuggg999/telegram-bot) | HTTP `POST /alerts` receiver + Telegram delivery + per-chat rate limiting + audit log |

## Quick Start

### Prerequisites
- Docker & Docker Compose v2
- ~10 GB free disk for ClickHouse + NATS volumes
- For exchanges blocked from your network: a SOCKS5 proxy

### Run

```bash
git clone https://github.com/shuggg999/candleforge.git
cd candleforge
cp .env.example .env
# edit .env if you need to override CLICKHOUSE_DATA_DIR or set BINANCE_PROXY_URL
docker compose up -d
```

Three containers come up: `clickhouse-db`, `nats`, `data-service`.

### Verify

```bash
# Container health
docker compose ps

# Service health (status + per-module sub-probes)
curl -s http://localhost:8000/api/v1/health | jq

# Latest 100 candles
curl -s 'http://localhost:8000/api/v1/ohlcv/binance/BTC%2FUSDT/5m?limit=100' | jq '.data | length'

# Multi-timeframe in one shot
curl -s 'http://localhost:8000/api/v1/multi-timeframe/BTC%2FUSDT' | jq 'keys'
```

The `/health` body has the shape `{status, details: {database, ws_collectors, recovery, nats}}` — anything failing returns HTTP 503 so Docker's healthcheck flips the container to `unhealthy`.

## API Surface

All under `/api/v1/*`.

| Endpoint | Purpose |
|---|---|
| `GET /health` | Per-module health (ClickHouse, WS collectors, recovery loop, NATS publisher) |
| `GET /status` | Detailed service status |
| `GET /ohlcv/{exchange}/{symbol}/{timeframe}` | Single timeframe history |
| `GET /multi-timeframe/{symbol}` | All timeframes for a symbol in one call |

Symbols use slash-form (`BTC/USDT`); URL-encode the slash (`BTC%2FUSDT`). Timeframes: `1m`, `5m`, `15m`, `1h`, `4h`, `1d`.

## Event Bus Schema

Every successful ClickHouse insert publishes one JSON message to NATS JetStream stream `OHLCV`:

- **Subject**: `ohlcv.{exchange}.{symbol_normalized}.{timeframe}` — e.g. `ohlcv.binance.BTCUSDT.5m`
- **Stream retention**: 24h, max 1 GiB
- **Payload**: `{schema_version, exchange, symbol, symbol_normalized, timeframe, timestamp, open, high, low, close, volume, turnover, trades_count, data_quality, is_closed, ingested_at}` — numeric fields as decimal strings to preserve precision

Full spec: `openspec/specs/nats-event-bus/spec.md`.

Subscribe with `nats sub` to sanity-check:

```bash
docker run --rm --network candleforge_default natsio/nats-box \
  nats -s nats://nats:4222 sub 'ohlcv.binance.BTCUSDT.>'
```

## Configuration

All runtime knobs live in `.env`. See `.env.example` for the complete list with comments. Highlights:

| Variable | Default | Notes |
|---|---|---|
| `CLICKHOUSE_DATA_DIR` | `./data/clickhouse` | Host path for ClickHouse volume — override for production |
| `ENABLED_EXCHANGES` | `binance` | Comma-separated; only `binance` is fully wired |
| `SYMBOLS` | `ALL` | `ALL` = every USDT-perpetual, or e.g. `BTC/USDT,ETH/USDT` |
| `TIMEFRAMES` | `1m,5m,15m,1h,4h,1d` | What to subscribe |
| `TTL_1M` ... `TTL_1D` | per-timeframe (days) | ClickHouse partition TTL |
| `BINANCE_PROXY_URL` | (empty) | e.g. `socks5h://host.docker.internal:10808` |
| `NATS_URL` | `nats://nats:4222` | Override for out-of-cluster subscribers |
| `NATS_ENABLE` | `true` | `false` makes the publisher a no-op |

**This repo does NOT contain Telegram, alert, detection, or classification configuration.** Those live in the sibling-service repos (`volume-monitor/.env.example`, `telegram-bot/.env.example`).

## Project Structure

```
src/
├── main.py                 FastAPI app, lifespan, /health
├── config.py               pydantic-settings
├── collectors/             exchange-specific WS clients
│   ├── base.py
│   ├── binance.py
│   └── manager.py
├── storage/
│   └── clickhouse.py       async writes + publish hook
├── events/
│   ├── nats_publisher.py   JetStream publisher
│   └── event_schema.py     payload contract
├── services/
│   └── recovery.py         REST gap-fill (currently dormant)
├── api/
│   ├── routes.py           OHLCV queries
│   └── freqtrade.py        Freqtrade-compatible adapter
└── utils/

tests/
├── unit/                   tests for events, storage, health endpoint, init SQL
└── integration/

openspec/
├── specs/                  current capabilities: baseline-infrastructure, nats-event-bus
└── changes/archive/        every change ever made, in order
```

## Known Gotchas

1. **Binance futures WebSocket needs `/market/` in the URL.** `wss://fstream.binance.com/market/ws/...`, not `wss://fstream.binance.com/ws/...`. The latter accepts the handshake and subscribe frame but never pushes any data. Fixed at `src/collectors/binance.py`. Verify with `SELECT data_quality, count() FROM ohlcv_futures WHERE timestamp >= now() - INTERVAL 5 MINUTE GROUP BY data_quality` — you should see `websocket` rows.

2. **`/api/v1/health` route uniqueness.** FastAPI keeps the first-registered handler. `src/api/routes.py` MUST NOT register `@router.get("/health")` or it silently overrides the rich version in `main.py` and the body degrades to `{status, timestamp}` with no sub-probes.

3. **TTL per-timeframe.** `config/clickhouse/init.sql` uses one `WHERE timeframe = '<tf>'` clause per timeframe, never an `IN (...)` collapse — otherwise changing one TTL silently changes all of them.

## Development

```bash
# Conda env
conda env create -f environment.yml
conda activate candleforge

# Or pip
pip install -r requirements.txt

# Tests
pytest tests/ -q

# Code format
black src/
```

## Spec-Driven Workflow

This repo uses [OpenSpec](https://github.com/Fission-AI/OpenSpec) to track capability changes. Every non-trivial change starts as `openspec/changes/<change-id>/` with `proposal.md`, `tasks.md`, and a per-capability spec delta, then gets archived after implementation.

## License

[AGPL v3](LICENSE). If you run a modified version as a network service, you must publish your source. This is intentional: the architecture is meant to be a shared substrate, not a freebie for closed-source commercial monitoring services.

## Acknowledgements

- [Freqtrade](https://www.freqtrade.io/) — the original API shape this service mirrors
- [ClickHouse](https://clickhouse.com/) — the persistence layer
- [NATS](https://nats.io/) — the event bus

---

**Disclaimer**: Educational and research use only. This service collects market data and exposes a read-only API — it does NOT place orders or hold funds. Cryptocurrency trading is high-risk; use what you build with this data at your own discretion.
