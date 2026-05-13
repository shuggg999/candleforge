"""
Configuration management for the Data Service
"""
from typing import List, Optional, Union
from pydantic_settings import BaseSettings
from pydantic import Field, field_validator


class Settings(BaseSettings):
    """Application settings"""
    
    # API Configuration
    API_HOST: str = Field(default="0.0.0.0", env="API_HOST")
    API_PORT: int = Field(default=8000, env="API_PORT")
    WS_PORT: int = Field(default=8001, env="WS_PORT")
    
    # ClickHouse Configuration
    CLICKHOUSE_HOST: str = Field(default="localhost", env="CLICKHOUSE_HOST")
    CLICKHOUSE_PORT: int = Field(default=8123, env="CLICKHOUSE_PORT")  # HTTP port for aiochclient
    CLICKHOUSE_DATABASE: str = Field(default="crypto_data", env="CLICKHOUSE_DATABASE")
    CLICKHOUSE_USER: str = Field(default="default", env="CLICKHOUSE_USER")
    CLICKHOUSE_PASSWORD: str = Field(default="", env="CLICKHOUSE_PASSWORD")
    
    # Data Collection Settings
    ENABLED_EXCHANGES: Union[str, List[str]] = Field(
        default=["binance"],
        env="ENABLED_EXCHANGES",
        description="Comma-separated list of exchanges"
    )
    SYMBOLS: str = Field(
        default="ALL",
        env="SYMBOLS",
        description="Symbols to track (ALL or comma-separated list)"
    )
    TIMEFRAMES: Union[str, List[str]] = Field(
        default=["1m", "5m", "15m", "1h", "4h", "1d"],
        env="TIMEFRAMES"
    )
    
    @field_validator('ENABLED_EXCHANGES', mode='before')
    def parse_enabled_exchanges(cls, v):
        if isinstance(v, str):
            return [s.strip() for s in v.split(',') if s.strip()]
        return v
    
    @field_validator('TIMEFRAMES', mode='before') 
    def parse_timeframes(cls, v):
        if isinstance(v, str):
            return [s.strip() for s in v.split(',') if s.strip()]
        return v
    
    # Data Retention (days)
    TTL_1M: int = Field(default=7, env="TTL_1M")
    TTL_5M: int = Field(default=30, env="TTL_5M")
    TTL_15M: int = Field(default=90, env="TTL_15M")
    TTL_1H: int = Field(default=365, env="TTL_1H")
    TTL_4H: int = Field(default=365, env="TTL_4H")
    TTL_1D: int = Field(default=1825, env="TTL_1D")
    
    # Recovery Settings
    RECOVERY_CHECK_INTERVAL: int = Field(
        default=60,  # Check every minute for gaps
        env="RECOVERY_CHECK_INTERVAL",
        description="Check interval in seconds"
    )
    MAX_GAP_MINUTES: int = Field(
        default=720,  # Allow filling larger gaps (12 hours)
        env="MAX_GAP_MINUTES",
        description="Maximum gap to fill via REST API"
    )
    
    # WebSocket connection settings
    MAX_SYMBOLS_PER_WS_CONNECTION: int = Field(
        default=100,
        env="MAX_SYMBOLS_PER_WS_CONNECTION",
        description="Maximum symbols per WebSocket connection"
    )
    
    MAX_TOTAL_SYMBOLS: int = Field(
        default=475,  # All USDT perpetual contracts
        env="MAX_TOTAL_SYMBOLS",
        description="Maximum total symbols to collect"
    )
    
    ENABLE_ALL_SYMBOLS: bool = Field(
        default=True,  # Enable all symbols by default
        env="ENABLE_ALL_SYMBOLS",
        description="Enable collection of all available symbols"
    )
    
    # Recovery coverage settings
    PRIORITY_SYMBOLS: List[str] = Field(
        default=['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT', 'DOGE/USDT', 'ADA/USDT', 'XRP/USDT', 'DOT/USDT', 'MATIC/USDT', 'AVAX/USDT'],
        description="Priority symbols for gap recovery"
    )
    
    RECOVERY_SYMBOLS_PER_CYCLE: int = Field(
        default=50,  # Check 50 symbols per recovery cycle
        env="RECOVERY_SYMBOLS_PER_CYCLE",
        description="Number of symbols to check per recovery cycle"
    )
    
    # Exchange API Keys (Optional)
    BINANCE_API_KEY: Optional[str] = Field(default=None, env="BINANCE_API_KEY")
    BINANCE_SECRET: Optional[str] = Field(default=None, env="BINANCE_SECRET")
    OKX_API_KEY: Optional[str] = Field(default=None, env="OKX_API_KEY")
    OKX_SECRET: Optional[str] = Field(default=None, env="OKX_SECRET")
    OKX_PASSPHRASE: Optional[str] = Field(default=None, env="OKX_PASSPHRASE")
    BYBIT_API_KEY: Optional[str] = Field(default=None, env="BYBIT_API_KEY")
    BYBIT_SECRET: Optional[str] = Field(default=None, env="BYBIT_SECRET")
    
    # System Settings
    LOG_LEVEL: str = Field(default="INFO", env="LOG_LEVEL")
    DEBUG: bool = Field(default=False, env="DEBUG")
    MAX_WEBSOCKET_CONNECTIONS: int = Field(default=1000, env="MAX_WEBSOCKET_CONNECTIONS")

    # Network proxy for outbound calls to Binance (REST + WebSocket).
    # Empty = direct connection. For deployments behind a SOCKS5 proxy, set e.g.
    # BINANCE_PROXY_URL=socks5h://host.docker.internal:10808
    BINANCE_PROXY_URL: str = Field(default="", env="BINANCE_PROXY_URL")

    # NATS event bus (introduce-nats-event-bus) — candleforge publishes K-line
    # events to subject `ohlcv.{exchange}.{symbol_normalized}.{timeframe}` after
    # each successful ClickHouse insert. Downstream services (volume-monitor,
    # telegram-bot) own their own business config; this repo MUST NOT contain
    # TELEGRAM_*, DETECTION_*, CLASSIFICATION_*, or ALERTS_* settings.
    NATS_URL: str = Field(default="nats://nats:4222", env="NATS_URL")
    NATS_ENABLE: bool = Field(default=True, env="NATS_ENABLE")
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True
    
    @property
    def clickhouse_url(self) -> str:
        """Get ClickHouse connection URL"""
        if self.CLICKHOUSE_PASSWORD:
            return f"clickhouse://{self.CLICKHOUSE_USER}:{self.CLICKHOUSE_PASSWORD}@{self.CLICKHOUSE_HOST}:{self.CLICKHOUSE_PORT}/{self.CLICKHOUSE_DATABASE}"
        return f"clickhouse://{self.CLICKHOUSE_USER}@{self.CLICKHOUSE_HOST}:{self.CLICKHOUSE_PORT}/{self.CLICKHOUSE_DATABASE}"
    
    @property
    def get_symbols_list(self) -> List[str]:
        """Get list of symbols to track"""
        if self.SYMBOLS == "ALL":
            return []  # Will be populated dynamically
        return [s.strip() for s in self.SYMBOLS.split(",")]
    
    def get_ttl_for_timeframe(self, timeframe: str) -> int:
        """Get TTL in days for a specific timeframe"""
        ttl_map = {
            "1m": self.TTL_1M,
            "5m": self.TTL_5M,
            "15m": self.TTL_15M,
            "1h": self.TTL_1H,
            "4h": self.TTL_4H,
            "1d": self.TTL_1D
        }
        return ttl_map.get(timeframe, 30)  # Default 30 days


# Create settings instance
settings = Settings()