"""
Base class for exchange data collectors using official connectors
"""
import asyncio
import json
import time
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Callable, Any, Union
from datetime import datetime, timezone

from loguru import logger

from src.storage.clickhouse import ClickHouseManager
from src.data.validator import DataValidator


class ExchangeCollector(ABC):
    """Base class for exchange WebSocket collectors using official connectors"""
    
    def __init__(self, exchange_id: str, db_manager: ClickHouseManager):
        self.exchange_id = exchange_id
        self.db_manager = db_manager
        
        # Generic connection objects (will be different for each exchange)
        self.websocket_clients: Dict[str, Any] = {}  # Store multiple WS connections
        self.rest_client: Optional[Any] = None       # For REST API calls
        
        # Connection management
        self.is_running = False
        self.connection_tasks: Dict[str, asyncio.Task] = {}
        self.reconnect_delays: Dict[str, float] = {}
        self.connection_health: Dict[str, Dict[str, Any]] = {}  # Health monitoring
        self.last_heartbeat: Dict[str, datetime] = {}  # Last message received per connection
        
        # Statistics
        self.stats = {
            'started_at': None,
            'websocket_connections': 0,
            'messages_received': 0,
            'data_points_stored': 0,
            'errors': 0,
            'reconnects': 0,
            'last_message_time': None
        }
        
        # Configuration
        self.timeframes = ["1m", "5m", "15m", "1h", "4h", "1d"]
        self.symbols: List[str] = []
        self.max_retries = 999999  # Infinite retries for WebSocket connections
        self.base_delay = 1.0
        self.max_symbols_per_connection = 200  # Most exchanges limit this
        
        # Import settings for connection management
        from src.config import settings
        self.max_total_symbols = settings.MAX_TOTAL_SYMBOLS
        self.enable_all_symbols = settings.ENABLE_ALL_SYMBOLS
        
        logger.info(f"Initialized {exchange_id} collector with official connector")
    
    # Abstract methods that each exchange must implement
    @abstractmethod
    async def initialize_clients(self):
        """Initialize both WebSocket and REST clients for the exchange"""
        pass
    
    @abstractmethod
    async def get_futures_symbols(self) -> List[str]:
        """Get list of USDT futures symbols using REST API"""
        pass
    
    @abstractmethod
    def normalize_symbol(self, symbol: str) -> str:
        """Convert standard symbol to exchange-specific format"""
        pass
    
    @abstractmethod
    def parse_ohlcv_message(self, raw_message: Any) -> Optional[Dict[str, Any]]:
        """Parse raw WebSocket message to standardized OHLCV format"""
        pass
    
    @abstractmethod
    async def subscribe_to_ohlcv(self, client_key: str, symbols: List[str], timeframes: List[str]):
        """Subscribe to OHLCV streams for given symbols and timeframes"""
        pass
    
    @abstractmethod
    async def handle_websocket_message(self, client_key: str, message: Any):
        """Handle incoming WebSocket messages"""
        pass
    
    @abstractmethod
    async def create_websocket_connection(self, client_key: str, symbols: List[str]) -> Any:
        """Create a WebSocket connection for given symbols"""
        pass
    
    @abstractmethod
    async def close_websocket_connection(self, client_key: str):
        """Close a specific WebSocket connection"""
        pass
    
    @abstractmethod
    async def get_historical_klines(self, symbol: str, timeframe: str, 
                                   start_time: int, end_time: int, 
                                   limit: int = 1000) -> List[Dict[str, Any]]:
        """Get historical kline data for gap filling"""
        pass
    
    # Common implementation methods
    async def initialize(self):
        """Initialize the collector"""
        try:
            # Initialize REST and WebSocket clients
            await self.initialize_clients()
            
            # Get available symbols
            if not self.symbols:
                logger.info(f"Fetching {self.exchange_id} futures symbols...")
                self.symbols = await self.get_futures_symbols()
                logger.info(f"Found {len(self.symbols)} {self.exchange_id} futures symbols")
            
            # Update database status
            await self.db_manager.update_collector_status(
                collector_id=f"{self.exchange_id}_main",
                exchange=self.exchange_id,
                status="initialized",
                websocket_connections=0
            )
            
            logger.info(f"✅ {self.exchange_id} collector initialized successfully")
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize {self.exchange_id} collector: {e}")
            raise
    
    async def start(self):
        """Start the collector with optimized connection management"""
        if self.is_running:
            logger.warning(f"{self.exchange_id} collector is already running")
            return
        
        self.is_running = True
        self.stats['started_at'] = datetime.now(timezone.utc)
        
        logger.info(f"🚀 Starting {self.exchange_id} collector...")
        
        try:
            # Group symbols into batches for connection management
            symbol_batches = self._create_symbol_batches()
            
            # Create WebSocket connections for each batch
            connection_tasks = []
            for i, symbol_batch in enumerate(symbol_batches):
                client_key = f"client_{i}"
                
                # Create connection task for this batch
                task = asyncio.create_task(
                    self._connection_manager(client_key, symbol_batch)
                )
                connection_tasks.append(task)
                self.connection_tasks[client_key] = task
            
            # Start heartbeat task
            heartbeat_task = asyncio.create_task(self._heartbeat_worker())
            connection_tasks.append(heartbeat_task)
            
            # Start connection health monitor
            health_monitor_task = asyncio.create_task(self._health_monitor())
            connection_tasks.append(health_monitor_task)
            
            # Update status
            await self.db_manager.update_collector_status(
                collector_id=f"{self.exchange_id}_main",
                exchange=self.exchange_id,
                status="running",
                websocket_connections=len(symbol_batches),
                started_at=self.stats['started_at']
            )
            
            logger.info(f"✅ {self.exchange_id} collector started with {len(symbol_batches)} connections")
            
            # Wait for all tasks
            await asyncio.gather(*connection_tasks, return_exceptions=True)
            
        except Exception as e:
            logger.error(f"❌ Error in {self.exchange_id} collector: {e}")
            self.stats['errors'] += 1
        finally:
            self.is_running = False
    
    async def stop(self):
        """Stop the collector"""
        logger.info(f"⏹️ Stopping {self.exchange_id} collector...")
        self.is_running = False
        
        # Cancel all connection tasks
        for client_key, task in self.connection_tasks.items():
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        
        # Close all WebSocket connections
        for client_key in list(self.websocket_clients.keys()):
            try:
                await self.close_websocket_connection(client_key)
            except Exception as e:
                logger.error(f"Error closing connection {client_key}: {e}")
        
        # Clear connection tracking
        self.websocket_clients.clear()
        self.connection_tasks.clear()
        self.reconnect_delays.clear()
        
        # Update status
        await self.db_manager.update_collector_status(
            collector_id=f"{self.exchange_id}_main",
            exchange=self.exchange_id,
            status="stopped",
            websocket_connections=0
        )
        
        logger.info(f"✅ {self.exchange_id} collector stopped")
    
    def _create_symbol_batches(self) -> List[List[str]]:
        """Group symbols into batches for connection management"""
        # Determine how many symbols to use
        if self.enable_all_symbols:
            # Use all available symbols up to the maximum
            symbols_to_use = self.symbols[:self.max_total_symbols] if len(self.symbols) > self.max_total_symbols else self.symbols
            logger.info(f"📈 Using ALL symbols: {len(symbols_to_use)}/{len(self.symbols)} available")
        else:
            # Use first 100 symbols for limited mode
            symbols_to_use = self.symbols[:100] if len(self.symbols) > 100 else self.symbols
            logger.info(f"📊 Using LIMITED symbols: {len(symbols_to_use)}/100 limit")
        
        batches = []
        for i in range(0, len(symbols_to_use), self.max_symbols_per_connection):
            batch = symbols_to_use[i:i + self.max_symbols_per_connection]
            batches.append(batch)
        
        logger.info(f"🔗 Created {len(batches)} WebSocket connection batches for {len(symbols_to_use)} symbols")
        return batches
    
    async def _connection_manager(self, client_key: str, symbols: List[str]):
        """Manage WebSocket connection for a batch of symbols"""
        retry_count = 0
        
        while self.is_running:
            try:
                logger.info(f"🔌 Starting connection {client_key} for {len(symbols)} symbols")
                
                # Create WebSocket connection
                client = await self.create_websocket_connection(client_key, symbols)
                self.websocket_clients[client_key] = client
                
                # Subscribe to OHLCV streams
                await self.subscribe_to_ohlcv(client_key, symbols, self.timeframes)
                self.stats['websocket_connections'] += 1
                
                # Listen for messages
                await self._message_loop(client_key)
                
                # Reset retry count on clean exit
                retry_count = 0
                
            except Exception as e:
                retry_count += 1
                self.stats['errors'] += 1
                
                if not self.is_running:
                    break
                
                if retry_count > self.max_retries:
                    logger.error(f"Max retries exceeded for {client_key}, stopping...")
                    break
                
                # Calculate exponential backoff delay
                delay = min(self.base_delay * (2 ** retry_count), 60)
                self.reconnect_delays[client_key] = delay
                
                logger.warning(f"Connection {client_key} error: {e}, retrying in {delay}s...")
                await asyncio.sleep(delay)
                self.stats['reconnects'] += 1
        
        # Cleanup
        if client_key in self.websocket_clients:
            await self.close_websocket_connection(client_key)
        
        logger.debug(f"Connection manager stopped: {client_key}")
    
    async def _message_loop(self, client_key: str):
        """Message handling loop for a WebSocket connection"""
        client = self.websocket_clients.get(client_key)
        if not client:
            return

        try:
            while self.is_running:
                # Receive message from WebSocket
                raw_message = await client.recv()

                if not raw_message:
                    continue

                # Handle the message
                await self.handle_websocket_message(client_key, raw_message)

        except Exception as e:
            logger.warning(f"Message loop ended for {client_key}: {e}")
            # Don't break here - let connection manager handle reconnection
            raise
    
    async def process_and_store_ohlcv(self, ohlcv_data: Dict[str, Any], client_key: str = "unknown"):
        """Process standardized OHLCV data and store to database"""
        try:
            # Ensure required fields are present
            required_fields = ['exchange', 'symbol', 'timeframe', 'timestamp', 
                              'open', 'high', 'low', 'close', 'volume']
            
            for field in required_fields:
                if field not in ohlcv_data:
                    raise ValueError(f"Missing required field: {field}")
            
            # Store to database (validation happens in ClickHouseManager.insert_ohlcv)
            await self.db_manager.insert_ohlcv(ohlcv_data)
            self.stats['data_points_stored'] += 1
            self.stats['messages_received'] += 1
            self.stats['last_message_time'] = datetime.now(timezone.utc)
            
            # Update connection health
            self.last_heartbeat[client_key] = datetime.now(timezone.utc)
            if client_key not in self.connection_health:
                self.connection_health[client_key] = {
                    'messages_count': 0,
                    'last_message_time': None,
                    'symbols': set(),
                    'status': 'healthy'
                }
            self.connection_health[client_key]['messages_count'] += 1
            self.connection_health[client_key]['last_message_time'] = datetime.now(timezone.utc)
            self.connection_health[client_key]['symbols'].add(ohlcv_data['symbol'])
            
            logger.debug(
                f"📊 Stored: {ohlcv_data['exchange']} {ohlcv_data['symbol']} "
                f"{ohlcv_data['timeframe']} @ {ohlcv_data['close']}"
            )
            
        except Exception as e:
            logger.error(f"Error processing OHLCV data: {e}")
            self.stats['errors'] += 1
    
    async def _heartbeat_worker(self):
        """Send periodic heartbeat updates"""
        while self.is_running:
            try:
                await self.db_manager.update_collector_status(
                    collector_id=f"{self.exchange_id}_main",
                    exchange=self.exchange_id,
                    status="running",
                    websocket_connections=self.stats['websocket_connections'],
                    messages_received=self.stats['messages_received'],
                    errors_count=self.stats['errors']
                )
                
                logger.debug(
                    f"💓 {self.exchange_id} Heartbeat: "
                    f"{self.stats['messages_received']} msgs, "
                    f"{self.stats['websocket_connections']} conns, "
                    f"{self.stats['errors']} errors"
                )
                
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")
            
            await asyncio.sleep(60)  # Every minute
    
    async def _health_monitor(self):
        """Monitor connection health and trigger reconnects if needed"""
        while self.is_running:
            try:
                current_time = datetime.now(timezone.utc)
                unhealthy_connections = []
                
                for client_key, last_heartbeat in self.last_heartbeat.items():
                    # Check if connection is stale (no messages for 5 minutes)
                    time_since_heartbeat = (current_time - last_heartbeat).total_seconds()
                    
                    if time_since_heartbeat > 300:  # 5 minutes
                        logger.warning(f"🔴 Connection {client_key} is unhealthy: {time_since_heartbeat:.0f}s since last message")
                        unhealthy_connections.append(client_key)
                        
                        if client_key in self.connection_health:
                            self.connection_health[client_key]['status'] = 'unhealthy'
                    elif client_key in self.connection_health:
                        self.connection_health[client_key]['status'] = 'healthy'
                
                # Log health summary every 5 minutes
                if len(self.connection_health) > 0:
                    healthy_count = sum(1 for h in self.connection_health.values() if h['status'] == 'healthy')
                    total_count = len(self.connection_health)
                    logger.debug(f"💓 Health Check: {healthy_count}/{total_count} connections healthy")
                
                await asyncio.sleep(60)  # Check every minute
                
            except Exception as e:
                logger.error(f"Error in health monitor: {e}")
                await asyncio.sleep(60)
    
    async def get_status(self) -> Dict[str, Any]:
        """Get collector status"""
        uptime = None
        if self.stats['started_at']:
            uptime = (datetime.now(timezone.utc) - self.stats['started_at']).total_seconds()
        
        # Convert connection health info for serialization
        connection_health_serializable = {}
        for client_key, health in self.connection_health.items():
            connection_health_serializable[client_key] = {
                'messages_count': health['messages_count'],
                'last_message_time': health['last_message_time'].isoformat() if health['last_message_time'] else None,
                'symbols_count': len(health['symbols']),
                'status': health['status']
            }
        
        return {
            'exchange': self.exchange_id,
            'running': self.is_running,
            'symbols_count': len(self.symbols),
            'timeframes_count': len(self.timeframes),
            'connections_count': len(self.websocket_clients),
            'active_connections': list(self.websocket_clients.keys()),
            'uptime_seconds': uptime,
            'stats': self.stats.copy(),
            'reconnect_delays': self.reconnect_delays.copy(),
            'connection_health': connection_health_serializable,
            'config': {
                'max_total_symbols': self.max_total_symbols,
                'enable_all_symbols': self.enable_all_symbols,
                'max_symbols_per_connection': self.max_symbols_per_connection
            }
        }
    
    def get_standard_symbol_format(self, exchange_symbol: str) -> str:
        """Convert exchange-specific symbol back to standard format (BASE/QUOTE)"""
        # This is a default implementation, exchanges should override if needed
        if '/' in exchange_symbol:
            return exchange_symbol
        
        # Common pattern: BTCUSDT -> BTC/USDT
        if exchange_symbol.endswith('USDT'):
            base = exchange_symbol[:-4]
            return f"{base}/USDT"
        
        return exchange_symbol
    
    async def add_symbols(self, new_symbols: List[str]):
        """Add new symbols to the collector"""
        added_symbols = []
        for symbol in new_symbols:
            if symbol not in self.symbols:
                self.symbols.append(symbol)
                added_symbols.append(symbol)
        
        if added_symbols:
            logger.info(f"Added {len(added_symbols)} symbols to {self.exchange_id} collector")
            
        return added_symbols
    
    async def remove_symbols(self, symbols_to_remove: List[str]):
        """Remove symbols from the collector"""
        removed_symbols = []
        for symbol in symbols_to_remove:
            if symbol in self.symbols:
                self.symbols.remove(symbol)
                removed_symbols.append(symbol)
        
        if removed_symbols:
            logger.info(f"Removed {len(removed_symbols)} symbols from {self.exchange_id} collector")
            
        return removed_symbols