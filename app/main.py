"""
MediaResolverAPI - Social media URL to direct download link resolver.

FastAPI application entry point.
"""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from .core.config import settings
from .core.database import init_db, SessionLocal
from .core.logging import setup_logging
from .api.resolve import router as resolve_router
from .api.dashboard import router as dashboard_router


def _cleanup_expired_data():
    """Clean up expired cache and old usage logs on startup."""
    try:
        from .models.usage_log import UsageLog
        from .models.video_cache import VideoCache

        db = SessionLocal()
        try:
            # Clear expired cache
            now = datetime.utcnow()
            expired = db.query(VideoCache).filter(VideoCache.expires_at < now).delete()
            if expired:
                logger.info(f"Cleaned up {expired} expired cache entries")

            # Clear old usage logs
            cutoff = now - timedelta(days=settings.USAGE_LOG_RETENTION_DAYS)
            old_logs = db.query(UsageLog).filter(UsageLog.created_at < cutoff).delete()
            if old_logs:
                logger.info(f"Cleaned up {old_logs} old usage log entries")

            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"Startup cleanup failed (non-fatal): {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown."""
    # Startup
    setup_logging()
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    init_db()
    logger.info("Database initialized")
    _cleanup_expired_data()
    yield
    # Shutdown
    logger.info("Shutting down")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Resolve social media URLs into direct download links with metadata.",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(resolve_router, prefix="/api", tags=["resolve"])
app.include_router(dashboard_router, prefix="/api", tags=["dashboard"])

# Static files for dashboard
_static_dir = Path(__file__).parent / "static"
if _static_dir.exists():
    app.mount("/dashboard", StaticFiles(directory=str(_static_dir), html=True), name="dashboard")


@app.get("/")
async def root():
    """Root endpoint with service info."""
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "running",
        "dashboard": "/dashboard/",
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler."""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"success": False, "error": "Internal server error"},
    )
