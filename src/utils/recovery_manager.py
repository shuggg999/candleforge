"""
🔄 异常自动恢复机制 - 监控和恢复系统组件
"""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass
from enum import Enum
import traceback
from loguru import logger


class ComponentStatus(Enum):
    """组件状态枚举"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILED = "failed"
    RECOVERING = "recovering"


@dataclass
class ComponentHealth:
    """组件健康状态"""
    name: str
    status: ComponentStatus
    last_check: datetime
    error_count: int
    last_error: Optional[str]
    recovery_attempts: int
    next_recovery: Optional[datetime]


class AutoRecoveryManager:
    """🔄 自动恢复管理器"""
    
    def __init__(self):
        self.components: Dict[str, ComponentHealth] = {}
        self.recovery_handlers: Dict[str, Callable] = {}
        self.health_checkers: Dict[str, Callable] = {}
        self.is_running = False
        self.check_interval = 30  # 30秒检查一次
        self.max_recovery_attempts = 5
        self.recovery_backoff_factor = 2  # 指数退避
        self._recovery_task: Optional[asyncio.Task] = None
        
        # 恢复统计
        self.recovery_stats = {
            'total_recoveries': 0,
            'successful_recoveries': 0,
            'failed_recoveries': 0,
            'last_recovery_time': None
        }
    
    def register_component(self, 
                          name: str, 
                          health_checker: Callable[[], bool],
                          recovery_handler: Callable[[], bool],
                          critical: bool = True):
        """📝 注册需要监控的组件"""
        self.components[name] = ComponentHealth(
            name=name,
            status=ComponentStatus.HEALTHY,
            last_check=datetime.now(timezone.utc),
            error_count=0,
            last_error=None,
            recovery_attempts=0,
            next_recovery=None
        )
        
        self.health_checkers[name] = health_checker
        self.recovery_handlers[name] = recovery_handler
        
        logger.info(f"📝 Registered component for monitoring: {name} (critical: {critical})")
    
    async def start_monitoring(self):
        """🚀 启动监控服务"""
        if self.is_running:
            logger.warning("Recovery manager is already running")
            return
        
        self.is_running = True
        self._recovery_task = asyncio.create_task(self._monitoring_loop())
        logger.info("🚀 Auto recovery manager started")
    
    async def stop_monitoring(self):
        """🛑 停止监控服务"""
        self.is_running = False
        
        if self._recovery_task and not self._recovery_task.done():
            self._recovery_task.cancel()
            try:
                await self._recovery_task
            except asyncio.CancelledError:
                pass
        
        logger.info("🛑 Auto recovery manager stopped")
    
    async def _monitoring_loop(self):
        """🔍 监控循环"""
        logger.info("🔍 Starting monitoring loop")
        
        while self.is_running:
            try:
                await self._check_all_components()
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                await asyncio.sleep(60)  # 出错时等待更长时间
        
        logger.info("🔍 Monitoring loop stopped")
    
    async def _check_all_components(self):
        """检查所有组件健康状态"""
        for component_name, component_health in self.components.items():
            try:
                await self._check_component(component_name, component_health)
            except Exception as e:
                logger.error(f"Error checking component {component_name}: {e}")
    
    async def _check_component(self, name: str, health: ComponentHealth):
        """检查单个组件"""
        try:
            # 如果组件正在恢复中，检查是否到了下次恢复时间
            if health.status == ComponentStatus.RECOVERING:
                if health.next_recovery and datetime.now(timezone.utc) < health.next_recovery:
                    return  # 还没到恢复时间
            
            # 执行健康检查
            health_checker = self.health_checkers[name]
            is_healthy = await self._run_health_check(health_checker)
            
            health.last_check = datetime.now(timezone.utc)
            
            if is_healthy:
                # 组件健康
                if health.status != ComponentStatus.HEALTHY:
                    logger.info(f"✅ Component {name} recovered to healthy state")
                    health.status = ComponentStatus.HEALTHY
                    health.error_count = 0
                    health.recovery_attempts = 0
                    health.last_error = None
                    health.next_recovery = None
            else:
                # 组件不健康
                await self._handle_unhealthy_component(name, health)
                
        except Exception as e:
            error_msg = f"Health check failed: {str(e)}"
            logger.error(f"❌ Component {name} health check error: {error_msg}")
            health.last_error = error_msg
            await self._handle_unhealthy_component(name, health)
    
    async def _run_health_check(self, health_checker: Callable) -> bool:
        """执行健康检查"""
        try:
            if asyncio.iscoroutinefunction(health_checker):
                return await health_checker()
            else:
                return health_checker()
        except Exception as e:
            logger.debug(f"Health check exception: {e}")
            return False
    
    async def _handle_unhealthy_component(self, name: str, health: ComponentHealth):
        """处理不健康的组件"""
        health.error_count += 1
        
        # 根据错误次数确定状态
        if health.error_count >= 3:
            if health.status != ComponentStatus.FAILED:
                logger.error(f"❌ Component {name} marked as FAILED (errors: {health.error_count})")
                health.status = ComponentStatus.FAILED
        else:
            if health.status != ComponentStatus.DEGRADED:
                logger.warning(f"⚠️ Component {name} marked as DEGRADED (errors: {health.error_count})")
                health.status = ComponentStatus.DEGRADED
        
        # 尝试自动恢复
        await self._attempt_recovery(name, health)
    
    async def _attempt_recovery(self, name: str, health: ComponentHealth):
        """尝试自动恢复组件"""
        # 检查是否已达到最大恢复尝试次数
        if health.recovery_attempts >= self.max_recovery_attempts:
            logger.error(f"🚫 Component {name} exceeded max recovery attempts ({self.max_recovery_attempts})")
            return
        
        # 检查是否在恢复冷却期
        if health.next_recovery and datetime.now(timezone.utc) < health.next_recovery:
            return
        
        # 开始恢复
        health.status = ComponentStatus.RECOVERING
        health.recovery_attempts += 1
        
        logger.info(f"🔄 Attempting recovery for {name} (attempt {health.recovery_attempts}/{self.max_recovery_attempts})")
        
        try:
            # 执行恢复处理器
            recovery_handler = self.recovery_handlers[name]
            success = await self._run_recovery_handler(recovery_handler)
            
            if success:
                logger.info(f"✅ Recovery successful for component {name}")
                health.status = ComponentStatus.HEALTHY
                health.error_count = 0
                health.recovery_attempts = 0
                health.last_error = None
                health.next_recovery = None
                
                # 更新统计
                self.recovery_stats['total_recoveries'] += 1
                self.recovery_stats['successful_recoveries'] += 1
                self.recovery_stats['last_recovery_time'] = datetime.now(timezone.utc)
            else:
                # 恢复失败，设置下次恢复时间（指数退避）
                backoff_minutes = (self.recovery_backoff_factor ** health.recovery_attempts) * 5
                health.next_recovery = datetime.now(timezone.utc) + timedelta(minutes=backoff_minutes)
                
                logger.warning(f"❌ Recovery failed for {name}. Next attempt in {backoff_minutes} minutes")
                
                self.recovery_stats['total_recoveries'] += 1
                self.recovery_stats['failed_recoveries'] += 1
                
        except Exception as e:
            error_msg = f"Recovery handler failed: {str(e)}"
            logger.error(f"❌ Recovery error for {name}: {error_msg}")
            health.last_error = error_msg
            
            # 设置下次恢复时间
            backoff_minutes = (self.recovery_backoff_factor ** health.recovery_attempts) * 5
            health.next_recovery = datetime.now(timezone.utc) + timedelta(minutes=backoff_minutes)
    
    async def _run_recovery_handler(self, recovery_handler: Callable) -> bool:
        """执行恢复处理器"""
        try:
            if asyncio.iscoroutinefunction(recovery_handler):
                return await recovery_handler()
            else:
                return recovery_handler()
        except Exception as e:
            logger.error(f"Recovery handler exception: {e}")
            return False
    
    def get_component_status(self, name: str) -> Optional[ComponentHealth]:
        """获取组件状态"""
        return self.components.get(name)
    
    def get_all_status(self) -> Dict[str, Any]:
        """获取所有组件状态"""
        status = {
            'monitoring_active': self.is_running,
            'check_interval': self.check_interval,
            'components': {},
            'statistics': self.recovery_stats.copy(),
            'overall_health': 'healthy'
        }
        
        failed_count = 0
        degraded_count = 0
        
        for name, health in self.components.items():
            status['components'][name] = {
                'status': health.status.value,
                'last_check': health.last_check.isoformat() if health.last_check else None,
                'error_count': health.error_count,
                'last_error': health.last_error,
                'recovery_attempts': health.recovery_attempts,
                'next_recovery': health.next_recovery.isoformat() if health.next_recovery else None
            }
            
            if health.status == ComponentStatus.FAILED:
                failed_count += 1
            elif health.status == ComponentStatus.DEGRADED:
                degraded_count += 1
        
        # 确定整体健康状态
        if failed_count > 0:
            status['overall_health'] = 'critical'
        elif degraded_count > 0:
            status['overall_health'] = 'degraded'
        
        return status
    
    async def force_recovery(self, component_name: str) -> bool:
        """🔧 强制恢复指定组件"""
        if component_name not in self.components:
            logger.error(f"Component {component_name} not found")
            return False
        
        health = self.components[component_name]
        
        # 重置恢复状态
        health.recovery_attempts = 0
        health.next_recovery = None
        
        logger.info(f"🔧 Force recovery triggered for {component_name}")
        
        await self._attempt_recovery(component_name, health)
        
        # 检查恢复结果
        return health.status == ComponentStatus.HEALTHY
    
    async def reset_component(self, component_name: str):
        """🔄 重置组件状态"""
        if component_name not in self.components:
            logger.error(f"Component {component_name} not found")
            return
        
        health = self.components[component_name]
        health.status = ComponentStatus.HEALTHY
        health.error_count = 0
        health.recovery_attempts = 0
        health.last_error = None
        health.next_recovery = None
        
        logger.info(f"🔄 Component {component_name} status reset to healthy")


# 全局自动恢复管理器实例
auto_recovery = AutoRecoveryManager()