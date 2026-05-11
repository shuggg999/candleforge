"""
Data recovery service for filling gaps in collected data
"""
import asyncio
from typing import Dict, List, Tuple, Optional
from datetime import datetime, timezone, timedelta
from loguru import logger

from src.config import settings
from src.storage.clickhouse import ClickHouseManager
from src.collectors.manager import CollectorManager


class RecoveryService:
    """Service to detect and fill gaps in collected data"""
    
    def __init__(self, db_manager: ClickHouseManager):
        self.db_manager = db_manager
        self.collector_manager: Optional[CollectorManager] = None
        self.is_running = False
        self.check_interval = settings.RECOVERY_CHECK_INTERVAL
        self.max_gap_minutes = settings.MAX_GAP_MINUTES
        # Last cycle completion timestamp — surfaced via /api/v1/health to detect stuck recovery loops
        self.last_cycle_ts: Optional[datetime] = None
        
    def set_collector_manager(self, collector_manager: CollectorManager):
        """Set collector manager reference"""
        self.collector_manager = collector_manager
    
    async def start(self):
        """Start the recovery service"""
        if self.is_running:
            logger.warning("Recovery service is already running")
            return
        
        self.is_running = True
        logger.info(f"🔄 Starting recovery service (check every {self.check_interval}s)")
        
        while self.is_running:
            try:
                await self._check_and_recover_gaps()
                self.last_cycle_ts = datetime.now(timezone.utc)
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in recovery service: {e}")
                await asyncio.sleep(60)  # Wait a minute before retrying
        
        logger.info("🔄 Recovery service stopped")
    
    async def stop(self):
        """Stop the recovery service"""
        self.is_running = False
    
    async def _check_and_recover_gaps(self):
        """Check for gaps and attempt to recover them"""
        if not self.collector_manager:
            logger.warning("Collector manager not available for recovery")
            return
        
        # Get all active symbols from collectors
        collectors_status = await self.collector_manager.get_status()
        
        for exchange_id, collector_info in collectors_status.get('collectors', {}).items():
            if not collector_info.get('running', False):
                continue
            
            collector = self.collector_manager.get_collector(exchange_id)
            if not collector:
                continue
            
            # Use configured priority symbols
            from src.config import settings
            priority_symbols = settings.PRIORITY_SYMBOLS
            recovery_symbols_per_cycle = settings.RECOVERY_SYMBOLS_PER_CYCLE
            
            # Check priority symbols first, then cycle through other symbols
            symbols_to_check = []
            for priority in priority_symbols:
                if priority in collector.symbols:
                    symbols_to_check.append(priority)
            
            # Add other symbols using round-robin approach
            other_symbols = [s for s in collector.symbols if s not in priority_symbols]
            
            # Implement cycling through all symbols
            if not hasattr(self, '_symbol_cycle_index'):
                self._symbol_cycle_index = 0
            
            # Calculate how many additional symbols to check this cycle
            remaining_slots = recovery_symbols_per_cycle - len(symbols_to_check)
            if remaining_slots > 0 and other_symbols:
                # Cycle through symbols starting from current index
                for i in range(remaining_slots):
                    if other_symbols:
                        symbol_index = (self._symbol_cycle_index + i) % len(other_symbols)
                        symbols_to_check.append(other_symbols[symbol_index])
                
                # Update cycle index for next round
                self._symbol_cycle_index = (self._symbol_cycle_index + remaining_slots) % len(other_symbols) if other_symbols else 0
            
            logger.info(f"🔄 Recovery cycle: checking {len(symbols_to_check)} symbols ({len([s for s in symbols_to_check if s in priority_symbols])} priority + {len([s for s in symbols_to_check if s not in priority_symbols])} others)")
            logger.debug(f"📊 Total symbols available: {len(collector.symbols)} ({len(other_symbols)} non-priority)")
            
            # Check gaps for each symbol and timeframe
            for symbol in symbols_to_check:
                for timeframe in ['1m', '5m', '15m', '1h']:  # Focus on important timeframes
                    await self._recover_symbol_gaps(exchange_id, symbol, timeframe)
    
    async def _recover_symbol_gaps(self, exchange: str, symbol: str, timeframe: str):
        """Recover gaps for a specific symbol and timeframe"""
        try:
            # Define time window to check (last 24 hours for short timeframes)
            end_time = datetime.now(timezone.utc)
            
            if timeframe in ['1m']:
                start_time = end_time - timedelta(hours=2)  # Last 2 hours for 1m
            elif timeframe == '5m':
                start_time = end_time - timedelta(hours=4)  # Last 4 hours for 5m  
            elif timeframe == '15m':
                start_time = end_time - timedelta(hours=12)  # Last 12 hours for 15m
            elif timeframe == '1h':
                start_time = end_time - timedelta(hours=24)  # Last 24 hours for 1h
            else:
                start_time = end_time - timedelta(days=2)  # Last 2 days for others
            
            # Find gaps
            gaps = await self.db_manager.find_data_gaps(
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                start_time=start_time,
                end_time=end_time
            )
            
            if not gaps:
                return  # No gaps found
            
            logger.info(f"🔍 Found {len(gaps)} gaps for {exchange} {symbol} {timeframe}")
            
            # Attempt to fill each gap
            for gap_start, gap_end in gaps:
                await self._fill_gap(exchange, symbol, timeframe, gap_start, gap_end)
                
                # Small delay between gap fills to avoid rate limiting
                await asyncio.sleep(1)
        
        except Exception as e:
            logger.error(f"Error recovering gaps for {exchange} {symbol} {timeframe}: {e}")
    
    async def _fill_gap(self, exchange: str, symbol: str, timeframe: str, 
                       start_time: datetime, end_time: datetime):
        """Fill a specific data gap using REST API"""
        try:
            # Calculate gap duration in minutes
            gap_duration = (end_time - start_time).total_seconds() / 60
            
            if gap_duration > self.max_gap_minutes:
                logger.warning(
                    f"Gap too large to fill via REST API: {exchange} {symbol} {timeframe} "
                    f"({gap_duration:.1f} minutes > {self.max_gap_minutes})"
                )
                return
            
            # Get collector for this exchange
            if not self.collector_manager:
                return
            
            collector = self.collector_manager.get_collector(exchange)
            if not collector:
                logger.warning(f"Collector not found for {exchange}")
                return
            
            # Fetch historical data using collector's method
            if hasattr(collector, 'get_historical_klines'):
                historical_data = await collector.get_historical_klines(
                    symbol=symbol,
                    timeframe=timeframe,
                    start_time=int(start_time.timestamp() * 1000),
                    end_time=int(end_time.timestamp() * 1000)
                )
                
                if historical_data:
                    # Insert the recovered data (ReplacingMergeTree will handle duplicates)
                    inserted_count = 0
                    for kline_data in historical_data:
                        # Check if this timestamp already exists to avoid unnecessary inserts
                        existing = await self._check_existing_data(
                            exchange, symbol, timeframe, kline_data['timestamp']
                        )
                        
                        if not existing:
                            await self.db_manager.insert_ohlcv(kline_data)
                            inserted_count += 1
                    
                    logger.info(
                        f"✅ Filled gap: {exchange} {symbol} {timeframe} "
                        f"{start_time} - {end_time} ({inserted_count}/{len(historical_data)} new records)"
                    )
                else:
                    logger.warning(f"No historical data returned for gap: {exchange} {symbol} {timeframe}")
            else:
                logger.warning(f"Collector {exchange} does not support historical data recovery")
        
        except Exception as e:
            logger.error(f"Error filling gap {exchange} {symbol} {timeframe}: {e}")
    
    async def _check_existing_data(self, exchange: str, symbol: str, 
                                  timeframe: str, timestamp: datetime) -> bool:
        """Check if data already exists for given timestamp"""
        try:
            if not self.db_manager:
                return False
            
            # Use simple count query to check existence
            result = await self.db_manager.client.execute(
                """
                SELECT COUNT(*) 
                FROM ohlcv_futures 
                WHERE exchange = %s AND symbol = %s AND timeframe = %s AND timestamp = %s
                """,
                (exchange, symbol, timeframe, timestamp)
            )
            
            return result and result[0][0] > 0
            
        except Exception as e:
            logger.debug(f"Error checking existing data: {e}")
            return False  # Assume doesn't exist if check fails
    
    async def recover_symbol_data(self, exchange: str, symbol: str, timeframe: str,
                                 start_time: datetime, end_time: datetime) -> bool:
        """Manually recover data for a specific symbol and time range"""
        try:
            logger.info(
                f"📥 Manual recovery requested: {exchange} {symbol} {timeframe} "
                f"{start_time} - {end_time}"
            )
            
            # Get collector
            if not self.collector_manager:
                logger.error("Collector manager not available")
                return False
            
            collector = self.collector_manager.get_collector(exchange)
            if not collector:
                logger.error(f"Collector not found for {exchange}")
                return False
            
            # Fetch historical data
            if hasattr(collector, 'get_historical_klines'):
                historical_data = await collector.get_historical_klines(
                    symbol=symbol,
                    timeframe=timeframe,
                    start_time=int(start_time.timestamp() * 1000),
                    end_time=int(end_time.timestamp() * 1000)
                )
                
                if historical_data:
                    # Insert the data
                    success_count = 0
                    for kline_data in historical_data:
                        try:
                            await self.db_manager.insert_ohlcv(kline_data)
                            success_count += 1
                        except Exception as e:
                            logger.warning(f"Failed to insert kline data: {e}")
                    
                    logger.info(
                        f"✅ Manual recovery completed: {exchange} {symbol} {timeframe} "
                        f"({success_count}/{len(historical_data)} records)"
                    )
                    return success_count > 0
                else:
                    logger.warning("No historical data returned")
                    return False
            else:
                logger.error(f"Collector {exchange} does not support historical data recovery")
                return False
        
        except Exception as e:
            logger.error(f"Error in manual recovery: {e}")
            return False
    
    async def get_recovery_stats(self) -> Dict:
        """Get recovery service statistics"""
        return {
            'running': self.is_running,
            'check_interval': self.check_interval,
            'max_gap_minutes': self.max_gap_minutes,
            'last_check': datetime.now(timezone.utc) if self.is_running else None
        }
    
    async def analyze_missing_data(self, hours_back: int = 24) -> Dict:
        """Analyze missing data across all symbols"""
        try:
            end_time = datetime.now(timezone.utc)
            start_time = end_time - timedelta(hours=hours_back)
            
            # Get all symbols with data
            stats = await self.db_manager.get_symbols_stats()
            
            missing_data_report = {
                'analysis_period': {
                    'start': start_time,
                    'end': end_time,
                    'hours': hours_back
                },
                'exchanges': {}
            }
            
            # Group by exchange
            for stat in stats:
                exchange = stat['exchange']
                symbol = stat['symbol']
                
                if exchange not in missing_data_report['exchanges']:
                    missing_data_report['exchanges'][exchange] = {
                        'symbols': {},
                        'total_gaps': 0
                    }
                
                # Check for gaps in each timeframe
                symbol_gaps = {}
                for timeframe in ['1m', '5m', '15m', '1h']:
                    gaps = await self.db_manager.find_data_gaps(
                        exchange=exchange,
                        symbol=symbol,
                        timeframe=timeframe,
                        start_time=start_time,
                        end_time=end_time
                    )
                    
                    if gaps:
                        symbol_gaps[timeframe] = len(gaps)
                        missing_data_report['exchanges'][exchange]['total_gaps'] += len(gaps)
                
                if symbol_gaps:
                    missing_data_report['exchanges'][exchange]['symbols'][symbol] = symbol_gaps
            
            return missing_data_report
            
        except Exception as e:
            logger.error(f"Error analyzing missing data: {e}")
            return {}