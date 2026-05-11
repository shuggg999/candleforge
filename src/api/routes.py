"""
API routes for the data service
"""
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field

from src.storage.clickhouse import ClickHouseManager
from src.collectors.manager import CollectorManager
from src.utils.formatter import formatter
from src.utils.log_manager import log_manager
from src.utils.recovery_manager import auto_recovery


router = APIRouter()


# Pydantic models
class OHLCVResponse(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    turnover: Optional[float] = None


class StatusResponse(BaseModel):
    service: str = "Freqtrade Data Service"
    version: str = "1.0.0"
    status: str
    database: str
    collectors: Dict[str, Any]


# Dependency injection (will be set by main.py)
_db_manager: Optional[ClickHouseManager] = None
_collector_manager: Optional[CollectorManager] = None


def set_managers(db_manager: ClickHouseManager, collector_manager: CollectorManager):
    """Set manager instances for dependency injection"""
    global _db_manager, _collector_manager
    _db_manager = db_manager
    _collector_manager = collector_manager


def get_db_manager() -> ClickHouseManager:
    if _db_manager is None:
        raise HTTPException(status_code=503, detail="Database manager not initialized")
    return _db_manager


def get_collector_manager() -> CollectorManager:
    if _collector_manager is None:
        raise HTTPException(status_code=503, detail="Collector manager not initialized")
    return _collector_manager


@router.get("/status", response_model=StatusResponse)
async def get_status(
    db: ClickHouseManager = Depends(get_db_manager),
    collectors: CollectorManager = Depends(get_collector_manager)
):
    """Get service status"""
    try:
        db_health = await db.health_check()
        collectors_status = await collectors.get_status()
        
        return StatusResponse(
            status="healthy" if db_health == "healthy" else "degraded",
            database=db_health,
            collectors=collectors_status
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Status check failed: {str(e)}")


@router.get("/exchanges")
async def get_exchanges(
    collectors: CollectorManager = Depends(get_collector_manager)
):
    """Get list of supported exchanges"""
    try:
        status = await collectors.get_status()
        exchanges = list(status.get('collectors', {}).keys())
        
        return {
            "exchanges": exchanges,
            "total": len(exchanges)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get exchanges: {str(e)}")


@router.get("/symbols/{exchange}")
async def get_symbols(
    exchange: str,
    collectors: CollectorManager = Depends(get_collector_manager)
):
    """Get symbols for a specific exchange"""
    try:
        symbols = await collectors.get_collector_symbols(exchange)
        
        if not symbols:
            raise HTTPException(status_code=404, detail=f"Exchange {exchange} not found or no symbols")
        
        return {
            "exchange": exchange,
            "symbols": symbols,
            "total": len(symbols)
        }
    except Exception as e:
        if "not found" in str(e):
            raise e
        raise HTTPException(status_code=500, detail=f"Failed to get symbols: {str(e)}")


@router.get("/ohlcv/{exchange}/{symbol}")
async def get_ohlcv(
    exchange: str,
    symbol: str,
    timeframe: str = Query(..., description="Timeframe (1m, 5m, 15m, 1h, 4h, 1d)"),
    start_time: Optional[datetime] = Query(None, description="Start time (ISO format)"),
    end_time: Optional[datetime] = Query(None, description="End time (ISO format)"),
    limit: int = Query(1000, description="Maximum number of records", le=5000),
    db: ClickHouseManager = Depends(get_db_manager)
):
    """Get OHLCV data for a specific symbol"""
    try:
        # Validate timeframe
        valid_timeframes = ['1m', '5m', '15m', '30m', '1h', '4h', '1d']
        if timeframe not in valid_timeframes:
            raise HTTPException(
                status_code=400, 
                detail=f"Invalid timeframe. Must be one of: {', '.join(valid_timeframes)}"
            )
        
        # Set default time range if not provided
        if not end_time:
            end_time = datetime.now(timezone.utc)
        if not start_time:
            start_time = end_time - timedelta(days=1)  # Default to 1 day
        
        # Fetch data
        data = await db.get_ohlcv_data(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            start_time=start_time,
            end_time=end_time,
            limit=limit
        )
        
        return {
            "exchange": exchange,
            "symbol": symbol,
            "timeframe": timeframe,
            "start_time": start_time,
            "end_time": end_time,
            "data": data,
            "count": len(data)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get OHLCV data: {str(e)}")


@router.get("/stats")
async def get_statistics(
    collectors: CollectorManager = Depends(get_collector_manager),
    db: ClickHouseManager = Depends(get_db_manager)
):
    """Get service statistics"""
    try:
        # Get collector statistics
        collector_stats = await collectors.get_statistics()
        
        # Get database statistics
        db_stats = await db.get_symbols_stats()
        
        # Get database size info
        db_size = await db.get_database_size()
        
        stats_data = {
            "collectors": collector_stats,
            "database": {
                "symbols_stats": db_stats,
                "size_info": db_size,
                "total_records": sum(s['records_count'] for s in db_stats) if db_stats else 0
            },
            "summary": {
                "total_symbols": len(set((s['exchange'], s['symbol']) for s in db_stats)) if db_stats else 0,
                "total_records": sum(s['records_count'] for s in db_stats) if db_stats else 0,
                "total_messages": collector_stats.get('total_messages', 0),
                "total_errors": collector_stats.get('total_errors', 0)
            }
        }
        
        return stats_data
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get statistics: {str(e)}")


@router.get("/stats/display")
async def get_stats_display(
    collectors: CollectorManager = Depends(get_collector_manager),
    db: ClickHouseManager = Depends(get_db_manager)
):
    """Get formatted statistics display"""
    try:
        # Get statistics
        collector_stats = await collectors.get_statistics()
        db_stats = await db.get_symbols_stats()
        db_size = await db.get_database_size()
        
        stats_data = {
            "collectors": collector_stats,
            "database": {
                "symbols_stats": db_stats,
                "size_info": db_size,
                "total_records": sum(s['records_count'] for s in db_stats) if db_stats else 0
            }
        }
        
        # Format for display
        formatted_display = formatter.format_summary_stats(stats_data)
        
        return {"formatted_display": formatted_display}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get formatted stats: {str(e)}")


@router.get("/gaps/{exchange}/{symbol}")
async def get_data_gaps(
    exchange: str,
    symbol: str,
    timeframe: str = Query(..., description="Timeframe to check for gaps"),
    start_time: datetime = Query(..., description="Start time for gap analysis"),
    end_time: datetime = Query(..., description="End time for gap analysis"),
    db: ClickHouseManager = Depends(get_db_manager)
):
    """Find data gaps for a specific symbol"""
    try:
        gaps = await db.find_data_gaps(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            start_time=start_time,
            end_time=end_time
        )
        
        return {
            "exchange": exchange,
            "symbol": symbol,
            "timeframe": timeframe,
            "analysis_period": {
                "start": start_time,
                "end": end_time
            },
            "gaps": [
                {
                    "start": gap[0],
                    "end": gap[1],
                    "duration_minutes": (gap[1] - gap[0]).total_seconds() / 60
                }
                for gap in gaps
            ],
            "total_gaps": len(gaps)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to analyze gaps: {str(e)}")


@router.post("/collectors/{exchange}/restart")
async def restart_collector(
    exchange: str,
    collectors: CollectorManager = Depends(get_collector_manager)
):
    """Restart a specific collector"""
    try:
        await collectors.restart_collector(exchange)
        
        return {
            "message": f"Collector {exchange} restarted successfully",
            "exchange": exchange,
            "timestamp": datetime.now(timezone.utc)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to restart collector: {str(e)}")


@router.post("/collectors/{exchange}/symbols")
async def add_symbol(
    exchange: str,
    symbol: str = Query(..., description="Symbol to add"),
    collectors: CollectorManager = Depends(get_collector_manager)
):
    """Add a symbol to a collector"""
    try:
        await collectors.add_symbol(exchange, symbol)
        
        return {
            "message": f"Symbol {symbol} added to {exchange} collector",
            "exchange": exchange,
            "symbol": symbol,
            "timestamp": datetime.now(timezone.utc)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to add symbol: {str(e)}")


@router.delete("/collectors/{exchange}/symbols/{symbol}")
async def remove_symbol(
    exchange: str,
    symbol: str,
    collectors: CollectorManager = Depends(get_collector_manager)
):
    """Remove a symbol from a collector"""
    try:
        await collectors.remove_symbol(exchange, symbol)
        
        return {
            "message": f"Symbol {symbol} removed from {exchange} collector",
            "exchange": exchange,
            "symbol": symbol,
            "timestamp": datetime.now(timezone.utc)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to remove symbol: {str(e)}")


@router.post("/maintenance/cleanup")
async def cleanup_data(
    db: ClickHouseManager = Depends(get_db_manager)
):
    """Manually trigger data cleanup"""
    try:
        await db.cleanup_old_data()
        
        return {
            "message": "Data cleanup completed successfully",
            "timestamp": datetime.now(timezone.utc)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to cleanup data: {str(e)}")


# NOTE: The `/health` endpoint is owned exclusively by `src/main.py` and exposes
# per-module sub-probes (database, classification, detection, alerts, ws_collectors,
# recovery). Do NOT re-register `@router.get("/health")` here — FastAPI keeps the
# first registered handler and silently overrides later ones, which is how the
# 2026-05-12 sub-probe regression was introduced. See `openspec/changes/
# fix-ttl-and-health-probe/` and CLAUDE.md "Common Issues #6".

# 🗂️ Log Management Endpoints
@router.get("/admin/logs/stats")
async def get_log_stats():
    """📊 Get log file statistics"""
    try:
        stats = await log_manager.get_log_stats()
        return {
            "status": "success",
            "data": stats,
            "timestamp": datetime.now(timezone.utc)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get log stats: {str(e)}")


@router.post("/admin/logs/rotate")
async def rotate_logs():
    """🔄 Manually trigger log rotation"""
    try:
        result = await log_manager.rotate_logs()
        return {
            "status": "success",
            "message": "Log rotation completed",
            "data": result,
            "timestamp": datetime.now(timezone.utc)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Log rotation failed: {str(e)}")


# 🔄 Auto Recovery Endpoints
@router.get("/admin/recovery/status")
async def get_recovery_status():
    """📊 Get auto recovery system status"""
    try:
        status = auto_recovery.get_all_status()
        return {
            "status": "success", 
            "data": status,
            "timestamp": datetime.now(timezone.utc)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get recovery status: {str(e)}")


@router.post("/admin/recovery/force/{component_name}")
async def force_component_recovery(component_name: str):
    """🔧 Force recovery for a specific component"""
    try:
        success = await auto_recovery.force_recovery(component_name)
        
        if success:
            return {
                "status": "success",
                "message": f"Component {component_name} recovery completed",
                "timestamp": datetime.now(timezone.utc)
            }
        else:
            return {
                "status": "failed",
                "message": f"Component {component_name} recovery failed",
                "timestamp": datetime.now(timezone.utc)
            }
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Recovery failed: {str(e)}")


@router.post("/admin/recovery/reset/{component_name}")
async def reset_component_status(component_name: str):
    """🔄 Reset component status to healthy"""
    try:
        await auto_recovery.reset_component(component_name)
        return {
            "status": "success",
            "message": f"Component {component_name} status reset to healthy",
            "timestamp": datetime.now(timezone.utc)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Reset failed: {str(e)}")


# 🏥 Enhanced System Health Endpoint  
@router.get("/admin/system/health")
async def get_system_health(
    db: ClickHouseManager = Depends(get_db_manager),
    collectors: CollectorManager = Depends(get_collector_manager)
):
    """🏥 Comprehensive system health check"""
    try:
        # Get basic status
        db_health = await db.health_check()
        collectors_status = await collectors.get_status()
        
        # Get auto recovery status
        recovery_status = auto_recovery.get_all_status()
        
        # Get database performance stats
        db_performance = await db.get_batch_performance_stats()
        
        # Determine overall system health
        overall_health = "healthy"
        if db_health != "healthy":
            overall_health = "degraded"
        if recovery_status.get('overall_health') == 'critical':
            overall_health = "critical"
        
        return {
            "status": "success",
            "overall_health": overall_health,
            "components": {
                "database": {
                    "status": db_health,
                    "performance": db_performance
                },
                "collectors": collectors_status,
                "auto_recovery": recovery_status
            },
            "timestamp": datetime.now(timezone.utc)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Health check failed: {str(e)}")
