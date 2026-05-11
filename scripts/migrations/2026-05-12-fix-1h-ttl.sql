-- Migration: fix-ttl-and-health-probe (2026-05-12)
--
-- Purpose: Repair the `ohlcv_futures` TTL expression on already-deployed clusters.
-- Bug: `WHERE timeframe IN ('15m', '1h')` shared the 90-day TTL across two timeframes,
--      so 1h candles were being dropped after 90 days instead of the documented 365 days
--      (per .env.example: TTL_1H=365). Splitting `IN (...)` into independent clauses
--      lets each timeframe carry its own TTL day count.
--
-- Operation: ALTER TABLE ... MODIFY TTL is a metadata-only change in ClickHouse
-- (no data rewrite, no lock). Safe to run online while collectors are writing.
--
-- Usage on jarvis:
--   docker exec -i clickhouse-db clickhouse-client < scripts/migrations/2026-05-12-fix-1h-ttl.sql
--
-- Verification after running:
--   docker exec clickhouse-db clickhouse-client -q \
--     "SHOW CREATE TABLE crypto_data.ohlcv_futures" | grep -E "1h|1h'"
--   -> should display `INTERVAL 365` (or `toIntervalDay(365)`) WHERE timeframe = '1h'

ALTER TABLE crypto_data.ohlcv_futures
MODIFY TTL
    timestamp + INTERVAL 7    DAY WHERE timeframe = '1m',
    timestamp + INTERVAL 30   DAY WHERE timeframe = '5m',
    timestamp + INTERVAL 90   DAY WHERE timeframe = '15m',
    timestamp + INTERVAL 365  DAY WHERE timeframe = '1h',
    timestamp + INTERVAL 365  DAY WHERE timeframe = '4h',
    timestamp + INTERVAL 1825 DAY WHERE timeframe = '1d';
