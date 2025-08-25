"""
数据库模型定义
使用TimescaleDB存储时序数据
"""

from datetime import datetime
from sqlalchemy import Column, String, Float, BigInteger, DateTime, Index, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.dialects.postgresql import UUID
import uuid

Base = declarative_base()


class OHLCVData(Base):
    """OHLCV数据模型"""
    __tablename__ = 'ohlcv_data'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    exchange = Column(String(50), nullable=False)
    symbol = Column(String(50), nullable=False)
    timeframe = Column(String(10), nullable=False)
    timestamp = Column(DateTime, nullable=False)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False)
    quote_volume = Column(Float)
    trades_count = Column(BigInteger)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        # 唯一约束，防止重复数据
        UniqueConstraint('exchange', 'symbol', 'timeframe', 'timestamp'),
        # 索引优化查询
        Index('idx_ohlcv_lookup', 'exchange', 'symbol', 'timeframe', 'timestamp'),
        Index('idx_timestamp', 'timestamp'),
        # TimescaleDB超表配置
        {'timescaledb_hypertable': {
            'time_column_name': 'timestamp',
            'chunk_time_interval': '1 week',
            'compress_after': '1 month'
        }}
    )


class TickerData(Base):
    """实时行情数据"""
    __tablename__ = 'ticker_data'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    exchange = Column(String(50), nullable=False)
    symbol = Column(String(50), nullable=False)
    timestamp = Column(DateTime, nullable=False)
    bid = Column(Float)
    bid_volume = Column(Float)
    ask = Column(Float)
    ask_volume = Column(Float)
    last = Column(Float)
    volume_24h = Column(Float)
    quote_volume_24h = Column(Float)
    open_24h = Column(Float)
    high_24h = Column(Float)
    low_24h = Column(Float)
    change_24h = Column(Float)
    percentage_24h = Column(Float)
    vwap = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        Index('idx_ticker_lookup', 'exchange', 'symbol', 'timestamp'),
        {'timescaledb_hypertable': {
            'time_column_name': 'timestamp',
            'chunk_time_interval': '1 day'
        }}
    )


class OrderBookSnapshot(Base):
    """订单簿快照"""
    __tablename__ = 'orderbook_snapshots'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    exchange = Column(String(50), nullable=False)
    symbol = Column(String(50), nullable=False)
    timestamp = Column(DateTime, nullable=False)
    bids = Column(String)  # JSON格式存储
    asks = Column(String)  # JSON格式存储
    spread = Column(Float)
    mid_price = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        Index('idx_orderbook_lookup', 'exchange', 'symbol', 'timestamp'),
        {'timescaledb_hypertable': {
            'time_column_name': 'timestamp',
            'chunk_time_interval': '1 hour'
        }}
    )


class TradeData(Base):
    """成交数据"""
    __tablename__ = 'trade_data'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    exchange = Column(String(50), nullable=False)
    symbol = Column(String(50), nullable=False)
    trade_id = Column(String(100))
    timestamp = Column(DateTime, nullable=False)
    price = Column(Float, nullable=False)
    amount = Column(Float, nullable=False)
    side = Column(String(10))  # buy/sell
    taker_or_maker = Column(String(10))
    created_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        Index('idx_trade_lookup', 'exchange', 'symbol', 'timestamp'),
        Index('idx_trade_id', 'exchange', 'trade_id'),
        {'timescaledb_hypertable': {
            'time_column_name': 'timestamp',
            'chunk_time_interval': '1 day'
        }}
    )


class DataQualityMetrics(Base):
    """数据质量指标"""
    __tablename__ = 'data_quality_metrics'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    exchange = Column(String(50), nullable=False)
    symbol = Column(String(50), nullable=False)
    timeframe = Column(String(10), nullable=False)
    date = Column(DateTime, nullable=False)
    completeness = Column(Float)  # 完整性百分比
    missing_candles = Column(BigInteger)
    duplicate_candles = Column(BigInteger)
    anomaly_count = Column(BigInteger)
    last_update = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        UniqueConstraint('exchange', 'symbol', 'timeframe', 'date'),
        Index('idx_quality_lookup', 'exchange', 'symbol', 'timeframe', 'date'),
    )


class CollectorStatus(Base):
    """采集器状态"""
    __tablename__ = 'collector_status'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    collector_id = Column(String(100), unique=True, nullable=False)
    exchange = Column(String(50), nullable=False)
    status = Column(String(20))  # running/stopped/error
    last_heartbeat = Column(DateTime)
    api_calls_count = Column(BigInteger, default=0)
    errors_count = Column(BigInteger, default=0)
    data_points_collected = Column(BigInteger, default=0)
    start_time = Column(DateTime)
    config = Column(String)  # JSON配置
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        Index('idx_collector_status', 'collector_id', 'status'),
    )