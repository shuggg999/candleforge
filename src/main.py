"""
Main FastAPI application for Freqtrade Data Service
"""
import asyncio
import signal
import sys
from contextlib import asynccontextmanager
from typing import Dict, Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import uvicorn
from loguru import logger

from src.config import settings
from src.api import routes
from src.storage.clickhouse import ClickHouseManager
from src.collectors.manager import CollectorManager
from src.services.recovery import RecoveryService
from src.utils.log_manager import log_manager
from src.utils.recovery_manager import auto_recovery
from src.classification import Classifier
from src.classification.classifier import adapt_aiochclient
from src.classification.api import router as classification_router, set_classifier as set_classification_singleton


# Configure logger
logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>",
    level=settings.LOG_LEVEL
)
logger.add(
    "logs/data_service.log",
    rotation="50 MB",  # 改为大小轮转，避免单文件过大
    retention="7 days",
    compression="gz",  # 启用压缩
    enqueue=True,      # 异步写入，避免阻塞
    level=settings.LOG_LEVEL
)


class DataService:
    """Main application service manager"""
    
    def __init__(self):
        self.db_manager: ClickHouseManager = None
        self.collector_manager: CollectorManager = None
        self.recovery_service: RecoveryService = None
        self.classifier: Optional[Classifier] = None
        self.is_running = False
    
    async def startup(self):
        """Initialize all services"""
        logger.info("🚀 Starting Freqtrade Data Service...")
        
        # Initialize database
        logger.info("📊 Initializing ClickHouse connection...")
        self.db_manager = ClickHouseManager()
        await self.db_manager.initialize()
        
        # Initialize collector manager
        logger.info("📡 Initializing collectors...")
        self.collector_manager = CollectorManager(self.db_manager)
        await self.collector_manager.initialize()
        
        # Initialize recovery service
        logger.info("🔄 Initializing recovery service...")
        self.recovery_service = RecoveryService(self.db_manager)
        self.recovery_service.set_collector_manager(self.collector_manager)
        
        # 🗂️ Initialize log management
        logger.info("🗂️ Starting log rotation scheduler...")
        asyncio.create_task(log_manager.schedule_rotation(interval_hours=24))
        
        # 🔄 Initialize auto recovery system
        logger.info("🔄 Setting up auto recovery monitoring...")
        await self._setup_auto_recovery()
        await auto_recovery.start_monitoring()
        
        # Set API dependencies
        from src.api import routes
        routes.set_managers(self.db_manager, self.collector_manager)
        
        # Start collectors
        logger.info("▶️ Starting data collectors...")
        await self.collector_manager.start_all()
        
        # Start recovery service - TEMPORARILY DISABLED
        # asyncio.create_task(self.recovery_service.start())
        logger.info("⚠️ Recovery service temporarily disabled")
        
        # Volume tier classifier (add-volume-classification)
        try:
            self.classifier = Classifier(
                ch_client=adapt_aiochclient(self.db_manager.client),
                refresh_hours=settings.CLASSIFICATION_REFRESH_HOURS,
                exchange="binance",
            )
            await self.classifier.refresh_tiers()
            logger.info("📐 Volume tier classifier initialized")
        except Exception as e:
            logger.warning(f"⚠️ Classifier initial refresh failed (non-fatal): {e}")
        set_classification_singleton(self.classifier)

        self.is_running = True
        logger.info("✅ Data Service is ready!")
    
    async def _setup_auto_recovery(self):
        """🔄 设置自动恢复监控组件"""
        
        # 注册ClickHouse数据库健康检查
        auto_recovery.register_component(
            name="clickhouse_db",
            health_checker=self._check_db_health,
            recovery_handler=self._recover_db,
            critical=True
        )
        
        # 注册收集器健康检查
        auto_recovery.register_component(
            name="data_collectors", 
            health_checker=self._check_collectors_health,
            recovery_handler=self._recover_collectors,
            critical=True
        )
        
        # 注册恢复服务健康检查
        auto_recovery.register_component(
            name="recovery_service",
            health_checker=self._check_recovery_health,
            recovery_handler=self._recover_recovery_service,
            critical=False
        )
        
        logger.info("🔄 Auto recovery components registered")
    
    async def _check_db_health(self) -> bool:
        """检查数据库健康状态"""
        if not self.db_manager:
            return False
        try:
            health = await self.db_manager.health_check()
            return health == "healthy"
        except:
            return False
    
    async def _recover_db(self) -> bool:
        """恢复数据库连接"""
        try:
            logger.info("🔧 Attempting to recover ClickHouse connection...")
            if self.db_manager:
                await self.db_manager.close()
            
            self.db_manager = ClickHouseManager()
            await self.db_manager.initialize()
            
            # 重新设置API依赖
            from src.api import routes
            routes.set_managers(self.db_manager, self.collector_manager)
            
            logger.info("✅ ClickHouse connection recovered")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to recover ClickHouse: {e}")
            return False
    
    async def _check_collectors_health(self) -> bool:
        """检查收集器健康状态"""
        if not self.collector_manager:
            return False
        try:
            status = await self.collector_manager.get_status()
            return status.get('running', False)
        except:
            return False
    
    async def _recover_collectors(self) -> bool:
        """恢复收集器"""
        try:
            logger.info("🔧 Attempting to recover data collectors...")
            if self.collector_manager:
                await self.collector_manager.stop_all()
                await asyncio.sleep(5)
                await self.collector_manager.start_all()
            
            logger.info("✅ Data collectors recovered")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to recover collectors: {e}")
            return False
    
    async def _check_recovery_health(self) -> bool:
        """检查恢复服务健康状态"""
        if not self.recovery_service:
            return False
        return self.recovery_service.is_running
    
    async def _recover_recovery_service(self) -> bool:
        """恢复恢复服务"""
        try:
            logger.info("🔧 Attempting to recover recovery service...")
            if self.recovery_service:
                await self.recovery_service.stop()
                await asyncio.sleep(2)
                asyncio.create_task(self.recovery_service.start())
            
            logger.info("✅ Recovery service recovered")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to recover recovery service: {e}")
            return False
    
    async def shutdown(self):
        """Cleanup all services"""
        logger.info("🛑 Shutting down Data Service...")
        self.is_running = False

        # Volume tier classifier
        if self.classifier:
            try:
                await self.classifier.shutdown()
            except Exception as e:
                logger.warning(f"Classifier shutdown error: {e}")

        # 🔄 Stop auto recovery monitoring
        await auto_recovery.stop_monitoring()
        
        if self.collector_manager:
            await self.collector_manager.stop_all()
        
        if self.recovery_service:
            await self.recovery_service.stop()
        
        if self.db_manager:
            await self.db_manager.close()
        
        # 🗂️ Final log rotation before shutdown
        try:
            await log_manager.rotate_logs()
            logger.info("📋 Final log rotation completed")
        except Exception as e:
            logger.error(f"Error in final log rotation: {e}")
        
        logger.info("👋 Data Service stopped")
    
    async def get_status(self) -> Dict[str, Any]:
        """Get service status"""
        status = {
            "running": self.is_running,
            "database": "unknown",
            "collectors": {},
            "recovery": "unknown"
        }
        
        if self.db_manager:
            status["database"] = await self.db_manager.health_check()
        
        if self.collector_manager:
            status["collectors"] = await self.collector_manager.get_status()
        
        if self.recovery_service:
            status["recovery"] = "running" if self.recovery_service.is_running else "stopped"
        
        return status


