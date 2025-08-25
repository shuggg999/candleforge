"""
交易所数据采集器
负责从各交易所API获取市场数据
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import ccxt.async_support as ccxt
import pandas as pd
from asyncio import Queue
import json

logger = logging.getLogger(__name__)


class ExchangeCollector:
    """
    交易所数据采集器
    特性：
    1. 异步高并发采集
    2. 智能限流保护
    3. 增量更新策略
    4. 错误重试机制
    5. 数据质量检查
    """
    
    def __init__(self, exchange: str, db_manager, redis_cache, ws_manager):
        self.exchange_name = exchange
        self.db_manager = db_manager
        self.redis_cache = redis_cache
        self.ws_manager = ws_manager
        
        # 初始化交易所连接
        self.exchange = self._init_exchange(exchange)
        
        # 采集配置
        self.pairs = []
        self.timeframes = ['1m', '5m', '15m', '1h', '4h', '1d']
        self.is_running = False
        self.tasks = []
        
        # 限流控制
        self.rate_limiter = RateLimiter(
            requests_per_minute=30,
            requests_per_second=2
        )
        
        # 统计信息
        self.stats = {
            'api_calls': 0,
            'data_points': 0,
            'errors': 0,
            'last_update': None
        }
        
        # 任务队列
        self.task_queue = Queue()
        
        logger.info(f"ExchangeCollector initialized for {exchange}")
    
    def _init_exchange(self, exchange_name: str):
        """初始化交易所连接"""
        exchange_class = getattr(ccxt, exchange_name)
        
        config = {
            'enableRateLimit': True,
            'rateLimit': 1000,
            'options': {
                'defaultType': 'spot',
                'adjustForTimeDifference': True
            }
        }
        
        # 特定交易所配置
        if exchange_name == 'binance':
            config['options']['recvWindow'] = 60000
        elif exchange_name == 'okx':
            config['options']['defaultType'] = 'spot'
        
        return exchange_class(config)
    
    async def start(self):
        """启动采集器"""
        if self.is_running:
            return
        
        self.is_running = True
        logger.info(f"Starting collector for {self.exchange_name}")
        
        # 加载市场信息
        await self._load_markets()
        
        # 启动采集任务
        self.tasks = [
            asyncio.create_task(self._collect_ohlcv_worker()),
            asyncio.create_task(self._collect_ticker_worker()),
            asyncio.create_task(self._collect_orderbook_worker()),
            asyncio.create_task(self._quality_check_worker()),
            asyncio.create_task(self._heartbeat_worker())
        ]
        
        # 启动WebSocket流（如果支持）
        if self.exchange.has['watchOHLCV']:
            self.tasks.append(
                asyncio.create_task(self._websocket_worker())
            )
    
    async def stop(self):
        """停止采集器"""
        self.is_running = False
        
        # 取消所有任务
        for task in self.tasks:
            task.cancel()
        
        # 等待任务结束
        await asyncio.gather(*self.tasks, return_exceptions=True)
        
        # 关闭交易所连接
        await self.exchange.close()
        
        logger.info(f"Collector for {self.exchange_name} stopped")
    
    async def _load_markets(self):
        """加载市场信息"""
        try:
            markets = await self.exchange.load_markets()
            
            # 过滤活跃的现货市场
            self.pairs = [
                symbol for symbol, market in markets.items()
                if market['active'] and market['spot'] and 
                '/USDT' in symbol  # 只收集USDT交易对
            ][:50]  # 限制数量，避免过载
            
            logger.info(f"Loaded {len(self.pairs)} pairs for {self.exchange_name}")
            
        except Exception as e:
            logger.error(f"Failed to load markets: {e}")
            self.pairs = ['BTC/USDT', 'ETH/USDT']  # 默认交易对
    
    async def _collect_ohlcv_worker(self):
        """OHLCV数据采集工作线程"""
        while self.is_running:
            try:
                for pair in self.pairs:
                    for timeframe in self.timeframes:
                        # 检查是否需要更新
                        if await self._should_update(pair, timeframe):
                            await self._collect_ohlcv(pair, timeframe)
                        
                        # 限流
                        await self.rate_limiter.acquire()
                
                # 休息一段时间
                await asyncio.sleep(60)  # 每分钟循环一次
                
            except Exception as e:
                logger.error(f"OHLCV collector error: {e}")
                self.stats['errors'] += 1
                await asyncio.sleep(5)
    
    async def _collect_ohlcv(self, pair: str, timeframe: str):
        """采集单个交易对的OHLCV数据"""
        try:
            # 获取最后更新时间
            last_timestamp = await self._get_last_timestamp(pair, timeframe)
            
            # 计算需要获取的时间范围
            if last_timestamp:
                since = int(last_timestamp.timestamp() * 1000)
            else:
                # 获取最近30天数据
                since = int((datetime.utcnow() - timedelta(days=30)).timestamp() * 1000)
            
            # 获取数据
            ohlcv = await self.exchange.fetch_ohlcv(
                symbol=pair,
                timeframe=timeframe,
                since=since,
                limit=1000
            )
            
            if ohlcv:
                # 转换为DataFrame
                df = pd.DataFrame(
                    ohlcv,
                    columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
                )
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                df['exchange'] = self.exchange_name
                df['symbol'] = pair
                df['timeframe'] = timeframe
                
                # 保存到数据库
                await self._save_ohlcv(df)
                
                # 更新缓存
                await self._update_cache(pair, timeframe, df)
                
                # 更新统计
                self.stats['api_calls'] += 1
                self.stats['data_points'] += len(df)
                self.stats['last_update'] = datetime.utcnow()
                
                logger.debug(f"Collected {len(df)} candles for {pair} {timeframe}")
            
        except Exception as e:
            logger.error(f"Failed to collect OHLCV for {pair} {timeframe}: {e}")
            self.stats['errors'] += 1
    
    async def _collect_ticker_worker(self):
        """实时行情采集工作线程"""
        while self.is_running:
            try:
                # 批量获取所有交易对的ticker
                if self.exchange.has['fetchTickers']:
                    tickers = await self.exchange.fetch_tickers(self.pairs)
                    
                    for symbol, ticker in tickers.items():
                        await self._save_ticker(symbol, ticker)
                        
                        # 推送到WebSocket
                        await self.ws_manager.broadcast(
                            f"{self.exchange_name}:{symbol}",
                            ticker
                        )
                else:
                    # 逐个获取
                    for pair in self.pairs[:10]:  # 限制数量
                        ticker = await self.exchange.fetch_ticker(pair)
                        await self._save_ticker(pair, ticker)
                        await self.rate_limiter.acquire()
                
                await asyncio.sleep(5)  # 5秒更新一次
                
            except Exception as e:
                logger.error(f"Ticker collector error: {e}")
                await asyncio.sleep(10)
    
    async def _collect_orderbook_worker(self):
        """订单簿采集工作线程"""
        while self.is_running:
            try:
                # 只采集主要交易对的订单簿
                main_pairs = ['BTC/USDT', 'ETH/USDT']
                
                for pair in main_pairs:
                    if pair in self.pairs:
                        orderbook = await self.exchange.fetch_order_book(pair, limit=20)
                        await self._save_orderbook(pair, orderbook)
                        await self.rate_limiter.acquire()
                
                await asyncio.sleep(10)  # 10秒更新一次
                
            except Exception as e:
                logger.error(f"Orderbook collector error: {e}")
                await asyncio.sleep(30)
    
    async def _websocket_worker(self):
        """WebSocket数据流处理"""
        if not self.exchange.has['watchOHLCV']:
            return
        
        while self.is_running:
            try:
                # 订阅主要交易对的实时数据
                for pair in self.pairs[:5]:  # 限制订阅数量
                    asyncio.create_task(self._watch_ohlcv(pair, '1m'))
                
                await asyncio.sleep(3600)  # 每小时重新订阅
                
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                await asyncio.sleep(60)
    
    async def _watch_ohlcv(self, pair: str, timeframe: str):
        """监听实时OHLCV数据"""
        try:
            while self.is_running:
                ohlcv = await self.exchange.watch_ohlcv(pair, timeframe)
                
                # 处理实时数据
                if ohlcv:
                    latest = ohlcv[-1]
                    await self._process_realtime_candle(pair, timeframe, latest)
                    
        except Exception as e:
            logger.error(f"Watch OHLCV error for {pair}: {e}")
    
    async def _quality_check_worker(self):
        """数据质量检查工作线程"""
        while self.is_running:
            try:
                await asyncio.sleep(3600)  # 每小时检查一次
                
                for pair in self.pairs:
                    for timeframe in self.timeframes:
                        await self._check_data_quality(pair, timeframe)
                
            except Exception as e:
                logger.error(f"Quality check error: {e}")
    
    async def _check_data_quality(self, pair: str, timeframe: str):
        """检查数据质量"""
        try:
            # 获取最近的数据
            data = await self.db_manager.get_ohlcv(
                exchange=self.exchange_name,
                pair=pair,
                timeframe=timeframe,
                limit=1000
            )
            
            if data:
                df = pd.DataFrame(data)
                
                # 检查缺失数据
                expected_intervals = self._get_expected_intervals(timeframe, len(df))
                missing = expected_intervals - len(df)
                
                # 检查异常值
                anomalies = self._detect_anomalies(df)
                
                # 保存质量指标
                await self._save_quality_metrics(
                    pair, timeframe,
                    completeness=(len(df) / expected_intervals) * 100,
                    missing_candles=missing,
                    anomaly_count=anomalies
                )
                
        except Exception as e:
            logger.error(f"Quality check failed for {pair} {timeframe}: {e}")
    
    async def _heartbeat_worker(self):
        """心跳工作线程"""
        while self.is_running:
            try:
                # 更新采集器状态
                await self._update_collector_status()
                await asyncio.sleep(30)  # 每30秒更新一次
                
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")
    
    async def _should_update(self, pair: str, timeframe: str) -> bool:
        """判断是否需要更新数据"""
        cache_key = f"last_update:{self.exchange_name}:{pair}:{timeframe}"
        last_update = await self.redis_cache.get(cache_key)
        
        if not last_update:
            return True
        
        # 根据时间框架决定更新频率
        update_intervals = {
            '1m': timedelta(minutes=1),
            '5m': timedelta(minutes=5),
            '15m': timedelta(minutes=15),
            '1h': timedelta(hours=1),
            '4h': timedelta(hours=4),
            '1d': timedelta(days=1)
        }
        
        interval = update_intervals.get(timeframe, timedelta(hours=1))
        return datetime.utcnow() - last_update > interval
    
    async def _get_last_timestamp(self, pair: str, timeframe: str) -> Optional[datetime]:
        """获取最后更新时间"""
        result = await self.db_manager.get_last_timestamp(
            exchange=self.exchange_name,
            pair=pair,
            timeframe=timeframe
        )
        return result
    
    async def _save_ohlcv(self, df: pd.DataFrame):
        """保存OHLCV数据"""
        await self.db_manager.save_ohlcv_batch(df)
    
    async def _save_ticker(self, symbol: str, ticker: dict):
        """保存行情数据"""
        await self.db_manager.save_ticker(
            exchange=self.exchange_name,
            symbol=symbol,
            ticker=ticker
        )
    
    async def _save_orderbook(self, symbol: str, orderbook: dict):
        """保存订单簿"""
        await self.db_manager.save_orderbook(
            exchange=self.exchange_name,
            symbol=symbol,
            orderbook=orderbook
        )
    
    async def _update_cache(self, pair: str, timeframe: str, df: pd.DataFrame):
        """更新缓存"""
        cache_key = f"ohlcv:{self.exchange_name}:{pair}:{timeframe}"
        
        # 缓存最近100条数据
        recent_data = df.tail(100).to_json(orient='records')
        await self.redis_cache.set(cache_key, recent_data, ttl=300)
        
        # 更新最后更新时间
        update_key = f"last_update:{self.exchange_name}:{pair}:{timeframe}"
        await self.redis_cache.set(update_key, datetime.utcnow(), ttl=86400)
    
    async def _update_collector_status(self):
        """更新采集器状态"""
        await self.db_manager.update_collector_status(
            collector_id=f"{self.exchange_name}_collector_1",
            status='running',
            stats=self.stats
        )
    
    async def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            'exchange': self.exchange_name,
            'pairs_count': len(self.pairs),
            'is_running': self.is_running,
            **self.stats
        }
    
    async def download_historical(self, pair: str, timeframe: str, 
                                 start: str = None, end: str = None):
        """下载历史数据"""
        logger.info(f"Downloading historical data for {pair} {timeframe}")
        
        # 实现历史数据下载逻辑
        # ...
        
        return True
    
    def _get_expected_intervals(self, timeframe: str, period_hours: int = 24) -> int:
        """计算预期的数据点数量"""
        intervals = {
            '1m': 60 * period_hours,
            '5m': 12 * period_hours,
            '15m': 4 * period_hours,
            '1h': period_hours,
            '4h': period_hours // 4,
            '1d': period_hours // 24
        }
        return intervals.get(timeframe, period_hours)
    
    def _detect_anomalies(self, df: pd.DataFrame) -> int:
        """检测数据异常"""
        anomalies = 0
        
        # 检查价格异常（超过10%的突变）
        if 'close' in df.columns:
            price_changes = df['close'].pct_change()
            anomalies += (price_changes.abs() > 0.1).sum()
        
        # 检查成交量异常
        if 'volume' in df.columns:
            volume_z_score = (df['volume'] - df['volume'].mean()) / df['volume'].std()
            anomalies += (volume_z_score.abs() > 3).sum()
        
        return anomalies
    
    async def _save_quality_metrics(self, pair: str, timeframe: str, **metrics):
        """保存数据质量指标"""
        await self.db_manager.save_quality_metrics(
            exchange=self.exchange_name,
            symbol=pair,
            timeframe=timeframe,
            metrics=metrics
        )
    
    async def _process_realtime_candle(self, pair: str, timeframe: str, candle: list):
        """处理实时K线数据"""
        # 转换并保存
        df = pd.DataFrame([candle], columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df['exchange'] = self.exchange_name
        df['symbol'] = pair
        df['timeframe'] = timeframe
        
        await self._save_ohlcv(df)
        
        # 推送到WebSocket
        await self.ws_manager.broadcast(
            f"ohlcv:{self.exchange_name}:{pair}:{timeframe}",
            candle
        )


class RateLimiter:
    """限流器"""
    
    def __init__(self, requests_per_minute: int = 60, requests_per_second: int = 2):
        self.rpm = requests_per_minute
        self.rps = requests_per_second
        self.minute_requests = []
        self.second_requests = []
    
    async def acquire(self):
        """获取许可"""
        now = datetime.utcnow()
        
        # 清理过期记录
        self.minute_requests = [
            req for req in self.minute_requests 
            if now - req < timedelta(minutes=1)
        ]
        self.second_requests = [
            req for req in self.second_requests 
            if now - req < timedelta(seconds=1)
        ]
        
        # 检查限制
        while len(self.minute_requests) >= self.rpm or len(self.second_requests) >= self.rps:
            await asyncio.sleep(0.1)
            now = datetime.utcnow()
            self.minute_requests = [
                req for req in self.minute_requests 
                if now - req < timedelta(minutes=1)
            ]
            self.second_requests = [
                req for req in self.second_requests 
                if now - req < timedelta(seconds=1)
            ]
        
        # 记录请求
        self.minute_requests.append(now)
        self.second_requests.append(now)