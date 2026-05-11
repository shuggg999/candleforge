-- Create database if not exists
CREATE DATABASE IF NOT EXISTS crypto_data;

USE crypto_data;

-- Main OHLCV table for futures data
CREATE TABLE IF NOT EXISTS ohlcv_futures (
    exchange String COMMENT 'Exchange name (binance/okx/bybit)',
    symbol String COMMENT 'Trading pair symbol (e.g., BTC/USDT)',
    timeframe String COMMENT 'Timeframe (1m/5m/15m/1h/4h/1d)',
    timestamp DateTime64(3) COMMENT 'Candle timestamp in Beijing time (Asia/Shanghai)',
    open Decimal64(8) COMMENT 'Open price',
    high Decimal64(8) COMMENT 'High price',
    low Decimal64(8) COMMENT 'Low price',
    close Decimal64(8) COMMENT 'Close price',
    volume Decimal128(4) COMMENT 'Volume in contracts',
    turnover Decimal128(4) COMMENT 'Turnover in USDT',
    open_interest Nullable(Decimal128(4)) COMMENT 'Open interest',
    funding_rate Nullable(Decimal32(8)) COMMENT 'Funding rate',
    trades_count Nullable(UInt32) COMMENT 'Number of trades',
    buy_volume Nullable(Decimal128(4)) COMMENT 'Buy volume',
    created_at DateTime DEFAULT now() COMMENT 'Record creation time',
    data_quality String DEFAULT 'raw' COMMENT 'Data quality indicator (raw/validated/cleaned/synthetic)'
)
ENGINE = ReplacingMergeTree()
PARTITION BY toYYYYMM(timestamp)
PRIMARY KEY (exchange, symbol, timeframe, timestamp)
ORDER BY (exchange, symbol, timeframe, timestamp)
TTL timestamp + INTERVAL 7    DAY WHERE timeframe = '1m',
    timestamp + INTERVAL 30   DAY WHERE timeframe = '5m',
    timestamp + INTERVAL 90   DAY WHERE timeframe = '15m',
    timestamp + INTERVAL 365  DAY WHERE timeframe = '1h',
    timestamp + INTERVAL 365  DAY WHERE timeframe = '4h',
    timestamp + INTERVAL 1825 DAY WHERE timeframe = '1d'
SETTINGS index_granularity = 8192;

-- Create indexes for better query performance
ALTER TABLE ohlcv_futures ADD INDEX IF NOT EXISTS idx_symbol (symbol) TYPE bloom_filter() GRANULARITY 4;
ALTER TABLE ohlcv_futures ADD INDEX IF NOT EXISTS idx_timeframe (timeframe) TYPE set(10) GRANULARITY 2;

-- Create materialized view for latest prices
CREATE MATERIALIZED VIEW IF NOT EXISTS ohlcv_latest
ENGINE = Memory
POPULATE AS
SELECT 
    exchange,
    symbol,
    timeframe,
    argMax(timestamp, timestamp) as latest_timestamp,
    argMax(close, timestamp) as latest_price,
    argMax(volume, timestamp) as latest_volume,
    count() as total_candles
FROM ohlcv_futures
GROUP BY exchange, symbol, timeframe;

-- Create table for tracking data gaps
CREATE TABLE IF NOT EXISTS data_gaps (
    exchange String,
    symbol String,
    timeframe String,
    gap_start DateTime64(3),
    gap_end DateTime64(3),
    gap_size_minutes UInt32,
    recovered Boolean DEFAULT 0,
    recovery_attempted_at Nullable(DateTime),
    created_at DateTime DEFAULT now()
)
ENGINE = MergeTree()
ORDER BY (exchange, symbol, timeframe, gap_start)
TTL created_at + INTERVAL 7 DAY;

