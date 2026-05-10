"""
Data cleaning and preprocessing utilities
"""
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta, timezone
import statistics
from loguru import logger


class DataCleaner:
    """Cleans and preprocesses market data"""
    
    @staticmethod
    def fill_missing_candles(data: List[Dict[str, Any]], timeframe: str) -> List[Dict[str, Any]]:
        """
        Fill missing candles in a time series
        
        Args:
            data: List of OHLCV data sorted by timestamp
            timeframe: Timeframe string (e.g., '1m', '5m', '1h')
            
        Returns:
            Complete data series with filled gaps
        """
        if len(data) < 2:
            return data
        
        # Calculate timeframe delta
        timeframe_delta = DataCleaner._get_timeframe_delta(timeframe)
        if not timeframe_delta:
            return data
        
        # Sort data by timestamp
        sorted_data = sorted(data, key=lambda x: x['timestamp'])
        filled_data = []
        
        for i, current in enumerate(sorted_data):
            filled_data.append(current)
            
            # Check if next candle exists and calculate gap
            if i < len(sorted_data) - 1:
                next_candle = sorted_data[i + 1]
                current_time = current['timestamp']
                next_time = next_candle['timestamp']
                
                # Fill gaps between candles
                expected_next = current_time + timeframe_delta
                while expected_next < next_time:
                    # Create synthetic candle
                    synthetic_candle = DataCleaner._create_synthetic_candle(
                        current, expected_next, timeframe
                    )
                    filled_data.append(synthetic_candle)
                    expected_next += timeframe_delta
        
        logger.debug(f"Filled {len(filled_data) - len(data)} missing candles for {timeframe}")
        return filled_data
    
    @staticmethod
    def _get_timeframe_delta(timeframe: str) -> Optional[timedelta]:
        """Convert timeframe string to timedelta"""
        timeframe_map = {
            '1m': timedelta(minutes=1),
            '3m': timedelta(minutes=3),
            '5m': timedelta(minutes=5),
            '15m': timedelta(minutes=15),
            '30m': timedelta(minutes=30),
            '1h': timedelta(hours=1),
            '2h': timedelta(hours=2),
            '4h': timedelta(hours=4),
            '6h': timedelta(hours=6),
            '8h': timedelta(hours=8),
            '12h': timedelta(hours=12),
            '1d': timedelta(days=1),
            '3d': timedelta(days=3),
            '1w': timedelta(weeks=1)
        }
        return timeframe_map.get(timeframe)
    
    @staticmethod
    def _create_synthetic_candle(previous: Dict[str, Any], timestamp: datetime, timeframe: str) -> Dict[str, Any]:
        """Create synthetic candle for missing data"""
        # Use previous close as all OHLC values for synthetic candle
        close_price = previous['close']
        
        return {
            'exchange': previous['exchange'],
            'symbol': previous['symbol'],
            'timeframe': timeframe,
            'timestamp': timestamp,
            'open': close_price,
            'high': close_price,
            'low': close_price,
            'close': close_price,
            'volume': 0.0,  # Zero volume for synthetic data
            'turnover': 0.0,
            'open_interest': previous.get('open_interest'),
            'funding_rate': previous.get('funding_rate'),
            'trades_count': 0,
            'buy_volume': 0.0,
            'created_at': datetime.now(timezone.utc),
            'data_quality': 'synthetic'  # Mark as synthetic
        }
    
    @staticmethod
    def remove_duplicates(data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove duplicate candles based on timestamp"""
        if not data:
            return data
        
        seen_timestamps = set()
        unique_data = []
        
        for candle in sorted(data, key=lambda x: x['timestamp']):
            timestamp_key = (candle['exchange'], candle['symbol'], candle['timeframe'], candle['timestamp'])
            
            if timestamp_key not in seen_timestamps:
                seen_timestamps.add(timestamp_key)
                unique_data.append(candle)
        
        removed_count = len(data) - len(unique_data)
        if removed_count > 0:
            logger.debug(f"Removed {removed_count} duplicate candles")
        
        return unique_data
    
    @staticmethod
    def smooth_price_spikes(data: List[Dict[str, Any]], threshold_percent: float = 20.0) -> List[Dict[str, Any]]:
        """
        Smooth extreme price spikes that are likely erroneous
        
        Args:
            data: List of OHLCV data sorted by timestamp
            threshold_percent: Spike threshold as percentage
            
        Returns:
            Data with smoothed spikes
        """
        if len(data) < 3:
            return data
        
        smoothed_data = data.copy()
        
        for i in range(1, len(smoothed_data) - 1):
            current = smoothed_data[i]
            previous = smoothed_data[i - 1]
            next_candle = smoothed_data[i + 1]
            
            # Check for spikes in close price
            if DataCleaner._is_price_spike(previous['close'], current['close'], next_candle['close'], threshold_percent):
                # Smooth the spike using interpolation
                smoothed_price = (previous['close'] + next_candle['close']) / 2
                
                logger.warning(
                    f"Smoothing price spike: {current['symbol']} {current['timestamp']} "
                    f"{current['close']} -> {smoothed_price}"
                )
                
                # Adjust OHLC values
                current['open'] = min(current['open'], smoothed_price * 1.01, previous['close'] * 1.01)
                current['high'] = max(current['open'], smoothed_price, current['low'])
                current['low'] = min(current['open'], smoothed_price, current['high'])
                current['close'] = smoothed_price
                current['data_quality'] = 'smoothed'
        
        return smoothed_data
    
    @staticmethod
    def _is_price_spike(prev_price: float, current_price: float, next_price: float, threshold: float) -> bool:
        """Check if current price is a spike compared to neighbors"""
        if prev_price <= 0 or next_price <= 0:
            return False
        
        # Calculate percentage change from both neighbors
        change_from_prev = abs((current_price - prev_price) / prev_price) * 100
        change_from_next = abs((current_price - next_price) / next_price) * 100
        
        # Check if both changes exceed threshold (indicating a spike)
        return change_from_prev > threshold and change_from_next > threshold
    
    @staticmethod
    def calculate_technical_indicators(data: List[Dict[str, Any]], window: int = 20) -> List[Dict[str, Any]]:
        """
        Add basic technical indicators to data
        
        Args:
            data: List of OHLCV data sorted by timestamp
            window: Window size for moving averages
            
        Returns:
            Data with added technical indicators
        """
        if len(data) < window:
            return data
        
        enhanced_data = []
        
        for i, candle in enumerate(data):
            enhanced_candle = candle.copy()
            
            # Calculate indicators only if we have enough data
            if i >= window - 1:
                # Get recent prices for calculations
                recent_closes = [data[j]['close'] for j in range(i - window + 1, i + 1)]
                recent_highs = [data[j]['high'] for j in range(i - window + 1, i + 1)]
                recent_lows = [data[j]['low'] for j in range(i - window + 1, i + 1)]
                recent_volumes = [data[j]['volume'] for j in range(i - window + 1, i + 1)]
                
                # Simple Moving Average
                enhanced_candle['sma'] = statistics.mean(recent_closes)
                
                # Price volatility (standard deviation)
                if len(recent_closes) > 1:
                    enhanced_candle['volatility'] = statistics.stdev(recent_closes)
                else:
                    enhanced_candle['volatility'] = 0.0
                
                # Average volume
                enhanced_candle['avg_volume'] = statistics.mean(recent_volumes)
                
                # Price range
                enhanced_candle['price_range'] = max(recent_highs) - min(recent_lows)
                
                # Volume-Weighted Average Price (approximation)
                total_volume = sum(recent_volumes)
                if total_volume > 0:
                    vwap_sum = sum(close * vol for close, vol in zip(recent_closes, recent_volumes))
                    enhanced_candle['vwap'] = vwap_sum / total_volume
                else:
                    enhanced_candle['vwap'] = enhanced_candle['close']
            
            enhanced_data.append(enhanced_candle)
        
        return enhanced_data
    
    @staticmethod
    def detect_gaps(data: List[Dict[str, Any]], timeframe: str) -> List[Tuple[datetime, datetime]]:
        """
        Detect time gaps in data series
        
        Args:
            data: List of OHLCV data sorted by timestamp
            timeframe: Expected timeframe
            
        Returns:
            List of (start_time, end_time) tuples representing gaps
        """
        if len(data) < 2:
            return []
        
        timeframe_delta = DataCleaner._get_timeframe_delta(timeframe)
        if not timeframe_delta:
            return []
        
        gaps = []
        sorted_data = sorted(data, key=lambda x: x['timestamp'])
        
        for i in range(len(sorted_data) - 1):
            current_time = sorted_data[i]['timestamp']
            next_time = sorted_data[i + 1]['timestamp']
            expected_next = current_time + timeframe_delta
            
            # If there's a gap larger than expected timeframe
            if next_time > expected_next + timedelta(seconds=60):  # 1 minute tolerance
                gaps.append((expected_next, next_time))
        
        return gaps
    
    @staticmethod
    def aggregate_to_higher_timeframe(data: List[Dict[str, Any]], target_timeframe: str) -> List[Dict[str, Any]]:
        """
        Aggregate lower timeframe data to higher timeframe
        
        Args:
            data: List of OHLCV data (must be sorted by timestamp)
            target_timeframe: Target timeframe (e.g., '1h', '1d')
            
        Returns:
            Aggregated data in target timeframe
        """
        if not data:
            return []
        
        target_delta = DataCleaner._get_timeframe_delta(target_timeframe)
        if not target_delta:
            logger.error(f"Unsupported target timeframe: {target_timeframe}")
            return []
        
        aggregated = []
        current_group = []
        
        # Group data by target timeframe periods
        base_time = DataCleaner._floor_timestamp(data[0]['timestamp'], target_timeframe)
        
        for candle in data:
            candle_base = DataCleaner._floor_timestamp(candle['timestamp'], target_timeframe)
            
            # Start new group if we've moved to next period
            if candle_base != base_time:
                if current_group:
                    aggregated_candle = DataCleaner._aggregate_group(current_group, target_timeframe)
                    aggregated.append(aggregated_candle)
                
                current_group = [candle]
                base_time = candle_base
            else:
                current_group.append(candle)
        
        # Don't forget the last group
        if current_group:
            aggregated_candle = DataCleaner._aggregate_group(current_group, target_timeframe)
            aggregated.append(aggregated_candle)
        
        return aggregated
    
    @staticmethod
    def _floor_timestamp(timestamp: datetime, timeframe: str) -> datetime:
        """Floor timestamp to timeframe boundary"""
        if timeframe == '1h':
            return timestamp.replace(minute=0, second=0, microsecond=0)
        elif timeframe == '4h':
            hour = (timestamp.hour // 4) * 4
            return timestamp.replace(hour=hour, minute=0, second=0, microsecond=0)
        elif timeframe == '1d':
            return timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
        # Add more as needed
        return timestamp
    
    @staticmethod
    def _aggregate_group(group: List[Dict[str, Any]], target_timeframe: str) -> Dict[str, Any]:
        """Aggregate a group of candles into single candle"""
        if not group:
            return {}
        
        first = group[0]
        last = group[-1]
        
        # OHLC aggregation
        open_price = first['open']
        close_price = last['close']
        high_price = max(candle['high'] for candle in group)
        low_price = min(candle['low'] for candle in group)
        
        # Volume aggregation
        total_volume = sum(candle['volume'] for candle in group)
        total_turnover = sum(candle.get('turnover', 0) for candle in group)
        total_trades = sum(candle.get('trades_count', 0) for candle in group if candle.get('trades_count'))
        
        return {
            'exchange': first['exchange'],
            'symbol': first['symbol'],
            'timeframe': target_timeframe,
            'timestamp': DataCleaner._floor_timestamp(first['timestamp'], target_timeframe),
            'open': open_price,
            'high': high_price,
            'low': low_price,
            'close': close_price,
            'volume': total_volume,
            'turnover': total_turnover,
            'trades_count': total_trades if total_trades > 0 else None,
            'buy_volume': None,  # Would need to sum if available
            'open_interest': last.get('open_interest'),  # Use last value
            'funding_rate': last.get('funding_rate'),    # Use last value
            'created_at': datetime.now(timezone.utc),
            'data_quality': 'aggregated'
        }