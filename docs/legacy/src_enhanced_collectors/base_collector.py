"""
BaseExchangeCollector - 交易所数据收集器抽象基类
借鉴Qlib专业设计，支持WebSocket优先的数据收集
"""
import abc
import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import pandas as pd
from loguru import logger

from src.utils.enhanced.config_manager import config_manager


@dataclass
class CollectionResult:
    """数据收集结果"""
    success: bool
    symbol: str
    data: Optional[pd.DataFrame] = None
    duration: float = 0.0
    validation_issues: Optional[List[str]] = None
    error: Optional[Exception] = None
    metadata: Optional[Dict[str, Any]] = None


class BaseExchangeCollector(abc.ABC):
    """交易所数据收集器抽象基类"""
    
    # 支持的时间间隔常量
    INTERVAL_1M = "1m"
    INTERVAL_5M = "5m" 
    INTERVAL_15M = "15m"
    INTERVAL_1H = "1h"
    INTERVAL_4H = "4h"
    INTERVAL_1D = "1d"
    
    # 收集状态标志
    NORMAL_FLAG = "NORMAL"
    CACHED_FLAG = "CACHED" 
    ERROR_FLAG = "ERROR"
    
    def __init__(
        self,
        exchange_name: str,
        max_concurrent: int = 10,
        max_retry_count: int = 3,
        delay_between_requests: float = 0.1,
        min_data_points: int = 100,
        enable_data_validation: bool = True,
        websocket: Optional[Dict] = None,
        rest_api: Optional[Dict] = None,
        rate_limit: float = 10.0,
        **kwargs
    ):
        """
        初始化收集器
        
        Args:
            exchange_name: 交易所名称
            max_concurrent: 最大并发数
            max_retry_count: 最大重试次数
            delay_between_requests: 请求间延迟(秒)
            min_data_points: 最小数据点数量
            enable_data_validation: 启用数据验证
            websocket: WebSocket配置
            rest_api: REST API配置
            rate_limit: 速率限制(每秒请求数)
        """
        self.exchange_name = exchange_name
        self.max_concurrent = max_concurrent
        self.max_retry_count = max_retry_count
        self.delay = delay_between_requests
        self.min_data_points = min_data_points
        self.enable_validation = enable_data_validation
        self.rate_limit = rate_limit
        
        # WebSocket和REST配置
        self.websocket_config = websocket or {'enabled': True}
        self.rest_api_config = rest_api or {'enabled': True}
        
        # 内部状态管理
        self._retry_cache: Dict[str, List] = {}
        self._failed_symbols: set = set()
        self._stats = {
            'total_attempts': 0,
            'successful_collections': 0, 
            'failed_collections': 0,
            'validation_failures': 0,
            'total_records': 0,
            'start_time': time.time()
        }
        
        # 连接状态
        self._websocket_connected = False
        self._rest_connected = False
        self._initialized = False
        
    async def initialize(self):
        """初始化收集器连接"""
        if self._initialized:
            return
            
        try:
            # 初始化WebSocket连接（如果启用）
            if self.websocket_config.get('enabled', False):
                await self._setup_websocket_connection()
                self._websocket_connected = True
                logger.info(f"{self.exchange_name}: WebSocket连接已建立")
                
            # 初始化REST连接（如果启用）
            if self.rest_api_config.get('enabled', False):
                await self._setup_rest_connection()
                self._rest_connected = True
                logger.info(f"{self.exchange_name}: REST API连接已建立")
                
            # 验证连接
            if not await self.validate_connection():
                raise Exception("连接验证失败")
                
            self._initialized = True
            logger.info(f"{self.exchange_name}: 收集器初始化完成")
            
        except Exception as e:
            logger.error(f"{self.exchange_name}: 初始化失败 - {e}")
            raise
            
    async def cleanup(self):
        """清理资源"""
        try:
            await self._cleanup_websocket_connection()
            await self._cleanup_rest_connection()
            self._initialized = False
            logger.info(f"{self.exchange_name}: 资源清理完成")
        except Exception as e:
            logger.error(f"{self.exchange_name}: 资源清理失败 - {e}")
            
    # ===============================
    # 抽象方法 - 子类必须实现
    # ===============================
    
    @abc.abstractmethod
    async def get_symbol_list(self) -> List[str]:
        """获取交易对列表"""
        pass
        
    @abc.abstractmethod
    def normalize_symbol(self, symbol: str) -> str:
        """标准化交易对名称"""
        pass
        
    @abc.abstractmethod
    async def fetch_kline_data(
        self,
        symbol: str,
        interval: str, 
        start_time: datetime,
        end_time: datetime
    ) -> Optional[pd.DataFrame]:
        """获取K线数据的核心实现"""
        pass
        
    @abc.abstractmethod
    async def validate_connection(self) -> bool:
        """验证连接状态"""
        pass
        
    @abc.abstractmethod
    async def _setup_websocket_connection(self):
        """设置WebSocket连接 - 子类实现具体逻辑"""
        pass
        
    @abc.abstractmethod  
    async def _setup_rest_connection(self):
        """设置REST连接 - 子类实现具体逻辑"""
        pass
        
    # ===============================
    # 公共数据收集接口
    # ===============================
    
    async def collect_symbol_data(
        self,
        symbol: str,
        interval: str,
        start_time: datetime,
        end_time: datetime,
        use_websocket: bool = True
    ) -> CollectionResult:
        """
        收集单个交易对的数据
        
        Args:
            symbol: 交易对名称
            interval: 时间间隔
            start_time: 开始时间  
            end_time: 结束时间
            use_websocket: 优先使用WebSocket
            
        Returns:
            CollectionResult: 收集结果
        """
        if not self._initialized:
            await self.initialize()
            
        start_collect_time = time.time()
        self._stats['total_attempts'] += 1
        
        try:
            # 标准化交易对名称
            normalized_symbol = self.normalize_symbol(symbol)
            
            # 获取数据（优先使用WebSocket，失败时回退到REST）
            data = None
            if use_websocket and self._websocket_connected:
                try:
                    data = await self.fetch_kline_data(normalized_symbol, interval, start_time, end_time)
                except Exception as e:
                    logger.warning(f"{self.exchange_name}: WebSocket获取失败，回退到REST - {e}")
                    
            # REST API回退
            if data is None and self._rest_connected:
                data = await self.fetch_kline_data(normalized_symbol, interval, start_time, end_time)
                
            if data is None or data.empty:
                raise Exception("未获取到数据")
                
            # 数据验证
            validation_issues = None
            if self.enable_validation:
                validation_result = await self._validate_data(data)
                if not validation_result['is_valid']:
                    validation_issues = validation_result['issues']
                    if validation_result['fixed_data'] is not None:
                        data = validation_result['fixed_data']
                    else:
                        self._stats['validation_failures'] += 1
                        
            # 统计更新
            duration = time.time() - start_collect_time
            self._stats['successful_collections'] += 1
            self._stats['total_records'] += len(data)
            
            return CollectionResult(
                success=True,
                symbol=normalized_symbol,
                data=data,
                duration=duration,
                validation_issues=validation_issues,
                error=None,
                metadata={
                    'data_source': 'websocket' if use_websocket and self._websocket_connected else 'rest',
                    'record_count': len(data),
                    'time_range': f"{start_time} to {end_time}"
                }
            )
            
        except Exception as e:
            duration = time.time() - start_collect_time
            self._stats['failed_collections'] += 1
            
            logger.error(f"{self.exchange_name}: 收集失败 {symbol} - {e}")
            
            return CollectionResult(
                success=False,
                symbol=symbol,
                data=None,
                duration=duration,
                validation_issues=None,
                error=e,
                metadata={'error_type': type(e).__name__}
            )
            
    async def collect_multiple_symbols(
        self,
        symbols: List[str],
        interval: str,
        start_time: datetime,
        end_time: datetime,
        max_concurrent: Optional[int] = None
    ) -> List[CollectionResult]:
        """
        并发收集多个交易对的数据
        
        Args:
            symbols: 交易对列表
            interval: 时间间隔
            start_time: 开始时间
            end_time: 结束时间  
            max_concurrent: 最大并发数（None使用默认值）
            
        Returns:
            List[CollectionResult]: 收集结果列表
        """
        if not symbols:
            return []
            
        concurrent_limit = max_concurrent or self.max_concurrent
        semaphore = asyncio.Semaphore(concurrent_limit)
        
        async def collect_with_semaphore(symbol):
            async with semaphore:
                # 请求间延迟
                if self.delay > 0:
                    await asyncio.sleep(self.delay)
                return await self.collect_symbol_data(symbol, interval, start_time, end_time)
        
        # 创建并发任务
        tasks = [collect_with_semaphore(symbol) for symbol in symbols]
        
        # 执行并发收集
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 处理异常结果
        processed_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                processed_results.append(CollectionResult(
                    success=False,
                    symbol=symbols[i],
                    error=result
                ))
            else:
                processed_results.append(result)
                
        return processed_results
        
    # ===============================
    # 统计和监控
    # ===============================
    
    def get_collection_stats(self) -> Dict[str, Any]:
        """获取收集统计信息"""
        runtime = time.time() - self._stats['start_time']
        total_attempts = self._stats['total_attempts']
        
        return {
            'exchange': self.exchange_name,
            'runtime_seconds': runtime,
            'total_attempts': total_attempts,
            'successful_collections': self._stats['successful_collections'],
            'failed_collections': self._stats['failed_collections'],
            'validation_failures': self._stats['validation_failures'],
            'total_records': self._stats['total_records'],
            'success_rate': (
                self._stats['successful_collections'] / max(1, total_attempts)
            ),
            'avg_records_per_collection': (
                self._stats['total_records'] / max(1, self._stats['successful_collections'])  
            ),
            'connections': {
                'websocket': self._websocket_connected,
                'rest': self._rest_connected
            }
        }
        
    def reset_stats(self):
        """重置统计信息"""
        self._stats = {
            'total_attempts': 0,
            'successful_collections': 0,
            'failed_collections': 0, 
            'validation_failures': 0,
            'total_records': 0,
            'start_time': time.time()
        }
        
    # ===============================
    # 内部辅助方法
    # ===============================
    
    async def _validate_data(self, data: pd.DataFrame) -> Dict[str, Any]:
        """
        数据验证（简化版，完整版在data_validator中）
        
        Returns:
            Dict with keys: is_valid, issues, fixed_data
        """
        issues = []
        
        if data is None or data.empty:
            return {'is_valid': False, 'issues': ['数据为空'], 'fixed_data': None}
            
        # 基础验证
        required_columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
        missing_columns = [col for col in required_columns if col not in data.columns]
        if missing_columns:
            issues.append(f"缺少必要列: {missing_columns}")
            
        # 价格逻辑验证
        if 'high' in data.columns and 'low' in data.columns:
            invalid_price_logic = data['high'] < data['low']
            if invalid_price_logic.any():
                issues.append(f"发现{invalid_price_logic.sum()}条high<low的无效数据")
                
        # 数据量验证
        if len(data) < self.min_data_points:
            issues.append(f"数据点不足: {len(data)} < {self.min_data_points}")
            
        return {
            'is_valid': len(issues) == 0,
            'issues': issues,
            'fixed_data': None  # 简化版不做自动修复
        }
        
    async def _cleanup_websocket_connection(self):
        """清理WebSocket连接 - 子类可重写"""
        pass
        
    async def _cleanup_rest_connection(self):  
        """清理REST连接 - 子类可重写"""
        pass