-- Create table for collector status
CREATE TABLE IF NOT EXISTS collector_status (
    collector_id String,
    exchange String,
    status String COMMENT 'running/stopped/error',
    last_heartbeat DateTime,
    websocket_connections UInt32,
    messages_received UInt64,
    errors_count UInt64,
    started_at DateTime,
    updated_at DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (collector_id, exchange);

-- Create system stats table
CREATE TABLE IF NOT EXISTS system_stats (
    timestamp DateTime DEFAULT now(),
    total_records UInt64,
    disk_usage_bytes UInt64,
    memory_usage_bytes UInt64,
    active_symbols UInt32,
    api_requests_count UInt64,
    avg_query_time_ms Float32
)
ENGINE = MergeTree()
ORDER BY timestamp
TTL timestamp + INTERVAL 30 DAY;

-- Volume anomaly alerts audit (add-telegram-alerts), 365-day TTL
CREATE TABLE IF NOT EXISTS volume_alerts (
    detected_at DateTime64(3) COMMENT 'Detection cycle timestamp (UTC)',
    exchange LowCardinality(String) COMMENT 'binance/okx/bybit',
    symbol String COMMENT 'Trading pair, e.g. BTC/USDT',
    tier LowCardinality(String) COMMENT 'mega/large/mid/small',
    level LowCardinality(String) COMMENT 'warn/strong/extreme',
    prev_level LowCardinality(String) COMMENT 'normal/warn/strong/extreme/none',
    ratio Float64 COMMENT 'current_avg / baseline_median',
    curr_5min_avg Float64 COMMENT 'mean volume over the last current_minutes window',
    baseline_median Float64 COMMENT 'median volume over baseline_hours',
    threshold_used Float64 COMMENT 'tier threshold value that was crossed',
    telegram_status LowCardinality(String) DEFAULT 'pending' COMMENT 'pending/sent/failed/skipped',
    telegram_error String DEFAULT '' COMMENT 'last error message if failed',
    sent_at Nullable(DateTime64(3)) COMMENT 'when Telegram acknowledged the send'
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(detected_at)
ORDER BY (detected_at, exchange, symbol)
TTL toDateTime(detected_at) + INTERVAL 365 DAY
SETTINGS index_granularity = 8192;

-- Symbol tier classification snapshots (volume-based, refreshed every 12h)
CREATE TABLE IF NOT EXISTS symbol_tiers (
    exchange String COMMENT 'Exchange name (binance/okx/bybit)',
    symbol String COMMENT 'Trading pair symbol (e.g., BTC/USDT)',
    refreshed_at DateTime64(3) COMMENT 'Refresh cycle timestamp (UTC), all rows in one cycle share the same value',
    tier LowCardinality(String) COMMENT 'Tier label: mega / large / mid / small',
    quote_volume_24h Decimal128(4) COMMENT 'Sum of quote_volume over the past 24h at refresh time (USDT)',
    p25 Decimal128(4) COMMENT 'Global P25 threshold for this cycle (USDT)',
    p50 Decimal128(4) COMMENT 'Global P50 threshold for this cycle (USDT)',
    p75 Decimal128(4) COMMENT 'Global P75 threshold for this cycle (USDT)',
    symbol_count UInt32 COMMENT 'Total participating symbols in this cycle (audit aid)'
)
ENGINE = ReplacingMergeTree()
PARTITION BY toYYYYMM(refreshed_at)
PRIMARY KEY (exchange, symbol, refreshed_at)
ORDER BY (exchange, symbol, refreshed_at)
TTL toDateTime(refreshed_at) + INTERVAL 90 DAY
SETTINGS index_granularity = 8192;

-- Insert initial collector status
INSERT INTO collector_status (collector_id, exchange, status, last_heartbeat, websocket_connections, messages_received, errors_count, started_at)
VALUES ('main', 'binance', 'stopped', now(), 0, 0, 0, now());

-- Note: Function for data gap detection will be created separately if needed
-- The CREATE FUNCTION syntax varies between ClickHouse versions