"""
智能并发控制器
借鉴Qlib的任务调度模式，提供资源感知的并发控制
"""
import asyncio
import heapq
import psutil
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Union
from dataclasses import dataclass, field
from loguru import logger
import threading
from collections import defaultdict, deque


@dataclass
class TaskMetrics:
    """任务指标"""
    task_id: str
    priority: int
    submit_time: datetime
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    status: str = "pending"  # pending, running, completed, failed, timeout
    error: Optional[Exception] = None
    retry_count: int = 0
    resource_type: str = "general"
    
    @property
    def duration(self) -> Optional[float]:
        """任务持续时间（秒）"""
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return None
    
    @property
    def wait_time(self) -> Optional[float]:
        """任务等待时间（秒）"""
        if self.start_time:
            return (self.start_time - self.submit_time).total_seconds()
        return None


@dataclass
class TaskInfo:
    """任务信息"""
    task_id: str
    coro: Callable
    args: tuple
    kwargs: dict
    priority: int
    timeout: Optional[float]
    max_retries: int
    resource_type: str
    submit_time: datetime = field(default_factory=datetime.now)
    future: Optional[asyncio.Future] = None
    
    def __lt__(self, other):
        """优先级比较（数值越小优先级越高）"""
        return self.priority < other.priority


class SmartConcurrencyController:
    """
    智能并发控制器
    
    功能：
    1. 动态并发数调节（基于系统资源）
    2. 任务优先级队列
    3. 任务超时和重试机制
    4. 熔断器模式
    5. 资源感知调度
    6. 性能监控和统计
    """
    
    def __init__(self,
                 max_concurrent: int = 10,
                 min_concurrent: int = 1,
                 absolute_max_concurrent: int = 50,
                 target_cpu_usage: float = 70.0,
                 target_memory_usage: float = 80.0,
                 adjustment_interval: float = 5.0,
                 enable_circuit_breaker: bool = True,
                 failure_threshold: int = 5,
                 circuit_breaker_timeout: float = 60.0,
                 enable_resource_aware_scheduling: bool = True,
                 controller_id: str = None):
        """
        初始化智能并发控制器
        
        Args:
            max_concurrent: 最大并发数
            min_concurrent: 最小并发数
            absolute_max_concurrent: 绝对最大并发数
            target_cpu_usage: 目标CPU使用率（%）
            target_memory_usage: 目标内存使用率（%）
            adjustment_interval: 调节间隔（秒）
            enable_circuit_breaker: 启用熔断器
            failure_threshold: 熔断器失败阈值
            circuit_breaker_timeout: 熔断器超时时间（秒）
            enable_resource_aware_scheduling: 启用资源感知调度
            controller_id: 控制器ID
        """
        # 基本配置
        self.max_concurrent = max_concurrent
        self.min_concurrent = min_concurrent
        self.absolute_max_concurrent = absolute_max_concurrent
        self.target_cpu_usage = target_cpu_usage
        self.target_memory_usage = target_memory_usage
        self.adjustment_interval = adjustment_interval
        self.controller_id = controller_id or f"controller_{id(self)}"
        
        # 状态管理
        self.is_running = False
        self.current_concurrent = 0
        self._running_tasks: Dict[str, TaskInfo] = {}
        self._task_queue: List[TaskInfo] = []
        self._task_id_counter = 0
        
        # 熔断器
        self.enable_circuit_breaker = enable_circuit_breaker
        self.failure_threshold = failure_threshold
        self.circuit_breaker_timeout = circuit_breaker_timeout
        self._circuit_breaker_open = False
        self._circuit_breaker_last_failure = None
        self._consecutive_failures = 0
        
        # 资源感知调度
        self.enable_resource_aware_scheduling = enable_resource_aware_scheduling
        self._resource_usage: Dict[str, int] = defaultdict(int)
        
        # 统计信息
        self._stats = {
            'total_tasks_submitted': 0,
            'total_tasks_completed': 0,
            'total_tasks_failed': 0,
            'total_tasks_timeout': 0,
            'total_execution_time': 0.0,
            'task_durations': deque(maxlen=1000),  # 保留最近1000个任务的持续时间
            'concurrency_adjustments': 0,
            'start_time': None
        }
        
        # 任务指标
        self._task_metrics: Dict[str, TaskMetrics] = {}
        
        # 调度器
        self._task_scheduler: Optional[Callable] = None
        
        # 控制任务
        self._adjustment_task = None
        self._worker_task = None
        self._monitor_task = None
        
        # 同步原语
        self._queue_lock = asyncio.Lock()
        self._stats_lock = threading.Lock()
        
        logger.info(
            f"SmartConcurrencyController '{self.controller_id}' 初始化完成",
            extra={
                "max_concurrent": self.max_concurrent,
                "target_cpu": self.target_cpu_usage,
                "target_memory": self.target_memory_usage,
                "circuit_breaker_enabled": self.enable_circuit_breaker
            }
        )
    
    async def start(self):
        """启动控制器"""
        if self.is_running:
            return
        
        self.is_running = True
        self._stats['start_time'] = datetime.now()
        
        # 启动控制任务
        self._worker_task = asyncio.create_task(self._worker_loop())
        self._adjustment_task = asyncio.create_task(self._adjustment_loop())
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        
        logger.info(f"SmartConcurrencyController '{self.controller_id}' 已启动")
    
    async def stop(self):
        """停止控制器"""
        if not self.is_running:
            return
        
        self.is_running = False
        
        # 等待运行中的任务完成
        if self._running_tasks:
            logger.info(f"等待 {len(self._running_tasks)} 个任务完成...")
            running_futures = [task.future for task in self._running_tasks.values() if task.future]
            if running_futures:
                await asyncio.gather(*running_futures, return_exceptions=True)
        
        # 取消控制任务
        for task in [self._worker_task, self._adjustment_task, self._monitor_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        
        logger.info(f"SmartConcurrencyController '{self.controller_id}' 已停止")
    
    async def submit(self,
                    coro: Callable,
                    *args,
                    priority: int = 5,
                    timeout: Optional[float] = None,
                    max_retries: int = 0,
                    resource_type: str = "general",
                    **kwargs) -> asyncio.Future:
        """
        提交任务
        
        Args:
            coro: 协程函数
            *args: 位置参数
            priority: 优先级（数值越小优先级越高）
            timeout: 超时时间（秒）
            max_retries: 最大重试次数
            resource_type: 资源类型
            **kwargs: 关键字参数
        """
        if not self.is_running:
            raise RuntimeError("Controller is not running")
        
        # 熔断器检查
        if self._is_circuit_breaker_open():
            raise Exception("Circuit breaker is open, rejecting new tasks")
        
        # 生成任务ID
        self._task_id_counter += 1
        task_id = f"{self.controller_id}_task_{self._task_id_counter}"
        
        # 创建Future用于返回结果
        future = asyncio.Future()
        
        # 创建任务信息
        task_info = TaskInfo(
            task_id=task_id,
            coro=coro,
            args=args,
            kwargs=kwargs,
            priority=priority,
            timeout=timeout,
            max_retries=max_retries,
            resource_type=resource_type,
            future=future
        )
        
        # 创建任务指标
        self._task_metrics[task_id] = TaskMetrics(
            task_id=task_id,
            priority=priority,
            submit_time=datetime.now(),
            resource_type=resource_type
        )
        
        # 加入队列
        async with self._queue_lock:
            heapq.heappush(self._task_queue, task_info)
            
        with self._stats_lock:
            self._stats['total_tasks_submitted'] += 1
        
        logger.debug(f"任务 {task_id} 已提交到队列，优先级: {priority}")
        
        return future
    
    async def _worker_loop(self):
        """工作循环 - 处理任务队列"""
        while self.is_running:
            try:
                # 检查是否可以启动新任务
                if (self.current_concurrent < self.max_concurrent and 
                    self._task_queue):
                    
                    async with self._queue_lock:
                        if self._task_queue:
                            # 获取最高优先级任务
                            task_info = heapq.heappop(self._task_queue)
                            
                            # 资源感知调度检查
                            if (self.enable_resource_aware_scheduling and 
                                not self._can_schedule_task(task_info)):
                                # 重新放回队列
                                heapq.heappush(self._task_queue, task_info)
                                await asyncio.sleep(0.1)
                                continue
                            
                            # 启动任务
                            await self._start_task(task_info)
                
                await asyncio.sleep(0.01)  # 避免忙等待
                
            except Exception as e:
                logger.error(f"Worker loop error: {e}")
                await asyncio.sleep(1.0)
    
    async def _start_task(self, task_info: TaskInfo):
        """启动单个任务"""
        task_id = task_info.task_id
        
        try:
            # 更新状态
            self.current_concurrent += 1
            self._running_tasks[task_id] = task_info
            
            # 更新资源使用
            if self.enable_resource_aware_scheduling:
                self._resource_usage[task_info.resource_type] += 1
            
            # 更新任务指标
            metrics = self._task_metrics[task_id]
            metrics.start_time = datetime.now()
            metrics.status = "running"
            
            logger.debug(f"启动任务 {task_id}，当前并发数: {self.current_concurrent}")
            
            # 创建任务
            async def task_wrapper():
                try:
                    # 执行任务
                    if task_info.timeout:
                        result = await asyncio.wait_for(
                            task_info.coro(*task_info.args, **task_info.kwargs),
                            timeout=task_info.timeout
                        )
                    else:
                        result = await task_info.coro(*task_info.args, **task_info.kwargs)
                    
                    # 任务成功
                    await self._task_completed(task_id, result)
                    
                except asyncio.TimeoutError:
                    # 任务超时
                    await self._task_timeout(task_id)
                    
                except Exception as e:
                    # 任务失败
                    await self._task_failed(task_id, e)
            
            # 启动任务
            asyncio.create_task(task_wrapper())
            
        except Exception as e:
            logger.error(f"启动任务 {task_id} 失败: {e}")
            await self._task_failed(task_id, e)
    
    async def _task_completed(self, task_id: str, result: Any):
        """任务完成处理"""
        task_info = self._running_tasks.pop(task_id, None)
        if not task_info:
            return
        
        # 更新状态
        self.current_concurrent -= 1
        
        # 更新资源使用
        if self.enable_resource_aware_scheduling:
            self._resource_usage[task_info.resource_type] -= 1
        
        # 更新指标
        metrics = self._task_metrics[task_id]
        metrics.end_time = datetime.now()
        metrics.status = "completed"
        
        # 更新统计
        with self._stats_lock:
            self._stats['total_tasks_completed'] += 1
            if metrics.duration:
                self._stats['task_durations'].append(metrics.duration)
                self._stats['total_execution_time'] += metrics.duration
        
        # 重置熔断器
        self._consecutive_failures = 0
        
        # 设置Future结果
        if task_info.future and not task_info.future.done():
            task_info.future.set_result(result)
        
        logger.debug(f"任务 {task_id} 完成，耗时: {metrics.duration:.3f}秒")
    
    async def _task_failed(self, task_id: str, error: Exception):
        """任务失败处理"""
        task_info = self._running_tasks.pop(task_id, None)
        if not task_info:
            return
        
        # 更新状态
        self.current_concurrent -= 1
        
        # 更新资源使用
        if self.enable_resource_aware_scheduling:
            self._resource_usage[task_info.resource_type] -= 1
        
        # 更新指标
        metrics = self._task_metrics[task_id]
        metrics.end_time = datetime.now()
        metrics.error = error
        metrics.retry_count += 1
        
        # 检查是否需要重试
        if metrics.retry_count <= task_info.max_retries:
            metrics.status = "retrying"
            
            # 重新提交任务（增加优先级）
            retry_task = TaskInfo(
                task_id=f"{task_id}_retry_{metrics.retry_count}",
                coro=task_info.coro,
                args=task_info.args,
                kwargs=task_info.kwargs,
                priority=max(1, task_info.priority - 1),  # 提高优先级
                timeout=task_info.timeout,
                max_retries=task_info.max_retries - metrics.retry_count,
                resource_type=task_info.resource_type,
                future=task_info.future
            )
            
            async with self._queue_lock:
                heapq.heappush(self._task_queue, retry_task)
            
            logger.warning(f"任务 {task_id} 失败，将重试 ({metrics.retry_count}/{task_info.max_retries}): {error}")
            return
        
        # 重试耗尽或不重试
        metrics.status = "failed"
        
        # 更新统计
        with self._stats_lock:
            self._stats['total_tasks_failed'] += 1
        
        # 更新熔断器
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.failure_threshold:
            self._circuit_breaker_open = True
            self._circuit_breaker_last_failure = datetime.now()
            logger.warning(f"熔断器开启，连续失败次数: {self._consecutive_failures}")
        
        # 设置Future异常
        if task_info.future and not task_info.future.done():
            task_info.future.set_exception(error)
        
        logger.error(f"任务 {task_id} 最终失败: {error}")
    
    async def _task_timeout(self, task_id: str):
        """任务超时处理"""
        error = asyncio.TimeoutError(f"Task {task_id} timed out")
        
        # 更新统计
        with self._stats_lock:
            self._stats['total_tasks_timeout'] += 1
        
        await self._task_failed(task_id, error)
    
    async def _adjustment_loop(self):
        """动态调节循环"""
        while self.is_running:
            try:
                await self._adjust_concurrency()
                await asyncio.sleep(self.adjustment_interval)
            except Exception as e:
                logger.error(f"Concurrency adjustment error: {e}")
                await asyncio.sleep(self.adjustment_interval)
    
    async def _adjust_concurrency(self):
        """调节并发数"""
        try:
            # 获取系统指标
            metrics = self._get_system_metrics()
            cpu_usage = metrics['cpu_usage']
            memory_usage = metrics['memory_usage']
            
            old_max = self.max_concurrent
            
            # 动态调节逻辑
            if cpu_usage > self.target_cpu_usage * 1.2 or memory_usage > self.target_memory_usage * 1.1:
                # 系统负载过高，降低并发数
                self.max_concurrent = max(self.min_concurrent, int(self.max_concurrent * 0.8))
                
            elif cpu_usage < self.target_cpu_usage * 0.7 and memory_usage < self.target_memory_usage * 0.7:
                # 系统负载较低，可以增加并发数
                self.max_concurrent = min(self.absolute_max_concurrent, int(self.max_concurrent * 1.2))
            
            # 边界检查
            self.max_concurrent = max(self.min_concurrent, 
                                    min(self.absolute_max_concurrent, self.max_concurrent))
            
            if old_max != self.max_concurrent:
                with self._stats_lock:
                    self._stats['concurrency_adjustments'] += 1
                
                logger.info(
                    f"并发数调节: {old_max} -> {self.max_concurrent}",
                    extra={
                        "cpu_usage": cpu_usage,
                        "memory_usage": memory_usage,
                        "current_concurrent": self.current_concurrent,
                        "queue_size": len(self._task_queue)
                    }
                )
                
        except Exception as e:
            logger.error(f"Concurrency adjustment failed: {e}")
    
    async def _monitor_loop(self):
        """监控循环"""
        while self.is_running:
            try:
                await self._log_status()
                await asyncio.sleep(30.0)  # 每30秒记录状态
            except Exception as e:
                logger.error(f"Monitor loop error: {e}")
                await asyncio.sleep(30.0)
    
    async def _log_status(self):
        """记录状态信息"""
        stats = self.get_stats()
        
        logger.info(
            f"Controller '{self.controller_id}' 状态",
            extra={
                "current_concurrent": self.current_concurrent,
                "max_concurrent": self.max_concurrent,
                "queue_size": len(self._task_queue),
                "total_submitted": stats['total_tasks_submitted'],
                "total_completed": stats['total_tasks_completed'],
                "total_failed": stats['total_tasks_failed'],
                "tasks_per_second": stats.get('tasks_per_second', 0),
                "average_duration": stats.get('average_task_duration', 0)
            }
        )
    
    def _get_system_metrics(self) -> Dict[str, float]:
        """获取系统指标"""
        try:
            cpu_usage = psutil.cpu_percent(interval=0.1)
            memory_usage = psutil.virtual_memory().percent
            load_avg = psutil.getloadavg()[0] if hasattr(psutil, 'getloadavg') else 0.0
            
            return {
                'cpu_usage': cpu_usage,
                'memory_usage': memory_usage,
                'load_average': load_avg
            }
        except Exception as e:
            logger.error(f"Failed to get system metrics: {e}")
            return {
                'cpu_usage': 50.0,  # 默认值
                'memory_usage': 50.0,
                'load_average': 1.0
            }
    
    def _can_schedule_task(self, task_info: TaskInfo) -> bool:
        """检查是否可以调度任务（资源感知）"""
        if not self.enable_resource_aware_scheduling:
            return True
        
        # 简单的资源类型限制
        resource_limits = {
            'cpu': self.max_concurrent // 2,
            'memory': self.max_concurrent // 3,
            'general': self.max_concurrent
        }
        
        current_usage = self._resource_usage[task_info.resource_type]
        limit = resource_limits.get(task_info.resource_type, self.max_concurrent)
        
        return current_usage < limit
    
    def _is_circuit_breaker_open(self) -> bool:
        """检查熔断器是否开启"""
        if not self.enable_circuit_breaker or not self._circuit_breaker_open:
            return False
        
        # 检查是否应该尝试恢复
        if self._circuit_breaker_last_failure:
            time_since_failure = (datetime.now() - self._circuit_breaker_last_failure).total_seconds()
            if time_since_failure > self.circuit_breaker_timeout:
                self._circuit_breaker_open = False
                self._consecutive_failures = 0
                logger.info("熔断器已恢复")
                return False
        
        return True
    
    def set_scheduler(self, scheduler_func: Callable):
        """设置自定义任务调度器"""
        self._task_scheduler = scheduler_func
        logger.info(f"设置自定义调度器: {scheduler_func.__name__}")
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        with self._stats_lock:
            stats = self._stats.copy()
        
        # 计算派生统计
        if self._stats['start_time']:
            uptime = (datetime.now() - self._stats['start_time']).total_seconds()
            stats['uptime_seconds'] = uptime
            stats['tasks_per_second'] = stats['total_tasks_submitted'] / max(1, uptime)
        else:
            stats['uptime_seconds'] = 0
            stats['tasks_per_second'] = 0
        
        # 平均任务持续时间
        if stats['task_durations']:
            stats['average_task_duration'] = sum(stats['task_durations']) / len(stats['task_durations'])
        else:
            stats['average_task_duration'] = 0
        
        # 当前状态
        stats['current_concurrent'] = self.current_concurrent
        stats['max_concurrent'] = self.max_concurrent
        stats['current_queue_size'] = len(self._task_queue)
        stats['circuit_breaker_open'] = self._circuit_breaker_open
        stats['consecutive_failures'] = self._consecutive_failures
        
        return stats
    
    def get_task_metrics(self, task_id: str) -> Optional[TaskMetrics]:
        """获取指定任务的指标"""
        return self._task_metrics.get(task_id)
    
    def get_all_task_metrics(self) -> Dict[str, TaskMetrics]:
        """获取所有任务指标"""
        return self._task_metrics.copy()
    
    def clear_completed_metrics(self, keep_recent_hours: int = 24):
        """清理完成任务的指标（保留最近N小时）"""
        cutoff_time = datetime.now() - timedelta(hours=keep_recent_hours)
        
        to_remove = []
        for task_id, metrics in self._task_metrics.items():
            if (metrics.status in ['completed', 'failed'] and 
                metrics.end_time and 
                metrics.end_time < cutoff_time):
                to_remove.append(task_id)
        
        for task_id in to_remove:
            del self._task_metrics[task_id]
        
        if to_remove:
            logger.info(f"清理了 {len(to_remove)} 个旧任务指标")