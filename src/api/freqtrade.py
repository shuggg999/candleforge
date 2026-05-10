"""
Freqtrade兼容的API端点
"""
from typing import List
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Query, Depends

from src.storage.clickhouse import ClickHouseManager
from src.api.routes import get_db_manager

router = APIRouter(prefix="/freqtrade", tags=["Freqtrade"])

@router.get("/{exchange}/{pair}/{timeframe}")
async def get_freqtrade_ohlcv(
    exchange: str,
    pair: str,
    timeframe: str,
    limit: int = Query(1000, ge=1, le=5000, description="Number of candles to return"),
    since: int = Query(None, description="Start timestamp in milliseconds"),
    db: ClickHouseManager = Depends(get_db_manager)
) -> List[List[float]]:
    """
    获取Freqtrade格式的OHLCV数据

    返回格式: [[timestamp_ms, open, high, low, close, volume], ...]
    """
    try:
        # 构建查询条件
        where_conditions = [
            f"exchange = '{exchange}'",
            f"symbol = '{pair}'",
            f"timeframe = '{timeframe}'"
        ]

        if since:
            # 将毫秒时间戳转换为DateTime
            since_dt = datetime.fromtimestamp(since / 1000, timezone.utc)
            where_conditions.append(f"timestamp >= '{since_dt.strftime('%Y-%m-%d %H:%M:%S')}'")

        where_clause = " AND ".join(where_conditions)

        # 构建SQL查询 - 返回Freqtrade需要的格式
        query = f"""
        SELECT
            toUnixTimestamp(timestamp) * 1000 as timestamp_ms,
            toFloat64(open) as open,
            toFloat64(high) as high,
            toFloat64(low) as low,
            toFloat64(close) as close,
            toFloat64(volume) as volume
        FROM crypto_data.ohlcv_futures
        WHERE {where_clause}
        ORDER BY timestamp ASC
        LIMIT {limit}
        """

        # 执行查询
        result = await db.client.fetch(query)

        # 转换为Freqtrade格式的二维数组
        ohlcv_data = []
        for row in result:
            ohlcv_data.append([
                int(row['timestamp_ms']),  # 时间戳(整数)
                float(row['open']),        # 开盘价
                float(row['high']),        # 最高价
                float(row['low']),         # 最低价
                float(row['close']),       # 收盘价
                float(row['volume'])       # 成交量
            ])

        return ohlcv_data

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching OHLCV data: {str(e)}"
        )

@router.get("/{exchange}/pairs")
async def get_available_pairs(
    exchange: str,
    db: ClickHouseManager = Depends(get_db_manager)
) -> List[str]:
    """获取可用的交易对列表"""
    try:
        query = f"""
        SELECT DISTINCT symbol
        FROM crypto_data.ohlcv_futures
        WHERE exchange = '{exchange}'
        ORDER BY symbol
        """

        result = await db.client.fetch(query)
        return [row['symbol'] for row in result]

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching pairs: {str(e)}"
        )

@router.get("/{exchange}/timeframes")
async def get_available_timeframes(
    exchange: str,
    db: ClickHouseManager = Depends(get_db_manager)
) -> List[str]:
    """获取可用的时间框架列表"""
    try:
        query = f"""
        SELECT DISTINCT timeframe
        FROM crypto_data.ohlcv_futures
        WHERE exchange = '{exchange}'
        ORDER BY
            CASE timeframe
                WHEN '1m' THEN 1
                WHEN '5m' THEN 2
                WHEN '15m' THEN 3
                WHEN '1h' THEN 4
                WHEN '4h' THEN 5
                WHEN '1d' THEN 6
                ELSE 7
            END
        """

        result = await db.client.fetch(query)
        return [row['timeframe'] for row in result]

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching timeframes: {str(e)}"
        )