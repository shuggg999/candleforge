"""
专业异步重试装饰器
借鉴Qlib设计，增强异步支持
"""
import asyncio
import functools
import random
import time
from typing import Callable, Type, Union, Tuple, Any
from loguru import logger


def async_retry(
    max_attempts: int = 3,
    backoff_factor: float = 1.5,
    max_delay: float = 60.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    jitter: bool = True
):
    """
    异步重试装饰器
    
    Args:
        max_attempts: 最大尝试次数
        backoff_factor: 退避因子，每次重试延迟乘以此因子
        max_delay: 最大延迟时间（秒）
        exceptions: 需要重试的异常类型元组
        jitter: 是否添加随机抖动
    
    Example:
        @async_retry(max_attempts=5, backoff_factor=2.0)
        async def fetch_data():
            # 可能失败的异步操作
            pass
    """
    
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            last_exception = None
            
            for attempt in range(max_attempts):
                try:
                    # 执行原函数
                    result = await func(*args, **kwargs)
                    
                    # 成功则返回结果
                    return result
                    
                except exceptions as e:
                    last_exception = e
                    
                    # 如果是最后一次尝试，直接抛出异常
                    if attempt == max_attempts - 1:
                        logger.error(
                            f"{func.__name__} 最终失败，已尝试 {max_attempts} 次: {e}",
                            extra={
                                "function": func.__name__,
                                "total_attempts": max_attempts,
                                "final_error": str(e),
                                "error_type": type(e).__name__
                            }
                        )
                        raise e
                    
                    # 计算延迟时间（指数退避）
                    delay = min(
                        backoff_factor ** attempt,
                        max_delay
                    )
                    
                    # 添加随机抖动（避免惊群效应）
                    if jitter:
                        jitter_factor = 0.1  # 10%的抖动
                        delay += random.uniform(0, delay * jitter_factor)
                    
                    logger.warning(
                        f"{func.__name__} 第 {attempt + 1}/{max_attempts} 次尝试失败: {e}, "
                        f"将在 {delay:.2f} 秒后重试",
                        extra={
                            "function": func.__name__,
                            "attempt": attempt + 1,
                            "max_attempts": max_attempts,
                            "delay": delay,
                            "error": str(e),
                            "error_type": type(e).__name__
                        }
                    )
                    
                    # 异步等待
                    await asyncio.sleep(delay)
                    
                except Exception as e:
                    # 不在重试范围内的异常直接抛出
                    logger.error(
                        f"{func.__name__} 遇到不可重试的异常: {type(e).__name__}: {e}",
                        extra={
                            "function": func.__name__,
                            "error_type": type(e).__name__,
                            "error": str(e),
                            "retryable": False
                        }
                    )
                    raise e
                    
            # 理论上不会到达这里，但为了完整性
            if last_exception:
                raise last_exception
                
        return wrapper
    return decorator


class RetryConfig:
    """重试配置类 - 提供预设的重试策略"""
    
    # 网络请求重试（适用于API调用）
    NETWORK_RETRY = {
        'max_attempts': 5,
        'backoff_factor': 1.5,
        'max_delay': 30.0,
        'exceptions': (
            ConnectionError,
            TimeoutError,
            OSError,  # 包含网络相关的OS错误
        ),
        'jitter': True
    }
    
    # HTTP请求重试（适用于aiohttp等）
    HTTP_RETRY = {
        'max_attempts': 4,
        'backoff_factor': 2.0,
        'max_delay': 60.0,
        'exceptions': (
            # 这里会在运行时导入aiohttp异常，避免导入问题
        ),
        'jitter': True
    }
    
    # 数据库操作重试
    DATABASE_RETRY = {
        'max_attempts': 3,
        'backoff_factor': 1.2,
        'max_delay': 10.0,
        'exceptions': (
            ConnectionError,
            TimeoutError,
        ),
        'jitter': False  # 数据库操作通常不需要抖动
    }
    
    # 快速重试（用于测试或轻量操作）
    FAST_RETRY = {
        'max_attempts': 3,
        'backoff_factor': 1.1,
        'max_delay': 2.0,
        'exceptions': (Exception,),
        'jitter': True
    }
    
    # 关键操作重试（重要的业务操作）
    CRITICAL_RETRY = {
        'max_attempts': 10,
        'backoff_factor': 1.3,
        'max_delay': 120.0,
        'exceptions': (
            ConnectionError,
            TimeoutError,
            OSError,
        ),
        'jitter': True
    }


def network_retry(func: Callable) -> Callable:
    """网络请求重试装饰器 - 预配置"""
    return async_retry(**RetryConfig.NETWORK_RETRY)(func)


