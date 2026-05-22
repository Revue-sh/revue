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

    yield db_path


@pytest_asyncio.fixture
async def client(_tmp_db) -> AsyncGenerator[AsyncClient, None]:
    """Async test client for the FastAPI app."""
    from main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def pytest_collection_modifyitems(config, items):
    """Move e2e tests to the END of the collection.

    The e2e suite spins up a uvicorn server in a session-scoped fixture.
    Uvicorn keeps a background thread with an active asyncio event loop
    alive for the rest of the session — any pytest-asyncio test running
    *after* an e2e test then fails with "Cannot run the event loop while
    another loop is running". The fixture teardown only runs at session
    end, so the simplest fix is to ensure no async tests run after the
    e2e suite. Running e2e last means the leaked loop doesn't matter.
    """
    e2e_items = [i for i in items if "/e2e/" in str(i.fspath) or "\\e2e\\" in str(i.fspath)]
    non_e2e_items = [i for i in items if i not in e2e_items]
    items[:] = non_e2e_items + e2e_items
