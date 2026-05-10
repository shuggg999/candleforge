"""
异步重试装饰器单元测试
TDD: 先写测试定义重试行为，再实现
"""
import pytest
import asyncio
import time
from unittest.mock import AsyncMock
import aiohttp


class TestAsyncRetry:
    """异步重试装饰器测试"""
    
    @pytest.mark.unit
    async def test_success_on_first_attempt(self):
        """测试第一次尝试就成功的情况"""
        from src.utils.enhanced.retry_decorator import async_retry
        
        call_count = 0
        
        @async_retry(max_attempts=3)
        async def successful_function():
            nonlocal call_count
            call_count += 1
            return "success"
        
        result = await successful_function()
        
        assert result == "success"
        assert call_count == 1  # 只调用一次
    
    @pytest.mark.unit  
    async def test_success_after_failures(self):
        """测试失败几次后成功的情况"""
        from src.utils.enhanced.retry_decorator import async_retry
        
        call_count = 0
        
        @async_retry(max_attempts=3, backoff_factor=0.01)  # 快速重试用于测试
        async def flaky_function():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("Network failed")
            return "success"
        
        result = await flaky_function()
        
        assert result == "success"
        assert call_count == 3  # 调用3次
    
    @pytest.mark.unit
    async def test_final_failure_after_all_attempts(self):
        """测试所有尝试都失败的情况"""
        from src.utils.enhanced.retry_decorator import async_retry
        
        call_count = 0
        
        @async_retry(max_attempts=3, backoff_factor=0.01)
        async def failing_function():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("Always fails")
        
        with pytest.raises(ConnectionError, match="Always fails"):
            await failing_function()
        
        assert call_count == 3  # 尝试了3次
    
    @pytest.mark.unit
    async def test_exponential_backoff_timing(self):
        """测试指数退避时间"""
        from src.utils.enhanced.retry_decorator import async_retry
        
        call_times = []
        
        @async_retry(max_attempts=3, backoff_factor=0.1, max_delay=1.0)
        async def timed_function():
            call_times.append(time.time())
            raise TimeoutError("Timeout")
        
        start_time = time.time()
        
        with pytest.raises(TimeoutError):
            await timed_function()
            
        # 检查时间间隔
        assert len(call_times) == 3
        
        # 第一次和第二次之间应该有约0.1秒间隔
        interval1 = call_times[1] - call_times[0]
        assert 0.09 <= interval1 <= 0.2  # 允许一定误差
        
        # 第二次和第三次之间应该有约0.15秒间隔 (0.1 * 1.5)
        interval2 = call_times[2] - call_times[1]
        assert 0.14 <= interval2 <= 0.25
    
    @pytest.mark.unit
    async def test_jitter_randomization(self):
        """测试随机抖动功能"""
        from src.utils.enhanced.retry_decorator import async_retry
        
        call_times = []
        
        @async_retry(max_attempts=5, backoff_factor=0.1, jitter=True)
        async def jitter_function():
            call_times.append(time.time())
            raise Exception("Fail")
            
        with pytest.raises(Exception):
            await jitter_function()
        
        # 检查间隔不完全相等（说明有抖动）
        intervals = []
        for i in range(1, len(call_times)):
            intervals.append(call_times[i] - call_times[i-1])
            
        # 间隔应该都不完全相等（抖动效果）
        assert len(set(f"{x:.3f}" for x in intervals)) > 1
    
    @pytest.mark.unit
    async def test_specific_exception_types(self):
        """测试只对特定异常类型重试"""
        from src.utils.enhanced.retry_decorator import async_retry
        
        call_count = 0
        
        @async_retry(max_attempts=3, exceptions=(ConnectionError, TimeoutError))
        async def selective_retry_function(exception_type):
            nonlocal call_count
            call_count += 1
            
            if exception_type == "connection":
                raise ConnectionError("Connection failed")
            elif exception_type == "timeout":
                raise TimeoutError("Timeout")
            elif exception_type == "value":
                raise ValueError("Value error")
        
        # ConnectionError应该重试
        call_count = 0
        with pytest.raises(ConnectionError):
            await selective_retry_function("connection")
        assert call_count == 3
        
        # TimeoutError应该重试
        call_count = 0  
        with pytest.raises(TimeoutError):
            await selective_retry_function("timeout")
        assert call_count == 3
        
        # ValueError不应该重试
        call_count = 0
        with pytest.raises(ValueError):
            await selective_retry_function("value")
        assert call_count == 1  # 只调用一次，没有重试
    
    @pytest.mark.unit
    async def test_max_delay_cap(self):
        """测试最大延迟时间上限"""
        from src.utils.enhanced.retry_decorator import async_retry
        
        call_times = []
        
        @async_retry(max_attempts=4, backoff_factor=1.0, max_delay=0.2)
        async def capped_delay_function():
            call_times.append(time.time())
            raise Exception("Fail")
        
        with pytest.raises(Exception):
            await capped_delay_function()
        
        # 检查后面的间隔被限制在max_delay内
        intervals = []
        for i in range(1, len(call_times)):
            intervals.append(call_times[i] - call_times[i-1])
        
        # 所有间隔都应该小于等于max_delay + 一点误差
        for interval in intervals:
            assert interval <= 0.25  # max_delay + 误差
    
    @pytest.mark.unit
    async def test_retry_with_different_backoff_factors(self):
        """测试不同的退避因子"""
        from src.utils.enhanced.retry_decorator import async_retry
        
        # 测试线性退避 (backoff_factor = 1.0)
        call_times_linear = []
        
        @async_retry(max_attempts=3, backoff_factor=1.0)
        async def linear_backoff():
            call_times_linear.append(time.time())
            raise Exception("Fail")
        
        with pytest.raises(Exception):
            await linear_backoff()
        
        # 检查是线性增长 (1.0, 1.0, ...)
        intervals_linear = [call_times_linear[i+1] - call_times_linear[i] for i in range(len(call_times_linear)-1)]
        
        # 测试指数退避 (backoff_factor = 2.0)
        call_times_exp = []
        
        @async_retry(max_attempts=3, backoff_factor=2.0)
        async def exp_backoff():
            call_times_exp.append(time.time())
            raise Exception("Fail")
        
        with pytest.raises(Exception):
            await exp_backoff()
        
        intervals_exp = [call_times_exp[i+1] - call_times_exp[i] for i in range(len(call_times_exp)-1)]
        
        # 指数退避的第二个间隔应该大于第一个间隔
        if len(intervals_exp) >= 2:
            assert intervals_exp[1] > intervals_exp[0] * 1.5  # 考虑抖动等因素
    
    @pytest.mark.unit
    async def test_function_arguments_preserved(self):
        """测试函数参数在重试中被正确保留"""
        from src.utils.enhanced.retry_decorator import async_retry
        
        received_args = []
        received_kwargs = []
        
        @async_retry(max_attempts=3, backoff_factor=0.01)
        async def arg_test_function(*args, **kwargs):
            received_args.append(args)
            received_kwargs.append(kwargs)
            raise Exception("Fail")
        
        with pytest.raises(Exception):
            await arg_test_function("arg1", "arg2", key1="value1", key2="value2")
        
        # 检查所有重试中参数都相同
        assert len(received_args) == 3
        assert len(received_kwargs) == 3
        
        for args in received_args:
            assert args == ("arg1", "arg2")
            
        for kwargs in received_kwargs:
            assert kwargs == {"key1": "value1", "key2": "value2"}
    
    @pytest.mark.unit
    async def test_return_value_preserved(self):
        """测试返回值被正确保留"""
        from src.utils.enhanced.retry_decorator import async_retry
        
        @async_retry(max_attempts=3)
        async def return_complex_value():
            return {"status": "success", "data": [1, 2, 3], "nested": {"key": "value"}}
        
        result = await return_complex_value()
        
        assert result == {"status": "success", "data": [1, 2, 3], "nested": {"key": "value"}}


