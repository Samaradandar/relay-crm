# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Creates the FastAPI app, mounts every router, /health."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import get_settings, validate_for_start
from app.core.db import dispose_engine, get_sessionmaker
from app.core.errors import register_error_handlers
from app.core.limits import BodyLimitMiddleware
from app.modules.accounts.router import router as accounts_router
from app.modules.activities.router import router as activities_router
from app.modules.billing.router import router as billing_router
from app.modules.capture.router import router as capture_router
from app.modules.catalog.router import router as catalog_router
from app.modules.churn.router import router as churn_router
from app.modules.customers.router import router as customers_router
from app.modules.deals.router import router as deals_router
from app.modules.documents.router import router as documents_router
from app.modules.exports.router import router as exports_router
from app.modules.google.router import router as google_router
from app.modules.leads.assignment.router import router as assignment_router
from app.modules.leads.router import router as leads_router
from app.modules.performance.router import router as performance_router
from app.modules.pipeline.config.router import router as pipeline_config_router
from app.modules.pipeline.engine.router import router as pipeline_engine_router
from app.modules.profiles.router import router as profiles_router
from app.modules.proposals.router import router as proposals_router
from app.modules.usage_feed.router import router as usage_router
from app.modules.users.router import router as users_router
from app.platform import api as platform_api
from app.platform import registry


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
    stop, task = await platform_api.start_realtime()
    try:
        yield
    finally:
        await platform_api.stop_realtime(stop, task)
        await dispose_engine()


def create_app() -> FastAPI:
    registry.setup()
    settings = get_settings()
    validate_for_start(settings)
    app = FastAPI(title="Relay CRM", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(BodyLimitMiddleware)
    register_error_handlers(app)

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", tags=["system"], response_model=None)
    async def ready() -> dict[str, str] | JSONResponse:
        """For the uptime check and the load balancer: answers only when the database does
        (``/health`` above only says the process is up)."""
        try:
            async with get_sessionmaker()() as s:
                await s.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001 - any failure means "not ready"
            return JSONResponse({"status": "unavailable", "database": "down"}, status_code=503)
        return {"status": "ok", "database": "ok"}

    app.include_router(platform_api.router)
    app.include_router(users_router)
    app.include_router(deals_router)  # before leads: /deals/bulk/* must win over /deals/{id}/*
    app.include_router(accounts_router)
    app.include_router(leads_router)
    app.include_router(assignment_router)
    app.include_router(pipeline_config_router)
    app.include_router(pipeline_engine_router)
    app.include_router(activities_router)
    app.include_router(catalog_router)
    app.include_router(billing_router)
    app.include_router(customers_router)
    app.include_router(churn_router)
    app.include_router(usage_router)
    app.include_router(proposals_router)
    app.include_router(documents_router)
    app.include_router(exports_router)
    app.include_router(profiles_router)
    app.include_router(performance_router)
    app.include_router(capture_router)
    app.include_router(google_router)
    return app


app = create_app()
