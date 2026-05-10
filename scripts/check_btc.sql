-- BTC数据量查询脚本

-- 1. BTC数据总览
SELECT
    '📊 BTC数据总览' as info,
    symbol,
    COUNT(*) as total_records,
    COUNT(DISTINCT timeframe) as timeframes,
    MIN(timestamp) as earliest_data,
    MAX(timestamp) as latest_data
FROM crypto_data.ohlcv_futures
WHERE symbol = 'BTC/USDT' AND exchange = 'binance'
GROUP BY symbol;

-- 2. 按时间框架统计
SELECT
    '📈 按时间框架统计' as info,
    timeframe,
    COUNT(*) as records,
    MIN(timestamp) as first_candle,
    MAX(timestamp) as last_candle,
    dateDiff('hour', MIN(timestamp), MAX(timestamp)) as hours_covered
FROM crypto_data.ohlcv_futures
WHERE symbol = 'BTC/USDT' AND exchange = 'binance'
GROUP BY timeframe
ORDER BY
    CASE timeframe
        WHEN '1m' THEN 1
        WHEN '5m' THEN 2
        WHEN '15m' THEN 3
        WHEN '1h' THEN 4
        WHEN '4h' THEN 5
        WHEN '1d' THEN 6
        ELSE 7
    END;

-- 3. 最近1小时数据接收情况
SELECT
    '⏰ 最近1小时数据' as info,
    timeframe,
    COUNT(*) as candles_received,
    MAX(timestamp) as latest_candle
FROM crypto_data.ohlcv_futures
WHERE symbol = 'BTC/USDT'
    AND exchange = 'binance'
    AND timestamp > now() - INTERVAL 1 HOUR
GROUP BY timeframe;

-- 4. 数据完整性检查（1分钟K线应该连续）
WITH gaps AS (
    SELECT
        timestamp,
        LAG(timestamp, 1) OVER (ORDER BY timestamp) as prev_timestamp,
        dateDiff('second', LAG(timestamp, 1) OVER (ORDER BY timestamp), timestamp) as gap_seconds
    FROM crypto_data.ohlcv_futures
    WHERE symbol = 'BTC/USDT'
        AND exchange = 'binance'
        AND timeframe = '1m'
        AND timestamp > now() - INTERVAL 1 HOUR
)
SELECT
    '🔍 数据完整性(1m)' as info,
    COUNT(*) as total_candles,
    COUNT(CASE WHEN gap_seconds > 60 THEN 1 END) as gaps_found,
    MAX(gap_seconds) / 60 as max_gap_minutes
FROM gaps
WHERE prev_timestamp IS NOT NULL;