class TestRetryIntegrationWithExchanges:
    """重试装饰器与交易所API集成测试"""
    
    @pytest.mark.unit
    async def test_binance_api_retry_simulation(self):
        """模拟Binance API重试场景"""
        from src.utils.enhanced.retry_decorator import async_retry
        
        call_count = 0
        
        @async_retry(max_attempts=5, exceptions=(aiohttp.ClientError, ConnectionError))
        async def mock_binance_api():
            nonlocal call_count
            call_count += 1
            
            if call_count <= 2:
                # 模拟网络错误
                raise aiohttp.ClientConnectionError("Connection failed")
            elif call_count == 3:
                # 模拟超时
                raise asyncio.TimeoutError("Request timeout")
            else:
                # 成功返回数据
                return {"symbol": "BTCUSDT", "price": "50000"}
        
        result = await mock_binance_api()
        
        assert result == {"symbol": "BTCUSDT", "price": "50000"}
        assert call_count == 4  # 失败3次，第4次成功
    
    @pytest.mark.unit  
    async def test_rate_limit_retry_with_longer_delays(self):
        """测试API限流时的长延迟重试"""
        from src.utils.enhanced.retry_decorator import async_retry
        
        call_count = 0
        start_time = time.time()
        
        @async_retry(max_attempts=3, backoff_factor=0.1, max_delay=0.5)
        async def rate_limited_api():
            nonlocal call_count
            call_count += 1
            
            if call_count <= 2:
                # 模拟429 Too Many Requests
                raise aiohttp.ClientResponseError(
                    request_info=None, 
                    history=None,
                    status=429,
                    message="Rate limit exceeded"
                )
            else:
                return {"data": "success"}
        
        result = await rate_limited_api()
        elapsed = time.time() - start_time
        
        assert result == {"data": "success"}
        assert call_count == 3
        # 应该有退避延迟
        assert elapsed >= 0.15  # 至少有两次延迟: ~0.1s + ~0.15s


# ===============================
# 辅助测试fixtures
# ===============================

@pytest.fixture
def mock_network_error():
    """模拟网络错误"""
    return ConnectionError("Network connection failed")


@pytest.fixture
def mock_timeout_error():
    """模拟超时错误"""  
    return asyncio.TimeoutError("Request timeout")


@pytest.fixture
def mock_aiohttp_error():
    """模拟aiohttp错误"""
    return aiohttp.ClientError("HTTP client error")