def http_retry(func: Callable) -> Callable:
    """HTTP请求重试装饰器 - 预配置"""
    # 动态导入aiohttp异常，避免导入问题
    try:
        import aiohttp
        exceptions = (
            aiohttp.ClientError,
            aiohttp.ClientConnectionError,
            aiohttp.ClientResponseError,
            aiohttp.ClientTimeout,
            ConnectionError,
            TimeoutError,
        )
    except ImportError:
        exceptions = (ConnectionError, TimeoutError)
    
    config = RetryConfig.HTTP_RETRY.copy()
    config['exceptions'] = exceptions
    
    return async_retry(**config)(func)


def database_retry(func: Callable) -> Callable:
    """数据库操作重试装饰器 - 预配置"""
    return async_retry(**RetryConfig.DATABASE_RETRY)(func)


def fast_retry(func: Callable) -> Callable:
    """快速重试装饰器 - 用于测试或轻量操作"""
    return async_retry(**RetryConfig.FAST_RETRY)(func)


def critical_retry(func: Callable) -> Callable:
    """关键操作重试装饰器 - 用于重要业务操作"""
    return async_retry(**RetryConfig.CRITICAL_RETRY)(func)


class RetryStats:
    """重试统计信息收集器"""
    
    def __init__(self):
        self.stats = {
            'total_calls': 0,
            'total_retries': 0,
            'success_after_retry': 0,
            'final_failures': 0,
            'by_function': {},
            'by_exception_type': {}
        }
    
    def record_attempt(self, function_name: str, attempt: int, exception_type: str = None):
        """记录重试尝试"""
        self.stats['total_calls'] += 1
        
        if attempt > 1:
            self.stats['total_retries'] += 1
            
        # 按函数统计
        if function_name not in self.stats['by_function']:
            self.stats['by_function'][function_name] = {
                'calls': 0, 'retries': 0, 'failures': 0
            }
        self.stats['by_function'][function_name]['calls'] += 1
        
        if attempt > 1:
            self.stats['by_function'][function_name]['retries'] += 1
            
        # 按异常类型统计
        if exception_type:
            if exception_type not in self.stats['by_exception_type']:
                self.stats['by_exception_type'][exception_type] = 0
            self.stats['by_exception_type'][exception_type] += 1
    
    def record_success_after_retry(self, function_name: str):
        """记录重试后成功"""
        self.stats['success_after_retry'] += 1
    
    def record_final_failure(self, function_name: str):
        """记录最终失败"""
        self.stats['final_failures'] += 1
        if function_name in self.stats['by_function']:
            self.stats['by_function'][function_name]['failures'] += 1
    
    def get_stats(self) -> dict:
        """获取统计信息"""
        total_calls = self.stats['total_calls']
        if total_calls == 0:
            return self.stats
            
        return {
            **self.stats,
            'retry_rate': self.stats['total_retries'] / total_calls,
            'success_rate': (total_calls - self.stats['final_failures']) / total_calls,
            'recovery_rate': (
                self.stats['success_after_retry'] / max(1, self.stats['total_retries'])
            )
        }
    
    def reset(self):
        """重置统计信息"""
        self.__init__()


# 全局统计实例
retry_stats = RetryStats()


def with_retry_stats(func: Callable) -> Callable:
    """添加重试统计的装饰器"""
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        function_name = func.__name__
        attempt = 1
        
        try:
            result = await func(*args, **kwargs)
            retry_stats.record_attempt(function_name, attempt)
            return result
        except Exception as e:
            retry_stats.record_attempt(function_name, attempt, type(e).__name__)
            retry_stats.record_final_failure(function_name)
            raise
            
    return wrapper


# ===============================
# 便捷函数
# ===============================

def create_exchange_retry(exchange_name: str) -> Callable:
    """为特定交易所创建重试装饰器"""
    
    # 不同交易所的重试策略可能不同
    exchange_configs = {
        'binance': {
            'max_attempts': 5,
            'backoff_factor': 1.5,
            'max_delay': 30.0,
            'jitter': True
        },
        'okx': {
            'max_attempts': 4,
            'backoff_factor': 2.0,
            'max_delay': 45.0,
            'jitter': True
        },
        'bybit': {
            'max_attempts': 3,
            'backoff_factor': 1.8,
            'max_delay': 30.0,
            'jitter': True
        }
    }
    
    config = exchange_configs.get(exchange_name.lower(), RetryConfig.NETWORK_RETRY)
    
    # 添加HTTP异常
    try:
        import aiohttp
        config['exceptions'] = (
            aiohttp.ClientError,
            ConnectionError,
            TimeoutError,
        )
    except ImportError:
        config['exceptions'] = (ConnectionError, TimeoutError)
    
    def exchange_retry_decorator(func: Callable) -> Callable:
        @async_retry(**config)
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            return await func(*args, **kwargs)
        return wrapper
    
    return exchange_retry_decorator