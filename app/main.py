import logging
from contextlib import asynccontextmanager
from pathlib import Path

import anyio.to_thread
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.api.routes import (
    actions,
    auth,
    colleges,
    exports,
    health,
    internal,
    ppms,
    scores,
    secteurs,
    stats,
)
from app.core.config import settings
from app.core.logging import RequestLogMiddleware, configure_logging
from app.core.monitoring import init_error_reporting
from app.core.security import bootstrap_admin, validate_security_settings
from app.data.store import get_store
from app.db import init_db

configure_logging(settings.log_format, settings.log_level)
init_error_reporting("api")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_security_settings()
    # Fewer worker threads than FastAPI's default 40: CPU-bound endpoints share one GIL, and
    # with 40 of them the event loop (which sends responses and returns connections to the
    # pool) is starved under load. Scale with processes instead (WEB_CONCURRENCY).
    anyio.to_thread.current_default_thread_limiter().total_tokens = settings.worker_threads
    if settings.rate_limit_storage_uri.startswith("memory://"):
        logger.warning(
            "Rate limits are kept in memory: they reset on restart and are not "
            "shared between workers. Set SRT_RATE_LIMIT_STORAGE_URI for production."
        )
    init_db()
    bootstrap_admin()
    store = get_store()  # load the reference data once
    logger.info(
        "startup complete",
        extra={"version": settings.build_version, "data_source": store.source,
               "schools": len(store.list_colleges())},
    )
    yield


app = FastAPI(
    title=settings.app_name,
    description="Track natural and technological risks for collèges, department by department.",
    version=settings.build_version,
    lifespan=lifespan,
)

app.state.limiter = auth.limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.add_middleware(RequestLogMiddleware)
# All of France is ~3 MB of school summaries and ~12 MB of sectors: compress JSON and GeoJSON.
# Level 5: ~8× faster than the default 9 on 2 MB of JSON, for 1 % more bytes.
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)

if settings.trusted_proxies:
    # Added last so it runs first: rate limiting, request logs and the audit log
    # then see the real client IP instead of the proxy's.
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=settings.trusted_proxies)

for r in (
    auth.router, colleges.router, ppms.router, secteurs.router, actions.router, stats.router,
    exports.router, internal.router, scores.router, stats.territories_router,
):
    app.include_router(r, prefix=settings.api_prefix)
app.include_router(health.router)


app.mount(
    "/",
    StaticFiles(directory=Path(__file__).resolve().parent.parent / "frontend", html=True),
    name="frontend",
)
