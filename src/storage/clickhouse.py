"""
ClickHouse database manager for cryptocurrency data storage
"""
import asyncio
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timezone, timedelta
import zoneinfo
import json

from aiochclient import ChClient
from aiohttp import ClientSession, ClientTimeout
import aiohttp
from loguru import logger

from src.config import settings
from src.data.validator import DataValidator


class ClickHouseManager:
    """Manages ClickHouse database operations for cryptocurrency data"""
    
    def __init__(self):
        self.client: Optional[ChClient] = None
        self.session: Optional[ClientSession] = None
        self.is_connected = False
        
        # 🚀 Optimized batch processing settings
        self._write_queue = asyncio.Queue(maxsize=20000)  # Increased queue size
        self._batch_size = 1000  # Larger batch size for better throughput
        self._batch_timeout = 1.5   # Optimized timeout
        self._max_concurrent_batches = 3  # Allow concurrent batch processing
        self._write_task: Optional[asyncio.Task] = None
        self._batch_semaphore = asyncio.Semaphore(self._max_concurrent_batches)
        
        # Performance metrics
        self._batch_stats = {
            'total_batches': 0,
            'total_records': 0,
            'failed_batches': 0,
            'avg_batch_time': 0.0,
            'last_batch_time': None
        }
        
        # Connection settings with optimization
        self._url = f"http://{settings.CLICKHOUSE_HOST}:{settings.CLICKHOUSE_PORT}"
        self._database = settings.CLICKHOUSE_DATABASE
        self._user = settings.CLICKHOUSE_USER
        self._password = settings.CLICKHOUSE_PASSWORD
        
        # Debug logging for connection parameters
        logger.info(f"🔗 ClickHouse connection URL: {self._url}")
        logger.info(f"🔗 Database: {self._database}, User: {self._user}")
        
        # Time zone settings
        self._beijing_tz = zoneinfo.ZoneInfo("Asia/Shanghai")
        
        # 🔧 Connection pool optimization
        self._connection_pool_size = 10
        self._compression_enabled = True
    
    def _convert_to_beijing_time(self, dt: datetime) -> datetime:
        """Convert datetime to Beijing time (Asia/Shanghai)"""
        if dt.tzinfo is None:
            # Assume UTC if no timezone info
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(self._beijing_tz)
    
    def _format_beijing_time(self, dt: datetime) -> str:
        """Format datetime as Beijing time string for display"""
        beijing_dt = self._convert_to_beijing_time(dt)
        return beijing_dt.strftime('%Y-%m-%d %H:%M:%S')
    
    async def initialize(self):
        """Initialize ClickHouse connection and start batch writer"""
        try:
            # 🚀 Create optimized HTTP session with connection pooling
            timeout = ClientTimeout(total=45, connect=15, sock_read=30)
            connector = aiohttp.TCPConnector(
                limit=self._connection_pool_size,
                limit_per_host=self._connection_pool_size,
                enable_cleanup_closed=True,
                keepalive_timeout=60,
                ttl_dns_cache=300
            )
            self.session = ClientSession(
                timeout=timeout,
                connector=connector,
                headers={'User-Agent': 'CryptoDataService/1.0'}
            )
            
            # 🔧 Create optimized ClickHouse client
            # Use explicit URL format for aiochclient 
            clickhouse_url = f"http://{settings.CLICKHOUSE_HOST}:{settings.CLICKHOUSE_PORT}/"
            logger.info(f"🔗 Connecting to ClickHouse at: {clickhouse_url}")
            
            self.client = ChClient(
                session=self.session,
                url=clickhouse_url,
                user=self._user,
                password=self._password,
                database=self._database,
                compress_response=self._compression_enabled
            )
            
            # Test connection
            await self.health_check()
            
            # Ensure database and tables exist
            await self._ensure_database_schema()
            
            # Start batch writer
            self._write_task = asyncio.create_task(self._batch_writer())
            
            self.is_connected = True
            logger.info("✅ ClickHouse connection initialized")
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize ClickHouse: {e}")
            await self.close()
            raise
    
    async def close(self):
        """Close ClickHouse connection and cleanup"""
        self.is_connected = False
        
        if self._write_task and not self._write_task.done():
            self._write_task.cancel()
            try:
                await self._write_task
            except asyncio.CancelledError:
                pass
        
        if self.client:
            self.client = None
        
        if self.session:
            await self.session.close()
            self.session = None
        
        logger.info("👋 ClickHouse connection closed")
    
    async def health_check(self) -> str:
        """🏥 Check database connectivity with robust health check"""
        try:
            if not self.client:
                logger.warning("ClickHouse health check: client not initialized")
                return "disconnected"
            
            # Use a more reliable query that should return data
            result = await self.client.execute("SELECT count() FROM system.databases WHERE name = 'crypto_data'")
            logger.debug(f"ClickHouse health check result: {result}")
            
            # If we can execute the query without exception, connection is healthy
            # Even if result is None/empty, the fact we got here means connection works
            logger.debug("✅ ClickHouse health check: healthy (query executed successfully)")
            return "healthy"
                
        except Exception as e:
            logger.error(f"❌ ClickHouse health check failed: {e}")
            return "unhealthy"
    
    async def _ensure_database_schema(self):
        """Ensure database and tables exist"""
        if not self.client:
            raise RuntimeError("ClickHouse client not initialized")
        
        try:
            # Create database if not exists
            await self.client.execute(f"CREATE DATABASE IF NOT EXISTS {self._database}")
            
            # Read and execute schema
            with open("config/clickhouse/init.sql", 'r') as f:
                schema_sql = f.read()
            
            # Split and execute each statement
            statements = [stmt.strip() for stmt in schema_sql.split(';') if stmt.strip()]
            for statement in statements:
                # Skip comments and empty lines
                if statement and not statement.startswith('--'):
                    await self.client.execute(statement)
            
            logger.info("📋 Database schema verified/created")
            
        except Exception as e:
            logger.error(f"❌ Failed to ensure database schema: {e}")
            raise
    
    async def insert_ohlcv(self, data: Dict[str, Any]):
        """Queue OHLCV data for batch insertion"""
        try:
            # Validate data before queuing
            validated_data = DataValidator.validate_ohlcv(data)
            
            # Convert timestamp to Beijing time
            if 'timestamp' in validated_data and isinstance(validated_data['timestamp'], datetime):
                validated_data['timestamp'] = self._convert_to_beijing_time(validated_data['timestamp'])
            
            # Add to write queue (non-blocking)
            try:
                self._write_queue.put_nowait(validated_data)
            except asyncio.QueueFull:
                logger.warning("Write queue is full, dropping data point")
                
        except Exception as e:
            logger.error(f"Error queuing OHLCV data: {e}")
    
    async def _batch_writer(self):
        """🚀 Optimized background task to batch write data to ClickHouse"""
        logger.info(f"📝 Starting optimized ClickHouse batch writer (max_batches={self._max_concurrent_batches}, batch_size={self._batch_size})")
        
        batch = []
        last_write = datetime.now()
        pending_writes = set()
        
        while self.is_connected:
            try:
                # Collect data into batch
                try:
                    data = await asyncio.wait_for(self._write_queue.get(), timeout=0.5)
                    batch.append(data)
                except asyncio.TimeoutError:
                    data = None
                
                # Check if we should write batch
                now = datetime.now()
                should_write = (
                    len(batch) >= self._batch_size or
                    (batch and (now - last_write).total_seconds() >= self._batch_timeout)
                )
                
                if should_write and batch:
                    # Create a copy of the batch for async processing
                    batch_copy = batch.copy()
                    batch.clear()
                    last_write = now
                    
                    # Submit batch for async processing
                    write_task = asyncio.create_task(self._write_batch_async(batch_copy))
                    pending_writes.add(write_task)
                    write_task.add_done_callback(pending_writes.discard)
                
                # Clean up completed write tasks
                if len(pending_writes) >= self._max_concurrent_batches:
                    # Wait for at least one batch to complete before continuing
                    done, pending = await asyncio.wait(
                        pending_writes, 
                        return_when=asyncio.FIRST_COMPLETED,
                        timeout=0.1
                    )
                    pending_writes = pending
                    
            except asyncio.CancelledError:
                # Write remaining data before cancelling
                if batch:
                    await self._write_batch(batch)
                # Wait for all pending writes to complete
                if pending_writes:
                    await asyncio.gather(*pending_writes, return_exceptions=True)
                break
            except Exception as e:
                logger.error(f"Error in batch writer: {e}")
                await asyncio.sleep(1)
        
        logger.info("📝 ClickHouse batch writer stopped")
    
    async def _write_batch_async(self, batch: List[Dict[str, Any]]):
        """🚀 Async batch writer with concurrency control"""
        async with self._batch_semaphore:
            await self._write_batch(batch)
    
    async def _write_batch(self, batch: List[Dict[str, Any]]):
        """📊 Optimized batch writer with performance tracking"""
        if not batch or not self.client:
            return
        
        batch_start = datetime.now()
        batch_size = len(batch)
        
        try:
            # 🔧 Prepare optimized batch for insertion
            values = []
            for item in batch:
                # Ensure timestamps are in the correct format
                timestamp = item['timestamp']
                if hasattr(timestamp, 'replace'):
                    timestamp = timestamp.replace(tzinfo=None, microsecond=timestamp.microsecond // 1000 * 1000)
                
                created_at = item['created_at']
                if hasattr(created_at, 'replace'):
                    # For created_at (DateTime), remove microseconds completely
                    created_at = created_at.replace(tzinfo=None, microsecond=0)
                
                values.append((
                    item['exchange'],
                    item['symbol'],
                    item['timeframe'],
                    timestamp,
                    item['open'],
                    item['high'],
                    item['low'],
                    item['close'],
                    item['volume'],
                    item.get('turnover', 0),
                    item.get('open_interest'),
                    item.get('funding_rate'),
                    item.get('trades_count'),
                    item.get('buy_volume'),
                    created_at,
                    item.get('data_quality', 'raw')
                ))
            
            # 🚀 Execute optimized batch insert with compression
            await self.client.execute(
                """
                INSERT INTO ohlcv_futures (
                    exchange, symbol, timeframe, timestamp, open, high, low, close,
                    volume, turnover, open_interest, funding_rate, trades_count,
                    buy_volume, created_at, data_quality
                ) VALUES
                """,
                *values
            )
            
            # 📈 Update performance metrics
            batch_duration = (datetime.now() - batch_start).total_seconds()
            self._batch_stats['total_batches'] += 1
            self._batch_stats['total_records'] += batch_size
            self._batch_stats['last_batch_time'] = batch_start
            
            # Update rolling average batch time
            if self._batch_stats['avg_batch_time'] == 0:
                self._batch_stats['avg_batch_time'] = batch_duration
            else:
                # Exponential moving average
                alpha = 0.1
                self._batch_stats['avg_batch_time'] = (
                    alpha * batch_duration + 
                    (1 - alpha) * self._batch_stats['avg_batch_time']
                )
            
            logger.debug(
                f"📊 Batch written: {batch_size} records in {batch_duration:.3f}s "
                f"(avg: {self._batch_stats['avg_batch_time']:.3f}s, "
                f"total_batches: {self._batch_stats['total_batches']})"
            )
            
        except Exception as e:
            self._batch_stats['failed_batches'] += 1
            logger.error(f"❌ Failed to write batch ({batch_size} records): {e}")
            
            # 🔄 Implement exponential backoff retry for failed batches
            if self._batch_stats['failed_batches'] < 3:  # Max 3 retries
                retry_delay = min(2 ** self._batch_stats['failed_batches'], 10)
                logger.info(f"🔄 Retrying batch write in {retry_delay}s...")
                await asyncio.sleep(retry_delay)
                # Reset failed count on successful retry
                try:
                    await self._write_batch(batch)
                    self._batch_stats['failed_batches'] = 0  # Reset on success
                except Exception as retry_e:
                    logger.error(f"❌ Batch retry failed: {retry_e}")
    
    async def get_latest_timestamp(self, exchange: str, symbol: str, timeframe: str) -> Optional[datetime]:
        """Get the latest timestamp for a specific symbol"""
        try:
            if not self.client:
                return None
            
            query = """
                SELECT MAX(timestamp) as latest
                FROM ohlcv_futures
                WHERE exchange = %s AND symbol = %s AND timeframe = %s
            """
            
            result = await self.client.execute(query.replace('%s', '{}').format(exchange, symbol, timeframe))
            
            if result and len(result) > 0 and result[0][0]:
                return result[0][0]
            
            return None
            
        except Exception as e:
            logger.error(f"Error getting latest timestamp: {e}")
            return None
    
    async def get_ohlcv_data(self, exchange: str, symbol: str, timeframe: str,
                           start_time: Optional[datetime] = None,
                           end_time: Optional[datetime] = None,
                           limit: int = 1000) -> List[Dict[str, Any]]:
        """Get OHLCV data from database"""
        try:
            if not self.client:
                return []
            
            # Build query conditions
            conditions = ["exchange = %s", "symbol = %s", "timeframe = %s"]
            params = [exchange, symbol, timeframe]
            
            if start_time:
                conditions.append("timestamp >= %s")
                params.append(start_time)
            
            if end_time:
                conditions.append("timestamp <= %s")
                params.append(end_time)
            
            query = f"""
                SELECT 
                    timestamp, open, high, low, close, volume, turnover,
                    open_interest, funding_rate, trades_count, buy_volume,
                    data_quality
                FROM ohlcv_futures
                WHERE {' AND '.join(conditions)}
                ORDER BY timestamp ASC
                LIMIT {limit}
            """
            
            if params:
                formatted_query = query
                for param in params:
                    if isinstance(param, str):
                        formatted_query = formatted_query.replace('%s', f"'{param}'", 1)
                    else:
                        formatted_query = formatted_query.replace('%s', str(param), 1)
                result = await self.client.execute(formatted_query)
            else:
                result = await self.client.execute(query)
            
            # Convert to dictionaries
            data = []
            for row in result:
                data.append({
                    'timestamp': row[0],
                    'open': float(row[1]),
                    'high': float(row[2]),
                    'low': float(row[3]),
                    'close': float(row[4]),
                    'volume': float(row[5]),
                    'turnover': float(row[6]) if row[6] else 0.0,
                    'open_interest': float(row[7]) if row[7] else None,
                    'funding_rate': float(row[8]) if row[8] else None,
                    'trades_count': int(row[9]) if row[9] else None,
                    'buy_volume': float(row[10]) if row[10] else None,
                    'data_quality': row[11]
                })
            
            return data
            
        except Exception as e:
            logger.error(f"Error getting OHLCV data: {e}")
            return []
    
    async def find_data_gaps(self, exchange: str, symbol: str, timeframe: str,
                           start_time: datetime, end_time: datetime) -> List[Tuple[datetime, datetime]]:
        """Find gaps in data for a specific symbol and timeframe"""
        try:
            if not self.client:
                return []
            
            query = """
                SELECT timestamp
                FROM ohlcv_futures
                WHERE exchange = %s AND symbol = %s AND timeframe = %s
                    AND timestamp >= %s AND timestamp <= %s
                ORDER BY timestamp ASC
            """
            
            # Format timestamps for ClickHouse
            start_ts = int(start_time.timestamp()) if hasattr(start_time, 'timestamp') else start_time
            end_ts = int(end_time.timestamp()) if hasattr(end_time, 'timestamp') else end_time
            
            formatted_query = query.replace('%s', '{}').format(
                f"'{exchange}'", f"'{symbol}'", f"'{timeframe}'", start_ts, end_ts
            )
            result = await self.client.execute(formatted_query)
            
            if not result:
                return [(start_time, end_time)]
            
            # Calculate expected timeframe delta
            timeframe_minutes = {
                '1m': 1, '5m': 5, '15m': 15, '30m': 30,
                '1h': 60, '4h': 240, '1d': 1440
            }
            
            delta_minutes = timeframe_minutes.get(timeframe, 1)
            delta = timedelta(minutes=delta_minutes)
            
            # Find gaps
            gaps = []
            timestamps = [row[0] for row in result]
            
            # Check gap at beginning
            if timestamps[0] > start_time:
                gaps.append((start_time, timestamps[0]))
            
            # Check gaps between timestamps
            for i in range(len(timestamps) - 1):
                current = timestamps[i]
                next_timestamp = timestamps[i + 1]
                expected_next = current + delta
                
                if next_timestamp > expected_next + timedelta(minutes=1):  # 1 minute tolerance
                    gaps.append((expected_next, next_timestamp))
            
            # Check gap at end
            if timestamps[-1] < end_time:
                gaps.append((timestamps[-1] + delta, end_time))
            
            return gaps
            
        except Exception as e:
            logger.error(f"Error finding data gaps: {e}")
            return []
    
    async def get_symbols_stats(self) -> List[Dict[str, Any]]:
        """Get statistics for all symbols"""
        try:
            if not self.client:
                return []
            
            query = """
                SELECT 
                    exchange,
                    symbol,
                    timeframe,
                    COUNT(*) as records_count,
                    MIN(timestamp) as first_record,
                    MAX(timestamp) as last_record,
                    AVG(volume) as avg_volume
                FROM ohlcv_futures
                GROUP BY exchange, symbol, timeframe
                ORDER BY exchange, symbol, timeframe
            """
            
            result = await self.client.execute(query)
            
            stats = []
            if result is None:
                return stats
            
            for row in result:
                stats.append({
                    'exchange': row[0],
                    'symbol': row[1],
                    'timeframe': row[2],
                    'records_count': int(row[3]),
                    'first_record': row[4],
                    'last_record': row[5],
                    'avg_volume': float(row[6]) if row[6] else 0.0
                })
            
            return stats
            
        except Exception as e:
            logger.error(f"Error getting symbols stats: {e}")
            return []
    
    async def update_collector_status(self, collector_id: str, exchange: str, 
                                    status: str, **kwargs):
        """Update collector status in database"""
        try:
            if not self.client:
                return
            
            # Prepare status data
            status_data = {
                'collector_id': collector_id,
                'exchange': exchange,
                'status': status,
                'updated_at': datetime.now(timezone.utc),
                **kwargs
            }
            
            # Insert/update status using string formatting for ClickHouse compatibility
            started_at = status_data.get('started_at')
            updated_at = status_data.get('updated_at')
            
            # Convert datetime objects to string for ClickHouse (remove microseconds)
            if started_at:
                started_at_clean = started_at.replace(tzinfo=None, microsecond=0)
                started_at_str = f"'{started_at_clean.isoformat()}'"
            else:
                started_at_str = 'NULL'
                
            if updated_at:
                updated_at_clean = updated_at.replace(tzinfo=None, microsecond=0)
                updated_at_str = f"'{updated_at_clean.isoformat()}'"
            else:
                updated_at_str = 'NULL'
            
            query = f"""
                INSERT INTO collector_status (
                    collector_id, exchange, status, websocket_connections,
                    messages_received, errors_count, started_at, updated_at
                ) VALUES (
                    '{status_data.get('collector_id')}',
                    '{status_data.get('exchange')}',
                    '{status_data.get('status')}',
                    {status_data.get('websocket_connections', 0)},
                    {status_data.get('messages_received', 0)},
                    {status_data.get('errors_count', 0)},
                    {started_at_str},
                    {updated_at_str}
                )
            """
            
            await self.client.execute(query)
            
        except Exception as e:
            logger.error(f"Error updating collector status: {e}")
    
    async def cleanup_old_data(self):
        """Cleanup old data based on TTL settings (manual trigger)"""
        try:
            if not self.client:
                return
            
            # This will be handled by ClickHouse TTL automatically,
            # but we can also manually trigger cleanup if needed
            
            logger.info("🧹 Triggering ClickHouse data cleanup...")
            
            # Optimize tables to apply TTL
            await self.client.execute("OPTIMIZE TABLE ohlcv_futures FINAL")
            
            logger.info("✅ Data cleanup completed")
            
        except Exception as e:
            logger.error(f"Error during data cleanup: {e}")
    
    async def get_database_size(self) -> Dict[str, Any]:
        """Get database size information"""
        try:
            if not self.client:
                return {}
            
            query = """
                SELECT 
                    table,
                    formatReadableSize(sum(data_compressed_bytes)) AS compressed_size,
                    formatReadableSize(sum(data_uncompressed_bytes)) AS uncompressed_size,
                    sum(rows) as total_rows
                FROM system.parts
                WHERE database = %s AND active = 1
                GROUP BY table
                ORDER BY sum(data_compressed_bytes) DESC
            """
            
            result = await self.client.execute(query.replace('%s', "'{}'").format(self._database))
            
            size_info = {
                'database': self._database,
                'tables': []
            }
            
            for row in result:
                size_info['tables'].append({
                    'table': row[0],
                    'compressed_size': row[1],
                    'uncompressed_size': row[2],
                    'total_rows': int(row[3])
                })
            
            return size_info
            
        except Exception as e:
            logger.error(f"Error getting database size: {e}")
            return {}
    
    async def get_batch_performance_stats(self) -> Dict[str, Any]:
        """🚀 Get batch processing performance statistics"""
        queue_size = self._write_queue.qsize()
        queue_utilization = (queue_size / self._write_queue.maxsize) * 100
        
        # Calculate throughput (records per second)
        if self._batch_stats['total_batches'] > 0 and self._batch_stats['last_batch_time']:
            time_since_start = (datetime.now() - self._batch_stats['last_batch_time']).total_seconds()
            records_per_second = self._batch_stats['total_records'] / max(time_since_start, 1)
        else:
            records_per_second = 0
        
        return {
            'batch_processing': {
                'total_batches': self._batch_stats['total_batches'],
                'total_records': self._batch_stats['total_records'],
                'failed_batches': self._batch_stats['failed_batches'],
                'avg_batch_time': round(self._batch_stats['avg_batch_time'], 3),
                'last_batch_time': self._batch_stats['last_batch_time'],
                'records_per_second': round(records_per_second, 2)
            },
            'queue_stats': {
                'queue_size': queue_size,
                'queue_capacity': self._write_queue.maxsize,
                'queue_utilization_percent': round(queue_utilization, 2),
                'is_queue_full': queue_size >= self._write_queue.maxsize * 0.9  # 90% threshold
            },
            'connection_stats': {
                'is_connected': self.is_connected,
                'max_concurrent_batches': self._max_concurrent_batches,
                'compression_enabled': self._compression_enabled,
                'connection_pool_size': self._connection_pool_size
            }
        }
    
    async def optimize_database_performance(self):
        """🔧 Run database performance optimizations"""
        try:
            if not self.client:
                logger.warning("Cannot optimize: ClickHouse client not connected")
                return
            
            logger.info("🚀 Starting database performance optimizations...")
            
            # 1. Optimize main table
            logger.info("📊 Optimizing ohlcv_futures table...")
            await self.client.execute("OPTIMIZE TABLE ohlcv_futures FINAL")
            
            # 2. Optimize collector status table
            logger.info("📈 Optimizing collector_status table...")
            await self.client.execute("OPTIMIZE TABLE collector_status FINAL")
            
            # 3. Update table statistics
            logger.info("📋 Updating table statistics...")
            await self.client.execute("ANALYZE TABLE ohlcv_futures")
            await self.client.execute("ANALYZE TABLE collector_status")
            
            # 4. Check for performance settings
            logger.info("⚙️ Checking ClickHouse performance settings...")
            
            # Get current settings
            result = await self.client.execute("""
                SELECT name, value 
                FROM system.settings 
                WHERE name IN (
                    'max_insert_block_size',
                    'max_block_size',
                    'max_memory_usage',
                    'max_bytes_before_external_group_by'
                )
                ORDER BY name
            """)
            
            settings_info = {}
            for row in result:
                settings_info[row[0]] = row[1]
            
            logger.info("✅ Database optimization completed")
            
            return {
                'optimization_completed': True,
                'current_settings': settings_info,
                'optimized_tables': ['ohlcv_futures', 'collector_status'],
                'timestamp': datetime.now()
            }
            
        except Exception as e:
            logger.error(f"❌ Database optimization failed: {e}")
            return {
                'optimization_completed': False,
                'error': str(e),
                'timestamp': datetime.now()
            }