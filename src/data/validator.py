"""
Data validation and cleaning utilities
"""
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from loguru import logger


class DataValidator:
    """Validates and cleans cryptocurrency market data"""
    
    # Price change limits (for anomaly detection)
    MAX_PRICE_CHANGE_PERCENT = 50.0  # 50% max change between candles
    MIN_PRICE = 0.000001  # Minimum valid price
    MAX_PRICE = 1000000.0  # Maximum reasonable price
    
    # Volume limits
    MIN_VOLUME = 0.0
    MAX_VOLUME_MULTIPLIER = 1000.0  # 1000x average volume
    
    @staticmethod
    def validate_ohlcv(data: Dict[str, Any], previous_close: Optional[float] = None) -> Dict[str, Any]:
        """
        Validate and clean OHLCV data
        
        Args:
            data: Raw OHLCV data dictionary
            previous_close: Previous candle's close price for validation
            
        Returns:
            Cleaned and validated data dictionary
            
        Raises:
            ValueError: If data is invalid and cannot be cleaned
        """
        try:
            # Extract and validate basic fields
            validated = {
                'exchange': DataValidator._validate_string(data.get('exchange'), 'exchange'),
                'symbol': DataValidator._validate_string(data.get('symbol'), 'symbol'),
                'timeframe': DataValidator._validate_timeframe(data.get('timeframe')),
                'timestamp': DataValidator._validate_timestamp(data.get('timestamp')),
            }
            
            # Validate and clean price data
            open_price = DataValidator._validate_price(data.get('open'), 'open')
            high_price = DataValidator._validate_price(data.get('high'), 'high')
            low_price = DataValidator._validate_price(data.get('low'), 'low')
            close_price = DataValidator._validate_price(data.get('close'), 'close')
            
            # Ensure OHLC consistency
            validated.update(DataValidator._validate_ohlc_consistency(
                open_price, high_price, low_price, close_price
            ))
            
            # Validate volume data
            validated['volume'] = DataValidator._validate_volume(data.get('volume'))
            validated['turnover'] = DataValidator._validate_volume(data.get('turnover', 0))
            
            # Validate price movement if we have previous data
            if previous_close is not None:
                DataValidator._validate_price_movement(validated['close'], previous_close)
            
            # Optional fields with defaults
            validated['open_interest'] = DataValidator._validate_optional_float(data.get('open_interest'))
            validated['funding_rate'] = DataValidator._validate_optional_float(data.get('funding_rate'))
            validated['trades_count'] = DataValidator._validate_optional_int(data.get('trades_count'))
            validated['buy_volume'] = DataValidator._validate_optional_float(data.get('buy_volume'))
            
            # Add metadata - use Beijing timezone (force +8 hours from UTC)
            utc_now = datetime.now(timezone.utc)
            beijing_time = utc_now + timedelta(hours=8)
            validated['created_at'] = beijing_time.replace(tzinfo=None)  # Remove timezone info for ClickHouse
            # Log to verify this code is executed
            logger.info(f"🕐 Setting created_at to Beijing time: {validated['created_at']}")
            # Preserve original data_quality if provided, otherwise mark as validated
            validated['data_quality'] = data.get('data_quality', 'validated')
            
            return validated
            
        except Exception as e:
            logger.error(f"Data validation failed: {e}")
            raise ValueError(f"Invalid OHLCV data: {e}")
    
    @staticmethod
    def _validate_string(value: Any, field_name: str) -> str:
        """Validate string field"""
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Invalid {field_name}: must be non-empty string")
        return value.strip()
    
    @staticmethod
    def _validate_timeframe(value: Any) -> str:
        """Validate timeframe format"""
        valid_timeframes = ['1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '8h', '12h', '1d', '3d', '1w']
        
        if not isinstance(value, str) or value not in valid_timeframes:
            raise ValueError(f"Invalid timeframe: {value}")
        
        return value
    
    @staticmethod
    def _validate_timestamp(value: Any) -> int:
        """Validate and convert timestamp to milliseconds (Freqtrade compatible)"""
        if isinstance(value, datetime):
            return int(value.timestamp() * 1000)
        elif isinstance(value, (int, float)):
            # Return as-is if already in milliseconds, convert if in seconds
            return int(value) if value > 1e10 else int(value * 1000)
        else:
            raise ValueError(f"Invalid timestamp: {value}")
    
    @staticmethod
    def _validate_price(value: Any, field_name: str) -> float:
        """Validate price field"""
        try:
            price = float(value) if value is not None else None
            
            if price is None or price <= 0:
                raise ValueError(f"Invalid {field_name}: must be positive number")
            
            if price < DataValidator.MIN_PRICE or price > DataValidator.MAX_PRICE:
                raise ValueError(f"Invalid {field_name}: price out of reasonable range")
            
            # Round to 8 decimal places to avoid precision issues
            return round(price, 8)
            
        except (TypeError, ValueError, InvalidOperation):
            raise ValueError(f"Invalid {field_name}: cannot convert to float")
    
    @staticmethod
    def _validate_volume(value: Any) -> float:
        """Validate volume field"""
        try:
            volume = float(value) if value is not None else 0.0
            
            if volume < DataValidator.MIN_VOLUME:
                return 0.0
            
            return round(volume, 8)
            
        except (TypeError, ValueError, InvalidOperation):
            logger.warning(f"Invalid volume value: {value}, using 0.0")
            return 0.0
    
    @staticmethod
    def _validate_optional_float(value: Any) -> Optional[float]:
        """Validate optional float field"""
        if value is None:
            return None
        
        try:
            return round(float(value), 8)
        except (TypeError, ValueError, InvalidOperation):
            return None
    
    @staticmethod
    def _validate_optional_int(value: Any) -> Optional[int]:
        """Validate optional integer field"""
        if value is None:
            return None
        
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    
    @staticmethod
    def _validate_ohlc_consistency(open_p: float, high_p: float, low_p: float, close_p: float) -> Dict[str, float]:
        """Validate OHLC price consistency and fix minor issues"""
        prices = [open_p, high_p, low_p, close_p]
        
        # Check for basic OHLC rules
        actual_high = max(prices)
        actual_low = min(prices)
        
        # Fix high/low if they're inconsistent
        if high_p < actual_high:
            logger.warning(f"Corrected high price: {high_p} -> {actual_high}")
            high_p = actual_high
        
        if low_p > actual_low:
            logger.warning(f"Corrected low price: {low_p} -> {actual_low}")
            low_p = actual_low
        
        return {
            'open': open_p,
            'high': high_p,
            'low': low_p,
            'close': close_p
        }
    
    @staticmethod
    def _validate_price_movement(current_price: float, previous_price: float) -> None:
        """Validate price movement for anomaly detection"""
        if previous_price <= 0:
            return
        
        price_change_percent = abs((current_price - previous_price) / previous_price) * 100
        
        if price_change_percent > DataValidator.MAX_PRICE_CHANGE_PERCENT:
            logger.warning(
                f"Large price movement detected: {price_change_percent:.2f}% "
                f"(from {previous_price} to {current_price})"
            )
    
    @staticmethod
    def detect_anomalies(data_points: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Detect anomalies in a series of data points
        
        Args:
            data_points: List of OHLCV data points sorted by timestamp
            
        Returns:
            List of data points with anomaly flags
        """
        if len(data_points) < 2:
            return data_points
        
        cleaned_data = []
        
        for i, point in enumerate(data_points):
            point_copy = point.copy()
            point_copy['anomaly_flags'] = []
            
            # Check for price spikes
            if i > 0:
                prev_close = data_points[i-1]['close']
                DataValidator._check_price_spike(point_copy, prev_close)
            
            # Check for volume anomalies
            if i >= 5:  # Need at least 5 points for volume analysis
                recent_volumes = [dp['volume'] for dp in data_points[i-5:i]]
                DataValidator._check_volume_anomaly(point_copy, recent_volumes)
            
            cleaned_data.append(point_copy)
        
        return cleaned_data
    
    @staticmethod
    def _check_price_spike(point: Dict[str, Any], previous_close: float) -> None:
        """Check for price spike anomalies"""
        if previous_close <= 0:
            return
        
        current_close = point['close']
        price_change_percent = abs((current_close - previous_close) / previous_close) * 100
        
        if price_change_percent > DataValidator.MAX_PRICE_CHANGE_PERCENT:
            point['anomaly_flags'].append('price_spike')
            point['price_change_percent'] = price_change_percent
    
    @staticmethod
    def _check_volume_anomaly(point: Dict[str, Any], recent_volumes: List[float]) -> None:
        """Check for volume anomalies"""
        if not recent_volumes:
            return
        
        avg_volume = sum(recent_volumes) / len(recent_volumes)
        current_volume = point['volume']
        
        if avg_volume > 0 and current_volume > avg_volume * DataValidator.MAX_VOLUME_MULTIPLIER:
            point['anomaly_flags'].append('volume_spike')
            point['volume_multiplier'] = current_volume / avg_volume