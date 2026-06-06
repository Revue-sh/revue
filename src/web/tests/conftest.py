"""Shared test fixtures."""
from __future__ import annotations

import os
import sys
import tempfile
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Ensure src/web is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Set test env before importing app modules
os.environ["SECRET_KEY"] = "test-secret"


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    """Use a temporary database for every test."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)

    import database
    # Patch the module-level function to use the temp path
    monkeypatch.setattr(database, "get_db_path", lambda: db_path)
    database.init_db(db_path)

    import auth
    auth.reset_serializer()

    import csrf
    csrf.reset_serializer()

    yield db_path


class _CSRFAwareClient(AsyncClient):
    """Test client that transparently satisfies CSRF for protected form posts.

    REVUE-418 added systemic CSRF protection: every cookie-session form POST
    now requires a double-submit token. Rather than rewrite ~90 existing call
    sites, this wrapper injects the token automatically — but ONLY for requests
    that pass form data (``data=``). That single discriminator does the right
    thing without any path knowledge:

      - Protected routes are all HTML form posts (``data=``) → token injected.
      - Exempt routes (Stripe webhook, ``/api`` + ``/v2`` JWT/body APIs) post
        ``json=`` / ``content=`` → never touched, bodies never corrupted.

    Faithfulness: the injected value is the REAL cookie the production CSRF
    middleware set (lazily seeded via one GET /login if the jar is empty), never
    a fabricated token — so a broken cookie-setting path would still fail.

    Tests that must exercise the raw enforcement path (e.g. "no token → 403")
    use a separate raw client, or pass ``_csrf=False`` to opt out here.
    """

    async def _ensure_csrf_cookie(self) -> str:
        from csrf import CSRF_COOKIE_BASE
        token = self.cookies.get(CSRF_COOKIE_BASE)
        if not token:
            # One unauthenticated GET mints the cookie via the middleware.
            await self.get("/login")
            token = self.cookies.get(CSRF_COOKIE_BASE)
        return token or ""

    async def request(self, method, url, *args, **kwargs):  # type: ignore[override]
        from csrf import CSRF_FORM_FIELD
        inject = kwargs.pop("_csrf", True)
        data = kwargs.get("data")
        # A request is "form-shaped" when it passes a dict ``data=`` OR carries
        # no body at all (no json/content/files). Both cases map to a protected
        # cookie-session form POST in this app; JSON/content bodies (the exempt
        # API + webhook surface) are left untouched so their bodies never get a
        # spurious form field.
        has_other_body = any(
            kwargs.get(k) is not None for k in ("json", "content", "files")
        )
        is_form_shaped = isinstance(data, dict) or (data is None and not has_other_body)
        if (
            inject
            and is_form_shaped
            and method.upper() in {"POST", "PUT", "PATCH", "DELETE"}
            and not (isinstance(data, dict) and CSRF_FORM_FIELD in data)
        ):
            token = await self._ensure_csrf_cookie()
            if token:
                base = data if isinstance(data, dict) else {}
                kwargs["data"] = {**base, CSRF_FORM_FIELD: token}
        return await super().request(method, url, *args, **kwargs)


@pytest_asyncio.fixture
async def client(_tmp_db) -> AsyncGenerator[AsyncClient, None]:
    """Async test client for the FastAPI app (CSRF-aware — see _CSRFAwareClient)."""
    from main import app
    transport = ASGITransport(app=app)
    async with _CSRFAwareClient(transport=transport, base_url="http://test") as ac:
        yield ac


def pytest_collection_modifyitems(config, items):
    """Run e2e (Playwright) tests LAST so async (pytest-asyncio) tests run first.

    REVUE-332 moved the e2e server out-of-process (subprocess uvicorn), removing
    the *uvicorn* asyncio-loop leak. A SECOND, independent conflict remains:
    pytest-playwright's SYNC API leaves a running event loop, so any
    pytest-asyncio test running AFTER a Playwright test fails with
    "RuntimeError: Runner.run() cannot be called from a running event loop".
    Until the e2e tests are ported to Playwright's async API (REVUE-411), this
    reorder keeps local full-suite runs green. CI is unaffected — it runs the
    unit and e2e subsets as separate pytest invocations.
    """
    e2e_items = [i for i in items if "/e2e/" in str(i.fspath) or "\\e2e\\" in str(i.fspath)]
    non_e2e_items = [i for i in items if i not in e2e_items]
    items[:] = non_e2e_items + e2e_items
