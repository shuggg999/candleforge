"""
Main FastAPI application for Freqtrade Data Service
"""
import asyncio
import signal
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
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
from src.detection import Detector, LoggingPublisher
from src.detection.api import router as detection_router, set_detector as set_detection_singleton
from src.detection.thresholds import DEFAULT_THRESHOLDS, merge_thresholds, parse_thresholds_env
from src.scheduler import build_scheduler
from src.alerts import Notifier, TelegramClient
from src.alerts.api import router as alerts_router, set_notifier as set_alerts_singleton


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
        self.detector: Optional[Detector] = None
        self.notifier: Optional[Notifier] = None
        self.scheduler = None
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
        
        # Start collectors (WS via proxy when BINANCE_PROXY_URL is set;
        # websockets 14+ + python-socks[asyncio] handle SOCKS5 transparently)
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

        # Telegram alerts notifier (add-telegram-alerts) — replaces LoggingPublisher injection
        try:
            tg_client = None
            if settings.TELEGRAM_BOT_TOKEN:
                tg_client = TelegramClient(
                    token=settings.TELEGRAM_BOT_TOKEN,
                    parse_mode=settings.TELEGRAM_PARSE_MODE,
                    proxy=settings.TELEGRAM_PROXY_URL or None,
                )
            self.notifier = Notifier(
                ch_client=adapt_aiochclient(self.db_manager.client),
                telegram_client=tg_client,
                chat_id=settings.TELEGRAM_CHAT_ID or None,
                dry_run=settings.ALERTS_DRY_RUN,
                exchange="binance",
            )
            set_alerts_singleton(self.notifier)
            logger.info(
                "📨 Telegram notifier initialized (dry_run={})",
                self.notifier._dry_run,
            )
        except Exception as e:
            logger.warning(f"⚠️ Notifier init failed (falling back to LoggingPublisher): {e}")
            self.notifier = None

        # Volume anomaly detector + scheduler (add-volume-detection)
        try:
            overrides = parse_thresholds_env(settings.DETECTION_THRESHOLDS)
            thresholds = merge_thresholds(DEFAULT_THRESHOLDS, overrides) if overrides else DEFAULT_THRESHOLDS
            publisher = self.notifier if self.notifier is not None else LoggingPublisher()
            self.detector = Detector(
                ch_client=adapt_aiochclient(self.db_manager.client),
                classifier=self.classifier,
                publisher=publisher,
                thresholds=thresholds,
                baseline_hours=settings.DETECTION_BASELINE_HOURS,
                current_minutes=settings.DETECTION_CURRENT_MINUTES,
                min_samples=settings.DETECTION_MIN_SAMPLES,
                interval_minutes=settings.DETECTION_INTERVAL_MINUTES,
                exchange="binance",
            )
            set_detection_singleton(self.detector)
            logger.info("📊 Volume anomaly detector initialized")

            # NOTE: cold-start backfill skipped by default (本地 dev 连不到 Binance);
            # production deploy on Jarvis will set BACKFILL_ON_STARTUP and call ensure_baseline_data.

            self.scheduler = build_scheduler(
                classifier=self.classifier,
                detector=self.detector,
                detection_interval_minutes=settings.DETECTION_INTERVAL_MINUTES,
                classification_refresh_hours=settings.CLASSIFICATION_REFRESH_HOURS,
            )
            self.scheduler.start()
            logger.info("⏰ Scheduler started (detection cycle + classification refresh)")
        except Exception as e:
            logger.warning(f"⚠️ Detection bootstrap failed (non-fatal): {e}")

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

        # Detection scheduler (add-volume-detection)
        if self.scheduler:
            try:
                self.scheduler.shutdown(wait=True)
            except Exception as e:
                logger.warning(f"Scheduler shutdown error: {e}")
        set_detection_singleton(None)
        set_alerts_singleton(None)

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

# Include volume detection routes
app.include_router(detection_router)

# Include telegram alerts routes
app.include_router(alerts_router)


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


def _safe_subprobe(name: str, fn):
    """Run a sub-probe callable, returning a failed-shape dict instead of raising.

    Sub-probe functions sometimes touch live state (DB connections, collector
    internals); we don't want any one of them taking the whole /health endpoint
    down with a 500. Wrap each call and surface the error inline so operators
    can see which subsystem misbehaved.
    """
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — sub-probes are arbitrary code
        logger.warning("Health sub-probe {!r} raised: {}", name, exc)
        return {"status": "failed", "reason": str(exc)}


