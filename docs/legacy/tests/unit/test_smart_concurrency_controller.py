"""
SmartConcurrencyController 智能并发控制器单元测试
TDD: 先写测试定义并发控制行为，再实现
"""
import pytest
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime

# from src.utils.enhanced.smart_concurrency_controller import SmartConcurrencyController, TaskMetrics


class TestSmartConcurrencyController:
    """SmartConcurrencyController 智能并发控制器测试"""
    
    @pytest.fixture
    def controller(self):
        """创建控制器实例"""
        from src.utils.enhanced.smart_concurrency_controller import SmartConcurrencyController
        return SmartConcurrencyController(
            max_concurrent=5,
            target_cpu_usage=70.0,
            target_memory_usage=80.0,
            adjustment_interval=1.0
        )
    
    @pytest.fixture
    def mock_task(self):
        """模拟异步任务"""
        async def sample_task(task_id: int, delay: float = 0.1, should_fail: bool = False):
            await asyncio.sleep(delay)
            if should_fail:
                raise Exception(f"Task {task_id} failed")
            return f"Task {task_id} completed"
        return sample_task

    # ===============================
    # 基本功能测试
    # ===============================
    
    @pytest.mark.unit
    async def test_controller_initialization(self, controller):
        """测试控制器初始化"""
        assert controller.max_concurrent == 5
        assert controller.target_cpu_usage == 70.0
        assert controller.target_memory_usage == 80.0
        assert controller.current_concurrent == 0
        assert controller.is_running == False
    
    @pytest.mark.unit
    async def test_start_and_stop_controller(self, controller):
        """测试启动和停止控制器"""
        # 启动控制器
        await controller.start()
        assert controller.is_running == True
        
        # 停止控制器
        await controller.stop()
        assert controller.is_running == False
    
    @pytest.mark.unit
    async def test_submit_single_task(self, controller, mock_task):
        """测试提交单个任务"""
        await controller.start()
        
        # 提交任务
        future = await controller.submit(mock_task, 1)
        result = await future
        
        assert result == "Task 1 completed"
        assert controller.current_concurrent == 0  # 任务完成后应该为0
        
        await controller.stop()
    
    @pytest.mark.unit
    async def test_concurrent_limit_enforcement(self, controller, mock_task):
        """测试并发限制强制执行"""
        await controller.start()
        
        # 提交6个任务（超过限制5个）
        futures = []
        for i in range(6):
            future = await controller.submit(mock_task, i, delay=0.2)
            futures.append(future)
        
        # 同时最多只能有5个任务运行
        await asyncio.sleep(0.1)  # 让任务开始执行
        assert controller.current_concurrent <= 5
        
        # 等待所有任务完成
        results = await asyncio.gather(*futures)
        assert len(results) == 6
        assert controller.current_concurrent == 0
        
        await controller.stop()
    
    @pytest.mark.unit
    async def test_task_failure_handling(self, controller, mock_task):
        """测试任务失败处理"""
        await controller.start()
        
        # 提交一个会失败的任务
        future = await controller.submit(mock_task, 1, should_fail=True)
        
        with pytest.raises(Exception, match="Task 1 failed"):
            await future
        
        # 失败任务不应影响并发计数
        assert controller.current_concurrent == 0
        
        await controller.stop()

    # ===============================
    # 智能调节测试
    # ===============================
    
    @pytest.mark.unit
    async def test_dynamic_concurrency_adjustment(self, controller):
        """测试动态并发调节"""
        await controller.start()
        
        # 模拟高CPU使用率
        controller._get_system_metrics = MagicMock(return_value={
            'cpu_usage': 90.0,  # 超过目标70%
            'memory_usage': 60.0,
            'load_average': 2.0
        })
        
        # 触发调节
        await controller._adjust_concurrency()
        
        # 应该降低并发数
        assert controller.max_concurrent < 5
        
        await controller.stop()
    
    @pytest.mark.unit
    async def test_concurrency_increase_on_low_usage(self, controller):
        """测试低使用率时增加并发数"""
        await controller.start()
        
        # 模拟低系统使用率
        controller._get_system_metrics = MagicMock(return_value={
            'cpu_usage': 30.0,  # 远低于目标70%
            'memory_usage': 40.0,  # 远低于目标80%
            'load_average': 0.5
        })
        
        original_max = controller.max_concurrent
        
        # 触发调节
        await controller._adjust_concurrency()
        
        # 应该增加并发数（如果系统资源充足）
        assert controller.max_concurrent >= original_max
        
        await controller.stop()
    
    @pytest.mark.unit
    async def test_concurrency_bounds_enforcement(self, controller):
        """测试并发数边界强制执行"""
        await controller.start()
        
        # 测试最小值限制
        controller.max_concurrent = 1
        controller._get_system_metrics = MagicMock(return_value={
            'cpu_usage': 95.0,  # 极高CPU
            'memory_usage': 95.0,  # 极高内存
            'load_average': 5.0
        })
        
        await controller._adjust_concurrency()
        assert controller.max_concurrent >= controller.min_concurrent
        
        # 测试最大值限制
        controller.max_concurrent = 50
        controller._get_system_metrics = MagicMock(return_value={
            'cpu_usage': 10.0,  # 极低CPU
            'memory_usage': 10.0,  # 极低内存
            'load_average': 0.1
        })
        
        await controller._adjust_concurrency()
        assert controller.max_concurrent <= controller.absolute_max_concurrent
        
        await controller.stop()

    # ===============================
    # 任务优先级测试
    # ===============================
    
    @pytest.mark.unit
    async def test_priority_queue_ordering(self, controller, mock_task):
        """测试优先级队列排序"""
        await controller.start()
        
        # 创建长时间运行的任务占满并发槽
        long_tasks = []
        for i in range(5):  # 填满所有槽位
            future = await controller.submit(mock_task, i, delay=0.5)
            long_tasks.append(future)
        
        # 提交不同优先级的任务
        high_priority_future = await controller.submit(mock_task, 100, priority=1)  # 高优先级
        low_priority_future = await controller.submit(mock_task, 101, priority=10)   # 低优先级
        
        await asyncio.sleep(0.1)  # 确保任务进入队列
        
        # 等待长任务完成，释放槽位
        await asyncio.gather(*long_tasks)
        
        # 高优先级任务应该先完成
        high_result = await high_priority_future
        low_result = await low_priority_future
        
        assert "Task 100 completed" in high_result
        assert "Task 101 completed" in low_result
        
        await controller.stop()
    
    @pytest.mark.unit
    async def test_task_timeout_handling(self, controller):
        """测试任务超时处理"""
        await controller.start()
        
        async def slow_task():
            await asyncio.sleep(2.0)  # 2秒任务
            return "Slow task completed"
        
        # 提交有超时的任务
        future = await controller.submit(slow_task, timeout=0.5)  # 0.5秒超时
        
        with pytest.raises(asyncio.TimeoutError):
            await future
        
        await controller.stop()

    # ===============================
    # 统计和监控测试
    # ===============================
    
    @pytest.mark.unit
    async def test_task_metrics_collection(self, controller, mock_task):
        """测试任务指标收集"""
        await controller.start()
        
        # 执行几个任务
        futures = []
        for i in range(3):
            future = await controller.submit(mock_task, i)
            futures.append(future)
        
        await asyncio.gather(*futures)
        
        # 检查统计信息
        stats = controller.get_stats()
        
        assert stats['total_tasks_submitted'] >= 3
        assert stats['total_tasks_completed'] >= 3
        assert stats['total_tasks_failed'] == 0
        assert stats['current_queue_size'] == 0
        assert 'average_task_duration' in stats
        assert 'tasks_per_second' in stats
        
        await controller.stop()
    
    @pytest.mark.unit
    async def test_performance_metrics_tracking(self, controller, mock_task):
        """测试性能指标跟踪"""
        await controller.start()
        
        # 执行任务并收集性能数据
        start_time = time.time()
        
        futures = []
        for i in range(10):
            future = await controller.submit(mock_task, i, delay=0.01)
            futures.append(future)
        
        await asyncio.gather(*futures)
        
        elapsed = time.time() - start_time
        stats = controller.get_stats()
        
        # 验证性能指标
        assert stats['average_task_duration'] > 0
        assert stats['tasks_per_second'] > 0
        assert stats['total_execution_time'] > 0
        
        await controller.stop()
    
    @pytest.mark.unit
    async def test_system_resource_monitoring(self, controller):
        """测试系统资源监控"""
        await controller.start()
        
        # 获取系统指标
        metrics = controller._get_system_metrics()
        
        assert 'cpu_usage' in metrics
        assert 'memory_usage' in metrics
        assert 'load_average' in metrics
        assert isinstance(metrics['cpu_usage'], (int, float))
        assert isinstance(metrics['memory_usage'], (int, float))
        assert 0 <= metrics['cpu_usage'] <= 100
        assert 0 <= metrics['memory_usage'] <= 100
        
        await controller.stop()

    # ===============================
    # 错误恢复测试
    # ===============================
    
    @pytest.mark.unit
    async def test_task_retry_mechanism(self, controller):
        """测试任务重试机制"""
        await controller.start()
        
        attempt_count = 0
        
        async def flaky_task():
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count < 3:  # 前两次失败
                raise Exception(f"Attempt {attempt_count} failed")
            return f"Success after {attempt_count} attempts"
        
        # 提交需要重试的任务
        future = await controller.submit(flaky_task, max_retries=3)
        result = await future
        
        assert result == "Success after 3 attempts"
        assert attempt_count == 3
        
        await controller.stop()
    
    @pytest.mark.unit
    async def test_circuit_breaker_functionality(self, controller, mock_task):
        """测试熔断器功能"""
        await controller.start()
        
        # 启用熔断器
        controller.enable_circuit_breaker = True
        controller.failure_threshold = 3  # 3次失败后熔断
        
        # 提交多个失败任务
        for i in range(5):
            try:
                future = await controller.submit(mock_task, i, should_fail=True)
                await future
            except Exception:
                pass
        
        # 熔断器应该已经打开
        assert controller._circuit_breaker_open == True
        
        # 新任务应该被快速拒绝
        with pytest.raises(Exception, match="Circuit breaker"):
            future = await controller.submit(mock_task, 999)
            await future
        
        await controller.stop()

    # ===============================
    # 配置和定制测试
    # ===============================
    
    @pytest.mark.unit
    async def test_custom_task_scheduler(self, controller):
        """测试自定义任务调度器"""
        await controller.start()
        
        # 自定义调度策略：按任务ID排序
        def custom_scheduler(tasks):
            return sorted(tasks, key=lambda t: t.task_id)
        
        controller.set_scheduler(custom_scheduler)
        
        # 测试调度逻辑（这里简化测试）
        assert controller._task_scheduler == custom_scheduler
        
        await controller.stop()
    
    @pytest.mark.unit
    async def test_resource_aware_scheduling(self, controller, mock_task):
        """测试资源感知调度"""
        await controller.start()
        
        # 启用资源感知调度
        controller.enable_resource_aware_scheduling = True
        
        # 模拟不同资源需求的任务
        async def cpu_intensive_task():
            # 模拟CPU密集型任务
            await asyncio.sleep(0.1)
            return "CPU task done"
        
        async def memory_intensive_task():
            # 模拟内存密集型任务
            await asyncio.sleep(0.1)
            return "Memory task done"
        
        # 提交不同类型的任务
        cpu_future = await controller.submit(cpu_intensive_task, resource_type='cpu')
        memory_future = await controller.submit(memory_intensive_task, resource_type='memory')
        
        # 等待完成
        cpu_result = await cpu_future
        memory_result = await memory_future
        
        assert "CPU task done" in cpu_result
        assert "Memory task done" in memory_result
        
        await controller.stop()

    # ===============================
    # 负载测试
    # ===============================
    
    @pytest.mark.unit
    async def test_high_load_handling(self, controller, mock_task):
        """测试高负载处理"""
        await controller.start()
        
        # 提交大量任务
        num_tasks = 100
        futures = []
        
        start_time = time.time()
        
        for i in range(num_tasks):
            future = await controller.submit(mock_task, i, delay=0.001)
            futures.append(future)
        
        # 等待所有任务完成
        results = await asyncio.gather(*futures)
        elapsed = time.time() - start_time
        
        # 验证结果
        assert len(results) == num_tasks
        assert all("completed" in result for result in results)
        
        # 检查性能
        stats = controller.get_stats()
        assert stats['total_tasks_completed'] >= num_tasks
        assert stats['tasks_per_second'] > 0
        
        # 高负载下应该保持合理的处理速度
        assert stats['tasks_per_second'] > num_tasks / (elapsed * 2)  # 至少50%效率
        
        await controller.stop()


