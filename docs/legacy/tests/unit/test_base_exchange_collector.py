"""
BaseExchangeCollector 抽象基类单元测试
TDD: 先写测试定义接口行为，再实现
"""
import pytest
import asyncio
import pandas as pd
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from typing import List, Optional

# 这里先import我们即将创建的基类
# from src.enhanced_collectors.base_collector import BaseExchangeCollector


class TestBaseExchangeCollector:
    """BaseExchangeCollector 抽象基类测试"""
    
    @pytest.fixture
    def mock_collector_config(self):
        """Mock收集器配置"""
        return {
            'max_concurrent': 5,
            'max_retry_count': 3,
            'delay_between_requests': 0.1,
            'min_data_points': 100,
            'enable_data_validation': True,
            'websocket': {
                'enabled': True,
                'max_reconnect_attempts': 5,
                'heartbeat_interval': 30
            },
            'rest_api': {
                'enabled': True,
                'timeout': 30
            },
            'rate_limit': 10.0
        }
    
    @pytest.fixture
    def sample_kline_data(self):
        """样本K线数据"""
        now = datetime.now()
        data = []
        
        for i in range(10):
            timestamp = now - timedelta(minutes=i)
            data.append({
                'timestamp': timestamp,
                'open': 50000.0 + i,
                'high': 50100.0 + i,
                'low': 49900.0 + i,
                'close': 50050.0 + i,
                'volume': 100.0 + i,
                'symbol': 'BTC/USDT',
                'exchange': 'test_exchange'
            })
            
        return pd.DataFrame(data)

    # ===============================
    # 抽象方法行为测试
    # ===============================
    
    @pytest.mark.unit
    def test_abstract_class_cannot_be_instantiated(self):
        """测试抽象基类不能直接实例化"""
        from src.enhanced_collectors.base_collector import BaseExchangeCollector
        
        with pytest.raises(TypeError, match="Can't instantiate abstract class"):
            BaseExchangeCollector("test_exchange")
    
    @pytest.mark.unit
    async def test_concrete_implementation_requires_all_abstract_methods(self):
        """测试具体实现必须实现所有抽象方法"""
        from src.enhanced_collectors.base_collector import BaseExchangeCollector
        
        class IncompleteCollector(BaseExchangeCollector):
            """不完整的收集器实现 - 缺少抽象方法"""
            pass
        
        with pytest.raises(TypeError, match="Can't instantiate abstract class"):
            IncompleteCollector("incomplete_exchange")
    
    @pytest.mark.unit
    async def test_complete_implementation_works(self):
        """测试完整实现可以正常工作"""
        from src.enhanced_collectors.base_collector import BaseExchangeCollector
        
        class CompleteCollector(BaseExchangeCollector):
            """完整的收集器实现"""
            
            async def get_symbol_list(self) -> List[str]:
                return ['BTC/USDT', 'ETH/USDT']
                
            def normalize_symbol(self, symbol: str) -> str:
                return symbol.upper()
                
            async def fetch_kline_data(self, symbol: str, interval: str, 
                                     start_time: datetime, end_time: datetime) -> Optional[pd.DataFrame]:
                # 返回模拟数据
                return pd.DataFrame({
                    'timestamp': [datetime.now()],
                    'open': [50000.0],
                    'high': [50100.0], 
                    'low': [49900.0],
                    'close': [50050.0],
                    'volume': [100.0],
                    'symbol': [symbol],
                    'exchange': ['test']
                })
                
            async def validate_connection(self) -> bool:
                return True
                
            async def _setup_websocket_connection(self):
                pass
                
            async def _setup_rest_connection(self):
                pass
        
        # 应该能够正常实例化
        collector = CompleteCollector("test_exchange")
        assert collector.exchange_name == "test_exchange"

    # ===============================
    # 初始化和配置测试
    # ===============================
    
    @pytest.mark.unit
    async def test_initialization_with_default_config(self):
        """测试使用默认配置初始化"""
        from src.enhanced_collectors.base_collector import BaseExchangeCollector
        
        class TestCollector(BaseExchangeCollector):
            async def get_symbol_list(self): return []
            def normalize_symbol(self, symbol): return symbol
            async def fetch_kline_data(self, *args): return None
            async def validate_connection(self): return True
            async def _setup_websocket_connection(self): pass
            async def _setup_rest_connection(self): pass
        
        collector = TestCollector("test_exchange")
        
        # 检查默认配置值
        assert collector.max_concurrent == 10
        assert collector.max_retry_count == 3
        assert collector.delay == 0.1
        assert collector.min_data_points == 100
        assert collector.enable_validation == True
    
    @pytest.mark.unit
    async def test_initialization_with_custom_config(self, mock_collector_config):
        """测试使用自定义配置初始化"""
        from src.enhanced_collectors.base_collector import BaseExchangeCollector
        
        class TestCollector(BaseExchangeCollector):
            async def get_symbol_list(self): return []
            def normalize_symbol(self, symbol): return symbol  
            async def fetch_kline_data(self, *args): return None
            async def validate_connection(self): return True
            async def _setup_websocket_connection(self): pass
            async def _setup_rest_connection(self): pass
        
        collector = TestCollector("test_exchange", **mock_collector_config)
        
        # 检查自定义配置值
        assert collector.max_concurrent == 5
        assert collector.max_retry_count == 3
        assert collector.delay == 0.1
        assert collector.min_data_points == 100

    # ===============================
    # 数据收集流程测试
    # ===============================
    
    @pytest.mark.unit
    async def test_collect_single_symbol_success(self, sample_kline_data):
        """测试单个交易对数据收集成功"""
        from src.enhanced_collectors.base_collector import BaseExchangeCollector
        
        class TestCollector(BaseExchangeCollector):
            async def get_symbol_list(self): return ['BTC/USDT']
            def normalize_symbol(self, symbol): return symbol
            async def fetch_kline_data(self, symbol, interval, start_time, end_time):
                return sample_kline_data
            async def validate_connection(self): return True
            async def _setup_websocket_connection(self): pass
            async def _setup_rest_connection(self): pass
        
        collector = TestCollector("test_exchange")
        
        result = await collector.collect_symbol_data(
            symbol="BTC/USDT",
            interval="1m", 
            start_time=datetime.now() - timedelta(hours=1),
            end_time=datetime.now()
        )
        
        assert result.success == True
        assert result.data is not None
        assert len(result.data) == 10  # sample_kline_data has 10 rows
        assert result.error is None
    
    @pytest.mark.unit
    async def test_collect_single_symbol_with_validation_failure(self):
        """测试数据验证失败的情况"""
        from src.enhanced_collectors.base_collector import BaseExchangeCollector
        
        # 创建无效数据（high < low）
        invalid_data = pd.DataFrame({
            'timestamp': [datetime.now()],
            'open': [50000.0],
            'high': [49900.0],  # high < low，无效数据
            'low': [50100.0], 
            'close': [50050.0],
            'volume': [100.0],
            'symbol': ['BTC/USDT'],
            'exchange': ['test']
        })
        
        class TestCollector(BaseExchangeCollector):
            async def get_symbol_list(self): return ['BTC/USDT']
            def normalize_symbol(self, symbol): return symbol
            async def fetch_kline_data(self, symbol, interval, start_time, end_time):
                return invalid_data
            async def validate_connection(self): return True
            async def _setup_websocket_connection(self): pass
            async def _setup_rest_connection(self): pass
        
        collector = TestCollector("test_exchange", enable_data_validation=True)
        
        result = await collector.collect_symbol_data(
            symbol="BTC/USDT",
            interval="1m",
            start_time=datetime.now() - timedelta(hours=1), 
            end_time=datetime.now()
        )
        
        # 验证失败，但可能会有修复数据
        assert result.success == True  # 假设验证器能自动修复
        assert result.validation_issues is not None
        assert len(result.validation_issues) > 0

    # ===============================
    # 错误处理测试
    # ===============================
    
    @pytest.mark.unit
    async def test_collect_single_symbol_network_error(self):
        """测试网络错误处理"""
        from src.enhanced_collectors.base_collector import BaseExchangeCollector
        import aiohttp
        
        class TestCollector(BaseExchangeCollector):
            async def get_symbol_list(self): return ['BTC/USDT']
            def normalize_symbol(self, symbol): return symbol
            async def fetch_kline_data(self, symbol, interval, start_time, end_time):
                raise aiohttp.ClientError("Network error")
            async def validate_connection(self): return True
            async def _setup_websocket_connection(self): pass
            async def _setup_rest_connection(self): pass
        
        collector = TestCollector("test_exchange")
        
        result = await collector.collect_symbol_data(
            symbol="BTC/USDT",
            interval="1m",
            start_time=datetime.now() - timedelta(hours=1),
            end_time=datetime.now()
        )
        
        assert result.success == False
        assert result.data is None
        assert result.error is not None
        assert "Network error" in str(result.error)

    # ===============================
    # 统计和监控测试
    # ===============================
    
    @pytest.mark.unit
    async def test_collection_statistics_tracking(self):
        """测试收集统计信息跟踪"""
        from src.enhanced_collectors.base_collector import BaseExchangeCollector
        
        class TestCollector(BaseExchangeCollector):
            async def get_symbol_list(self): return ['BTC/USDT', 'ETH/USDT']
            def normalize_symbol(self, symbol): return symbol
            async def fetch_kline_data(self, symbol, interval, start_time, end_time):
                if symbol == 'BTC/USDT':
                    return pd.DataFrame({'timestamp': [datetime.now()], 'open': [50000]})
                else:
                    raise Exception("Fetch failed")
            async def validate_connection(self): return True
            async def _setup_websocket_connection(self): pass
            async def _setup_rest_connection(self): pass
        
        collector = TestCollector("test_exchange")
        
        # 收集多个交易对，一个成功一个失败
        results = []
        for symbol in ['BTC/USDT', 'ETH/USDT']:
            result = await collector.collect_symbol_data(
                symbol=symbol,
                interval="1m",
                start_time=datetime.now() - timedelta(hours=1),
                end_time=datetime.now()
            )
            results.append(result)
        
        # 检查统计信息
        stats = collector.get_collection_stats()
        
        assert stats['total_attempts'] >= 2
        assert stats['successful_collections'] >= 1
        assert stats['failed_collections'] >= 1
        assert 0 < stats['success_rate'] < 1.0

    # ===============================
    # WebSocket 相关测试
    # ===============================
    
    @pytest.mark.unit
    async def test_websocket_connection_initialization(self):
        """测试WebSocket连接初始化"""
        from src.enhanced_collectors.base_collector import BaseExchangeCollector
        
        class TestCollector(BaseExchangeCollector):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.websocket_initialized = False
                
            async def get_symbol_list(self): return []
            def normalize_symbol(self, symbol): return symbol
            async def fetch_kline_data(self, *args): return None
            async def validate_connection(self): return True
            
            async def _setup_websocket_connection(self):
                self.websocket_initialized = True
                
            async def _setup_rest_connection(self): pass
        
        websocket_config = {
            'websocket': {'enabled': True},
            'rest_api': {'enabled': True}
        }
        
        collector = TestCollector("test_exchange", **websocket_config)
        await collector.initialize()
        
        # WebSocket应该被初始化
        assert collector.websocket_initialized == True
    
    @pytest.mark.unit
    async def test_websocket_disabled_fallback_to_rest(self):
        """测试WebSocket禁用时回退到REST"""
        from src.enhanced_collectors.base_collector import BaseExchangeCollector
        
        class TestCollector(BaseExchangeCollector):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs) 
                self.websocket_initialized = False
                self.rest_initialized = False
                
            async def get_symbol_list(self): return []
            def normalize_symbol(self, symbol): return symbol
            async def fetch_kline_data(self, *args): return None
            async def validate_connection(self): return True
            
            async def _setup_websocket_connection(self):
                self.websocket_initialized = True
                
            async def _setup_rest_connection(self):
                self.rest_initialized = True
        
        rest_only_config = {
            'websocket': {'enabled': False},
            'rest_api': {'enabled': True}
        }
        
        collector = TestCollector("test_exchange", **rest_only_config) 
        await collector.initialize()
        
        # 只有REST应该被初始化
        assert collector.websocket_initialized == False
        assert collector.rest_initialized == True


