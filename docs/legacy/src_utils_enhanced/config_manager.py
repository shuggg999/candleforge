"""
配置管理器 - 支持热重载的YAML配置系统
"""
import asyncio
import yaml
from pathlib import Path
from typing import Dict, Any, Optional, Callable
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from loguru import logger
import time


class ConfigChangeHandler(FileSystemEventHandler):
    """配置文件变化处理器"""
    
    def __init__(self, config_manager: 'ConfigManager', debounce_seconds: float = 2.0):
        self.config_manager = config_manager
        self.debounce_seconds = debounce_seconds
        self.last_reload_time = 0
        
    def on_modified(self, event):
        """文件修改事件处理"""
        if event.is_directory:
            return
            
        if not event.src_path.endswith('.yml') and not event.src_path.endswith('.yaml'):
            return
            
        # 防抖动：避免频繁重载
        now = time.time()
        if now - self.last_reload_time < self.debounce_seconds:
            return
            
        self.last_reload_time = now
        
        logger.info(f"配置文件变化: {event.src_path}")
        asyncio.create_task(self.config_manager._reload_config())


class ConfigManager:
    """配置管理器 - 支持热重载"""
    
    def __init__(self, config_path: str = "config/enhanced/collectors.yml"):
        self.config_path = Path(config_path)
        self.config_data: Dict[str, Any] = {}
        self.observers: list = []
        self.change_callbacks: list[Callable] = []
        
        # 热重载相关
        self.hot_reload_enabled = False
        self.file_observer: Optional[Observer] = None
        
    async def initialize(self):
        """初始化配置管理器"""
        await self.load_config()
        
        # 启用热重载
        if self.config_data.get('hot_reload', {}).get('enabled', False):
            await self.enable_hot_reload()
            
    async def load_config(self):
        """加载配置文件"""
        try:
            if not self.config_path.exists():
                logger.warning(f"配置文件不存在: {self.config_path}")
                self.config_data = self._get_default_config()
                return
                
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self.config_data = yaml.safe_load(f)
                
            logger.info(f"配置加载成功: {self.config_path}")
            
        except Exception as e:
            logger.error(f"配置加载失败: {e}")
            self.config_data = self._get_default_config()
            
    async def enable_hot_reload(self):
        """启用热重载"""
        if self.file_observer is not None:
            return
            
        try:
            handler = ConfigChangeHandler(self)
            self.file_observer = Observer()
            
            # 监控配置文件目录
            config_dir = self.config_path.parent
            self.file_observer.schedule(handler, str(config_dir), recursive=True)
            self.file_observer.start()
            
            self.hot_reload_enabled = True
            logger.info(f"热重载已启用: {config_dir}")
            
        except Exception as e:
            logger.error(f"热重载启用失败: {e}")
            
    async def _reload_config(self):
        """重新加载配置"""
        logger.info("开始重新加载配置...")
        old_config = self.config_data.copy()
        
        await self.load_config()
        
        # 检查配置是否有变化
        if old_config != self.config_data:
            logger.info("配置已更新，通知观察者")
            await self._notify_change_callbacks()
        else:
            logger.debug("配置无变化")
            
    async def _notify_change_callbacks(self):
        """通知配置变化回调"""
        for callback in self.change_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(self.config_data)
                else:
                    callback(self.config_data)
            except Exception as e:
                logger.error(f"配置变化回调执行失败: {e}")
                
    def add_change_callback(self, callback: Callable):
        """添加配置变化回调"""
        self.change_callbacks.append(callback)
        
    def remove_change_callback(self, callback: Callable):
        """移除配置变化回调"""
        if callback in self.change_callbacks:
            self.change_callbacks.remove(callback)
            
    def get(self, key: str, default: Any = None) -> Any:
        """获取配置值 - 支持点号分隔的嵌套键"""
        keys = key.split('.')
        value = self.config_data
        
        try:
            for k in keys:
                value = value[k]
            return value
        except (KeyError, TypeError):
            return default
            
    def get_collector_config(self, exchange: str) -> Dict[str, Any]:
        """获取特定交易所的配置"""
        return self.get(f'collectors.{exchange}', {})
        
    def get_validation_config(self) -> Dict[str, Any]:
        """获取数据验证配置"""
        return self.get('data_validation', {})
        
    def get_concurrency_config(self) -> Dict[str, Any]:
        """获取并发控制配置"""
        return self.get('concurrency', {})
        
    def get_monitoring_config(self) -> Dict[str, Any]:
        """获取监控配置"""
        return self.get('monitoring', {})
        
    def is_collector_enabled(self, exchange: str) -> bool:
        """检查收集器是否启用"""
        return self.get(f'collectors.{exchange}.enabled', False)
        
    async def cleanup(self):
        """清理资源"""
        if self.file_observer:
            self.file_observer.stop()
            self.file_observer.join()
            self.file_observer = None
            
        self.hot_reload_enabled = False
        logger.info("配置管理器已清理")
        
    def _get_default_config(self) -> Dict[str, Any]:
        """获取默认配置"""
        return {
            'collectors': {
                'binance': {
                    'enabled': True,
                    'config': {
                        'max_concurrent': 10,
                        'max_retry_count': 3,
                        'delay_between_requests': 0.1,
                        'rate_limit': 10.0
                    }
                }
            },
            'data_validation': {
                'rules': []
            },
            'concurrency': {
                'global_max_concurrent': 50,
                'adaptive': True
            },
            'monitoring': {
                'enabled': False
            },
            'hot_reload': {
                'enabled': False
            }
        }


# 全局配置管理器实例
config_manager = ConfigManager()