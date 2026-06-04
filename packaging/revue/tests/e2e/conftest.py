"""E2E fixtures for the activate round-trip (REVUE-331).

This suite exercises the real licence-server -> CLI seam:

* a live FastAPI licence server (``src/web``) signs JWTs with a test
  private key supplied via the ``JWT_SIGNING_KEY`` env var, and
* the CLI (``revue_skill``) verifies those JWTs against the public half,
  which we monkeypatch over the embedded ``JWT_PUBLIC_KEY_PEM`` constant.

The signing key (server) and the public key (CLI) are generated as a
single matched RSA-2048 pair, so the happy path round-trips. The
key-mismatch case (TC3) deliberately points the server at a *different*
signing key -- that is the regression this whole ticket exists to catch.

What this verifies vs. what it does not
---------------------------------------
This proves the activation *contract* end to end: the server signs with
the documented algorithm + claim set and the CLI accepts exactly that
shape, writing the token where ``/revue-local`` reads it. It does NOT
prove the *production* embedded public key matches the *production* Fly
signing secret -- that pair cannot be exercised in CI without the Fly
secret, and AC8 explicitly hedges the binary path for that reason. The
contract coverage here is the part that can run hermetically on every PR.
"""
from __future__ import annotations

import base64
import os
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# ``src/web`` modules import each other by bare name (``from main import
# app``, ``from jwt_signing import ...``), so the web source dir must be on
# sys.path for the licence server to import at all. ``packaging/revue/src``
# is already added by the packaging conftest.py.
PACKAGING_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = PACKAGING_DIR.parent.parent
WEB_SRC = REPO_ROOT / "src" / "web"
if str(WEB_SRC) not in sys.path:
    sys.path.insert(0, str(WEB_SRC))


@pytest.fixture(scope="session")
def _e2e_rsa_keypair() -> tuple[bytes, bytes]:
    """One throwaway RSA-2048 keypair for the whole e2e session.

    Returns ``(private_pem, public_pem)`` as bytes. Never the production
    key -- the server signs with the private half and the CLI verifies
    against the public half, so the pair must match for the happy path.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return priv_pem, pub_pem


@pytest.fixture(scope="session")
def _e2e_db():
    """A temporary SQLite DB that lives for the whole e2e session.

    Mirrors ``src/web/tests/e2e/conftest.py`` -- initialise the schema
    before the server boots so the activate route can read/write it.
    """
    tmp_dir = tempfile.mkdtemp()
    db_path = os.path.join(tmp_dir, "e2e_activate.db")

    os.environ["SECRET_KEY"] = "test-secret"
    os.environ["DATABASE_PATH"] = db_path

    import database

    database.init_db(db_path)

    import auth

    auth.reset_serializer()

    yield db_path


@pytest.fixture(scope="session")
def _e2e_db_patch(_e2e_db):
    """Monkeypatch the web module's get_db_path to return the test DB.

    Extracted to avoid duplication across licence_server and any other
    fixtures that need to inject a test database path. Owns the set/restore
    lifecycle of the patch.
    """
    import database

    original_get_db_path = database.get_db_path
    database.get_db_path = lambda: _e2e_db
    yield
    database.get_db_path = original_get_db_path


@pytest.fixture(scope="session")
def licence_server(_e2e_db_patch, _e2e_rsa_keypair):
    """Boot the licence server on a random port with a matched signing key.

    The server signs licence JWTs with the test *private* key (base64 into
    ``JWT_SIGNING_KEY``, read lazily at sign time by ``jwt_signing.py``).
    Yields the base URL. Session-scoped so the uvicorn thread is started
    once; the per-IP rate limit (5/10min, keyed on 127.0.0.1) bounds how
    many activate POSTs the session may make -- the suite stays well under.
    """
    import uvicorn

    priv_pem, _ = _e2e_rsa_keypair
    os.environ["JWT_SIGNING_KEY"] = base64.b64encode(priv_pem).decode()

    from main import app

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    for _ in range(50):
        if server.started:
            break
        time.sleep(0.1)
    else:
        raise RuntimeError("Uvicorn licence server did not start in time")

    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        # force_exit aborts pending requests so the daemon thread actually
        # joins (see src/web/tests/e2e/conftest.py for the rationale).
        server.should_exit = True
        server.force_exit = True
        thread.join(timeout=5)


@pytest.fixture
def seed_active_licence(_e2e_db):
    """Return a factory that inserts a workspace + active licence row and
    returns the generated licence key (``lic_`` + 32 hex).

    The key format matches the ``/activate`` form's
    ``pattern="^lic_[a-f0-9]{32}$"`` so HTML5 validation lets the browser
    submit it. Inserts directly via the DB to avoid the Stripe webhook
    plumbing -- same approach as ``src/web/tests/test_licence_activate.py``.
    """
    from database import get_db
    from license import generate_license_key
    from models import create_user, get_user_by_email

    def _seed(*, tier: str = "indie", is_active: bool = True) -> str:
        key = generate_license_key()
        email = f"e2e-{uuid.uuid4().hex[:8]}@test.com"
        with get_db() as conn:
            create_user(conn, email, "hashed-not-used")
            user = get_user_by_email(conn, email)
            cur = conn.execute(
                "INSERT INTO workspaces (user_id, name) VALUES (?, ?)",
                (user.id, "e2e-ws"),
            )
            ws_id = cur.lastrowid
            conn.execute(
                "INSERT INTO license_keys (workspace_id, key, tier, is_active, "
                "reviews_used_this_month, reviews_limit, period_reset_at) "
                "VALUES (?, ?, ?, ?, 0, 100, ?)",
                (
                    ws_id,
                    key,
                    tier,
                    1 if is_active else 0,
                    (
                        datetime.now(timezone.utc) + timedelta(days=30)
                    ).isoformat(),
                ),
            )
        return key

    return _seed
