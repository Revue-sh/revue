"""E2E test fixtures — spins up a live FastAPI server for Playwright."""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import tempfile
import time

import httpx
import pytest

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


def _bound_listener() -> "tuple[socket.socket, int]":
    """Bind a listening socket the child uvicorn inherits via ``--fd``.

    Returns the open, bound, listening socket and its port. Passing the
    already-bound socket to the child (``--fd`` + ``pass_fds``) removes the
    unbound window a reserve-then-release approach leaves — no other process
    can steal the port between release and uvicorn's bind (REVUE-332 review).
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    os.set_inheritable(sock.fileno(), True)
    return sock, sock.getsockname()[1]


def _terminate(proc: subprocess.Popen) -> None:
    """Tear down the uvicorn subprocess and its whole process group.

    The child is its own session leader (``start_new_session=True``), so
    killing the process *group* guarantees uvicorn's children die too — no
    orphaned server survives the fixture (REVUE-332 TC6). Escalates
    SIGTERM -> SIGKILL with a bounded wait at each step.
    """
    if proc.poll() is not None:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except (ProcessLookupError, PermissionError):
            # Group already gone, or no perms — signal the process directly,
            # ignoring it if it has already been reaped.
            try:
                proc.kill()
            except (ProcessLookupError, PermissionError):
                return
        try:
            proc.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            continue


@pytest.fixture(scope="session")
def base_url(_e2e_db):
    """Start the FastAPI app in a SEPARATE PROCESS for the whole session.

    REVUE-332: the server runs via ``subprocess.Popen`` (not an in-process
    thread), so uvicorn's asyncio event loop lives and dies inside that
    process. Nothing leaks into the test process. (A separate pytest-playwright
    vs pytest-asyncio conflict still requires the e2e-last collection hook in
    ``src/web/tests/conftest.py`` — see REVUE-411.)

    The child is a fresh interpreter that sees none of the test process's
    monkeypatches — only the environment. ``DATABASE_PATH`` (honoured by
    ``database.get_db_path``) and ``SECRET_KEY`` are passed via ``env`` so the
    server boots against the same temporary DB the tests seed.

    REVUE-407 staging parity (TC-11): when ``E2E_BASE_URL`` is set, the tests run
    against that already-running deployment (e.g. staging) instead of spawning a
    local subprocess. This keeps the same E2E suite usable for post-merge staging
    validation without duplicating fixtures.
    """
    staging_url = os.environ.get("E2E_BASE_URL")
    if staging_url:
        yield staging_url.rstrip("/")
        return

    web_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    sock, port = _bound_listener()
    url = f"http://127.0.0.1:{port}"

    env = dict(os.environ)
    env["DATABASE_PATH"] = _e2e_db
    env.setdefault("SECRET_KEY", "test-secret")

    try:
        proc = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn", "main:app",
                "--fd", str(sock.fileno()),
                "--log-level", "warning",
            ],
            cwd=web_dir,
            env=env,
            start_new_session=True,
            pass_fds=(sock.fileno(),),
        )
    finally:
        # The child holds the bound socket now; drop the parent's copy.
        sock.close()

    # Any failure between launch and yield must reap the process, or an
    # unexpected error would leak the subprocess and hold the port.
    try:
        # Readiness probe: do not yield until /health returns 200, bounded by a
        # timeout. last_err records the most recent reason (transport error or
        # non-200 status) so the timeout message is actionable.
        deadline = time.monotonic() + 15.0
        last_err: object = None
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(
                    f"uvicorn exited early (code {proc.returncode}) before "
                    f"readiness — app import or startup failure (check its logs)?"
                )
            try:
                resp = httpx.get(f"{url}/health", timeout=1.0)
                if resp.status_code == 200:
                    break
                last_err = f"/health returned HTTP {resp.status_code}"
            except httpx.HTTPError as exc:  # still booting / connection refused
                last_err = exc
            time.sleep(0.1)
        else:
            raise RuntimeError(
                f"uvicorn did not become ready within 15s (last: {last_err})"
            )
    except BaseException:
        _terminate(proc)
        raise

    try:
        yield url
    finally:
        _terminate(proc)


@pytest.fixture(scope="function")
def seed_active_licence(_e2e_db):
    """SQL factory: create a user + workspace + active licence, return the key.

    REVUE-384: the e2e server runs out-of-process against the same SQLite file
    (``DATABASE_PATH`` == ``_e2e_db``), so a row written here from the test
    process is visible to the uvicorn child. Reuses the ``models`` layer
    (``create_user``/``create_workspace``/``create_license_key``) rather than
    raw SQL so the seed stays in lock-step with the schema. The returned key
    matches ``generate_license_key()`` (``lic_`` + 32 hex), which is exactly the
    shape ``/activate`` validates client-side.
    """
    import sqlite3
    import uuid

    from license import generate_license_key
    from models import create_license_key, create_user, create_workspace

    def _seed(*, tier: str = "indie") -> str:
        key = generate_license_key()
        conn = sqlite3.connect(_e2e_db)
        conn.row_factory = sqlite3.Row
        try:
            user_id = create_user(
                conn,
                email=f"seed-{uuid.uuid4().hex[:8]}@test.com",
                password_hash="x",
            )
            ws_id = create_workspace(conn, user_id, "seed-ws")
            create_license_key(conn, ws_id, key, tier=tier)
            conn.commit()
        finally:
            conn.close()
        return key

    return _seed


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
