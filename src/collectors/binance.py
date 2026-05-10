"""
Binance futures data collector using official binance-connector-python
"""
import asyncio
import json
import websockets
import httpx
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from urllib.parse import urlencode

from binance.spot import Spot
from loguru import logger

from src.config import settings
from src.storage.clickhouse import ClickHouseManager
from .base import ExchangeCollector


class BinanceCollector(ExchangeCollector):
    """Binance USDT futures data collector using official connector"""
    
    def __init__(self, db_manager: ClickHouseManager):
        super().__init__("binance", db_manager)
        
        # Binance-specific settings
        self.base_url = "https://api.binance.com"  # Public API
        self.futures_base_url = "https://fapi.binance.com"  # Futures API
        self.ws_base_url = "wss://fstream.binance.com/ws/"
        self.max_symbols_per_connection = 100  # Binance limit
    
    async def initialize_clients(self):
        """Initialize Binance REST and WebSocket clients"""
        try:
            # Create HTTP client (optionally via proxy — set BINANCE_PROXY_URL on hosts
            # that can't reach Binance directly, e.g. socks5h://host.docker.internal:10808)
            proxy = settings.BINANCE_PROXY_URL or None
            if proxy:
                logger.info(f"🌐 Binance HTTP client using proxy: {proxy}")
                # httpx 0.25 uses `proxies=` (single str or dict). Newer 0.27+ uses `proxy=`.
                self.http_client = httpx.AsyncClient(proxies=proxy, timeout=30.0)
            else:
                self.http_client = httpx.AsyncClient(timeout=30.0)

            # Test REST connection by getting exchange info using direct HTTP
            exchange_info_url = f"{self.futures_base_url}/fapi/v1/exchangeInfo"
            response = await self.http_client.get(exchange_info_url)
            
            if response.status_code == 200:
                exchange_info = response.json()
                if 'symbols' in exchange_info:
                    logger.info(f"✅ Binance futures REST client initialized - {len(exchange_info['symbols'])} symbols available")
                    self.exchange_info = exchange_info  # Cache for later use
                else:
                    raise Exception("Failed to get exchange info")
            else:
                raise Exception(f"HTTP error {response.status_code}: {response.text}")
            
            # Initialize binance-connector client for WebSocket and authenticated calls if needed
            if settings.BINANCE_API_KEY and settings.BINANCE_SECRET:
                self.rest_client = Spot(
                    api_key=settings.BINANCE_API_KEY,
                    api_secret=settings.BINANCE_SECRET,
                    base_url=self.futures_base_url
                )
                logger.info("🔑 Authenticated Binance client also available")
            else:
                logger.info("🌐 Using public HTTP client only")
                
        except Exception as e:
            logger.error(f"❌ Failed to initialize Binance clients: {type(e).__name__}: {e}")
            raise
    
    async def get_futures_symbols(self) -> List[str]:
        """Get all active USDT futures symbols from Binance"""
        try:
            # Use cached exchange info from initialize_clients
            exchange_info = getattr(self, 'exchange_info', None)
            if not exchange_info:
                # Fallback: fetch exchange info again
                response = await self.http_client.get(f"{self.futures_base_url}/fapi/v1/exchangeInfo")
                if response.status_code == 200:
                    exchange_info = response.json()
                else:
                    raise Exception(f"Failed to fetch exchange info: {response.status_code}")
            
            futures_symbols = []
            for symbol_info in exchange_info.get('symbols', []):
                symbol = symbol_info.get('symbol', '')
                status = symbol_info.get('status', '')
                quote_asset = symbol_info.get('quoteAsset', '')
                contract_type = symbol_info.get('contractType', '')
                
                # Only USDT perpetual futures that are trading
                if (status == 'TRADING' and 
                    quote_asset == 'USDT' and 
                    contract_type == 'PERPETUAL'):
                    
                    # Convert to standard format (BTCUSDT -> BTC/USDT)
                    if symbol.endswith('USDT'):
                        base_asset = symbol[:-4]
                        standard_symbol = f"{base_asset}/USDT"
                        futures_symbols.append(standard_symbol)
            
            logger.info(f"📈 Found {len(futures_symbols)} Binance USDT perpetual futures")
            return sorted(futures_symbols)
            
        except Exception as e:
            logger.error(f"Error fetching Binance futures symbols: {e}")
            return []
    
    def normalize_symbol(self, symbol: str) -> str:
        """Convert standard symbol format to Binance format"""
        # BTC/USDT -> BTCUSDT (Binance format)
        if '/' in symbol:
            return symbol.replace('/', '')
        return symbol
    
    def parse_ohlcv_message(self, raw_message: Any) -> Optional[Dict[str, Any]]:
        """Parse Binance WebSocket message to standardized OHLCV format"""
        try:
            # Binance WebSocket kline message format
            if isinstance(raw_message, dict) and 'k' in raw_message:
                kline = raw_message['k']
                
                # Only process closed candles for accuracy
                if not kline.get('x', False):
                    return None
                
                # Convert to standard format
                symbol_binance = kline['s']  # BTCUSDT
                symbol_standard = self.get_standard_symbol_format(symbol_binance)  # BTC/USDT
                
                # 🔧 CRITICAL: Use open time (t) instead of close time (T) for consistency
                # This ensures WebSocket and REST API data have the same timestamp
                kline_open_time = datetime.fromtimestamp(int(kline['t']) / 1000, timezone.utc)

                # Normalize timestamp to exact timeframe boundary
                kline_open_time = self._normalize_timestamp(kline_open_time, self._parse_timeframe(kline['i']))

                # Convert to milliseconds timestamp for Freqtrade compatibility
                timestamp_ms = int(kline_open_time.timestamp() * 1000)

                return {
                    'exchange': self.exchange_id,
                    'symbol': symbol_standard,
                    'timeframe': self._parse_timeframe(kline['i']),
                    'timestamp': timestamp_ms,  # Milliseconds timestamp for Freqtrade
                    'open': float(kline['o']),
                    'high': float(kline['h']),
                    'low': float(kline['l']),
                    'close': float(kline['c']),
                    'volume': float(kline['v']),
                    'turnover': float(kline['q']),  # Quote asset volume
                    'trades_count': int(kline['n']),
                    'buy_volume': float(kline['V']),  # Taker buy base asset volume
                    'open_interest': None,  # Not available in kline data
                    'funding_rate': None,    # Not available in kline data
                    'data_quality': 'websocket'  # Mark as WebSocket data
                }
            
            return None
            
        except (KeyError, ValueError, TypeError) as e:
            logger.error(f"Error parsing Binance message: {e}")
            return None
    
    def _parse_timeframe(self, binance_interval: str) -> str:
        """Convert Binance interval to standard timeframe"""
        timeframe_map = {
            '1m': '1m',
            '3m': '3m',
            '5m': '5m',
            '15m': '15m',
            '30m': '30m',
            '1h': '1h',
            '2h': '2h',
            '4h': '4h',
            '6h': '6h',
            '8h': '8h',
            '12h': '12h',
            '1d': '1d',
            '3d': '3d',
            '1w': '1w',
            '1M': '1M'
        }
        return timeframe_map.get(binance_interval, binance_interval)
    
    async def create_websocket_connection(self, client_key: str, symbols: List[str]) -> Any:
        """Create WebSocket connection for given symbols"""
        try:
            # Build stream names for kline subscriptions
            streams = []
            for symbol in symbols:
                binance_symbol = self.normalize_symbol(symbol).lower()
                for timeframe in self.timeframes:
                    stream_name = f"{binance_symbol}@kline_{timeframe}"
                    streams.append(stream_name)
            
            # Create combined stream URL
            stream_params = "/".join(streams)
            ws_url = f"{self.ws_base_url}{stream_params}"
            
            logger.info(f"🔌 Creating Binance WebSocket connection {client_key} with {len(streams)} streams")
            
            # Create WebSocket connection
            websocket = await websockets.connect(ws_url)
            
            return websocket
            
        except Exception as e:
            logger.error(f"Failed to create WebSocket connection {client_key}: {e}")
            raise
    
    async def subscribe_to_ohlcv(self, client_key: str, symbols: List[str], timeframes: List[str]):
        """Subscribe to OHLCV streams - handled in URL for Binance"""
        # Binance handles subscription via URL parameters, so this is a no-op
        logger.info(f"📡 Subscribed {client_key} to {len(symbols)} symbols, {len(timeframes)} timeframes")
    
    async def handle_websocket_message(self, client_key: str, raw_message: str):
        """Handle a single WebSocket message - called by base class message loop"""
        try:
            # Parse JSON message
            message_data = json.loads(raw_message)

            # Parse OHLCV data
            ohlcv_data = self.parse_ohlcv_message(message_data)

            if ohlcv_data:
                # Store the data
                await self.process_and_store_ohlcv(ohlcv_data)

        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON message from {client_key}: {e}")
        except Exception as e:
            logger.error(f"Error processing message from {client_key}: {e}")
    
    async def close_websocket_connection(self, client_key: str):
        """Close a specific WebSocket connection"""
        if client_key in self.websocket_clients:
            websocket = self.websocket_clients[client_key]
            try:
                await websocket.close()
                logger.info(f"🔌 Closed WebSocket connection {client_key}")
            except Exception as e:
                logger.warning(f"Error closing WebSocket connection {client_key}: {e}")
            finally:
                del self.websocket_clients[client_key]
    
    async def get_historical_klines(self, symbol: str, timeframe: str, 
                                   start_time: int, end_time: int) -> List[Dict]:
        """Fetch historical kline data for gap filling using public API"""
        try:
            # Convert symbol format
            binance_symbol = self.normalize_symbol(symbol)
            
            # Map timeframe to Binance format  
            timeframe_map = {
                '1m': '1m', '5m': '5m', '15m': '15m', '1h': '1h', 
                '4h': '4h', '1d': '1d'
            }
            interval = timeframe_map.get(timeframe, '5m')
            
            # Use public HTTP client to get historical klines
            params = {
                'symbol': binance_symbol,
                'interval': interval,
                'startTime': start_time,
                'endTime': end_time,
                'limit': 1000
            }
            
            url = f"{self.futures_base_url}/fapi/v1/klines"
            response = await self.http_client.get(url, params=params)
            
            if response.status_code != 200:
                logger.error(f"Failed to fetch historical data: HTTP {response.status_code}")
                return []
            
            klines_raw = response.json()
            
            if not klines_raw:
                return []
            
            # Convert to our data format
            historical_data = []
            for kline in klines_raw:
                # Binance kline format: [open_time, open, high, low, close, volume, close_time, quote_volume, count, taker_buy_volume, taker_buy_quote_volume, ignore]
                
                # 🔧 CRITICAL: Normalize timestamp to exact timeframe boundary
                kline_open_time = datetime.fromtimestamp(int(kline[0]) / 1000, timezone.utc)
                kline_open_time = self._normalize_timestamp(kline_open_time, timeframe)

                # Convert to milliseconds timestamp for Freqtrade compatibility
                timestamp_ms = int(kline_open_time.timestamp() * 1000)

                data = {
                    'exchange': self.exchange_id,
                    'symbol': symbol,  # Use standard format
                    'timeframe': timeframe,
                    'timestamp': timestamp_ms,  # Milliseconds timestamp for Freqtrade
                    'open': float(kline[1]),
                    'high': float(kline[2]),
                    'low': float(kline[3]),
                    'close': float(kline[4]),
                    'volume': float(kline[5]),
                    'turnover': float(kline[7]),  # Quote asset volume
                    'trades_count': int(kline[8]),
                    'buy_volume': float(kline[9]),
                    'open_interest': None,
                    'funding_rate': None,
                    'data_quality': 'rest_api'  # Mark as REST API data
                }
                historical_data.append(data)
                
            logger.info(f"📊 Fetched {len(historical_data)} historical records for {symbol} {timeframe}")
            return historical_data
            
        except Exception as e:
            logger.error(f"Failed to fetch historical data for {symbol}: {e}")
            return []
    
    async def get_funding_rate(self, symbol: str) -> Optional[float]:
        """Get current funding rate for a symbol"""
        try:
            binance_symbol = self.normalize_symbol(symbol)
            
            # Get funding rate info
            funding_info = self.rest_client.funding_rate(symbol=binance_symbol)
            
            if funding_info and 'fundingRate' in funding_info:
                return float(funding_info['fundingRate'])
            
            return None
            
        except Exception as e:
            logger.debug(f"Could not fetch funding rate for {symbol}: {e}")
            return None
    
    async def get_open_interest(self, symbol: str) -> Optional[float]:
        """Get current open interest for a symbol"""
        try:
            binance_symbol = self.normalize_symbol(symbol)
            
            # Get open interest statistics
            oi_stats = self.rest_client.open_interest(symbol=binance_symbol)
            
            if oi_stats and 'openInterest' in oi_stats:
                return float(oi_stats['openInterest'])
            
            return None
            
        except Exception as e:
            logger.debug(f"Could not fetch open interest for {symbol}: {e}")
            return None
    
    def get_standard_symbol_format(self, exchange_symbol: str) -> str:
        """Convert Binance symbol back to standard format"""
        # BTCUSDT -> BTC/USDT
        if exchange_symbol.endswith('USDT'):
            base = exchange_symbol[:-4]
            return f"{base}/USDT"
        return exchange_symbol
    
    def _normalize_timestamp(self, timestamp: datetime, timeframe: str) -> datetime:
        """
        🔧 Normalize timestamp to exact timeframe boundaries
        This ensures WebSocket and REST API data have identical timestamps
        
        Examples:
        - 5m: 21:14:59.999 -> 21:10:00.000
        - 15m: 21:14:59.999 -> 21:00:00.000  
        - 1h: 21:59:59.999 -> 21:00:00.000
        """
        # Convert timeframe to minutes
        timeframe_minutes = {
            '1m': 1, '3m': 3, '5m': 5, '15m': 15, '30m': 30,
            '1h': 60, '2h': 120, '4h': 240, '6h': 360,
            '8h': 480, '12h': 720, '1d': 1440, '3d': 4320,
            '1w': 10080, '1M': 43200
        }
        
        minutes = timeframe_minutes.get(timeframe, 5)
        
        # Round down to the nearest timeframe boundary
        if minutes < 60:
            # For sub-hour timeframes, round to nearest minute boundary
            timestamp = timestamp.replace(second=0, microsecond=0)
            # Round minute to timeframe boundary
            rounded_minute = (timestamp.minute // minutes) * minutes
            timestamp = timestamp.replace(minute=rounded_minute)
        elif minutes == 60:
            # For 1h, round to hour boundary
            timestamp = timestamp.replace(minute=0, second=0, microsecond=0)
        elif minutes < 1440:
            # For multi-hour timeframes
            timestamp = timestamp.replace(minute=0, second=0, microsecond=0)
            hours = minutes // 60
            rounded_hour = (timestamp.hour // hours) * hours
            timestamp = timestamp.replace(hour=rounded_hour)
        else:
            # For daily and above
            timestamp = timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
        
        return timestamp
    
