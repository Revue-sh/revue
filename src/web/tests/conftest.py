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
