"""
增强数据收集架构演示
展示如何使用新的专业数据收集架构
"""
import asyncio
from datetime import datetime, timedelta
from loguru import logger

from src.enhanced_collectors.collection_manager import EnhancedCollectionManager
from src.enhanced_collectors.binance_collector import EnhancedBinanceCollector
from src.validators.crypto_data_validator import CryptoDataValidator
from src.utils.enhanced.smart_concurrency_controller import SmartConcurrencyController
from src.utils.enhanced.config_manager import ConfigManager
from src.storage.clickhouse import ClickHouseManager


async def demo_enhanced_architecture():
    """演示增强数据收集架构"""
    logger.info("🚀 启动增强数据收集架构演示")
    
    # 1. 初始化核心组件
    logger.info("📋 初始化核心组件")
    
    # 配置管理器
    config_manager = ConfigManager("config/enhanced/collectors.yml")
    
    # 数据验证器
    validator = CryptoDataValidator(
        enable_outlier_detection=True,
        outlier_threshold=3.0,
        auto_fix=True
    )
    
    # 智能并发控制器
    concurrency_controller = SmartConcurrencyController(
        max_concurrent=5,
        target_cpu_usage=70.0,
        target_memory_usage=80.0
    )
    
    # 数据库管理器（示例中使用None）
    db_manager = None  # ClickHouseManager() 实际部署时启用
    
    # 2. 创建收集管理器
    logger.info("🎛️ 创建增强收集管理器")
    
    collection_manager = EnhancedCollectionManager(
        config_manager=config_manager,
        db_manager=db_manager,
        validator=validator,
        concurrency_controller=concurrency_controller
    )
    
    try:
        # 3. 启动管理器
        logger.info("▶️ 启动收集管理器")
        await collection_manager.start()
        
        # 4. 手动添加Binance收集器（演示动态添加）
        logger.info("➕ 添加Binance收集器")
        
        binance_config = {
            'class': 'EnhancedBinanceCollector',
            'enabled': True,
            'config': {
                'max_concurrent': 3,
                'max_retry_count': 3,
                'delay_between_requests': 0.1,
                'websocket': {
                    'enabled': True,
                    'max_reconnect_attempts': 5
                },
                'rest_api': {
                    'enabled': True,
                    'timeout': 30
                },
                'enable_data_validation': True
            }
        }
        
        success = await collection_manager.add_collector(
            'binance', 
            EnhancedBinanceCollector,
            binance_config
        )
        
        if success:
            logger.info("✅ Binance收集器添加成功")
        else:
            logger.error("❌ Binance收集器添加失败")
            return
        
        # 5. 监控系统状态
        logger.info("📊 监控系统状态")
        
        for i in range(10):  # 监控10轮
            await asyncio.sleep(5)
            
            # 获取收集器状态
            collector_status = await collection_manager.get_collector_status()
            logger.info(f"📈 收集器状态 (第{i+1}轮): {len(collector_status)} 个收集器活跃")
            
            # 获取系统指标
            system_metrics = await collection_manager.get_system_metrics()
            logger.info(
                f"💻 系统指标: CPU {system_metrics['current']['cpu_usage_percent']:.1f}%, "
                f"内存 {system_metrics['current']['memory_usage_mb']:.1f}MB, "
                f"数据点/分钟 {system_metrics['current']['data_points_per_minute']:.1f}"
            )
            
            # 获取并发控制器统计
            if concurrency_controller:
                cc_stats = concurrency_controller.get_stats()
                logger.info(
                    f"🔄 并发控制: 当前 {cc_stats['current_concurrent']}/{cc_stats['max_concurrent']}, "
                    f"队列 {cc_stats['current_queue_size']}, "
                    f"成功率 {cc_stats.get('success_rate', 0):.2%}"
                )
        
        # 6. 演示动态配置更新
        logger.info("🔄 演示配置热更新")
        
        # 重启收集器（演示）
        await collection_manager.restart_collector('binance')
        logger.info("♻️ Binance收集器重启完成")
        
        # 7. 数据验证演示
        logger.info("🔍 数据验证演示")
        
        import pandas as pd
        import numpy as np
        
        # 创建测试数据（包含问题）
        test_data = pd.DataFrame({
            'timestamp': [datetime.now() - timedelta(minutes=i) for i in range(5)],
            'open': [50000.0, np.nan, 50020.0, -50030.0, 50040.0],  # 包含NaN和负数
            'high': [50100.0, 50110.0, 49900.0, 50130.0, 50140.0],  # high < low (第3行)
            'low': [49900.0, 49910.0, 50100.0, 49930.0, 49940.0],   # low > high (第3行)
            'close': [50050.0, 50060.0, 50070.0, 50080.0, 50090.0],
            'volume': [100.0, -101.0, 102.0, 103.0, 104.0],          # 负数成交量
            'symbol': ['BTC/USDT'] * 5,
            'exchange': ['binance'] * 5
        })
        
        logger.info(f"📊 原始数据: {len(test_data)} 行")
        
        # 验证数据
        validation_result = validator.validate_kline_data(test_data, fix_issues=True)
        
        logger.info(
            f"✅ 验证结果: {'通过' if validation_result.is_valid else '失败'}, "
            f"错误 {validation_result.error_count} 个, "
            f"警告 {validation_result.warning_count} 个"
        )
        
        if validation_result.fixed_data is not None:
            logger.info(f"🔧 修复后数据: {len(validation_result.fixed_data)} 行")
        
        # 8. 性能统计
        logger.info("📈 性能统计总结")
        
        # 收集管理器统计
        manager_summary = collection_manager.get_summary()
        logger.info(f"🎛️ 管理器: 运行时间 {manager_summary['uptime_seconds']:.1f}秒")
        
        # 并发控制器统计
        final_cc_stats = concurrency_controller.get_stats()
        logger.info(
            f"🔄 并发控制: 总任务 {final_cc_stats['total_tasks_submitted']}, "
            f"成功 {final_cc_stats['total_tasks_completed']}, "
            f"失败 {final_cc_stats['total_tasks_failed']}"
        )
        
        logger.info("🎉 增强数据收集架构演示完成")
        
    except Exception as e:
        logger.error(f"❌ 演示过程中发生错误: {e}")
        import traceback
        traceback.print_exc()
        
    finally:
        # 9. 清理资源
        logger.info("🧹 清理资源")
        await collection_manager.stop()
        logger.info("✨ 资源清理完成")


