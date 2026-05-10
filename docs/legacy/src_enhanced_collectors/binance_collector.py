"""
增强版Binance数据收集器
继承BaseExchangeCollector，保持WebSocket优先架构，集成专业验证和重试机制
"""
import asyncio
import json
import websockets
import httpx
import pandas as pd
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode

from binance.spot import Spot
from loguru import logger

from src.config import settings
from src.storage.clickhouse import ClickHouseManager
from src.enhanced_collectors.base_collector import BaseExchangeCollector, CollectionResult
from src.validators.crypto_data_validator import CryptoDataValidator
from src.utils.enhanced.retry_decorator import http_retry, network_retry
from src.utils.enhanced.smart_concurrency_controller import SmartConcurrencyController


class EnhancedBinanceCollector(BaseExchangeCollector):
    """
    增强版Binance数据收集器
    
    功能特性：
    1. 继承专业数据收集架构
    2. 保持WebSocket优先数据收集
    3. 集成智能重试机制
    4. 专业数据验证
    5. 智能并发控制
    6. 全面监控和统计
    """
    
    def __init__(self, 
                 exchange_name: str = "binance",
                 db_manager: Optional[ClickHouseManager] = None,
                 validator: Optional[CryptoDataValidator] = None,
                 concurrency_controller: Optional[SmartConcurrencyController] = None,
                 **config):
        """
        初始化增强版Binance收集器
        
        Args:
            exchange_name: 交易所名称
            db_manager: ClickHouse管理器
            validator: 数据验证器
            concurrency_controller: 并发控制器
            **config: 其他配置选项
        """
        # 调用父类初始化
        super().__init__(exchange_name, **config)
        
        # 组件注入
        self.db_manager = db_manager
        self.validator = validator or CryptoDataValidator(
            enable_outlier_detection=True,
            outlier_threshold=3.0,
            auto_fix=True
        )
        self.concurrency_controller = concurrency_controller
        
        # Binance特定配置
        self.base_url = "https://api.binance.com"
        self.futures_base_url = "https://fapi.binance.com"
        self.ws_base_url = "wss://fstream.binance.com/ws/"
        self.max_symbols_per_connection = 100
        
        # 客户端
        self.http_client: Optional[httpx.AsyncClient] = None
        self.rest_client: Optional[Spot] = None
        self.websocket_clients: Dict[str, Any] = {}
        
        # 缓存
        self.exchange_info: Optional[Dict] = None
        self.symbol_list_cache: Optional[List[str]] = None
        self.symbol_list_cache_time: Optional[datetime] = None
        
        # WebSocket流管理
        self.active_streams: Dict[str, List[str]] = {}
        self.stream_handlers: Dict[str, asyncio.Task] = {}
        
        logger.info(
            f"EnhancedBinanceCollector 初始化完成",
            extra={
                "validator_enabled": self.validator is not None,
                "concurrency_controller": self.concurrency_controller is not None,
                "websocket_enabled": self.websocket_config.get('enabled', True),
                "rest_api_enabled": self.rest_api_config.get('enabled', True)
            }
        )
    
    async def get_symbol_list(self) -> List[str]:
        """获取交易所交易对列表（带缓存）"""
        # 检查缓存
        if (self.symbol_list_cache and 
            self.symbol_list_cache_time and 
            (datetime.now() - self.symbol_list_cache_time) < timedelta(hours=1)):
            return self.symbol_list_cache
        
        try:
            symbols = await self._fetch_futures_symbols()
            
            # 更新缓存
            self.symbol_list_cache = symbols
            self.symbol_list_cache_time = datetime.now()
            
            return symbols
            
        except Exception as e:
            logger.error(f"获取Binance交易对列表失败: {e}")
            # 返回缓存的数据（如果有）
            return self.symbol_list_cache or []
    
    def normalize_symbol(self, symbol: str) -> str:
        """标准化交易对符号格式"""
        # BTC/USDT -> BTCUSDT (Binance格式)
        if '/' in symbol:
            return symbol.replace('/', '')
        return symbol
    
    async def fetch_kline_data(self, 
                              symbol: str, 
                              interval: str, 
                              start_time: datetime, 
                              end_time: datetime) -> Optional[pd.DataFrame]:
        """
        获取K线数据（WebSocket优先，REST备用）
        
        实现BaseExchangeCollector的抽象方法
        """
        # 优先尝试从WebSocket获取实时数据
        if self.websocket.get('enabled', True) and self._is_websocket_active():
            websocket_data = await self._try_get_websocket_data(symbol, interval, start_time, end_time)
            if websocket_data is not None and not websocket_data.empty:
                logger.debug(f"通过WebSocket获取 {symbol} 数据: {len(websocket_data)} 条记录")
                return websocket_data
        
        # 回退到REST API
        if self.rest_api.get('enabled', True):
            rest_data = await self._fetch_historical_klines(symbol, interval, start_time, end_time)
            if rest_data is not None and not rest_data.empty:
                logger.debug(f"通过REST API获取 {symbol} 数据: {len(rest_data)} 条记录")
                return rest_data
        
        logger.warning(f"无法获取 {symbol} 的K线数据")
        return None
    
    async def validate_connection(self) -> bool:
        """验证连接状态"""
        try:
            # 测试REST连接
            if self.rest_api.get('enabled', True):
                if not await self._test_rest_connection():
                    return False
            
            # 测试WebSocket连接
            if self.websocket.get('enabled', True):
                if not self._test_websocket_connections():
                    return False
            
            return True
            
        except Exception as e:
            logger.error(f"连接验证失败: {e}")
            return False
    
    async def _setup_websocket_connection(self):
        """设置WebSocket连接"""
        if not self.websocket.get('enabled', True):
            return
        
        try:
            # 获取交易对列表
            symbols = await self.get_symbol_list()
            if not symbols:
                logger.warning("没有可用的交易对，跳过WebSocket设置")
                return
            
            # 根据每个连接的最大交易对数量分组
            symbol_groups = [
                symbols[i:i + self.max_symbols_per_connection]
                for i in range(0, len(symbols), self.max_symbols_per_connection)
            ]
            
            # 为每组创建WebSocket连接
            for i, symbol_group in enumerate(symbol_groups):
                client_key = f"binance_ws_{i}"
                
                if self.concurrency_controller:
                    # 使用并发控制器提交连接任务
                    await self.concurrency_controller.submit(
                        self._create_websocket_client,
                        client_key, symbol_group,
                        priority=1  # 高优先级
                    )
                else:
                    # 直接创建连接
                    await self._create_websocket_client(client_key, symbol_group)
            
            logger.info(f"WebSocket连接设置完成，共 {len(symbol_groups)} 个连接")
            
        except Exception as e:
            logger.error(f"WebSocket连接设置失败: {e}")
            raise
    
    async def _setup_rest_connection(self):
        """设置REST API连接"""
        if not self.rest_api.get('enabled', True):
            return
        
        try:
            # 创建HTTP客户端
            timeout = self.rest_api.get('timeout', 30)
            self.http_client = httpx.AsyncClient(
                timeout=timeout,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10)
            )
            
            # 测试连接
            if await self._test_rest_connection():
                logger.info("REST API连接设置成功")
            else:
                raise Exception("REST API连接测试失败")
            
            # 如果有API密钥，创建认证客户端
            if settings.BINANCE_API_KEY and settings.BINANCE_SECRET:
                self.rest_client = Spot(
                    api_key=settings.BINANCE_API_KEY,
                    api_secret=settings.BINANCE_SECRET,
                    base_url=self.futures_base_url
                )
                logger.info("认证REST客户端设置完成")
            
        except Exception as e:
            logger.error(f"REST API连接设置失败: {e}")
            raise
    
    @network_retry
    async def _fetch_futures_symbols(self) -> List[str]:
        """获取期货交易对列表（带重试）"""
        exchange_info_url = f"{self.futures_base_url}/fapi/v1/exchangeInfo"
        
        if not self.http_client:
            await self._setup_rest_connection()
        
        response = await self.http_client.get(exchange_info_url)
        
        if response.status_code != 200:
            raise Exception(f"HTTP错误 {response.status_code}: {response.text}")
        
        exchange_info = response.json()
        if 'symbols' not in exchange_info:
            raise Exception("交易所信息格式错误")
        
        # 缓存交易所信息
        self.exchange_info = exchange_info
        
        # 提取USDT永续期货交易对
        futures_symbols = []
        for symbol_info in exchange_info['symbols']:
            symbol = symbol_info.get('symbol', '')
            status = symbol_info.get('status', '')
            quote_asset = symbol_info.get('quoteAsset', '')
            contract_type = symbol_info.get('contractType', '')
            
            if (status == 'TRADING' and 
                quote_asset == 'USDT' and 
                contract_type == 'PERPETUAL'):
                
                # 转换为标准格式 (BTCUSDT -> BTC/USDT)
                if symbol.endswith('USDT'):
                    base_asset = symbol[:-4]
                    standard_symbol = f"{base_asset}/USDT"
                    futures_symbols.append(standard_symbol)
        
        logger.info(f"获取到 {len(futures_symbols)} 个Binance USDT永续期货交易对")
        return sorted(futures_symbols)
    
    @http_retry
    async def _fetch_historical_klines(self, 
                                     symbol: str, 
                                     interval: str, 
                                     start_time: datetime, 
                                     end_time: datetime) -> Optional[pd.DataFrame]:
        """获取历史K线数据（带重试）"""
        try:
            # 转换参数
            binance_symbol = self.normalize_symbol(symbol)
            binance_interval = self._map_timeframe(interval)
            start_ms = int(start_time.timestamp() * 1000)
            end_ms = int(end_time.timestamp() * 1000)
            
            # 构建请求参数
            params = {
                'symbol': binance_symbol,
                'interval': binance_interval,
                'startTime': start_ms,
                'endTime': end_ms,
                'limit': 1000
            }
            
            url = f"{self.futures_base_url}/fapi/v1/klines"
            response = await self.http_client.get(url, params=params)
            
            if response.status_code != 200:
                raise Exception(f"HTTP错误 {response.status_code}: {response.text}")
            
            klines_raw = response.json()
            
            if not klines_raw:
                return pd.DataFrame()
            
            # 转换为DataFrame
            df_data = []
            for kline in klines_raw:
                # Binance K线格式: [开盘时间, 开盘价, 最高价, 最低价, 收盘价, 成交量, 收盘时间, 成交额, 成交笔数, 主动买入成交量, 主动买入成交额, 忽略字段]
                
                timestamp = datetime.fromtimestamp(int(kline[0]) / 1000, timezone.utc)
                timestamp = self._normalize_timestamp(timestamp, interval)
                
                df_data.append({
                    'timestamp': timestamp,
                    'open': float(kline[1]),
                    'high': float(kline[2]),
                    'low': float(kline[3]),
                    'close': float(kline[4]),
                    'volume': float(kline[5]),
                    'symbol': symbol,
                    'exchange': self.exchange_name
                })
            
            df = pd.DataFrame(df_data)
            
            # 数据验证
            if self.enable_validation and self.validator:
                validation_result = self.validator.validate_kline_data(df, fix_issues=True)
                
                if validation_result.has_errors:
                    logger.warning(f"{symbol} 数据验证发现 {validation_result.error_count} 个错误")
                
                if validation_result.fixed_data is not None:
                    df = validation_result.fixed_data
            
            return df
            
        except Exception as e:
            logger.error(f"获取 {symbol} 历史数据失败: {e}")
            return None
    
    async def _create_websocket_client(self, client_key: str, symbols: List[str]):
        """创建WebSocket客户端"""
        try:
            # 构建流名称
            streams = []
            for symbol in symbols:
                binance_symbol = self.normalize_symbol(symbol).lower()
                for timeframe in ['1m', '5m', '15m', '1h', '4h', '1d']:  # 支持的时间框架
                    stream_name = f"{binance_symbol}@kline_{timeframe}"
                    streams.append(stream_name)
            
            # 创建WebSocket URL
            stream_params = "/".join(streams)
            ws_url = f"{self.ws_base_url}{stream_params}"
            
            logger.info(f"创建WebSocket连接 {client_key}，包含 {len(streams)} 个流")
            
            # 创建WebSocket连接
            websocket = await websockets.connect(ws_url)
            self.websocket_clients[client_key] = websocket
            self.active_streams[client_key] = streams
            
            # 启动消息处理任务
            handler_task = asyncio.create_task(
                self._handle_websocket_messages(client_key, websocket)
            )
            self.stream_handlers[client_key] = handler_task
            
            logger.info(f"WebSocket客户端 {client_key} 创建成功")
            
        except Exception as e:
            logger.error(f"创建WebSocket客户端 {client_key} 失败: {e}")
            raise
    
    async def _handle_websocket_messages(self, client_key: str, websocket):
        """处理WebSocket消息"""
        try:
            while self.is_running and not websocket.closed:
                try:
                    # 接收消息
                    raw_message = await asyncio.wait_for(
                        websocket.recv(), 
                        timeout=30.0  # 30秒超时
                    )
                    
                    if not raw_message:
                        continue
                    
                    # 解析消息
                    message_data = json.loads(raw_message)
                    
                    # 处理K线数据
                    if 'k' in message_data:
                        kline_data = self._parse_kline_message(message_data)
                        
                        if kline_data:
                            # 提交数据处理任务
                            if self.concurrency_controller:
                                await self.concurrency_controller.submit(
                                    self._process_kline_data,
                                    kline_data,
                                    priority=3  # 中等优先级
                                )
                            else:
                                await self._process_kline_data(kline_data)
                    
                except asyncio.TimeoutError:
                    # 发送ping保持连接
                    if not websocket.closed:
                        await websocket.ping()
                        
                except json.JSONDecodeError as e:
                    logger.warning(f"WebSocket {client_key} JSON解析错误: {e}")
                    
                except Exception as e:
                    logger.error(f"WebSocket {client_key} 消息处理错误: {e}")
                    break
            
        except websockets.exceptions.ConnectionClosed:
            logger.warning(f"WebSocket连接 {client_key} 已关闭")
        except Exception as e:
            logger.error(f"WebSocket {client_key} 处理异常: {e}")
        finally:
            # 清理资源
            await self._cleanup_websocket_client(client_key)
    
    def _parse_kline_message(self, message_data: Dict) -> Optional[Dict]:
        """解析K线消息"""
        try:
            kline = message_data['k']
            
            # 只处理已关闭的K线
            if not kline.get('x', False):
                return None
            
            # 转换符号格式
            symbol_binance = kline['s']  # BTCUSDT
            symbol_standard = self._get_standard_symbol(symbol_binance)  # BTC/USDT
            
            # 获取时间戳
            timestamp = datetime.fromtimestamp(int(kline['t']) / 1000, timezone.utc)
            interval = self._parse_binance_interval(kline['i'])
            timestamp = self._normalize_timestamp(timestamp, interval)
            
            return {
                'timestamp': timestamp,
                'open': float(kline['o']),
                'high': float(kline['h']),
                'low': float(kline['l']),
                'close': float(kline['c']),
                'volume': float(kline['v']),
                'symbol': symbol_standard,
                'exchange': self.exchange_name,
                'timeframe': interval
            }
            
        except (KeyError, ValueError, TypeError) as e:
            logger.error(f"K线消息解析错误: {e}")
            return None
    
    async def _process_kline_data(self, kline_data: Dict):
        """处理K线数据"""
        try:
            # 转换为DataFrame进行验证
            df = pd.DataFrame([kline_data])
            
            # 数据验证
            if self.enable_validation and self.validator:
                validation_result = self.validator.validate_kline_data(df, fix_issues=True)
                
                if validation_result.has_errors:
                    logger.warning(f"K线数据验证错误: {validation_result.error_count} 个问题")
                    return  # 跳过有错误的数据
                
                if validation_result.fixed_data is not None:
                    df = validation_result.fixed_data
            
            # 存储到数据库
            if self.db_manager and not df.empty:
                await self.db_manager.insert_kline_batch(df.to_dict('records'))
            
            # 更新统计
            self._update_collection_stats(True, kline_data['symbol'], len(df))
            
        except Exception as e:
            logger.error(f"K线数据处理失败: {e}")
            self._update_collection_stats(False, kline_data.get('symbol', 'unknown'), 0, str(e))
    
    async def _test_rest_connection(self) -> bool:
        """测试REST API连接"""
        try:
            url = f"{self.futures_base_url}/fapi/v1/ping"
            response = await self.http_client.get(url)
            return response.status_code == 200
        except Exception:
            return False
    
    def _test_websocket_connections(self) -> bool:
        """测试WebSocket连接"""
        if not self.websocket_clients:
            return False
        
        # 检查所有连接是否活跃
        active_count = 0
        for client_key, websocket in self.websocket_clients.items():
            if not websocket.closed:
                active_count += 1
        
        return active_count > 0
    
    async def _try_get_websocket_data(self, 
                                    symbol: str, 
                                    interval: str, 
                                    start_time: datetime, 
                                    end_time: datetime) -> Optional[pd.DataFrame]:
        """尝试从WebSocket获取数据"""
        # 这里可以实现从WebSocket缓存获取数据的逻辑
        # 由于WebSocket是实时流，主要用于存储最新数据
        # 对于历史数据查询，通常还是需要REST API
        return None
    
    def _is_websocket_active(self) -> bool:
        """检查WebSocket是否活跃"""
        return len(self.websocket_clients) > 0 and any(
            not ws.closed for ws in self.websocket_clients.values()
        )
    
    def _get_standard_symbol(self, binance_symbol: str) -> str:
        """获取标准符号格式"""
        if binance_symbol.endswith('USDT'):
            base = binance_symbol[:-4]
            return f"{base}/USDT"
        return binance_symbol
    
    def _map_timeframe(self, standard_timeframe: str) -> str:
        """映射标准时间框架到Binance格式"""
        timeframe_map = {
            '1m': '1m', '5m': '5m', '15m': '15m', '1h': '1h', 
            '4h': '4h', '1d': '1d'
        }
        return timeframe_map.get(standard_timeframe, '5m')
    
    def _parse_binance_interval(self, binance_interval: str) -> str:
        """解析Binance时间间隔"""
        return binance_interval  # 通常Binance格式就是标准格式
    
    def _normalize_timestamp(self, timestamp: datetime, timeframe: str) -> datetime:
        """标准化时间戳到时间框架边界"""
        timeframe_minutes = {
            '1m': 1, '5m': 5, '15m': 15, '1h': 60, '4h': 240, '1d': 1440
        }
        
        minutes = timeframe_minutes.get(timeframe, 5)
        
        # 向下舍入到时间框架边界
        if minutes < 60:
            timestamp = timestamp.replace(second=0, microsecond=0)
            rounded_minute = (timestamp.minute // minutes) * minutes
            timestamp = timestamp.replace(minute=rounded_minute)
        elif minutes == 60:
            timestamp = timestamp.replace(minute=0, second=0, microsecond=0)
        elif minutes < 1440:
            timestamp = timestamp.replace(minute=0, second=0, microsecond=0)
            hours = minutes // 60
            rounded_hour = (timestamp.hour // hours) * hours
            timestamp = timestamp.replace(hour=rounded_hour)
        else:
            timestamp = timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
        
        return timestamp
    
    async def _cleanup_websocket_client(self, client_key: str):
        """清理WebSocket客户端"""
        # 关闭WebSocket连接
        if client_key in self.websocket_clients:
            websocket = self.websocket_clients.pop(client_key)
            try:
                if not websocket.closed:
                    await websocket.close()
            except Exception as e:
                logger.warning(f"关闭WebSocket连接失败: {e}")
        
        # 停止处理任务
        if client_key in self.stream_handlers:
            handler = self.stream_handlers.pop(client_key)
            if not handler.done():
                handler.cancel()
                try:
                    await handler
                except asyncio.CancelledError:
                    pass
        
        # 清理流信息
        self.active_streams.pop(client_key, None)
        
        logger.info(f"WebSocket客户端 {client_key} 已清理")
    
    async def cleanup(self):
        """清理资源"""
        logger.info("开始清理EnhancedBinanceCollector资源")
        
        # 清理WebSocket连接
        cleanup_tasks = []
        for client_key in list(self.websocket_clients.keys()):
            cleanup_tasks.append(self._cleanup_websocket_client(client_key))
        
        if cleanup_tasks:
            await asyncio.gather(*cleanup_tasks, return_exceptions=True)
        
        # 关闭HTTP客户端
        if self.http_client:
            await self.http_client.aclose()
            self.http_client = None
        
        # 调用父类清理
        await super().cleanup()
        
        logger.info("EnhancedBinanceCollector资源清理完成")