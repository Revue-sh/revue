"""E2E test fixtures — spins up a live FastAPI server for Playwright."""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time

import pytest
import uvicorn

# Ensure src/web is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


@pytest.fixture(scope="session")
def _e2e_db():
    """Create a temporary SQLite DB that lives for the entire test session."""
    tmp_dir = tempfile.mkdtemp()
    db_path = os.path.join(tmp_dir, "e2e_test.db")

    os.environ["SECRET_KEY"] = "test-secret"
    os.environ["DATABASE_PATH"] = db_path

    import database
    database.init_db(db_path)

    import auth
    auth.reset_serializer()

    yield db_path


@pytest.fixture(scope="session")
def base_url(_e2e_db):
    """Start the FastAPI app on a random port in a background thread."""
    # Force fresh module state with the test DB
    import database
    original_get_db_path = database.get_db_path
    database.get_db_path = lambda: _e2e_db

    from main import app

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for server to be ready
    for _ in range(50):
        if server.started:
            break
        time.sleep(0.1)
    else:
        raise RuntimeError("Uvicorn server did not start in time")

    # Extract the actual port
    sockets = server.servers[0].sockets
    port = sockets[0].getsockname()[1]

    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=5)

    # Restore
    database.get_db_path = original_get_db_path


@pytest.fixture(scope="function")
def logged_in_page(page, base_url):
    """Create a user via the signup UI and return the logged-in Playwright page."""
    import uuid

    email = f"e2e-{uuid.uuid4().hex[:8]}@test.com"
    password = "testpass123"

    page.goto(base_url + "/signup")
    page.locator("input[name='email']").fill(email)
    page.locator("input[name='password']").fill(password)
    page.locator("button[type='submit']").click()

    # Signup redirects to /onboarding
    page.wait_for_url(f"**{'/onboarding'}")

    return page
