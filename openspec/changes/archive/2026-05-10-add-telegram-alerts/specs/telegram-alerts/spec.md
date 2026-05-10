## ADDED Requirements

### Requirement: AlertPublisher Implementation

The `Notifier` class SHALL implement the `AlertPublisher` Protocol defined in `src/detection/`. The `publish(events)` method MUST: (a) batch insert all events into ClickHouse `volume_alerts` table with `telegram_status='pending'`, then (b) concurrently send each event as a Telegram message, then (c) update `telegram_status` to `sent` / `failed` per result.

#### Scenario: Empty event list is no-op

- **WHEN** `notifier.publish([])` is called
- **THEN** no ClickHouse writes occur, no Telegram requests occur, and the call returns successfully

#### Scenario: Three events fully processed

- **GIVEN** Telegram is reachable and configured
- **WHEN** `notifier.publish([e1, e2, e3])` is called
- **THEN** ClickHouse `volume_alerts` table contains 3 new rows (initial status `pending`, then updated to `sent`), and 3 Telegram messages are sent

### Requirement: Telegram Message Format

Each AlertEvent SHALL render to a Markdown-formatted Telegram message containing: a level-specific emoji (🟡 warn / 🟠 strong / 🔴 extreme), the symbol, tier name in human-readable form (e.g. `mega (超大盘)`), the ratio, the threshold that was crossed, current and baseline volume values, and the UTC timestamp. Messages for escalations (`prev_level` is not None) MUST include an `⬆ from <PREV_LEVEL>` marker.

#### Scenario: Warn event renders correctly

- **WHEN** `format_message(event)` is called on a `mega` tier `warn` level event with `ratio=3.4, current_avg=28500000, baseline_median=8400000`
- **THEN** the output contains: `🟡 *VOLUME WARN*`, `BTC/USDT`, `mega (超大盘)`, `*3.4×*`, `warn=3×`, `28.5M USDT`, `8.4M USDT`, ISO timestamp UTC

#### Scenario: Escalation marker present

- **WHEN** an event has `prev_level=Level.STRONG` and `level=Level.EXTREME`
- **THEN** the rendered message contains `⬆ from STRONG`

### Requirement: Persistent Audit Table

All events received by `Notifier.publish()` SHALL be written to ClickHouse table `volume_alerts` with the following columns: `detected_at`, `exchange`, `symbol`, `tier`, `level`, `prev_level`, `ratio`, `curr_5min_avg`, `baseline_median`, `threshold_used`, `telegram_status` (`pending`/`sent`/`failed`/`skipped`), `telegram_error`, `sent_at`. The table MUST be a `MergeTree` partitioned by `toYYYYMM(detected_at)` with TTL 365 days.

#### Scenario: Audit row written even if Telegram fails

- **GIVEN** Telegram returns 500 on every attempt
- **WHEN** `notifier.publish([e1])` is called
- **THEN** ClickHouse `volume_alerts` table contains 1 row with `telegram_status='failed'` and `telegram_error` populated; the call does NOT raise

### Requirement: Retry on Telegram Failure

Each Telegram send SHALL be wrapped with `tenacity` retry: 3 attempts with exponential backoff (2s / 4s / 8s). On HTTP 429 (rate limit), the system SHALL parse the `Retry-After` header and wait the indicated seconds before retrying once more. After all retries exhausted, the row's `telegram_status` is set to `failed` and a structured log line is emitted.

#### Scenario: Transient failure recovers

- **GIVEN** Telegram returns 502 once then 200
- **WHEN** notifier sends an event
- **THEN** the event is sent successfully on retry; `telegram_status='sent'`

#### Scenario: 429 rate limit honored

- **GIVEN** Telegram returns 429 with `Retry-After: 10`
- **WHEN** notifier attempts to send
- **THEN** notifier waits ≥10 seconds then retries once; if 200 returned, marks sent; else marks failed

### Requirement: Dry-Run Mode

When `ALERTS_DRY_RUN=true` (or when `TELEGRAM_BOT_TOKEN` is unset or empty), the Notifier SHALL still write all events to `volume_alerts` table but MUST NOT call the Telegram Bot API. The `telegram_status` column SHALL be `skipped` and `/api/v1/health.alerts.telegram_dry_run` SHALL be `true`.

#### Scenario: Dry-run via env var

- **GIVEN** `ALERTS_DRY_RUN=true`
- **WHEN** notifier publishes 5 events
- **THEN** 5 rows are written to `volume_alerts` with `telegram_status='skipped'`, no Telegram HTTP calls are made

#### Scenario: Auto dry-run when bot token missing

- **GIVEN** `TELEGRAM_BOT_TOKEN` is empty (not configured)
- **WHEN** the application starts
- **THEN** Notifier auto-enables dry-run mode, logs a WARNING explaining the auto-detection, and `/health.alerts.status='degraded'`

### Requirement: Health Sub-probe

The alerts module SHALL contribute a sub-key `alerts` to `GET /api/v1/health` exposing at minimum: `status` (`ok` / `degraded` / `failed`), `telegram_configured` (bool), `telegram_dry_run` (bool), `last_send_at` (ISO 8601 UTC or null), `stats_24h` (object with `sent`, `failed`, and per-level breakdown counts queried from `volume_alerts` over the last 24h).

#### Scenario: Alerts healthy

- **WHEN** notifier sent at least 1 message in the last 24h with no recent failures
- **THEN** sub-probe returns `"status": "ok"`

#### Scenario: Recent failures degrade status

- **WHEN** the last 6 consecutive Telegram sends all returned `failed` status
- **THEN** sub-probe returns `"status": "degraded"` and parent health degrades

### Requirement: Inspection & Test Endpoints

The system SHALL expose:

- `POST /api/v1/alerts/test` — sends a hard-coded test message to Telegram (`telegram_status='sent'` row added with `prev_level='none', level='warn', symbol='TEST/USDT'`); response indicates send result
- `GET /api/v1/alerts/recent?limit=N&min_level=warn` — returns the most recent N rows from `volume_alerts`, optionally filtered by minimum level
- `GET /api/v1/alerts/stats?since=24h` — returns aggregate stats: total sent, total failed, per-level counts, per-tier counts

#### Scenario: Test endpoint sends real Telegram

- **GIVEN** Telegram is configured and not in dry-run
- **WHEN** an operator calls `POST /api/v1/alerts/test`
- **THEN** a real Telegram message arrives at `TELEGRAM_CHAT_ID`, response body is `{"status": "sent", "message_id": ...}`

#### Scenario: Recent endpoint with filter

- **GIVEN** the past hour has produced events at all levels
- **WHEN** an operator calls `GET /api/v1/alerts/recent?limit=10&min_level=strong`
- **THEN** the response contains at most 10 rows, all with `level >= strong`, ordered by `detected_at DESC`

### Requirement: End-to-End Pipeline Verification

The change SHALL include a single end-to-end integration test that exercises the full pipeline: collector → backfill → classification refresh → detection cycle producing escalation events → notifier publish → `volume_alerts` table populated → Telegram fake server received messages with correct format.

#### Scenario: Full pipeline test passes

- **GIVEN** testcontainers ClickHouse + a Telegram fake HTTP server
- **WHEN** the pipeline runs end-to-end with mock Binance kline data designed to trigger 1 warn event for `BTC/USDT`
- **THEN** `volume_alerts` table contains exactly 1 row with `telegram_status='sent'`; the Telegram fake recorded 1 POST to `/sendMessage` with body matching the expected Markdown format
