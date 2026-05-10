# volume-classification Specification

## Purpose
TBD - created by archiving change add-volume-classification. Update Purpose after archive.
## Requirements
### Requirement: Tier Computation from Recent Trading Volume

The system SHALL classify every actively-traded Binance USDT-perpetual symbol into exactly one of four tiers (`mega`, `large`, `mid`, `small`) based on the symbol's `SUM(quote_volume)` over the most recent 24 hours of 1-minute kline data stored in `ohlcv_futures`. Tier boundaries SHALL be derived from the P25, P50, and P75 quantiles of all participating symbols' 24-hour quote-volume totals, recomputed on every refresh cycle.

#### Scenario: Symbol assigned to tier per quantile cut

- **WHEN** the classifier refreshes and computes P75 = 500 000 000, P50 = 80 000 000, P25 = 10 000 000 USDT
- **THEN** a symbol with `SUM(quote_volume) = 600 000 000` is classified as `mega`; one with `90 000 000` as `large`; one with `25 000 000` as `mid`; one with `2 000 000` as `small`

#### Scenario: Symbol with zero 24h volume excluded

- **WHEN** a symbol has `SUM(quote_volume) = 0` over the past 24 hours (newly listed, delisted, or maintenance halt)
- **THEN** the symbol does NOT appear in the resulting tier dictionary, and `classifier.get_tier(symbol)` returns `None`

### Requirement: Refresh Cadence

The classifier SHALL recompute tiers automatically every 12 hours by default, configurable via the `CLASSIFICATION_REFRESH_HOURS` environment variable (positive integer, hours). The scheduling MUST be performed by the cross-cutting scheduler registered in `src/main.py`'s lifespan; the classification module itself MUST NOT spawn its own threads or asyncio tasks for periodic execution.

#### Scenario: Default refresh interval

- **WHEN** `CLASSIFICATION_REFRESH_HOURS` is unset
- **THEN** `refresh_tiers()` is invoked every 12 hours after the bootstrap-time first run

#### Scenario: Custom refresh interval

- **WHEN** the operator sets `CLASSIFICATION_REFRESH_HOURS=6`
- **THEN** `refresh_tiers()` is invoked every 6 hours

### Requirement: Persistent History Snapshot

Every successful tier computation SHALL be persisted to ClickHouse table `symbol_tiers`, with one row per symbol per refresh cycle. The table MUST allow recovering "what tier this symbol was at any historical timestamp" for audit and explanation of past alerts. Data older than 90 days MAY be reclaimed via TTL.

#### Scenario: Tier history retrievable

- **WHEN** an operator queries `SELECT tier FROM symbol_tiers WHERE symbol='BTC/USDT' AND refreshed_at <= '2026-04-01' ORDER BY refreshed_at DESC LIMIT 1`
- **THEN** ClickHouse returns the tier value that was active for `BTC/USDT` at that point in time

#### Scenario: Each refresh creates a new versioned record

- **WHEN** `refresh_tiers()` runs successfully
- **THEN** all rows for `(exchange, symbol)` from this run get the same `refreshed_at` timestamp, distinct from prior runs; `ReplacingMergeTree` keeps only the latest per `(exchange, symbol, refreshed_at)` triplet

### Requirement: In-Memory Tier Lookup

The classifier SHALL maintain an in-memory dictionary `tier_cache: dict[symbol, tier]` updated atomically at the end of each successful refresh. Downstream modules (detection) MUST query this dictionary via the public method `classifier.get_tier(symbol) -> str | None` rather than running ClickHouse queries on the hot path.

#### Scenario: Lookup returns cached tier

- **WHEN** detection calls `classifier.get_tier('BTC/USDT')` after a successful refresh that classified BTC/USDT as `mega`
- **THEN** the call returns `'mega'` in O(1) time without any I/O

#### Scenario: Lookup before first refresh

- **WHEN** detection calls `classifier.get_tier('BTC/USDT')` before the first refresh has completed (e.g. during startup or after a refresh failure that left cache empty)
- **THEN** the call returns `None` and detection treats the symbol as "skipped this cycle"

### Requirement: Refresh Resilience

A failed `refresh_tiers()` invocation (ClickHouse query error, network blip, etc.) MUST NOT corrupt or empty the previously cached tier dictionary. The classifier SHALL retain the prior valid cache and surface the failure via the health endpoint. The scheduler SHALL NOT retry within the same interval (next attempt comes at the next scheduled cycle).

#### Scenario: Transient ClickHouse failure preserves prior cache

- **GIVEN** a previous `refresh_tiers()` populated the cache with 475 entries
- **WHEN** the next `refresh_tiers()` raises `ConnectionError`
- **THEN** `classifier.get_tier(symbol)` continues to return values from the prior cache, the failure is logged, and `/api/v1/health.classification.status` becomes `degraded`

### Requirement: Health Sub-probe

The classification module SHALL contribute a sub-key `classification` to the `GET /api/v1/health` JSON response, exposing at minimum: `status` (`ok` | `degraded` | `failed`), `last_refresh` (ISO 8601 UTC), `next_refresh` (ISO 8601 UTC), `symbol_count` (object mapping each tier name to count), and the most recent `p25` / `p50` / `p75` thresholds in USDT.

#### Scenario: Healthy classifier

- **WHEN** `refresh_tiers()` completed within the last 12 hours and cache contains entries
- **THEN** `/api/v1/health` returns `"classification": { "status": "ok", "last_refresh": "...", "symbol_count": { "mega": 119, ... }, ... }`

#### Scenario: Stale classifier

- **WHEN** the most recent successful refresh is older than `2 × CLASSIFICATION_REFRESH_HOURS`
- **THEN** the sub-probe reports `"status": "degraded"` and the parent `status` field becomes `degraded`

### Requirement: Inspection Endpoint

The classifier SHALL expose `GET /api/v1/classification/tiers` that returns the current in-memory `tier_cache` as JSON `{symbol: tier}`. This endpoint is intended for operators and integration tests; it MUST be rate-limited or guarded only to the extent that other read-only endpoints are.

#### Scenario: Tiers endpoint returns full mapping

- **WHEN** an operator issues `GET /api/v1/classification/tiers` with the cache populated
- **THEN** the response body is a JSON object with every classified symbol as a key and its tier (`mega`/`large`/`mid`/`small`) as the value