async def demo_individual_components():
    """演示各个组件的独立使用"""
    logger.info("🔧 演示各个组件的独立使用")
    
    # 1. 数据验证器独立使用
    logger.info("🔍 数据验证器独立演示")
    
    validator = CryptoDataValidator(enable_outlier_detection=True)
    
    # 创建包含异常的测试数据
    test_data = pd.DataFrame({
        'timestamp': [datetime.now()],
        'open': [50000.0],
        'high': [60000.0],  # 异常高价格（20%涨幅）
        'low': [49000.0],
        'close': [50500.0],
        'volume': [100.0],
        'symbol': ['BTC/USDT'],
        'exchange': ['test']
    })
    
    result = validator.validate_kline_data(test_data, detect_outliers=True, outlier_threshold=2.0)
    logger.info(f"🔍 验证结果: {'✅ 通过' if result.is_valid else '❌ 失败'} ({len(result.issues)} 个问题)")
    
    # 2. 并发控制器独立使用
    logger.info("🔄 并发控制器独立演示")
    
    controller = SmartConcurrencyController(max_concurrent=3)
    await controller.start()
    
    try:
        # 提交几个测试任务
        async def test_task(task_id: int):
            await asyncio.sleep(0.5)
            return f"任务 {task_id} 完成"
        
        futures = []
        for i in range(5):
            future = await controller.submit(test_task, i, priority=i)
            futures.append(future)
        
        # 等待所有任务完成
        results = await asyncio.gather(*futures)
        
        stats = controller.get_stats()
        logger.info(
            f"🔄 并发控制结果: {len(results)} 个任务完成, "
            f"平均耗时 {stats.get('average_task_duration', 0):.3f}秒"
        )
        
    finally:
        await controller.stop()
    
    # 3. 重试装饰器独立使用
    logger.info("🔄 重试装饰器独立演示")
    
    from src.utils.enhanced.retry_decorator import async_retry
    
    attempt_count = 0
    
    @async_retry(max_attempts=3, backoff_factor=0.1)
    async def flaky_function():
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count < 3:
            raise Exception(f"模拟失败 (尝试 {attempt_count})")
        return "最终成功!"
    
    try:
        result = await flaky_function()
        logger.info(f"🔄 重试结果: {result} (总尝试 {attempt_count} 次)")
    except Exception as e:
        logger.error(f"🔄 重试最终失败: {e}")
    
    logger.info("✨ 组件独立演示完成")


async def main():
    """主演示函数"""
    logger.info("🎬 开始增强数据收集架构全面演示")
    
    try:
        # 演示新架构
        await demo_enhanced_architecture()
        
        logger.info("\n" + "="*50)
        
        # 演示各组件独立使用
        await demo_individual_components()
        
    except Exception as e:
        logger.error(f"❌ 演示失败: {e}")
        import traceback
        traceback.print_exc()
    
    logger.info("🏁 增强数据收集架构演示结束")


if __name__ == "__main__":
    # 运行演示
    asyncio.run(main())