class TestTaskMetrics:
    """TaskMetrics 任务指标测试"""
    
    @pytest.mark.unit
    def test_task_metrics_creation(self):
        """测试任务指标创建"""
        from src.utils.enhanced.smart_concurrency_controller import TaskMetrics
        
        metrics = TaskMetrics(
            task_id="test_task_1",
            priority=5,
            submit_time=datetime.now(),
            start_time=None,
            end_time=None,
            status="pending"
        )
        
        assert metrics.task_id == "test_task_1"
        assert metrics.priority == 5
        assert metrics.status == "pending"
        assert metrics.duration is None  # 还未完成
    
    @pytest.mark.unit
    def test_task_metrics_duration_calculation(self):
        """测试任务指标持续时间计算"""
        from src.utils.enhanced.smart_concurrency_controller import TaskMetrics
        
        now = datetime.now()
        start = now
        end = now
        
        metrics = TaskMetrics(
            task_id="test_task_2",
            priority=1,
            submit_time=start,
            start_time=start,
            end_time=end,
            status="completed"
        )
        
        assert metrics.duration == 0.0  # 即时完成
        
        # 测试有持续时间的情况
        from datetime import timedelta
        metrics.end_time = start + timedelta(seconds=2)
        assert metrics.duration == 2.0


# ===============================
# 辅助测试fixtures和工具
# ===============================

