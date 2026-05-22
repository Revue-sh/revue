"""FastAPI application factory and startup."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from database import init_db
from routes.auth_routes import router as auth_router
from routes.billing_routes import router as billing_router
from routes.dashboard_routes import router as dashboard_router
from routes.docs_routes import router as docs_router
from routes.api_routes import router as api_router

# Hosts on which paths are rewritten to add the `/api` prefix internally,
# letting `api.revue.sh/<path>` serve the same handlers as `/api/<path>`
# on the marketing host. Deployed CLI binaries continue to hit
# `revue.sh/api/...`, so the marketing-host routes must stay.
API_SUBDOMAIN_HOSTS = {"api.revue.sh"}

# Paths that must remain reachable on the api subdomain WITHOUT the
# `/api` prefix being added.
API_SUBDOMAIN_PASSTHROUGH_PATHS = {"/health"}


@asynccontextmanager
async def lifespan(application: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    application = FastAPI(title="Revue", docs_url=None, redoc_url=None, lifespan=lifespan)

    @application.middleware("http")
    async def api_subdomain_path_rewrite(request: Request, call_next):
        host = request.headers.get("host", "").split(":")[0].lower()
        path = request.url.path
        if (
            host in API_SUBDOMAIN_HOSTS
            and path not in API_SUBDOMAIN_PASSTHROUGH_PATHS
            and not path.startswith("/api/")
        ):
            request.scope["path"] = "/api" + path
            raw_path = request.scope.get("raw_path")
            if raw_path is not None:
                request.scope["raw_path"] = b"/api" + raw_path
        return await call_next(request)

    application.include_router(auth_router)
    application.include_router(billing_router)
    application.include_router(dashboard_router)
    application.include_router(docs_router)
    application.include_router(api_router, prefix="/api")

    @application.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    return application


app = create_app()
