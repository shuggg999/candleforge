"""
增强数据收集管理器
统一管理多个交易所的数据收集器，提供协调和监控功能
"""
import asyncio
from typing import Dict, List, Optional, Any, Type
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from loguru import logger
import json
from collections import defaultdict

from src.enhanced_collectors.base_collector import BaseExchangeCollector, CollectionResult
from src.enhanced_collectors.binance_collector import EnhancedBinanceCollector
from src.validators.crypto_data_validator import CryptoDataValidator
from src.utils.enhanced.smart_concurrency_controller import SmartConcurrencyController
from src.utils.enhanced.config_manager import ConfigManager
from src.storage.clickhouse import ClickHouseManager


@dataclass
class CollectorStatus:
    """收集器状态"""
    name: str
    is_active: bool
    last_heartbeat: datetime
    connection_status: Dict[str, bool]  # websocket, rest_api
    collection_stats: Dict[str, Any]
    error_count: int = 0
    last_error: Optional[str] = None
    uptime: timedelta = field(default_factory=lambda: timedelta())


@dataclass
class SystemMetrics:
    """系统指标"""
    timestamp: datetime
    active_collectors: int
    total_symbols: int
    data_points_per_minute: float
    memory_usage_mb: float
    cpu_usage_percent: float
    websocket_connections: int
    rest_api_calls_per_minute: float
    error_rate: float


