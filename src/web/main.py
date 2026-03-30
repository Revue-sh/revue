"""FastAPI application factory and startup."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from database import init_db
from routes.auth_routes import router as auth_router
from routes.billing_routes import router as billing_router
from routes.dashboard_routes import router as dashboard_router
from routes.docs_routes import router as docs_router
from routes.api_routes import router as api_router


@asynccontextmanager
async def lifespan(application: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    application = FastAPI(title="Revue.io", docs_url=None, redoc_url=None, lifespan=lifespan)
    application.include_router(auth_router)
    application.include_router(billing_router)
    application.include_router(dashboard_router)
    application.include_router(docs_router)
    application.include_router(api_router, prefix="/api")
    return application


app = create_app()