# Create service instance
service = DataService()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    # Startup
    await service.startup()
    yield
    # Shutdown
    await service.shutdown()


# Create FastAPI app
app = FastAPI(
    title="Freqtrade Data Service",
    description="High-performance cryptocurrency futures data service",
    version="1.0.0",
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routes
app.include_router(routes.router, prefix="/api/v1")

# Include Freqtrade-compatible API routes
from src.api.freqtrade import router as freqtrade_router
app.include_router(freqtrade_router, prefix="/api/v1")

# Include volume classification routes (prefix is set inside the router)
app.include_router(classification_router)


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": "Freqtrade Data Service",
        "version": "1.0.0",
        "status": "running",
        "docs": "/docs",
        "dashboard": "/dashboard",
        "health": "/api/v1/health"
    }


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Data visualization dashboard"""
    try:
        dashboard_path = Path(__file__).parent.parent / "templates" / "dashboard.html"
        with open(dashboard_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(
            content="<h1>Dashboard not found</h1><p>Dashboard template is missing.</p>",
            status_code=404
        )


@app.get("/api/v1/health")
async def health_check():
    """Health check endpoint"""
    try:
        status = await service.get_status()

        # Determine overall health
        if not status["running"]:
            raise HTTPException(status_code=503, detail="Service not running")

        health_status = "healthy"
        if status["database"] != "healthy":
            health_status = "degraded"

        # Classification sub-probe (add-volume-classification)
        if service.classifier is not None:
            classification_health = service.classifier.health()
        else:
            classification_health = {
                "status": "failed",
                "reason": "classifier not initialized",
            }
        status["classification"] = classification_health
        if classification_health["status"] != "ok":
            health_status = "degraded"

        return JSONResponse(
            status_code=200 if health_status == "healthy" else 503,
            content={
                "status": health_status,
                "details": status
            }
        )
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "error": str(e)
            }
        )


def handle_signal(signum, frame):
    """Handle system signals"""
    logger.info(f"Received signal {signum}")
    sys.exit(0)


if __name__ == "__main__":
    # Register signal handlers
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    
    # Run the application
    uvicorn.run(
        "src.main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower()
    )