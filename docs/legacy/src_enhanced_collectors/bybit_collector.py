"""
增强版Bybit数据收集器
继承BaseExchangeCollector，实现Bybit特定的数据收集逻辑
"""
import asyncio
import json
import websockets
import httpx
import pandas as pd
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone, timedelta

from loguru import logger

from src.enhanced_collectors.base_collector import BaseExchangeCollector, CollectionResult
from src.validators.crypto_data_validator import CryptoDataValidator
from src.utils.enhanced.retry_decorator import http_retry, network_retry
from src.utils.enhanced.smart_concurrency_controller import SmartConcurrencyController


class EnhancedBybitCollector(BaseExchangeCollector):
    """
    增强版Bybit数据收集器
    """
    
    def __init__(self, 
                 exchange_name: str = "bybit",
                 db_manager: Optional[Any] = None,
                 validator: Optional[CryptoDataValidator] = None,
                 concurrency_controller: Optional[SmartConcurrencyController] = None,
                 **config):
        super().__init__(exchange_name, **config)
        
        self.db_manager = db_manager
        self.validator = validator or CryptoDataValidator()
        self.concurrency_controller = concurrency_controller
        
        # Bybit特定配置
        self.base_url = "https://api.bybit.com"
        self.ws_base_url = "wss://stream.bybit.com/v5/public/linear"
        self.max_symbols_per_connection = 50
        
        # 客户端
        self.http_client: Optional[httpx.AsyncClient] = None
        self.websocket_clients: Dict[str, Any] = {}
        
        # 缓存
        self.symbol_list_cache: Optional[List[str]] = None
        self.symbol_list_cache_time: Optional[datetime] = None
        
        logger.info(f"EnhancedBybitCollector 初始化完成")
    
    async def get_symbol_list(self) -> List[str]:
        """获取Bybit交易对列表"""
        if (self.symbol_list_cache and 
            self.symbol_list_cache_time and 
            (datetime.now() - self.symbol_list_cache_time) < timedelta(hours=1)):
            return self.symbol_list_cache
        
        try:
            symbols = await self._fetch_bybit_symbols()
            self.symbol_list_cache = symbols
            self.symbol_list_cache_time = datetime.now()
            return symbols
        except Exception as e:
            logger.error(f"获取Bybit交易对列表失败: {e}")
            return self.symbol_list_cache or []
    
    def normalize_symbol(self, symbol: str) -> str:
        """标准化交易对符号格式"""
        # BTC/USDT -> BTCUSDT (Bybit格式)
        if '/' in symbol:
            return symbol.replace('/', '')
        return symbol
    
    async def fetch_kline_data(self, 
                              symbol: str, 
                              interval: str, 
                              start_time: datetime, 
                              end_time: datetime) -> Optional[pd.DataFrame]:
        """获取K线数据"""
        try:
            return await self._fetch_historical_klines(symbol, interval, start_time, end_time)
        except Exception as e:
            logger.error(f"获取 {symbol} K线数据失败: {e}")
            return None
    
    async def validate_connection(self) -> bool:
        """验证连接状态"""
        try:
            return await self._test_rest_connection()
        except Exception as e:
            logger.error(f"连接验证失败: {e}")
            return False
    
    async def _setup_websocket_connection(self):
        """设置WebSocket连接"""
        if not self.websocket.get('enabled', True):
            return
        
        try:
            symbols = await self.get_symbol_list()
            if not symbols:
                logger.warning("没有可用的交易对，跳过WebSocket设置")
                return
            
            # 创建WebSocket连接
            await self._create_websocket_client("bybit_ws_0", symbols[:self.max_symbols_per_connection])
            logger.info("Bybit WebSocket连接设置完成")
            
        except Exception as e:
            logger.error(f"Bybit WebSocket连接设置失败: {e}")
            raise
    
    async def _setup_rest_connection(self):
        """设置REST API连接"""
        if not self.rest_api.get('enabled', True):
            return
        
        try:
            timeout = self.rest_api.get('timeout', 30)
            self.http_client = httpx.AsyncClient(timeout=timeout)
            
            if await self._test_rest_connection():
                logger.info("Bybit REST API连接设置成功")
            else:
                raise Exception("Bybit REST API连接测试失败")
                
        except Exception as e:
            logger.error(f"Bybit REST API连接设置失败: {e}")
            raise
    
    @network_retry
    async def _fetch_bybit_symbols(self) -> List[str]:
        """获取Bybit交易对列表"""
        if not self.http_client:
            await self._setup_rest_connection()
        
        url = f"{self.base_url}/v5/market/instruments-info"
        params = {'category': 'linear'}  # 线性合约
        
        response = await self.http_client.get(url, params=params)
        
        if response.status_code != 200:
            raise Exception(f"HTTP错误 {response.status_code}: {response.text}")
        
        data = response.json()
        if data.get('retCode') != 0:
            raise Exception(f"API错误: {data.get('retMsg')}")
        
        symbols = []
        for instrument in data.get('result', {}).get('list', []):
            symbol = instrument.get('symbol', '')
            if symbol.endswith('USDT') and instrument.get('status') == 'Trading':
                # BTCUSDT -> BTC/USDT
                base = symbol[:-4]
                symbols.append(f"{base}/USDT")
        
        logger.info(f"获取到 {len(symbols)} 个Bybit USDT线性合约")
        return sorted(symbols)
    
    @http_retry
    async def _fetch_historical_klines(self, 
                                     symbol: str, 
                                     interval: str, 
                                     start_time: datetime, 
                                     end_time: datetime) -> Optional[pd.DataFrame]:
        """获取历史K线数据"""
        try:
            bybit_symbol = self.normalize_symbol(symbol)
            bybit_interval = self._map_timeframe(interval)
            
            url = f"{self.base_url}/v5/market/kline"
            params = {
                'category': 'linear',
                'symbol': bybit_symbol,
                'interval': bybit_interval,
                'start': str(int(start_time.timestamp() * 1000)),
                'end': str(int(end_time.timestamp() * 1000)),
                'limit': 200
            }
            
            response = await self.http_client.get(url, params=params)
            
            if response.status_code != 200:
                raise Exception(f"HTTP错误 {response.status_code}")
            
            data = response.json()
            if data.get('retCode') != 0:
                raise Exception(f"API错误: {data.get('retMsg')}")
            
            klines_raw = data.get('result', {}).get('list', [])
            
            if not klines_raw:
                return pd.DataFrame()
            
            # 转换为DataFrame
            df_data = []
            for kline in klines_raw:
                # Bybit K线格式: [开始时间, 开盘价, 最高价, 最低价, 收盘价, 成交量, 成交额]
                timestamp = datetime.fromtimestamp(int(kline[0]) / 1000, timezone.utc)
                
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
                if validation_result.fixed_data is not None:
                    df = validation_result.fixed_data
            
            return df
            
        except Exception as e:
            logger.error(f"获取 {symbol} 历史数据失败: {e}")
            return None
    
    async def _create_websocket_client(self, client_key: str, symbols: List[str]):
        """创建WebSocket客户端"""
        try:
            websocket = await websockets.connect(self.ws_base_url)
            self.websocket_clients[client_key] = websocket
            
            # 订阅K线数据
            subscribe_msg = {
                "op": "subscribe",
                "args": []
            }
            
            for symbol in symbols:
                bybit_symbol = self.normalize_symbol(symbol)
                subscribe_msg["args"].append(f"kline.1.{bybit_symbol}")
            
            await websocket.send(json.dumps(subscribe_msg))
            
            # 启动消息处理
            handler_task = asyncio.create_task(
                self._handle_websocket_messages(client_key, websocket)
            )
            
            logger.info(f"Bybit WebSocket客户端 {client_key} 创建成功")
            
        except Exception as e:
            logger.error(f"创建Bybit WebSocket客户端失败: {e}")
            raise
    
    async def _handle_websocket_messages(self, client_key: str, websocket):
        """处理WebSocket消息"""
        try:
            while not websocket.closed:
                raw_message = await websocket.recv()
                message_data = json.loads(raw_message)
                
                # 处理K线数据
                if 'data' in message_data and 'topic' in message_data:
                    topic = message_data['topic']
                    if topic.startswith('kline.'):
                        for item in message_data['data']:
                            kline_data = self._parse_bybit_kline(item, topic)
                            if kline_data:
                                await self._process_kline_data(kline_data)
                            
        except websockets.exceptions.ConnectionClosed:
            logger.warning(f"Bybit WebSocket连接 {client_key} 已关闭")
        except Exception as e:
            logger.error(f"Bybit WebSocket消息处理错误: {e}")
    
    def _parse_bybit_kline(self, kline_data: Dict, topic: str) -> Optional[Dict]:
        """解析Bybit K线数据"""
        try:
            # 从topic提取符号: kline.1.BTCUSDT -> BTCUSDT
            parts = topic.split('.')
            if len(parts) < 3:
                return None
            
            bybit_symbol = parts[2]
            if not bybit_symbol.endswith('USDT'):
                return None
            
            # 转换符号格式
            base = bybit_symbol[:-4]
            symbol = f"{base}/USDT"
            
            timestamp = datetime.fromtimestamp(int(kline_data['start']) / 1000, timezone.utc)
            
            return {
                'timestamp': timestamp,
                'open': float(kline_data['open']),
                'high': float(kline_data['high']),
                'low': float(kline_data['low']),
                'close': float(kline_data['close']),
                'volume': float(kline_data['volume']),
                'symbol': symbol,
                'exchange': self.exchange_name,
                'timeframe': '1m'
            }
            
        except (KeyError, ValueError, IndexError) as e:
            logger.error(f"Bybit K线数据解析错误: {e}")
            return None
    
    async def _process_kline_data(self, kline_data: Dict):
        """处理K线数据"""
        try:
            df = pd.DataFrame([kline_data])
            
            if self.enable_validation and self.validator:
                validation_result = self.validator.validate_kline_data(df, fix_issues=True)
                if validation_result.has_errors:
                    return
                if validation_result.fixed_data is not None:
                    df = validation_result.fixed_data
            
            if self.db_manager and not df.empty:
                await self.db_manager.insert_kline_batch(df.to_dict('records'))
            
            self._update_collection_stats(True, kline_data['symbol'], len(df))
            
        except Exception as e:
            logger.error(f"Bybit K线数据处理失败: {e}")
            self._update_collection_stats(False, kline_data.get('symbol', 'unknown'), 0, str(e))
    
    async def _test_rest_connection(self) -> bool:
        """测试REST API连接"""
        try:
            url = f"{self.base_url}/v5/market/time"
            response = await self.http_client.get(url)
            return response.status_code == 200
        except Exception:
            return False
    
    def _map_timeframe(self, standard_timeframe: str) -> str:
        """映射标准时间框架到Bybit格式"""
        timeframe_map = {
            '1m': '1', '5m': '5', '15m': '15', '1h': '60', 
            '4h': '240', '1d': 'D'
        }
        return timeframe_map.get(standard_timeframe, '1')
    
    async def cleanup(self):
        """清理资源"""
        logger.info("清理Bybit收集器资源")
        
        # 关闭WebSocket连接
        for client_key, websocket in self.websocket_clients.items():
            try:
                if not websocket.closed:
                    await websocket.close()
            except Exception as e:
                logger.warning(f"关闭WebSocket连接失败: {e}")
        
        self.websocket_clients.clear()
        
        # 关闭HTTP客户端
        if self.http_client:
            await self.http_client.aclose()
            self.http_client = None
        
        await super().cleanup()
        logger.info("Bybit收集器资源清理完成")