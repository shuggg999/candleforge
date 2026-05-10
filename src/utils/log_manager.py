"""
🗂️ 日志管理工具 - 实现日志轮转和自动清理
"""
import os
import gzip
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
import shutil
from loguru import logger


class LogManager:
    """日志轮转和清理管理器"""
    
    def __init__(self, 
                 logs_dir: str = "logs",
                 max_log_size: int = 50 * 1024 * 1024,  # 50MB
                 max_log_files: int = 7,  # 保留7个日志文件
                 max_age_days: int = 30,  # 保留30天
                 compression: bool = True):
        """
        初始化日志管理器
        
        Args:
            logs_dir: 日志目录
            max_log_size: 单个日志文件最大大小 (bytes)
            max_log_files: 每个日志类型保留的文件数量
            max_age_days: 日志保留天数
            compression: 是否压缩旧日志文件
        """
        self.logs_dir = Path(logs_dir)
        self.max_log_size = max_log_size
        self.max_log_files = max_log_files
        self.max_age_days = max_age_days
        self.compression = compression
        
        # 创建日志目录
        self.logs_dir.mkdir(exist_ok=True)
        
        # 日志类型和文件名映射
        self.log_patterns = {
            'service': 'data_service*.log',  # 修复：匹配实际文件名
            'access': 'access*.log',
            'error': 'error*.log',
            'collector': 'collector*.log',
            'recovery': 'recovery*.log'
        }
    
    async def rotate_logs(self) -> Dict[str, int]:
        """🔄 执行日志轮转"""
        rotation_stats = {
            'rotated_files': 0,
            'compressed_files': 0,
            'cleaned_files': 0,
            'total_size_freed': 0
        }
        
        try:
            logger.info("🗂️ Starting log rotation...")
            
            # 为每种日志类型执行轮转
            for log_type, pattern in self.log_patterns.items():
                stats = await self._rotate_log_type(log_type, pattern)
                
                rotation_stats['rotated_files'] += stats['rotated']
                rotation_stats['compressed_files'] += stats['compressed']
                rotation_stats['cleaned_files'] += stats['cleaned']
                rotation_stats['total_size_freed'] += stats['size_freed']
            
            # 清理过期日志
            await self._cleanup_old_logs()
            
            logger.info(f"✅ Log rotation completed: {rotation_stats}")
            return rotation_stats
            
        except Exception as e:
            logger.error(f"❌ Log rotation failed: {e}")
            return rotation_stats
    
    async def _rotate_log_type(self, log_type: str, pattern: str) -> Dict[str, int]:
        """轮转特定类型的日志文件"""
        stats = {'rotated': 0, 'compressed': 0, 'cleaned': 0, 'size_freed': 0}
        
        try:
            # 查找匹配的日志文件
            log_files = list(self.logs_dir.glob(pattern))
            
            for log_file in log_files:
                # 检查文件大小
                if log_file.stat().st_size > self.max_log_size:
                    await self._rotate_file(log_file)
                    stats['rotated'] += 1
                
                # 压缩旧的日志文件
                if self.compression and self._should_compress(log_file):
                    compressed_size = await self._compress_file(log_file)
                    if compressed_size > 0:
                        stats['compressed'] += 1
                        stats['size_freed'] += compressed_size
            
            # 清理过多的日志文件
            cleaned = await self._cleanup_excess_files(log_type)
            stats['cleaned'] += cleaned
            
        except Exception as e:
            logger.error(f"Error rotating {log_type} logs: {e}")
        
        return stats
    
    async def _rotate_file(self, log_file: Path):
        """轮转单个日志文件"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            rotated_name = f"{log_file.stem}_{timestamp}{log_file.suffix}"
            rotated_path = log_file.parent / rotated_name
            
            # 移动当前文件到轮转文件名
            shutil.move(str(log_file), str(rotated_path))
            
            # 创建新的空日志文件
            log_file.touch()
            
            logger.info(f"📁 Rotated log: {log_file.name} -> {rotated_name}")
            
        except Exception as e:
            logger.error(f"Failed to rotate {log_file}: {e}")
    
    def _should_compress(self, log_file: Path) -> bool:
        """判断是否需要压缩文件"""
        # 不压缩当前活跃的日志文件
        if not ('_' in log_file.stem and any(char.isdigit() for char in log_file.stem)):
            return False
        
        # 不压缩已经压缩的文件
        if log_file.suffix == '.gz':
            return False
        
        # 压缩超过1天的日志文件
        file_time = datetime.fromtimestamp(log_file.stat().st_mtime)
        return datetime.now() - file_time > timedelta(days=1)
    
    async def _compress_file(self, log_file: Path) -> int:
        """压缩日志文件，返回节省的空间"""
        try:
            original_size = log_file.stat().st_size
            compressed_path = log_file.with_suffix(log_file.suffix + '.gz')
            
            # 压缩文件
            with open(log_file, 'rb') as f_in:
                with gzip.open(compressed_path, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
            
            # 删除原文件
            log_file.unlink()
            
            compressed_size = compressed_path.stat().st_size
            saved_space = original_size - compressed_size
            
            logger.info(f"🗜️ Compressed {log_file.name}: {original_size:,} -> {compressed_size:,} bytes (saved {saved_space:,})")
            return saved_space
            
        except Exception as e:
            logger.error(f"Failed to compress {log_file}: {e}")
            return 0
    
    async def _cleanup_excess_files(self, log_type: str) -> int:
        """清理过多的日志文件"""
        try:
            pattern = self.log_patterns[log_type]
            log_files = sorted(
                [f for f in self.logs_dir.glob(pattern) if '_' in f.stem],
                key=lambda x: x.stat().st_mtime,
                reverse=True
            )
            
            cleaned_count = 0
            
            # 保留最新的N个文件，删除其余的
            if len(log_files) > self.max_log_files:
                files_to_remove = log_files[self.max_log_files:]
                
                for file_to_remove in files_to_remove:
                    try:
                        file_to_remove.unlink()
                        cleaned_count += 1
                        logger.info(f"🗑️ Cleaned old log: {file_to_remove.name}")
                    except Exception as e:
                        logger.error(f"Failed to remove {file_to_remove}: {e}")
            
            return cleaned_count
            
        except Exception as e:
            logger.error(f"Failed to cleanup {log_type} logs: {e}")
            return 0
    
    async def _cleanup_old_logs(self):
        """清理过期的日志文件"""
        try:
            cutoff_date = datetime.now() - timedelta(days=self.max_age_days)
            
            all_log_files = []
            for pattern in self.log_patterns.values():
                all_log_files.extend(self.logs_dir.glob(pattern))
            
            cleaned_count = 0
            for log_file in all_log_files:
                file_time = datetime.fromtimestamp(log_file.stat().st_mtime)
                
                if file_time < cutoff_date:
                    try:
                        log_file.unlink()
                        cleaned_count += 1
                        logger.info(f"🗑️ Removed expired log: {log_file.name}")
                    except Exception as e:
                        logger.error(f"Failed to remove expired log {log_file}: {e}")
            
            if cleaned_count > 0:
                logger.info(f"🧹 Cleaned {cleaned_count} expired log files older than {self.max_age_days} days")
                
        except Exception as e:
            logger.error(f"Failed to cleanup old logs: {e}")
    
    async def get_log_stats(self) -> Dict[str, any]:
        """📊 获取日志统计信息"""
        try:
            stats = {
                'total_files': 0,
                'total_size': 0,
                'compressed_files': 0,
                'by_type': {}
            }
            
            for log_type, pattern in self.log_patterns.items():
                type_files = list(self.logs_dir.glob(pattern))
                type_size = sum(f.stat().st_size for f in type_files)
                compressed_count = len([f for f in type_files if f.suffix == '.gz'])
                
                stats['by_type'][log_type] = {
                    'files': len(type_files),
                    'size': type_size,
                    'compressed': compressed_count
                }
                
                stats['total_files'] += len(type_files)
                stats['total_size'] += type_size
                stats['compressed_files'] += compressed_count
            
            return stats
            
        except Exception as e:
            logger.error(f"Failed to get log stats: {e}")
            return {}
    
    async def schedule_rotation(self, interval_hours: int = 24):
        """📅 定期执行日志轮转"""
        logger.info(f"🕒 Scheduling log rotation every {interval_hours} hours")
        
        while True:
            try:
                await asyncio.sleep(interval_hours * 3600)  # 转换为秒
                await self.rotate_logs()
            except asyncio.CancelledError:
                logger.info("📅 Log rotation scheduler stopped")
                break
            except Exception as e:
                logger.error(f"Error in log rotation scheduler: {e}")
                await asyncio.sleep(600)  # 等待10分钟后重试


# 全局日志管理器实例
log_manager = LogManager()