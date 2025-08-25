# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a cryptocurrency futures data service that provides high-quality OHLCV data for Freqtrade trading strategies. It uses WebSocket connections to collect real-time data from exchanges (Binance, OKX, Bybit) and stores it in ClickHouse for fast querying.

## Development Commands

### Environment Setup
```bash
# Create conda environment
./setup_env.sh

# Or manually:
conda env create -f environment.yml
conda activate freqtrade-data-service
```

### Running the Service
```bash
# Start ClickHouse database
docker-compose up -d

# Start the data service
python -m src.main

# Health check
curl http://localhost:8000/api/v1/health
```

### Testing and Development
```bash
# Run tests (basic test file exists)
python test_connection.py

# Run with pytest (if tests are added)
pytest tests/

# Format code
black src/

# Monitor service
./monitor_data.sh
```

### Docker Operations
```bash
# Build and run with Docker
docker-compose up -d

# View logs
docker-compose logs -f data-service
docker-compose logs -f clickhouse

# Restart services
docker-compose restart
```

## Architecture Overview

### Core Components
- **FastAPI Service** (`src/main.py`): Main application with REST API and WebSocket endpoints
- **Data Collectors** (`src/collectors/`): WebSocket clients for each exchange
- **ClickHouse Storage** (`src/storage/clickhouse.py`): Time-series database operations
- **Recovery Service** (`src/services/recovery.py`): Fills data gaps using REST APIs
- **API Routes** (`src/api/routes.py`): REST endpoints for data access

### Data Flow
1. WebSocket collectors receive real-time kline data from exchanges
2. Data is validated and stored in ClickHouse `ohlcv_futures` table
3. Recovery service detects and fills gaps using REST API fallback
4. FastAPI serves data through REST endpoints with multi-timeframe support

### Key Features
- **Multi-Exchange Support**: Binance (primary), OKX, Bybit
- **WebSocket Data Collection**: Real-time data without API rate limits
- **Smart Data Recovery**: Automatic gap detection and filling
- **Multi-Timeframe Queries**: Single request for multiple timeframes (1m, 5m, 15m, 1h, 4h, 1d)
- **TTL Management**: Automatic data cleanup based on timeframe

## Configuration

### Environment Variables (.env)
- `CLICKHOUSE_HOST/PORT/DATABASE`: Database connection
- `ENABLED_EXCHANGES`: Comma-separated list of exchanges
- `LOG_LEVEL`: Logging verbosity
- Exchange API keys (optional, for REST backup)

### Key Settings (`src/config.py`)
- `MAX_SYMBOLS_PER_WS_CONNECTION=100`: WebSocket connection limits
- `RECOVERY_CHECK_INTERVAL=60`: Gap detection frequency
- TTL settings for each timeframe (1m: 7 days, 5m: 30 days, etc.)

## Database Schema

### Main Table: `ohlcv_futures`
```sql
- exchange (String): binance/okx/bybit
- symbol (String): BTC/USDT format
- timeframe (String): 1m/5m/15m/1h/4h/1d
- timestamp (DateTime64): millisecond precision
- ohlcv data (Decimal64): price/volume fields
- Optional fields: open_interest, funding_rate
```

Partitioned by month, ordered by (exchange, symbol, timeframe, timestamp).

## API Endpoints

### Main Endpoints
- `GET /api/v1/health`: Service health status
- `GET /api/v1/ohlcv/{exchange}/{symbol}/{timeframe}`: Single timeframe data
- `GET /api/v1/multi-timeframe/{symbol}`: All timeframes for a symbol
- `GET /api/v1/status`: Detailed service status
- `GET /dashboard`: Web UI for monitoring

### Key Parameters
- `limit`: Number of candles (default: 1000, max: 5000)
- `start/end`: Timestamp range filtering

## Troubleshooting

### Common Issues
1. **WebSocket Disconnections**: Check network connectivity, service auto-reconnects
2. **Data Gaps**: Recovery service runs every minute, check logs for REST API issues
3. **High Memory Usage**: Adjust symbol counts or TTL settings in config
4. **ClickHouse Connection**: Ensure Docker container is running and ports are accessible

### Log Locations
- Application logs: `logs/data_service.log` (rotated daily)
- Docker logs: `docker-compose logs data-service`
- Monitor script: `logs/monitor.log`

### Health Monitoring
Use `./monitor_data.sh` for real-time monitoring of:
- Service status and connection health
- Data ingestion rates and error counts
- Database connectivity and recent data counts
- System resource usage

## File Structure Notes

- `src/collectors/`: Exchange-specific WebSocket implementations
- `src/storage/`: Database operations and models
- `src/api/`: REST API routes and WebSocket handlers
- `src/services/`: Background services (recovery, aggregation)
- `src/utils/`: Logging, formatting, and utility functions
- `config/`: ClickHouse and logging configurations
- `scripts/`: Operational scripts for data migration and cleanup