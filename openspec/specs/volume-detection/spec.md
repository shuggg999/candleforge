# volume-detection Specification

## Purpose
TBD - created by archiving change add-volume-detection. Update Purpose after archive.
## Requirements
### Requirement: Periodic Detection Cycle

The system SHALL execute a detection cycle every `DETECTION_INTERVAL_MINUTES` minutes (default 5), aligned to the wall-clock interval boundary (e.g. for the 5-minute default: 00, 05, 10, ...). Each cycle MUST be triggered by the application-wide APScheduler instance, MUST run with `max_instances=1` to prevent overlapping invocations, and MUST log start / completion / duration.

#### Scenario: First run aligns to wall-clock boundary

- **WHEN** the application starts at 14:23:42 with default 5-minute interval
- **THEN** the first detection cycle runs at 14:25:00 (next 5-min boundary), and subsequent runs at 14:30:00, 14:35:00, ...

#### Scenario: Long-running cycle does not overlap

- **WHEN** a detection cycle takes 7 minutes to complete (abnormally slow)
- **THEN** the next scheduled trigger at the next 5-min boundary is **skipped** (max_instances=1), and the following one runs after the slow cycle finishes

### Requirement: Ratio Computation

For each Binance USDT-perpetual symbol that has at least `DETECTION_MIN_SAMPLES` (default 1200) one-minute kline rows in the past `DETECTION_BASELINE_HOURS` (default 24), the detector SHALL compute:

1. `current_avg` = mean of `volume` over the most recent `DETECTION_CURRENT_MINUTES` (default 5) one-minute kline rows
2. `baseline_median` = median of `volume` over the past `DETECTION_BASELINE_HOURS` (default 24) one-minute kline rows
3. `ratio = current_avg / baseline_median`, with `ratio = 0` when `baseline_median = 0` (skip downstream classification)

All three values MUST be derived from a **single ClickHouse query** per cycle (not 475 separate queries) using SQL aggregation grouped by symbol.

#### Scenario: Ratio computed correctly

- **GIVEN** symbol BTC/USDT has 24h baseline_median = 8 000 000 USDT and 5min current_avg = 28 000 000 USDT
- **WHEN** detection cycle runs
- **THEN** the computed `ratio` for BTC/USDT equals 3.5

#### Scenario: Insufficient samples skipped

- **WHEN** a symbol has only 800 one-minute rows in the past 24h (newly listed)
- **THEN** the symbol is excluded from the cycle's results entirely, and its absence is reflected in the `symbols_skipped_low_data` health metric

### Requirement: Tier-Based Threshold Classification

The detector SHALL classify each `(symbol, ratio)` pair into one of `normal | warn | strong | extreme` levels by looking up the symbol's tier (via `Classifier.get_tier(symbol)`) and applying the threshold table for that tier. The default threshold table SHALL be:

| Tier | warn | strong | extreme |
|------|------|--------|---------|
| mega | 3.0 | 5.0 | 10.0 |
| large | 5.0 | 10.0 | 20.0 |
| mid | 10.0 | 20.0 | 50.0 |
| small | 20.0 | 50.0 | 100.0 |

Threshold values MAY be overridden via the `DETECTION_THRESHOLDS` environment variable, parsed as `tier:warn,strong,extreme;...` semicolon-separated form.

#### Scenario: Mega tier with ratio 4.0 classified as warn

- **GIVEN** BTC/USDT is in tier `mega` with default thresholds
- **WHEN** ratio = 4.0
- **THEN** level = `warn`

#### Scenario: Small tier with ratio 25.0 classified as warn

- **GIVEN** MEMECOIN/USDT is in tier `small` with default thresholds
- **WHEN** ratio = 25.0
- **THEN** level = `warn` (since small.warn=20, small.strong=50)

#### Scenario: Symbol with no tier skipped

- **WHEN** `Classifier.get_tier(symbol)` returns `None` (symbol not yet classified or zero 24h volume)
- **THEN** the symbol is skipped from the current cycle, no event emitted

### Requirement: Cross-Cycle State Comparison (Escalation)

The detector SHALL maintain an in-memory `last_levels: dict[symbol, Level]` updated at the end of every cycle. An `AlertEvent` SHALL be emitted **only when** the symbol's current level is `warn` / `strong` / `extreme` AND the current level is **strictly higher** than `last_levels[symbol]` (treating `None` and `normal` as the lowest). Same-level cycles, downgrades, and recoveries to `normal` MUST NOT emit events.

The `last_levels` dictionary MUST be updated for **all** classified symbols (not only escalation cases) so that subsequent cycles see correct prior state.

#### Scenario: First-time entry to warn emits event

- **GIVEN** `last_levels['BTC/USDT'] = normal`
- **WHEN** current cycle classifies BTC/USDT as `warn`
- **THEN** an `AlertEvent` is emitted with `is_escalation=True, prev_level=None`

#### Scenario: Escalation from warn to extreme emits event

- **GIVEN** `last_levels['DOGE/USDT'] = warn` (from prior cycle)
- **WHEN** current cycle classifies DOGE/USDT as `extreme`
- **THEN** an event is emitted with `is_escalation=True, prev_level=warn`

#### Scenario: Same-level repeat does not emit

- **GIVEN** `last_levels['SOL/USDT'] = strong`
- **WHEN** current cycle also classifies SOL/USDT as `strong`
- **THEN** no event is emitted, but `last_levels['SOL/USDT']` remains `strong`

