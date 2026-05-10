"""
增强版OKX数据收集器
继承BaseExchangeCollector，实现OKX特定的数据收集逻辑
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


class EnhancedOKXCollector(BaseExchangeCollector):
    """
    增强版OKX数据收集器
    """
    
    def __init__(self, 
                 exchange_name: str = "okx",
                 db_manager: Optional[Any] = None,
                 validator: Optional[CryptoDataValidator] = None,
                 concurrency_controller: Optional[SmartConcurrencyController] = None,
                 **config):
        super().__init__(exchange_name, **config)
        
        self.db_manager = db_manager
        self.validator = validator or CryptoDataValidator()
        self.concurrency_controller = concurrency_controller
        
        # OKX特定配置
        self.base_url = "https://www.okx.com"
        self.ws_base_url = "wss://ws.okx.com:8443/ws/v5/public"
        self.max_symbols_per_connection = 50
        
        # 客户端
        self.http_client: Optional[httpx.AsyncClient] = None
        self.websocket_clients: Dict[str, Any] = {}
        
        # 缓存
        self.symbol_list_cache: Optional[List[str]] = None
        self.symbol_list_cache_time: Optional[datetime] = None
        
        logger.info(f"EnhancedOKXCollector 初始化完成")
    
    async def get_symbol_list(self) -> List[str]:
        """获取OKX交易对列表"""
        if (self.symbol_list_cache and 
            self.symbol_list_cache_time and 
            (datetime.now() - self.symbol_list_cache_time) < timedelta(hours=1)):
            return self.symbol_list_cache
        
        try:
            symbols = await self._fetch_okx_symbols()
            self.symbol_list_cache = symbols
            self.symbol_list_cache_time = datetime.now()
            return symbols
        except Exception as e:
            logger.error(f"获取OKX交易对列表失败: {e}")
            return self.symbol_list_cache or []
    
    def normalize_symbol(self, symbol: str) -> str:
        """标准化交易对符号格式"""
        # BTC/USDT -> BTC-USDT (OKX格式)
        if '/' in symbol:
            return symbol.replace('/', '-')
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
            await self._create_websocket_client("okx_ws_0", symbols[:self.max_symbols_per_connection])
            logger.info("OKX WebSocket连接设置完成")
            
        except Exception as e:
            logger.error(f"OKX WebSocket连接设置失败: {e}")
            raise
    
    async def _setup_rest_connection(self):
        """设置REST API连接"""
        if not self.rest_api.get('enabled', True):
            return
        
        try:
            timeout = self.rest_api.get('timeout', 30)
            self.http_client = httpx.AsyncClient(timeout=timeout)
            
            if await self._test_rest_connection():
                logger.info("OKX REST API连接设置成功")
            else:
                raise Exception("OKX REST API连接测试失败")
                
        except Exception as e:
            logger.error(f"OKX REST API连接设置失败: {e}")
            raise
    
    @network_retry
    async def _fetch_okx_symbols(self) -> List[str]:
        """获取OKX交易对列表"""
        if not self.http_client:
            await self._setup_rest_connection()
        
        url = f"{self.base_url}/api/v5/public/instruments"
        params = {'instType': 'SWAP'}  # 永续合约
        
        response = await self.http_client.get(url, params=params)
        
        if response.status_code != 200:
            raise Exception(f"HTTP错误 {response.status_code}: {response.text}")
        
        data = response.json()
        if data.get('code') != '0':
            raise Exception(f"API错误: {data.get('msg')}")
        
        symbols = []
        for instrument in data.get('data', []):
            inst_id = instrument.get('instId', '')
            if '-USDT-SWAP' in inst_id:
                # BTC-USDT-SWAP -> BTC/USDT
                base = inst_id.replace('-USDT-SWAP', '')
                symbols.append(f"{base}/USDT")
        
        logger.info(f"获取到 {len(symbols)} 个OKX USDT永续合约")
        return sorted(symbols)
    
    @http_retry
    async def _fetch_historical_klines(self, 
                                     symbol: str, 
                                     interval: str, 
                                     start_time: datetime, 
                                     end_time: datetime) -> Optional[pd.DataFrame]:
        """获取历史K线数据"""
        try:
            okx_symbol = self.normalize_symbol(symbol) + '-SWAP'
            okx_interval = self._map_timeframe(interval)
            
            url = f"{self.base_url}/api/v5/market/candles"
            params = {
                'instId': okx_symbol,
                'bar': okx_interval,
                'before': str(int(start_time.timestamp() * 1000)),
                'after': str(int(end_time.timestamp() * 1000)),
                'limit': 100
            }
            
            response = await self.http_client.get(url, params=params)
            
            if response.status_code != 200:
                raise Exception(f"HTTP错误 {response.status_code}")
            
            data = response.json()
            if data.get('code') != '0':
                raise Exception(f"API错误: {data.get('msg')}")
            
            klines_raw = data.get('data', [])
            
            if not klines_raw:
                return pd.DataFrame()
            
            # 转换为DataFrame
            df_data = []
            for kline in klines_raw:
                # OKX K线格式: [时间戳, 开盘价, 最高价, 最低价, 收盘价, 成交量, 成交额, 确认状态]
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
                okx_symbol = self.normalize_symbol(symbol) + '-SWAP'
                subscribe_msg["args"].append({
                    "channel": "candle1m",
                    "instId": okx_symbol
                })
            
            await websocket.send(json.dumps(subscribe_msg))
            
            # 启动消息处理
            handler_task = asyncio.create_task(
                self._handle_websocket_messages(client_key, websocket)
            )
            
            logger.info(f"OKX WebSocket客户端 {client_key} 创建成功")
            
        except Exception as e:
            logger.error(f"创建OKX WebSocket客户端失败: {e}")
            raise
    
    async def _handle_websocket_messages(self, client_key: str, websocket):
        """处理WebSocket消息"""
        try:
            while not websocket.closed:
                raw_message = await websocket.recv()
                message_data = json.loads(raw_message)
                
                # 处理K线数据
                if 'data' in message_data:
                    for item in message_data['data']:
                        kline_data = self._parse_okx_kline(item, message_data.get('arg', {}))
                        if kline_data:
                            await self._process_kline_data(kline_data)
                            
        except websockets.exceptions.ConnectionClosed:
            logger.warning(f"OKX WebSocket连接 {client_key} 已关闭")
        except Exception as e:
            logger.error(f"OKX WebSocket消息处理错误: {e}")
    
    def _parse_okx_kline(self, kline_data: List, arg: Dict) -> Optional[Dict]:
        """解析OKX K线数据"""
        try:
            inst_id = arg.get('instId', '')
            if not inst_id.endswith('-SWAP'):
                return None
            
            # 转换符号格式
            base = inst_id.replace('-USDT-SWAP', '')
            symbol = f"{base}/USDT"
            
            timestamp = datetime.fromtimestamp(int(kline_data[0]) / 1000, timezone.utc)
            
            return {
                'timestamp': timestamp,
                'open': float(kline_data[1]),
                'high': float(kline_data[2]),
                'low': float(kline_data[3]),
                'close': float(kline_data[4]),
                'volume': float(kline_data[5]),
                'symbol': symbol,
                'exchange': self.exchange_name,
                'timeframe': '1m'
            }
            
        except (KeyError, ValueError, IndexError) as e:
            logger.error(f"OKX K线数据解析错误: {e}")
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
            logger.error(f"OKX K线数据处理失败: {e}")
            self._update_collection_stats(False, kline_data.get('symbol', 'unknown'), 0, str(e))
    
    async def _test_rest_connection(self) -> bool:
        """测试REST API连接"""
        try:
            url = f"{self.base_url}/api/v5/public/time"
            response = await self.http_client.get(url)
            return response.status_code == 200
        except Exception:
            return False
    
    def _map_timeframe(self, standard_timeframe: str) -> str:
        """映射标准时间框架到OKX格式"""
        timeframe_map = {
            '1m': '1m', '5m': '5m', '15m': '15m', '1h': '1H', 
            '4h': '4H', '1d': '1D'
        }
        return timeframe_map.get(standard_timeframe, '1m')
    
    async def cleanup(self):
        """清理资源"""
        logger.info("清理OKX收集器资源")
        
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
        logger.info("OKX收集器资源清理完成")