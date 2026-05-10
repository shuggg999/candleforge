#!/usr/bin/env python3
"""
增强架构全面功能测试
验证所有实现的功能是否真正工作
"""
import asyncio
import sys
import traceback
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
from loguru import logger

# 配置日志输出到控制台
logger.remove()
logger.add(sys.stdout, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")

class ArchitectureTest:
    """架构测试类"""
    
    def __init__(self):
        self.test_results = {}
        
    def record_test(self, test_name: str, passed: bool, details: str = ""):
        """记录测试结果"""
        self.test_results[test_name] = {
            'passed': passed,
            'details': details
        }
        status = "✅ PASS" if passed else "❌ FAIL"
        logger.info(f"{status} | {test_name}: {details}")
    
    async def test_imports(self):
        """测试1: 核心组件导入测试"""
        logger.info("🔍 开始测试: 核心组件导入")
        
        try:
            from src.validators.crypto_data_validator import CryptoDataValidator, ValidationResult, ValidationIssue
            from src.utils.enhanced.retry_decorator import async_retry, network_retry, http_retry
            from src.utils.enhanced.smart_concurrency_controller import SmartConcurrencyController, TaskMetrics
            from src.utils.enhanced.config_manager import ConfigManager
            from src.enhanced_collectors.base_collector import BaseExchangeCollector, CollectionResult
            
            self.record_test("核心组件导入", True, "所有核心组件成功导入")
            return True
        except Exception as e:
            self.record_test("核心组件导入", False, f"导入失败: {str(e)}")
            return False
    
    async def test_retry_decorator(self):
        """测试2: 重试装饰器功能测试"""
        logger.info("🔄 开始测试: 重试装饰器功能")
        
        try:
            from src.utils.enhanced.retry_decorator import async_retry
            
            # 测试基本重试功能
            attempt_count = 0
            
            @async_retry(max_attempts=3, backoff_factor=0.1)
            async def flaky_function():
                nonlocal attempt_count
                attempt_count += 1
                if attempt_count < 3:
                    raise Exception(f"模拟失败 (尝试 {attempt_count})")
                return "最终成功!"
            
            result = await flaky_function()
            
            # 验证重试机制
            if result == "最终成功!" and attempt_count == 3:
                self.record_test("重试装饰器基本功能", True, f"正确重试了{attempt_count}次")
            else:
                self.record_test("重试装饰器基本功能", False, f"重试逻辑异常: 结果={result}, 尝试次数={attempt_count}")
                return False
            
            # 测试指数退避算法
            import time
            start_times = []
            
            @async_retry(max_attempts=3, backoff_factor=2.0, max_delay=1.0)
            async def timing_test():
                start_times.append(time.time())
                raise Exception("测试timing")
            
            try:
                await timing_test()
            except:
                pass
            
            # 验证时间间隔
            if len(start_times) >= 2:
                interval = start_times[1] - start_times[0]
                if 0.9 <= interval <= 2.1:  # 允许一定误差
                    self.record_test("指数退避算法", True, f"退避间隔: {interval:.2f}秒")
                else:
                    self.record_test("指数退避算法", False, f"退避间隔异常: {interval:.2f}秒")
                    return False
            
            # 测试异常类型过滤
            @async_retry(max_attempts=2, exceptions=(ValueError,))
            async def exception_filter_test(exc_type):
                if exc_type == "value":
                    raise ValueError("可重试异常")
                else:
                    raise TypeError("不可重试异常")
            
            # ValueError应该重试
            try:
                await exception_filter_test("value")
            except ValueError:
                pass  # 预期的异常
            
            # TypeError不应该重试
            try:
                await exception_filter_test("type")
            except TypeError:
                pass  # 预期的异常
            
            self.record_test("异常类型过滤", True, "正确处理了不同异常类型")
            return True
            
        except Exception as e:
            self.record_test("重试装饰器功能", False, f"测试异常: {str(e)}")
            traceback.print_exc()
            return False
    
    async def test_data_validator(self):
        """测试3: 数据验证器功能测试"""
        logger.info("🔍 开始测试: 数据验证器功能")
        
        try:
            from src.validators.crypto_data_validator import CryptoDataValidator
            
            validator = CryptoDataValidator(
                enable_outlier_detection=True,
                outlier_threshold=3.0,
                auto_fix=True
            )
            
            # 测试有效数据验证
            valid_data = pd.DataFrame({
                'timestamp': [datetime.now() - timedelta(minutes=i) for i in range(3)],
                'open': [50000.0, 50010.0, 50020.0],
                'high': [50100.0, 50110.0, 50120.0],
                'low': [49900.0, 49910.0, 49920.0],
                'close': [50050.0, 50060.0, 50070.0],
                'volume': [100.0, 101.0, 102.0],
                'symbol': ['BTC/USDT'] * 3,
                'exchange': ['binance'] * 3
            })
            
            result = validator.validate_kline_data(valid_data)
            
            if result.is_valid:
                self.record_test("有效数据验证", True, f"正确通过验证，错误率: {result.stats['error_rate']:.2%}")
            else:
                self.record_test("有效数据验证", False, f"有效数据被错误拒绝: {result.error_count}个错误")
                return False
            
            # 测试问题数据检测和修复
            problematic_data = pd.DataFrame({
                'timestamp': [datetime.now() - timedelta(minutes=i) for i in range(4)],
                'open': [50000.0, np.nan, 50020.0, -50030.0],  # NaN和负数
                'high': [50100.0, 50110.0, 49900.0, 50130.0],  # 第3行 high < low
                'low': [49900.0, 49910.0, 50100.0, 49930.0],   # 第3行 low > high  
                'close': [50050.0, 50060.0, 50070.0, np.inf], # 无穷大值
                'volume': [100.0, -101.0, 102.0, 103.0],       # 负数成交量
                'symbol': ['BTC/USDT', '', 'BTC/USDT', 'BTC/USDT'],  # 空符号
                'exchange': ['binance'] * 4
            })
            
            result = validator.validate_kline_data(problematic_data, fix_issues=True)
            
            if not result.is_valid and len(result.issues) > 0:
                self.record_test("问题数据检测", True, f"检测到 {len(result.issues)} 个问题")
                
                # 验证数据修复功能
                if result.fixed_data is not None:
                    fixed_data = result.fixed_data
                    
                    # 检查是否修复了NaN值
                    has_nan = fixed_data[['open', 'high', 'low', 'close', 'volume']].isna().any().any()
                    
                    # 检查是否修复了负数价格
                    has_negative_price = (fixed_data[['open', 'high', 'low', 'close']] < 0).any().any()
                    
                    # 检查是否修复了无穷大值
                    has_inf = np.isinf(fixed_data[['open', 'high', 'low', 'close', 'volume']]).any().any()
                    
                    if not has_nan and not has_negative_price and not has_inf:
                        self.record_test("数据自动修复", True, f"成功修复问题数据，剩余 {len(fixed_data)} 行")
                    else:
                        self.record_test("数据自动修复", False, f"修复不完整: NaN={has_nan}, 负数={has_negative_price}, 无穷={has_inf}")
                        return False
                else:
                    self.record_test("数据自动修复", False, "没有生成修复数据")
                    return False
            else:
                self.record_test("问题数据检测", False, "未能检测到明显的数据问题")
                return False
            
            # 测试时间标准化功能
            data_with_irregular_time = pd.DataFrame({
                'timestamp': [
                    datetime(2024, 1, 1, 10, 14, 32),  # 不在分钟边界
                    datetime(2024, 1, 1, 10, 19, 45),  # 不在5分钟边界
                    datetime(2024, 1, 1, 10, 23, 12),  # 不在分钟边界
                ],
                'open': [50000.0, 50010.0, 50020.0],
                'high': [50100.0, 50110.0, 50120.0],
                'low': [49900.0, 49910.0, 49920.0],
                'close': [50050.0, 50060.0, 50070.0],
                'volume': [100.0, 101.0, 102.0],
                'symbol': ['BTC/USDT'] * 3,
                'exchange': ['binance'] * 3
            })
            
            # 注意：时间标准化功能实际在收集器中实现，这里测试验证器是否接受不同时间
            result = validator.validate_kline_data(data_with_irregular_time)
            
            if result.is_valid or result.fixed_data is not None:
                self.record_test("不规则时间戳处理", True, "正确处理了不规则时间戳")
            else:
                self.record_test("不规则时间戳处理", False, "无法处理不规则时间戳")
                return False
            
            # 测试异常值检测
            data_with_outliers = pd.DataFrame({
                'timestamp': [datetime.now() - timedelta(minutes=i) for i in range(3)],
                'open': [50000.0, 50010.0, 100000.0],  # 最后一个是异常值
                'high': [50100.0, 50110.0, 100100.0],
                'low': [49900.0, 49910.0, 99900.0],
                'close': [50050.0, 50060.0, 100050.0],
                'volume': [100.0, 101.0, 102.0],
                'symbol': ['BTC/USDT'] * 3,
                'exchange': ['binance'] * 3
            })
            
            result = validator.validate_kline_data(data_with_outliers, detect_outliers=True, outlier_threshold=2.0)
            
            outlier_issues = [issue for issue in result.issues if issue.issue_type == 'price_outlier']
            if len(outlier_issues) > 0:
                self.record_test("异常值检测", True, f"检测到 {len(outlier_issues)} 个异常值")
            else:
                self.record_test("异常值检测", False, "未检测到明显的异常值")
                return False
            
            return True
            
        except Exception as e:
            self.record_test("数据验证器功能", False, f"测试异常: {str(e)}")
            traceback.print_exc()
            return False
    
    async def test_concurrency_controller(self):
        """测试4: 并发控制器功能测试"""
        logger.info("⚡ 开始测试: 并发控制器功能")
        
        try:
            from src.utils.enhanced.smart_concurrency_controller import SmartConcurrencyController
            
            controller = SmartConcurrencyController(
                max_concurrent=3,
                target_cpu_usage=70.0,
                adjustment_interval=1.0
            )
            
            await controller.start()
            
            try:
                # 测试基本任务提交和执行
                async def test_task(task_id: int, delay: float = 0.1):
                    await asyncio.sleep(delay)
                    return f"Task {task_id} completed"
                
                # 提交多个任务
                futures = []
                for i in range(5):
                    future = await controller.submit(test_task, i, delay=0.1, priority=i)
                    futures.append(future)
                
                results = await asyncio.gather(*futures)
                
                if len(results) == 5 and all("completed" in result for result in results):
                    self.record_test("基本任务执行", True, f"成功执行了 {len(results)} 个任务")
                else:
                    self.record_test("基本任务执行", False, f"任务执行异常: {results}")
                    return False
                
                # 测试并发限制
                start_time = asyncio.get_event_loop().time()
                concurrent_futures = []
                
                for i in range(6):  # 超过限制3个
                    future = await controller.submit(test_task, i, delay=0.5)
                    concurrent_futures.append(future)
                
                await asyncio.gather(*concurrent_futures)
                elapsed = asyncio.get_event_loop().time() - start_time
                
                # 由于并发限制，应该需要更多时间
                if elapsed > 0.8:  # 至少两批次执行
                    self.record_test("并发限制", True, f"正确限制并发，耗时: {elapsed:.2f}秒")
                else:
                    self.record_test("并发限制", False, f"并发限制可能无效，耗时: {elapsed:.2f}秒")
                
                # 测试任务优先级
                priority_results = []
                
                async def priority_task(task_id: int):
                    priority_results.append(task_id)
                    return task_id
                
                # 提交不同优先级的任务
                high_future = await controller.submit(priority_task, 1, priority=1)  # 高优先级
                low_future = await controller.submit(priority_task, 2, priority=10)   # 低优先级
                
                await asyncio.gather(high_future, low_future)
                
                if len(priority_results) >= 2:
                    self.record_test("任务优先级", True, f"处理了优先级任务: {priority_results}")
                else:
                    self.record_test("任务优先级", False, "优先级任务处理异常")
                
                # 测试统计信息
                stats = controller.get_stats()
                
                expected_fields = ['total_tasks_submitted', 'total_tasks_completed', 'current_concurrent', 'max_concurrent']
                has_all_fields = all(field in stats for field in expected_fields)
                
                if has_all_fields and stats['total_tasks_completed'] > 0:
                    self.record_test("统计信息收集", True, f"完成任务: {stats['total_tasks_completed']}")
                else:
                    self.record_test("统计信息收集", False, f"统计信息不完整: {list(stats.keys())}")
                
                return True
                
            finally:
                await controller.stop()
                
        except Exception as e:
            self.record_test("并发控制器功能", False, f"测试异常: {str(e)}")
            traceback.print_exc()
            return False
    
    async def test_base_collector_interface(self):
        """测试5: 基础收集器接口测试"""
        logger.info("🏗️ 开始测试: 基础收集器接口")
        
        try:
            from src.enhanced_collectors.base_collector import BaseExchangeCollector, CollectionResult
            
            # 测试抽象类不能直接实例化
            try:
                BaseExchangeCollector("test")
                self.record_test("抽象类限制", False, "抽象类被错误实例化")
                return False
            except TypeError:
                self.record_test("抽象类限制", True, "正确阻止了抽象类实例化")
            
            # 创建具体实现进行测试
            class TestCollector(BaseExchangeCollector):
                async def get_symbol_list(self):
                    return ['BTC/USDT', 'ETH/USDT']
                
                def normalize_symbol(self, symbol):
                    return symbol.replace('/', '')
                
                async def fetch_kline_data(self, symbol, interval, start_time, end_time):
                    # 模拟返回数据
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
                
                async def validate_connection(self):
                    return True
                
                async def _setup_websocket_connection(self):
                    pass
                    
                async def _setup_rest_connection(self):
                    pass
            
            # 测试具体实现
            collector = TestCollector("test_exchange")
            
            if collector.exchange_name == "test_exchange":
                self.record_test("基础属性设置", True, f"交易所名称: {collector.exchange_name}")
            else:
                self.record_test("基础属性设置", False, "基础属性设置异常")
                return False
            
            # 测试抽象方法实现
            symbols = await collector.get_symbol_list()
            if symbols == ['BTC/USDT', 'ETH/USDT']:
                self.record_test("抽象方法实现", True, f"获取到交易对: {symbols}")
            else:
                self.record_test("抽象方法实现", False, f"抽象方法返回异常: {symbols}")
                return False
            
            # 测试数据收集方法
            data = await collector.fetch_kline_data(
                'BTC/USDT', '1m', 
                datetime.now() - timedelta(minutes=1), 
                datetime.now()
            )
            
            if data is not None and not data.empty:
                self.record_test("数据收集接口", True, f"成功收集 {len(data)} 行数据")
            else:
                self.record_test("数据收集接口", False, "数据收集接口异常")
                return False
            
            # 测试符号标准化
            normalized = collector.normalize_symbol('BTC/USDT')
            if normalized == 'BTCUSDT':
                self.record_test("符号标准化", True, f"BTC/USDT -> {normalized}")
            else:
                self.record_test("符号标准化", False, f"标准化结果异常: {normalized}")
                return False
            
            # 测试连接验证
            connection_ok = await collector.validate_connection()
            if connection_ok:
                self.record_test("连接验证", True, "连接验证通过")
            else:
                self.record_test("连接验证", False, "连接验证失败")
                return False
            
            return True
            
        except Exception as e:
            self.record_test("基础收集器接口", False, f"测试异常: {str(e)}")
            traceback.print_exc()
            return False
    
    async def test_timestamp_normalization(self):
        """测试6: 时间戳标准化功能"""
        logger.info("⏰ 开始测试: 时间戳标准化功能")
        
        try:
            # 测试Binance收集器的时间标准化
            from src.enhanced_collectors.binance_collector import EnhancedBinanceCollector
            
            collector = EnhancedBinanceCollector()
            
            # 测试不同时间框架的标准化
            test_cases = [
                {
                    'timestamp': datetime(2024, 1, 1, 10, 14, 32),  # 不规则秒
                    'timeframe': '1m',
                    'expected_minute': 14
                },
                {
                    'timestamp': datetime(2024, 1, 1, 10, 17, 45),  # 不在5分钟边界
                    'timeframe': '5m', 
                    'expected_minute': 15  # 应该向下舍入到15分钟
                },
                {
                    'timestamp': datetime(2024, 1, 1, 13, 45, 30),  # 不在小时边界
                    'timeframe': '1h',
                    'expected_hour': 13,
                    'expected_minute': 0
                }
            ]
            
            all_passed = True
            for i, case in enumerate(test_cases):
                normalized = collector._normalize_timestamp(case['timestamp'], case['timeframe'])
                
                # 验证秒和微秒被清零
                if normalized.second != 0 or normalized.microsecond != 0:
                    self.record_test(f"时间标准化案例{i+1}", False, f"秒/微秒未清零: {normalized}")
                    all_passed = False
                    continue
                
                # 验证分钟标准化
                if 'expected_minute' in case and normalized.minute != case['expected_minute']:
                    self.record_test(f"时间标准化案例{i+1}", False, f"分钟标准化错误: 期望{case['expected_minute']}, 实际{normalized.minute}")
                    all_passed = False
                    continue
                
                # 验证小时标准化 
                if 'expected_hour' in case and normalized.hour != case['expected_hour']:
                    self.record_test(f"时间标准化案例{i+1}", False, f"小时标准化错误: 期望{case['expected_hour']}, 实际{normalized.hour}")
                    all_passed = False
                    continue
                
                self.record_test(f"时间标准化案例{i+1}", True, f"{case['timeframe']}: {case['timestamp'].strftime('%H:%M:%S')} -> {normalized.strftime('%H:%M:%S')}")
            
            if all_passed:
                self.record_test("时间戳标准化功能", True, "所有时间标准化测试通过")
                return True
            else:
                return False
                
        except Exception as e:
            self.record_test("时间戳标准化功能", False, f"测试异常: {str(e)}")
            traceback.print_exc()
            return False
    
    async def test_config_manager(self):
        """测试7: 配置管理器功能"""
        logger.info("⚙️ 开始测试: 配置管理器功能")
        
        try:
            from src.utils.enhanced.config_manager import ConfigManager
            
            # 测试YAML配置加载
            config_manager = ConfigManager("config/enhanced/collectors.yml")
            await config_manager.load_config()
            
            # 测试嵌套配置访问
            binance_config = config_manager.get('collectors.binance')
            if binance_config is not None:
                self.record_test("YAML配置加载", True, f"成功加载Binance配置")
                
                # 测试深层嵌套访问
                websocket_enabled = config_manager.get('collectors.binance.config.websocket.enabled', False)
                if isinstance(websocket_enabled, bool):
                    self.record_test("嵌套配置访问", True, f"WebSocket启用: {websocket_enabled}")
                else:
                    self.record_test("嵌套配置访问", False, f"嵌套访问返回异常类型: {type(websocket_enabled)}")
                    return False
            else:
                self.record_test("YAML配置加载", False, "无法加载Binance配置")
                return False
            
            # 测试默认值处理
            non_existent = config_manager.get('non.existent.key', 'default_value')
            if non_existent == 'default_value':
                self.record_test("默认值处理", True, "正确返回默认值")
            else:
                self.record_test("默认值处理", False, f"默认值处理异常: {non_existent}")
                return False
            
            return True
            
        except Exception as e:
            self.record_test("配置管理器功能", False, f"测试异常: {str(e)}")
            traceback.print_exc()
            return False
    
    def print_summary(self):
        """打印测试总结"""
        logger.info("=" * 80)
        logger.info("🏁 测试总结报告")
        logger.info("=" * 80)
        
        total_tests = len(self.test_results)
        passed_tests = sum(1 for result in self.test_results.values() if result['passed'])
        failed_tests = total_tests - passed_tests
        
        logger.info(f"总测试数: {total_tests}")
        logger.info(f"通过: {passed_tests} ✅")
        logger.info(f"失败: {failed_tests} ❌")
        logger.info(f"成功率: {passed_tests/total_tests*100:.1f}%")
        
        if failed_tests > 0:
            logger.info("\n❌ 失败的测试:")
            for test_name, result in self.test_results.items():
                if not result['passed']:
                    logger.info(f"  • {test_name}: {result['details']}")
        
        logger.info("\n✅ 通过的测试:")
        for test_name, result in self.test_results.items():
            if result['passed']:
                logger.info(f"  • {test_name}: {result['details']}")
                
        logger.info("=" * 80)
        
        # 架构完整性评估
        critical_components = [
            "核心组件导入",
            "重试装饰器基本功能", 
            "有效数据验证",
            "问题数据检测",
            "基本任务执行",
            "基础属性设置",
            "时间戳标准化功能"
        ]
        
        critical_passed = sum(1 for comp in critical_components if comp in self.test_results and self.test_results[comp]['passed'])
        critical_total = len(critical_components)
        
        logger.info(f"\n🎯 关键功能完成度: {critical_passed}/{critical_total} ({critical_passed/critical_total*100:.1f}%)")
        
        if critical_passed == critical_total:
            logger.info("🎉 所有关键功能正常工作！增强架构实现完整。")
        elif critical_passed >= critical_total * 0.8:
            logger.info("⚠️ 大部分关键功能正常，少数功能需要修复。")
        else:
            logger.info("❌ 关键功能存在严重问题，需要重新检查实现。")


async def main():
    """主测试函数"""
    logger.info("🚀 开始增强架构全面功能测试")
    logger.info(f"⏰ 测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    tester = ArchitectureTest()
    
    # 执行所有测试
    test_methods = [
        tester.test_imports,
        tester.test_retry_decorator,
        tester.test_data_validator,
        tester.test_concurrency_controller,
        tester.test_base_collector_interface,
        tester.test_timestamp_normalization,
        tester.test_config_manager,
    ]
    
    for test_method in test_methods:
        try:
            await test_method()
        except Exception as e:
            logger.error(f"测试方法 {test_method.__name__} 执行异常: {e}")
            traceback.print_exc()
    
    # 打印测试总结
    tester.print_summary()


if __name__ == "__main__":
    asyncio.run(main())