class TestCollectionResult:
    """数据收集结果类测试"""
    
    @pytest.mark.unit
    def test_collection_result_creation(self):
        """测试收集结果创建"""
        from src.enhanced_collectors.base_collector import CollectionResult
        
        result = CollectionResult(
            success=True,
            symbol="BTC/USDT",
            data=pd.DataFrame({'test': [1, 2, 3]}),
            duration=1.5,
            validation_issues=None,
            error=None
        )
        
        assert result.success == True
        assert result.symbol == "BTC/USDT" 
        assert len(result.data) == 3
        assert result.duration == 1.5
        assert result.validation_issues is None
        assert result.error is None
    
    @pytest.mark.unit
    def test_collection_result_failure(self):
        """测试收集失败结果"""
        from src.enhanced_collectors.base_collector import CollectionResult
        
        error = Exception("Test error")
        result = CollectionResult(
            success=False,
            symbol="ETH/USDT",
            data=None,
            duration=0.5,
            validation_issues=None,
            error=error
        )
        
        assert result.success == False
        assert result.symbol == "ETH/USDT"
        assert result.data is None
        assert result.error == error


# ===============================
# Mock Fixtures for Testing
# ===============================

@pytest.fixture
def mock_clickhouse_manager():
    """Mock ClickHouse管理器"""
    manager = MagicMock()
    manager.insert_kline_batch = AsyncMock()
    return manager


@pytest.fixture  
def mock_validator():
    """Mock 数据验证器"""
    from src.validators.data_validator import ValidationResult
    
    validator = MagicMock()
    validator.validate_kline_data.return_value = ValidationResult(
        is_valid=True,
        issues=[],
        fixed_data=None
    )
    return validator