#### Scenario: Downgrade does not emit

- **GIVEN** `last_levels['ADA/USDT'] = extreme`
- **WHEN** current cycle classifies ADA/USDT as `warn`
- **THEN** no event is emitted, and `last_levels['ADA/USDT']` is updated to `warn` (downgrade tracked silently)

#### Scenario: Process restart re-emits all currently abnormal symbols

- **WHEN** the process restarts and `last_levels` is empty, then the first detection cycle runs and classifies 7 symbols as warn/strong/extreme
- **THEN** all 7 emit `AlertEvent` with `prev_level=None` (this is the documented behavior — operators must re-see current abnormal state after restart)

### Requirement: AlertEvent Contract

Detection SHALL emit events as `AlertEvent` immutable dataclass instances with the exact fields: `symbol: str`, `tier: str`, `level: Level`, `ratio: float`, `current_avg: float`, `baseline_median: float`, `threshold_used: float`, `detected_at: datetime` (UTC), `is_escalation: bool`, `prev_level: Level | None`. The dataclass MUST be `frozen=True` to prevent accidental mutation by downstream consumers.

#### Scenario: Event includes all required fields

- **WHEN** detection emits an event for a `mega` tier symbol exceeding warn threshold
- **THEN** the AlertEvent has `threshold_used = 3.0` (the warn threshold for mega), and `current_avg`, `baseline_median`, `ratio` reflecting the detection cycle's measurements

### Requirement: Pluggable Publisher

The detector SHALL accept an `AlertPublisher` Protocol (interface) at construction time and call `publisher.publish(events)` after each successful cycle. The default `LoggingPublisher` MUST log each event as structured JSON. Downstream changes (e.g. add-telegram-alerts) MUST be able to inject a different publisher implementation without modifying detection module code.

#### Scenario: Default publisher logs events

- **WHEN** detection cycle produces 3 events with the default `LoggingPublisher`
- **THEN** 3 structured log lines are emitted to the application logger at INFO level, each containing the event's JSON representation

#### Scenario: Custom publisher receives events

- **GIVEN** detection is constructed with a custom `MyPublisher` implementing `AlertPublisher`
- **WHEN** detection cycle produces events
- **THEN** `MyPublisher.publish(events)` is called once per cycle (even with empty list), and `LoggingPublisher` is NOT called

### Requirement: Cold-Start Backfill

On application startup, the system SHALL ensure ClickHouse contains at least `DETECTION_BASELINE_HOURS` (default 24) of contiguous one-minute kline data for all enabled exchange's USDT-perpetual symbols before the scheduler starts emitting detection cycles. If data is missing, a backfill SHALL fetch missing klines from the Binance REST API `/fapi/v1/klines` endpoint, throttled to at most `BACKFILL_BATCH_RPS` (default 10) requests per second.

#### Scenario: Backfill runs when data missing

- **WHEN** the application starts on a fresh ClickHouse with no historical data
- **THEN** before scheduler starts, backfill fetches 24h × 475 symbols of 1m klines from Binance REST, throttled to ≤10 req/sec, and persists to `ohlcv_futures`

#### Scenario: Backfill skips when data complete

- **WHEN** the application starts and ClickHouse already has 24h+ contiguous data for all symbols
- **THEN** backfill completes in under 5 seconds (only verification queries) and does not call Binance REST

#### Scenario: Backfill respects rate limit

- **WHEN** backfill is in progress
- **THEN** observed request rate to Binance does not exceed `BACKFILL_BATCH_RPS` over any 1-second window

### Requirement: Health Sub-probe

The detection module SHALL contribute a sub-key `detection` to `GET /api/v1/health` exposing at minimum: `status` (`ok` / `degraded` / `failed`), `last_run` (ISO 8601 UTC), `last_run_duration_ms`, `active_alerts` (object mapping `warn`/`strong`/`extreme` to count of symbols currently in that level), and `symbols_skipped_low_data`.

#### Scenario: Healthy detector

- **WHEN** the most recent cycle completed successfully within `2 × DETECTION_INTERVAL_MINUTES`
- **THEN** sub-probe returns `"status": "ok"`

#### Scenario: Stale detector

- **WHEN** the most recent successful cycle is older than `2 × DETECTION_INTERVAL_MINUTES`
- **THEN** sub-probe returns `"status": "degraded"` and parent health status downgrades

### Requirement: Inspection Endpoints

The system SHALL expose three diagnostic HTTP endpoints under `/api/v1/detect/`:

- `POST /api/v1/detect/run` — manually trigger one detection cycle, returning the produced AlertEvent list as JSON (used for debugging and ad-hoc inspection)
- `GET /api/v1/detect/state` — return the current `last_levels` dictionary plus per-tier active alert counts
- `GET /api/v1/detect/thresholds` — return the currently effective threshold table (after applying any `DETECTION_THRESHOLDS` env override)

#### Scenario: Manual trigger returns events

- **WHEN** an operator issues `POST /api/v1/detect/run`
- **THEN** the response body is a JSON array of AlertEvent dicts (possibly empty), and `last_levels` is updated as if a normal cycle had run

#### Scenario: Thresholds endpoint reflects override

- **GIVEN** `DETECTION_THRESHOLDS=mega:2,4,8` is set
- **WHEN** an operator GETs `/api/v1/detect/thresholds`
- **THEN** the response shows mega tier as `{warn: 2.0, strong: 4.0, extreme: 8.0}` and other tiers at default values

