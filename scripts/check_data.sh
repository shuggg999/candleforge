#!/bin/bash

# 数据查询工具脚本

echo "=== Freqtrade Data Service 数据检查 ==="
echo ""

# 1. 服务状态
echo "📊 服务状态:"
curl -s http://localhost:8000/api/v1/status | python -m json.tool | grep -E '"exchange"|"symbols_count"|"messages_received"|"data_points_stored"'
echo ""

# 2. 最近5分钟数据统计
echo "📈 最近5分钟数据统计:"
docker exec clickhouse-db clickhouse-client --query "
SELECT
    exchange,
    COUNT(DISTINCT symbol) as symbols,
    COUNT(*) as total_records,
    MAX(timestamp) as latest_time
FROM crypto_data.ohlcv_futures
WHERE timestamp > now() - INTERVAL 5 MINUTE
GROUP BY exchange
FORMAT PrettySpace"
echo ""

# 3. 查看特定币种数据 (BTC/USDT)
echo "💰 BTC/USDT 最新数据:"
docker exec clickhouse-db clickhouse-client --query "
SELECT
    timeframe,
    timestamp,
    open,
    high,
    low,
    close,
    volume
FROM crypto_data.ohlcv_futures
WHERE symbol = 'BTC/USDT'
    AND exchange = 'binance'
    AND timestamp > now() - INTERVAL 10 MINUTE
ORDER BY timestamp DESC
LIMIT 5
FORMAT PrettySpace"
echo ""

# 4. 数据完整性检查
echo "✅ 数据完整性 (1分钟K线间隔):"
docker exec clickhouse-db clickhouse-client --query "
WITH gaps AS (
    SELECT
        symbol,
        timestamp,
        LAG(timestamp) OVER (PARTITION BY symbol ORDER BY timestamp) as prev_timestamp,
        timestamp - LAG(timestamp) OVER (PARTITION BY symbol ORDER BY timestamp) as gap_seconds
    FROM crypto_data.ohlcv_futures
    WHERE exchange = 'binance'
        AND timeframe = '1m'
        AND symbol = 'BTC/USDT'
        AND timestamp > now() - INTERVAL 1 HOUR
)
SELECT
    COUNT(*) as total_records,
    COUNT(CASE WHEN gap_seconds > 60 THEN 1 END) as gaps_found,
    MAX(gap_seconds) as max_gap_seconds
FROM gaps
FORMAT PrettySpace"
echo ""

# 5. 实时监控最新数据
echo "🔄 实时数据流 (最新10条):"
docker exec clickhouse-db clickhouse-client --query "
SELECT
    formatDateTime(timestamp, '%H:%M:%S') as time,
    symbol,
    timeframe,
    close as price,
    volume
FROM crypto_data.ohlcv_futures
WHERE exchange = 'binance'
ORDER BY timestamp DESC
LIMIT 10
FORMAT PrettySpace"