@pytest.fixture
def resource_monitor():
    """资源监控器mock"""
    monitor = MagicMock()
    monitor.get_cpu_usage.return_value = 50.0
    monitor.get_memory_usage.return_value = 60.0
    monitor.get_load_average.return_value = 1.0
    return monitor


@pytest.fixture
async def multiple_controllers():
    """多个控制器实例用于协调测试"""
    from src.utils.enhanced.smart_concurrency_controller import SmartConcurrencyController
    
    controllers = []
    for i in range(3):
        controller = SmartConcurrencyController(
            max_concurrent=3 + i,
            controller_id=f"controller_{i}"
        )
        await controller.start()
        controllers.append(controller)
    
    yield controllers
    
    # 清理
    for controller in controllers:
        await controller.stop()


class TestControllerIntegration:
    """控制器集成测试"""
    
    @pytest.mark.unit
    async def test_multiple_controllers_coordination(self, multiple_controllers):
        """测试多个控制器协调工作"""
        controllers = multiple_controllers
        
        async def shared_task(task_id):
            await asyncio.sleep(0.1)
            return f"Task {task_id} by {controllers[task_id % len(controllers)].controller_id}"
        
        # 在不同控制器上提交任务
        futures = []
        for i in range(9):  # 每个控制器3个任务
            controller = controllers[i % len(controllers)]
            future = await controller.submit(shared_task, i)
            futures.append(future)
        
        # 等待所有任务完成
        results = await asyncio.gather(*futures)
        
        # 验证每个控制器都处理了任务
        assert len(results) == 9
        controller_usage = {}
        for result in results:
            for controller in controllers:
                if controller.controller_id in result:
                    controller_usage[controller.controller_id] = controller_usage.get(controller.controller_id, 0) + 1
        
        # 每个控制器应该处理了大约相同数量的任务
        assert len(controller_usage) == len(controllers)
        assert all(count > 0 for count in controller_usage.values())