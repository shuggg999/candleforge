"""
Collector manager for orchestrating data collection across multiple exchanges
"""
import asyncio
from typing import Dict, List, Optional, Any
from loguru import logger

from src.config import settings
from src.storage.clickhouse import ClickHouseManager
from .base import ExchangeCollector
from .binance import BinanceCollector


class CollectorManager:
    """Manages all exchange collectors"""
    
    def __init__(self, db_manager: ClickHouseManager):
        self.db_manager = db_manager
        self.collectors: Dict[str, ExchangeCollector] = {}
        self.tasks: Dict[str, asyncio.Task] = {}
        self.is_running = False
    
    async def initialize(self):
        """Initialize all enabled collectors"""
        logger.info("🔧 Initializing collectors...")
        
        # Create collectors for enabled exchanges
        for exchange_id in settings.ENABLED_EXCHANGES:
            try:
                collector = await self._create_collector(exchange_id)
                if collector:
                    await collector.initialize()
                    self.collectors[exchange_id] = collector
                    logger.info(f"✅ {exchange_id.capitalize()} collector initialized")
                else:
                    logger.warning(f"❌ Unsupported exchange: {exchange_id}")
            except Exception as e:
                logger.error(f"❌ Failed to initialize {exchange_id} collector: {e}")
    
    async def _create_collector(self, exchange_id: str) -> Optional[ExchangeCollector]:
        """Create collector instance for the specified exchange"""
        collectors_map = {
            'binance': BinanceCollector,
            # TODO: Add OKX and Bybit collectors
            # 'okx': OKXCollector,
            # 'bybit': BybitCollector,
        }
        
        if exchange_id in collectors_map:
            return collectors_map[exchange_id](self.db_manager)
        
        return None
    
    async def start_all(self):
        """Start all collectors"""
        if self.is_running:
            logger.warning("Collectors are already running")
            return
        
        self.is_running = True
        logger.info("▶️ Starting all collectors...")
        
        # Start each collector in a separate task
        for exchange_id, collector in self.collectors.items():
            try:
                task = asyncio.create_task(collector.start())
                self.tasks[exchange_id] = task
                logger.info(f"🚀 Started {exchange_id} collector")
            except Exception as e:
                logger.error(f"❌ Failed to start {exchange_id} collector: {e}")
        
        logger.info(f"✅ Started {len(self.tasks)} collectors")
    
    async def stop_all(self):
        """Stop all collectors"""
        if not self.is_running:
            logger.warning("Collectors are not running")
            return
        
        logger.info("⏹️ Stopping all collectors...")
        self.is_running = False
        
        # Stop all collectors
        stop_tasks = []
        for exchange_id, collector in self.collectors.items():
            stop_tasks.append(collector.stop())
        
        if stop_tasks:
            await asyncio.gather(*stop_tasks, return_exceptions=True)
        
        # Cancel running tasks
        for exchange_id, task in self.tasks.items():
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        
        self.tasks.clear()
        logger.info("✅ All collectors stopped")
    
    async def restart_collector(self, exchange_id: str):
        """Restart a specific collector"""
        if exchange_id not in self.collectors:
            logger.error(f"Collector {exchange_id} not found")
            return
        
        logger.info(f"🔄 Restarting {exchange_id} collector...")
        
        # Stop the collector
        collector = self.collectors[exchange_id]
        await collector.stop()
        
        # Cancel existing task
        if exchange_id in self.tasks:
            task = self.tasks[exchange_id]
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            del self.tasks[exchange_id]
        
        # Start again
        try:
            await collector.initialize()
            task = asyncio.create_task(collector.start())
            self.tasks[exchange_id] = task
            logger.info(f"✅ {exchange_id} collector restarted")
        except Exception as e:
            logger.error(f"❌ Failed to restart {exchange_id} collector: {e}")
    
    async def get_status(self) -> Dict[str, Any]:
        """Get status of all collectors"""
        status = {
            'running': self.is_running,
            'collectors_count': len(self.collectors),
            'collectors': {}
        }
        
        # Get status from each collector
        for exchange_id, collector in self.collectors.items():
            try:
                collector_status = await collector.get_status()
                status['collectors'][exchange_id] = collector_status
            except Exception as e:
                logger.error(f"Error getting status for {exchange_id}: {e}")
                status['collectors'][exchange_id] = {
                    'error': str(e),
                    'running': False
                }
        
        return status
    
    async def get_collector_symbols(self, exchange_id: str) -> List[str]:
        """Get symbols being tracked by a specific collector"""
        if exchange_id in self.collectors:
            return self.collectors[exchange_id].symbols
        return []
    
    async def add_symbol(self, exchange_id: str, symbol: str):
        """Add a symbol to a specific collector"""
        if exchange_id not in self.collectors:
            logger.error(f"Collector {exchange_id} not found")
            return
        
        collector = self.collectors[exchange_id]
        if symbol not in collector.symbols:
            collector.symbols.append(symbol)
            logger.info(f"➕ Added {symbol} to {exchange_id} collector")
    
    async def remove_symbol(self, exchange_id: str, symbol: str):
        """Remove a symbol from a specific collector"""
        if exchange_id not in self.collectors:
            logger.error(f"Collector {exchange_id} not found")
            return
        
        collector = self.collectors[exchange_id]
        if symbol in collector.symbols:
            collector.symbols.remove(symbol)
            logger.info(f"➖ Removed {symbol} from {exchange_id} collector")
    
    def get_collector(self, exchange_id: str) -> Optional[ExchangeCollector]:
        """Get a specific collector instance"""
        return self.collectors.get(exchange_id)
    
    async def get_statistics(self) -> Dict[str, Any]:
        """Get aggregated statistics from all collectors"""
        stats = {
            'total_messages': 0,
            'total_errors': 0,
            'total_reconnects': 0,
            'total_data_points': 0,
            'collectors': {}
        }
        
        for exchange_id, collector in self.collectors.items():
            try:
                collector_stats = await collector.get_status()
                
                # Aggregate statistics
                collector_data = collector_stats.get('stats', {})
                stats['total_messages'] += collector_data.get('messages_received', 0)
                stats['total_errors'] += collector_data.get('errors', 0)
                stats['total_reconnects'] += collector_data.get('reconnects', 0)
                stats['total_data_points'] += collector_data.get('data_points_stored', 0)
                
                stats['collectors'][exchange_id] = collector_stats
                
            except Exception as e:
                logger.error(f"Error getting statistics for {exchange_id}: {e}")
        
        return stats