"""FastAPI application factory and startup."""
from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from starlette.routing import Match

from database import init_db
from metrics import MetricsRegistry
from routes.auth_routes import router as auth_router
from routes.billing_routes import router as billing_router
from routes.dashboard_routes import router as dashboard_router
from routes.docs_routes import router as docs_router
from routes.legal_routes import router as legal_router
from routes.api_routes import router as api_router
from routes.skills_routes import router as skills_router, make_manifest_builder

# Hosts on which paths are rewritten to add the `/api` prefix internally,
# letting `api.<env>.revue.sh/<path>` serve the same handlers as
# `/api/<path>` on the marketing host. Deployed CLI binaries continue
# to hit `revue.sh/api/...`, so the marketing-host routes must stay.
API_SUBDOMAIN_HOSTS = {"api.revue.sh", "api.staging.revue.sh"}

# The metrics scrape endpoint. Excluded from its own instrumentation so the
# Prometheus scrape interval does not become the traffic-anomaly baseline
# (REVUE-362): every scrape would otherwise add a synthetic request.
METRICS_PATH = "/metrics"

# Paths that must remain reachable on the api subdomain WITHOUT the
# `/api` prefix being added. ``/metrics`` is here so the endpoint resolves
# identically on every host — Fly scrapes the internal port, but pinning it to
# the apex route keeps the self-exclusion check host-independent rather than
# relying on the scrape never carrying an ``api.`` host header.
API_SUBDOMAIN_PASSTHROUGH_PATHS = {"/health", METRICS_PATH}

# Recorded when no route matches (404) or before routing resolves, so the
# error-rate alert still sees unmatched traffic without exploding cardinality
# on attacker-chosen raw paths.
_UNMATCHED_ROUTE = "__unmatched__"


def _resolve_route_template(application: FastAPI, request: Request) -> str:
    """Return the matched route's path template (e.g. ``/api/v2/licence/activate``)
    rather than the raw request path, so per-request identifiers in the URL can
    never explode metric series cardinality. Falls back to a single
    ``__unmatched__`` label for 404s."""
    for route in application.router.routes:
        matches, _ = route.matches(request.scope)
        if matches == Match.FULL:
            return getattr(route, "path", _UNMATCHED_ROUTE)
    return _UNMATCHED_ROUTE


@asynccontextmanager
async def lifespan(application: FastAPI):
    init_db()
    try:
        yield
    finally:
        # Close the shared httpx client created in create_app() so the
        # connection pool is drained on shutdown.
        http_client = getattr(application.state, "http_client", None)
        if http_client is not None:
            await http_client.aclose()


def create_app() -> FastAPI:
    application = FastAPI(title="Revue", docs_url=None, redoc_url=None, lifespan=lifespan)

    # Compose skills-route dependencies on app state (no module-level globals).
    # The client is created here and closed in `lifespan`.
    from httpx import AsyncClient
    http_client = AsyncClient()
    application.state.http_client = http_client
    application.state.manifest_builder = make_manifest_builder(http_client, {})

    # Shared metrics registry — one instance per app, fed by the timing
    # middleware below and read by GET /metrics (REVUE-362).
    metrics = MetricsRegistry()
    application.state.metrics = metrics

    # LOAD-BEARING ORDER: record_request_metrics MUST be registered before
    # api_subdomain_path_rewrite. The metrics self-exclusion guard for /metrics
    # depends on seeing the pre-rewrite path; if this runs after the path-rewrite
    # middleware, /metrics requests will be rewritten and the exclusion check will fail.
    @application.middleware("http")
    async def record_request_metrics(request: Request, call_next):
        # Endpoint-agnostic instrumentation: time the whole request by route
        # template + status. Deliberately NOT inside any handler — APM tracing
        # internal to a handler is out of scope (REVUE-362). The /metrics scrape
        # is excluded so it never inflates its own counters.
        if request.url.path == METRICS_PATH:
            return await call_next(request)

        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            duration = time.perf_counter() - start
            route = _resolve_route_template(application, request)
            metrics.observe(
                method=request.method,
                route=route,
                status=status,
                duration_seconds=duration,
            )

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
    application.include_router(legal_router)
    application.include_router(skills_router)
    application.include_router(api_router, prefix="/api")

    @application.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @application.get(METRICS_PATH)
    async def metrics_endpoint() -> PlainTextResponse:
        # Fly's managed Prometheus scrapes this. Content-Type is the Prometheus
        # text exposition format v0.0.4 so the scraper parses it natively.
        return PlainTextResponse(
            metrics.render(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    return application


app = create_app()