def _compute_ws_collectors_health(collector_manager) -> Dict[str, Any]:
    """Aggregate WS-client state across all collectors into a single sub-probe dict.

    A client_key counts as "expected" if it appears in either `websocket_clients`
    (currently open) or `connection_health` (was opened at least once). It counts
    as "connected" only if it's currently in `websocket_clients`. The difference
    is what landed in `disconnected_client_keys`.
    """
    if collector_manager is None:
        return {
            "status": "failed",
            "reason": "collector_manager not initialized",
            "expected_count": 0,
            "connected_count": 0,
            "disconnected_client_keys": [],
        }

    expected_keys: set = set()
    connected_keys: set = set()
    for collector_id, collector in getattr(collector_manager, "collectors", {}).items():
        ws_clients = getattr(collector, "websocket_clients", {}) or {}
        conn_health = getattr(collector, "connection_health", {}) or {}
        for k in set(ws_clients.keys()) | set(conn_health.keys()):
            scoped = f"{collector_id}:{k}"
            expected_keys.add(scoped)
            if k in ws_clients:
                connected_keys.add(scoped)

    disconnected = sorted(expected_keys - connected_keys)
    expected_count = len(expected_keys)
    connected_count = len(connected_keys)

    if expected_count == 0:
        status = "failed"
    elif connected_count < expected_count:
        status = "degraded"
    else:
        status = "ok"

    return {
        "status": status,
        "expected_count": expected_count,
        "connected_count": connected_count,
        "disconnected_client_keys": disconnected,
    }


def _compute_recovery_health(recovery_service) -> Dict[str, Any]:
    """Expose recovery loop liveness as a sub-probe dict.

    `last_cycle_age_seconds` is None when the loop has never completed a cycle
    (either freshly started or currently disabled). Operators reading /health
    can distinguish "disabled" (is_running=False) from "stuck" (is_running=True
    but age > 5min) without consulting logs.
    """
    if recovery_service is None:
        return {
            "status": "failed",
            "reason": "recovery_service not initialized",
            "is_running": False,
            "last_cycle_age_seconds": None,
        }

    is_running = bool(getattr(recovery_service, "is_running", False))
    last_cycle_ts = getattr(recovery_service, "last_cycle_ts", None)
    if last_cycle_ts is None:
        age_seconds = None
    else:
        age_seconds = (datetime.now(timezone.utc) - last_cycle_ts).total_seconds()

    if not is_running:
        # Recovery service intentionally disabled in current main.py startup;
        # treat as degraded (not failed) so operators see the signal but service
        # stays usable.
        status = "degraded"
    elif age_seconds is None or age_seconds > 300:
        status = "degraded"
    else:
        status = "ok"

    return {
        "status": status,
        "is_running": is_running,
        "last_cycle_age_seconds": age_seconds,
    }


@app.get("/api/v1/health")
async def health_check():
    """Per-module health probe — single source of truth for /api/v1/health.

    `src/api/routes.py` MUST NOT register a duplicate `/health` route — FastAPI
    keeps the first-registered handler and silently overrides this one. See
    `openspec/specs/baseline-infrastructure/spec.md` (Module Wiring Convention).
    """
    try:
        status = await service.get_status()
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Health check get_status failed: {exc}")
        return JSONResponse(
            status_code=503,
            content=jsonable_encoder({"status": "unhealthy", "error": str(exc)}),
        )

    if not status.get("running"):
        return JSONResponse(
            status_code=503,
            content=jsonable_encoder(
                {"status": "unhealthy", "error": "Service not running", "details": status}
            ),
        )

    health_status = "healthy"
    if status.get("database") != "healthy":
        health_status = "degraded"

    # Module sub-probes — each wrapped so one failure doesn't crash /health.
    classification_health = _safe_subprobe(
        "classification",
        lambda: service.classifier.health() if service.classifier is not None else {
            "status": "failed",
            "reason": "classifier not initialized",
        },
    )
    status["classification"] = classification_health
    if classification_health.get("status") != "ok":
        health_status = "degraded"

    detection_health = _safe_subprobe(
        "detection",
        lambda: service.detector.health() if service.detector is not None else {
            "status": "failed",
            "reason": "detector not initialized",
        },
    )
    status["detection"] = detection_health
    if detection_health.get("status") != "ok":
        health_status = "degraded"

    alerts_health = _safe_subprobe(
        "alerts",
        lambda: service.notifier.health() if service.notifier is not None else {
            "status": "failed",
            "reason": "notifier not initialized",
        },
    )
    status["alerts"] = alerts_health
    if alerts_health.get("status") != "ok":
        health_status = "degraded"

    # WebSocket collectors — aggregate WS-client state across all collectors
    ws_health = _safe_subprobe(
        "ws_collectors",
        lambda: _compute_ws_collectors_health(service.collector_manager),
    )
    status["ws_collectors"] = ws_health
    if ws_health.get("status") != "ok":
        health_status = "degraded"

    # Recovery loop liveness — replaces the legacy `recovery: "running"|"stopped"` string.
    recovery_health = _safe_subprobe(
        "recovery",
        lambda: _compute_recovery_health(service.recovery_service),
    )
    status["recovery"] = recovery_health
    if recovery_health.get("status") != "ok":
        health_status = "degraded"

    # status dict may carry datetime values via collector_manager.get_status()
    # (last_message_time etc.); JSONResponse doesn't run FastAPI's encoder by
    # default, so do it explicitly.
    return JSONResponse(
        status_code=200 if health_status == "healthy" else 503,
        content=jsonable_encoder({"status": health_status, "details": status}),
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