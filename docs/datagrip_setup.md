# DataGrip 连接配置

## ClickHouse 连接信息

### 连接参数
- **Host**: localhost
- **Port**: 8123 (HTTP) 或 9000 (Native)
- **Database**: crypto_data
- **User**: datagrip
- **Password**: datagrip123

### DataGrip 配置步骤

1. 打开 DataGrip
2. 点击 "+" 添加新数据源
3. 选择 "ClickHouse"
4. 填入以下信息：
   - Host: `localhost`
   - Port: `8123` (推荐使用HTTP端口)
   - Database: `crypto_data`
   - User: `datagrip`
   - Password: `datagrip123`
5. 点击 "Test Connection" 测试连接
6. 成功后点击 "OK" 保存

### 常用查询

```sql
-- 查看所有表
SHOW TABLES FROM crypto_data;

-- 查看最新数据
SELECT * FROM crypto_data.ohlcv_futures
ORDER BY timestamp DESC
LIMIT 100;

-- 查看特定币种
SELECT * FROM crypto_data.ohlcv_futures
WHERE symbol = 'BTC/USDT'
  AND timeframe = '1m'
ORDER BY timestamp DESC
LIMIT 100;

-- 数据统计
SELECT
    exchange,
    symbol,
    timeframe,
    COUNT(*) as count,
    MIN(timestamp) as first_time,
    MAX(timestamp) as last_time
FROM crypto_data.ohlcv_futures
GROUP BY exchange, symbol, timeframe
ORDER BY count DESC;

-- 查看实时数据流
SELECT
    formatDateTime(timestamp, '%Y-%m-%d %H:%M:%S') as time,
    symbol,
    open,
    high,
    low,
    close,
    volume
FROM crypto_data.ohlcv_futures
WHERE timestamp > now() - INTERVAL 10 MINUTE
ORDER BY timestamp DESC;
```

### 注意事项

1. 如果使用 Native Protocol (端口9000)，可能需要在 DataGrip 中选择 "ClickHouse (Legacy Driver)"
2. HTTP 端口 (8123) 通常更稳定，推荐使用
3. 数据会自动按TTL清理：
   - 1m: 7天
   - 5m: 30天
   - 15m: 90天
   - 1h/4h/1d: 永久保存