class EnhancedCollectionManager:
    """
    增强数据收集管理器
    
    功能：
    1. 统一管理多个交易所收集器
    2. 智能负载均衡和故障恢复
    3. 全局配置管理和热重载
    4. 系统监控和性能优化
    5. 数据质量监控
    6. 自动化运维
    """
    
    def __init__(self,
                 config_manager: ConfigManager,
                 db_manager: ClickHouseManager,
                 validator: Optional[CryptoDataValidator] = None,
                 concurrency_controller: Optional[SmartConcurrencyController] = None):
        """
        初始化收集管理器
        
        Args:
            config_manager: 配置管理器
            db_manager: 数据库管理器
            validator: 数据验证器
            concurrency_controller: 并发控制器
        """
        self.config_manager = config_manager
        self.db_manager = db_manager
        self.validator = validator or CryptoDataValidator()
        self.concurrency_controller = concurrency_controller or SmartConcurrencyController()
        
        # 收集器管理
        self.collectors: Dict[str, BaseExchangeCollector] = {}
        self.collector_tasks: Dict[str, asyncio.Task] = {}
        self.collector_status: Dict[str, CollectorStatus] = {}
        
        # 系统状态
        self.is_running = False
        self.start_time: Optional[datetime] = None
        
        # 监控和统计
        self.system_metrics: List[SystemMetrics] = []
        self.max_metrics_history = 1000
        
        # 管理任务
        self.monitor_task: Optional[asyncio.Task] = None
        self.health_check_task: Optional[asyncio.Task] = None
        self.config_watcher_task: Optional[asyncio.Task] = None
        
        # 注册可用的收集器类型
        self.collector_classes = {
            'EnhancedBinanceCollector': EnhancedBinanceCollector,
            # 未来可以添加更多交易所
        }
        
        logger.info(
            "EnhancedCollectionManager 初始化完成",
            extra={
                "available_collectors": list(self.collector_classes.keys()),
                "validator_enabled": self.validator is not None,
                "concurrency_controller": self.concurrency_controller is not None
            }
        )
    
    async def start(self):
        """启动收集管理器"""
        if self.is_running:
            logger.warning("收集管理器已经在运行")
            return
        
        logger.info("启动增强数据收集管理器")
        
        try:
            # 启动并发控制器
            if self.concurrency_controller:
                await self.concurrency_controller.start()
            
            # 初始化收集器
            await self._initialize_collectors()
            
            # 启动管理任务
            self.monitor_task = asyncio.create_task(self._monitor_loop())
            self.health_check_task = asyncio.create_task(self._health_check_loop())
            self.config_watcher_task = asyncio.create_task(self._config_watcher_loop())
            
            # 启动收集器
            await self._start_collectors()
            
            self.is_running = True
            self.start_time = datetime.now()
            
            logger.info(
                f"收集管理器启动完成，管理 {len(self.collectors)} 个收集器",
                extra={
                    "collectors": list(self.collectors.keys()),
                    "start_time": self.start_time.isoformat()
                }
            )
            
        except Exception as e:
            logger.error(f"收集管理器启动失败: {e}")
            await self.stop()
            raise
    
    async def stop(self):
        """停止收集管理器"""
        if not self.is_running:
            return
        
        logger.info("停止增强数据收集管理器")
        
        self.is_running = False
        
        # 停止收集器
        await self._stop_collectors()
        
        # 停止管理任务
        for task in [self.monitor_task, self.health_check_task, self.config_watcher_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        
        # 停止并发控制器
        if self.concurrency_controller:
            await self.concurrency_controller.stop()
        
        # 清理资源
        for collector in self.collectors.values():
            try:
                await collector.cleanup()
            except Exception as e:
                logger.warning(f"收集器清理失败: {e}")
        
        self.collectors.clear()
        self.collector_tasks.clear()
        self.collector_status.clear()
        
        logger.info("收集管理器已停止")
    
    async def add_collector(self, 
                           exchange_name: str, 
                           collector_class: Optional[Type[BaseExchangeCollector]] = None,
                           config: Optional[Dict] = None) -> bool:
        """
        添加收集器
        
        Args:
            exchange_name: 交易所名称
            collector_class: 收集器类（可选）
            config: 收集器配置（可选）
        """
        if exchange_name in self.collectors:
            logger.warning(f"收集器 {exchange_name} 已存在")
            return False
        
        try:
            # 获取配置
            if config is None:
                config = self.config_manager.get(f'collectors.{exchange_name}', {})
            
            # 确定收集器类
            if collector_class is None:
                class_name = config.get('class', f'Enhanced{exchange_name.title()}Collector')
                collector_class = self.collector_classes.get(class_name)
                
                if collector_class is None:
                    raise ValueError(f"未找到收集器类: {class_name}")
            
            # 创建收集器
            collector = collector_class(
                exchange_name=exchange_name,
                db_manager=self.db_manager,
                validator=self.validator,
                concurrency_controller=self.concurrency_controller,
                **config.get('config', {})
            )
            
            # 初始化收集器
            await collector.initialize()
            
            # 添加到管理器
            self.collectors[exchange_name] = collector
            self.collector_status[exchange_name] = CollectorStatus(
                name=exchange_name,
                is_active=False,
                last_heartbeat=datetime.now(),
                connection_status={'websocket': False, 'rest_api': False},
                collection_stats={}
            )
            
            # 如果管理器正在运行，立即启动收集器
            if self.is_running:
                await self._start_collector(exchange_name)
            
            logger.info(f"成功添加收集器: {exchange_name}")
            return True
            
        except Exception as e:
            logger.error(f"添加收集器 {exchange_name} 失败: {e}")
            return False
    
    async def remove_collector(self, exchange_name: str) -> bool:
        """移除收集器"""
        if exchange_name not in self.collectors:
            logger.warning(f"收集器 {exchange_name} 不存在")
            return False
        
        try:
            # 停止收集器任务
            if exchange_name in self.collector_tasks:
                task = self.collector_tasks.pop(exchange_name)
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            
            # 清理收集器
            collector = self.collectors.pop(exchange_name)
            await collector.cleanup()
            
            # 清理状态
            self.collector_status.pop(exchange_name, None)
            
            logger.info(f"成功移除收集器: {exchange_name}")
            return True
            
        except Exception as e:
            logger.error(f"移除收集器 {exchange_name} 失败: {e}")
            return False
    
    async def restart_collector(self, exchange_name: str) -> bool:
        """重启收集器"""
        if exchange_name not in self.collectors:
            logger.warning(f"收集器 {exchange_name} 不存在")
            return False
        
        try:
            logger.info(f"重启收集器: {exchange_name}")
            
            # 停止收集器
            await self._stop_collector(exchange_name)
            
            # 等待一段时间
            await asyncio.sleep(2.0)
            
            # 重新初始化
            await self.collectors[exchange_name].initialize()
            
            # 启动收集器
            await self._start_collector(exchange_name)
            
            logger.info(f"收集器 {exchange_name} 重启完成")
            return True
            
        except Exception as e:
            logger.error(f"重启收集器 {exchange_name} 失败: {e}")
            return False
    
    async def get_collector_status(self, exchange_name: Optional[str] = None) -> Dict[str, Any]:
        """获取收集器状态"""
        if exchange_name:
            if exchange_name in self.collector_status:
                status = self.collector_status[exchange_name]
                collector = self.collectors[exchange_name]
                
                return {
                    'name': status.name,
                    'is_active': status.is_active,
                    'last_heartbeat': status.last_heartbeat.isoformat(),
                    'connection_status': status.connection_status,
                    'collection_stats': collector.get_collection_stats() if hasattr(collector, 'get_collection_stats') else {},
                    'error_count': status.error_count,
                    'last_error': status.last_error,
                    'uptime': str(status.uptime)
                }
            else:
                return {'error': f'Collector {exchange_name} not found'}
        else:
            # 返回所有收集器状态
            all_status = {}
            for name in self.collectors:
                all_status[name] = await self.get_collector_status(name)
            return all_status
    
    async def get_system_metrics(self) -> Dict[str, Any]:
        """获取系统指标"""
        current_metrics = await self._collect_system_metrics()
        
        # 计算趋势数据
        recent_metrics = self.system_metrics[-10:] if len(self.system_metrics) >= 10 else self.system_metrics
        
        trend_data = {}
        if recent_metrics:
            trend_data = {
                'data_points_trend': [m.data_points_per_minute for m in recent_metrics],
                'memory_trend': [m.memory_usage_mb for m in recent_metrics],
                'cpu_trend': [m.cpu_usage_percent for m in recent_metrics],
                'error_rate_trend': [m.error_rate for m in recent_metrics]
            }
        
        return {
            'current': {
                'timestamp': current_metrics.timestamp.isoformat(),
                'active_collectors': current_metrics.active_collectors,
                'total_symbols': current_metrics.total_symbols,
                'data_points_per_minute': current_metrics.data_points_per_minute,
                'memory_usage_mb': current_metrics.memory_usage_mb,
                'cpu_usage_percent': current_metrics.cpu_usage_percent,
                'websocket_connections': current_metrics.websocket_connections,
                'rest_api_calls_per_minute': current_metrics.rest_api_calls_per_minute,
                'error_rate': current_metrics.error_rate
            },
            'trends': trend_data,
            'uptime_seconds': (datetime.now() - self.start_time).total_seconds() if self.start_time else 0
        }
    
    async def _initialize_collectors(self):
        """初始化收集器"""
        logger.info("初始化收集器")
        
        # 从配置中获取启用的收集器
        collectors_config = self.config_manager.get('collectors', {})
        
        initialization_tasks = []
        for exchange_name, exchange_config in collectors_config.items():
            if exchange_config.get('enabled', False):
                initialization_tasks.append(
                    self.add_collector(exchange_name, config=exchange_config)
                )
        
        if initialization_tasks:
            results = await asyncio.gather(*initialization_tasks, return_exceptions=True)
            
            success_count = sum(1 for r in results if r is True)
            logger.info(f"收集器初始化完成: {success_count}/{len(results)} 成功")
        else:
            logger.warning("没有启用的收集器")
    
    async def _start_collectors(self):
        """启动所有收集器"""
        start_tasks = []
        for exchange_name in self.collectors:
            start_tasks.append(self._start_collector(exchange_name))
        
        if start_tasks:
            await asyncio.gather(*start_tasks, return_exceptions=True)
    
    async def _start_collector(self, exchange_name: str):
        """启动单个收集器"""
        collector = self.collectors[exchange_name]
        
        try:
            # 创建收集器运行任务
            task = asyncio.create_task(self._run_collector(exchange_name, collector))
            self.collector_tasks[exchange_name] = task
            
            # 更新状态
            self.collector_status[exchange_name].is_active = True
            self.collector_status[exchange_name].last_heartbeat = datetime.now()
            
            logger.info(f"收集器 {exchange_name} 已启动")
            
        except Exception as e:
            logger.error(f"启动收集器 {exchange_name} 失败: {e}")
            self.collector_status[exchange_name].error_count += 1
            self.collector_status[exchange_name].last_error = str(e)
    
    async def _stop_collectors(self):
        """停止所有收集器"""
        stop_tasks = []
        for exchange_name in list(self.collector_tasks.keys()):
            stop_tasks.append(self._stop_collector(exchange_name))
        
        if stop_tasks:
            await asyncio.gather(*stop_tasks, return_exceptions=True)
    
    async def _stop_collector(self, exchange_name: str):
        """停止单个收集器"""
        if exchange_name in self.collector_tasks:
            task = self.collector_tasks.pop(exchange_name)
            
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            
            # 更新状态
            if exchange_name in self.collector_status:
                self.collector_status[exchange_name].is_active = False
            
            logger.info(f"收集器 {exchange_name} 已停止")
    
    async def _run_collector(self, exchange_name: str, collector: BaseExchangeCollector):
        """运行收集器的主循环"""
        logger.info(f"开始运行收集器: {exchange_name}")
        
        try:
            # 启动收集器
            await collector.start()
            
            # 保持运行直到被停止
            while self.is_running:
                # 更新心跳
                self.collector_status[exchange_name].last_heartbeat = datetime.now()
                
                # 检查连接状态
                connection_ok = await collector.validate_connection()
                self.collector_status[exchange_name].connection_status.update({
                    'websocket': connection_ok,
                    'rest_api': connection_ok
                })
                
                if not connection_ok:
                    logger.warning(f"收集器 {exchange_name} 连接异常，尝试重连")
                    await collector.initialize()
                
                await asyncio.sleep(5.0)  # 每5秒检查一次
                
        except asyncio.CancelledError:
            logger.info(f"收集器 {exchange_name} 被取消")
            raise
        except Exception as e:
            logger.error(f"收集器 {exchange_name} 运行异常: {e}")
            self.collector_status[exchange_name].error_count += 1
            self.collector_status[exchange_name].last_error = str(e)
            
            # 尝试自动重启
            if self.is_running:
                logger.info(f"尝试重启收集器: {exchange_name}")
                await asyncio.sleep(10.0)  # 等待10秒后重启
                await self._start_collector(exchange_name)
        finally:
            # 停止收集器
            try:
                await collector.stop()
            except Exception as e:
                logger.error(f"停止收集器 {exchange_name} 时发生错误: {e}")
    
    async def _monitor_loop(self):
        """监控循环"""
        logger.info("启动系统监控")
        
        while self.is_running:
            try:
                # 收集系统指标
                metrics = await self._collect_system_metrics()
                self.system_metrics.append(metrics)
                
                # 限制历史数据量
                if len(self.system_metrics) > self.max_metrics_history:
                    self.system_metrics = self.system_metrics[-self.max_metrics_history:]
                
                # 记录关键指标
                logger.info(
                    "系统监控指标",
                    extra={
                        "active_collectors": metrics.active_collectors,
                        "data_points_per_minute": metrics.data_points_per_minute,
                        "memory_usage_mb": metrics.memory_usage_mb,
                        "cpu_usage_percent": metrics.cpu_usage_percent,
                        "error_rate": metrics.error_rate
                    }
                )
                
                await asyncio.sleep(60.0)  # 每分钟收集一次
                
            except Exception as e:
                logger.error(f"监控循环错误: {e}")
                await asyncio.sleep(60.0)
    
    async def _health_check_loop(self):
        """健康检查循环"""
        logger.info("启动健康检查")
        
        while self.is_running:
            try:
                # 检查所有收集器健康状态
                for exchange_name, status in self.collector_status.items():
                    # 检查心跳超时
                    time_since_heartbeat = datetime.now() - status.last_heartbeat
                    if time_since_heartbeat > timedelta(minutes=2):
                        logger.warning(f"收集器 {exchange_name} 心跳超时，尝试重启")
                        await self.restart_collector(exchange_name)
                    
                    # 检查错误率
                    if status.error_count > 10:  # 错误超过10次
                        logger.warning(f"收集器 {exchange_name} 错误过多: {status.error_count}")
                        # 可以实现自动处理逻辑
                
                await asyncio.sleep(30.0)  # 每30秒检查一次
                
            except Exception as e:
                logger.error(f"健康检查循环错误: {e}")
                await asyncio.sleep(30.0)
    
    async def _config_watcher_loop(self):
        """配置监视循环"""
        logger.info("启动配置监视")
        
        while self.is_running:
            try:
                # 检查配置变化
                if hasattr(self.config_manager, 'check_for_changes'):
                    if self.config_manager.check_for_changes():
                        logger.info("检测到配置变化，重新加载收集器")
                        await self._reload_collectors()
                
                await asyncio.sleep(10.0)  # 每10秒检查一次
                
            except Exception as e:
                logger.error(f"配置监视循环错误: {e}")
                await asyncio.sleep(10.0)
    
    async def _collect_system_metrics(self) -> SystemMetrics:
        """收集系统指标"""
        import psutil
        
        # 基本系统指标
        memory_usage = psutil.virtual_memory().used / (1024 * 1024)  # MB
        cpu_usage = psutil.cpu_percent(interval=0.1)
        
        # 收集器指标
        active_collectors = len([s for s in self.collector_status.values() if s.is_active])
        
        # 计算数据点速率（简化版）
        total_data_points = 0
        total_symbols = 0
        websocket_connections = 0
        error_count = 0
        
        for name, collector in self.collectors.items():
            if hasattr(collector, 'get_collection_stats'):
                stats = collector.get_collection_stats()
                total_data_points += stats.get('total_data_points', 0)
                total_symbols += stats.get('symbol_count', 0)
                error_count += stats.get('error_count', 0)
            
            # 统计WebSocket连接
            if hasattr(collector, 'websocket_clients'):
                websocket_connections += len(collector.websocket_clients)
        
        # 计算速率（简化版）
        data_points_per_minute = total_data_points / max(1, (datetime.now() - self.start_time).total_seconds() / 60) if self.start_time else 0
        error_rate = error_count / max(1, total_data_points) if total_data_points > 0 else 0
        
        return SystemMetrics(
            timestamp=datetime.now(),
            active_collectors=active_collectors,
            total_symbols=total_symbols,
            data_points_per_minute=data_points_per_minute,
            memory_usage_mb=memory_usage,
            cpu_usage_percent=cpu_usage,
            websocket_connections=websocket_connections,
            rest_api_calls_per_minute=0.0,  # 简化版暂不统计
            error_rate=error_rate
        )
    
    async def _reload_collectors(self):
        """重新加载收集器配置"""
        logger.info("重新加载收集器配置")
        
        try:
            # 获取新配置
            new_config = self.config_manager.get('collectors', {})
            
            # 比较配置变化
            current_collectors = set(self.collectors.keys())
            new_collectors = set(name for name, config in new_config.items() if config.get('enabled', False))
            
            # 移除不再需要的收集器
            to_remove = current_collectors - new_collectors
            for exchange_name in to_remove:
                await self.remove_collector(exchange_name)
            
            # 添加新的收集器
            to_add = new_collectors - current_collectors
            for exchange_name in to_add:
                await self.add_collector(exchange_name, config=new_config[exchange_name])
            
            # 重启配置有变化的收集器
            to_restart = current_collectors & new_collectors
            for exchange_name in to_restart:
                # 简化版：暂时重启所有现有收集器
                await self.restart_collector(exchange_name)
            
            logger.info(f"配置重载完成: 移除{len(to_remove)}, 添加{len(to_add)}, 重启{len(to_restart)}")
            
        except Exception as e:
            logger.error(f"重新加载收集器配置失败: {e}")
    
    def get_summary(self) -> Dict[str, Any]:
        """获取管理器摘要信息"""
        return {
            'is_running': self.is_running,
            'start_time': self.start_time.isoformat() if self.start_time else None,
            'uptime_seconds': (datetime.now() - self.start_time).total_seconds() if self.start_time else 0,
            'total_collectors': len(self.collectors),
            'active_collectors': len([s for s in self.collector_status.values() if s.is_active]),
            'total_errors': sum(s.error_count for s in self.collector_status.values()),
            'collector_names': list(self.collectors.keys()),
            'system_metrics_count': len(self.system_metrics)
        }