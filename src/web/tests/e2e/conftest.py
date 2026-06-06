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

# Ensure the repo ``scripts/`` dir is on the path so the shared staging-E2E
# helper imports cleanly. It is stdlib-only at import time (httpx lazily
# imported, no src/web import), so this adds no collection-time dependency.
# Path: this file is src/web/tests/e2e/conftest.py → repo root is four ``..``
# levels up, then ``scripts``. APPEND (not insert-at-0): ``staging_e2e_accounts``
# is a unique name and must NOT shadow any src/web top-level module (auth,
# database, models, license, main, csrf, …) that the local e2e fixtures import.
sys.path.append(
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "scripts")
    )
)

from staging_e2e_accounts import (  # noqa: E402 — after the sys.path insert above
    DEFAULT_EMAIL_DOMAIN,
    STATE_ACTIVE_INDIE,
    STATE_ACTIVE_PRO,
    STATE_ACTIVE_PRO_RENEWAL,
    STATE_FREE,
    STATE_LAPSED,
    STATE_NOT_ACTIVATED,
    email_for,
    resolve_account_key,
)


# ---------------------------------------------------------------------------
# REVUE-409: staging seeding strategy (ensure-exists + runtime keys)
#
# Staging (E2E_BASE_URL set) has NO DB access, so the local SQL ``_seed`` factory
# cannot run. Instead each licence STATE the reused suite needs maps to an
# ensure-exists staging account whose e-mail is DERIVED (e2e-<state>@<domain>)
# and whose password is the ONE shared ``STAGING_E2E_PASSWORD`` secret. The
# account's licence KEY is read back at RUNTIME (resolve_account_key) — it is
# never stored as a secret.
#
# The dedicated ``Provision → Staging E2E accounts`` pipeline step
# (scripts/provision_staging_e2e.py, ensure-exists) makes the accounts exist
# BEFORE this suite runs, so a staging wipe is recovered by just re-running the
# main pipeline — no manual secret re-paste.
#
# The staging seeding fixtures classify each test's required state from the SAME
# parameters the local factory accepts (``tier`` / ``is_active`` / ``validated``)
# and resolve the matching account — so the SAME test bodies run unchanged: they
# read ``_last_email`` / the returned key exactly as before, only the values now
# come from a real account instead of a freshly-seeded SQLite row.
#
# The canonical STATE constants + e-mail derivation + runtime key read are
# imported from the shared ``staging_e2e_accounts`` helper (single source).
# ---------------------------------------------------------------------------


class _Redacted(str):
    """A secret string whose ``repr`` is masked (e.g. ``lic_***`` / ``***``).

    A pytest failure dumps locals, and the resolved staging licence keys + the
    shared ``STAGING_E2E_PASSWORD`` are REAL secrets. A plain ``str`` would print
    verbatim into the CI log. This ``str`` SUBCLASS behaves identically for every
    value-bearing use — ``==``, ``str()``, slicing, f-strings, dict membership all
    operate on the real characters, so the login/return paths and the existing
    ``key == "..."`` / ``payload == key`` / ``_last_password == "..."`` / dict
    equality assertions are unchanged — but its ``repr`` (what tracebacks / ``-l``
    locals dumps print) is masked. Only the debug REPRESENTATION is redacted.

    The mask is carried as a class attribute set by the factory helpers below so a
    key shows ``'lic_***'`` and a password shows ``'***'``.
    """

    __slots__ = ()
    _MASK = "'***'"

    def __repr__(self) -> str:  # noqa: D401 — masks the value in tracebacks
        return type(self)._MASK


class _RedactedKey(_Redacted):
    __slots__ = ()
    _MASK = "'lic_***'"


class _RedactedPassword(_Redacted):
    __slots__ = ()
    _MASK = "'***'"


def _classify_state(
    *,
    tier: str,
    is_active: bool,
    validated: bool,
    current_period_end: "str | None" = None,
) -> str:
    """Map the local seed factory's parameters to a canonical staging STATE.

    Precedence is load-bearing: subscription/validation flags are checked BEFORE
    the tier, because the lapsed tests pass ``tier="pro"`` — a tier-first check
    would misroute them to ACTIVE_PRO and silently exercise the wrong account.

    Order:
      1. ``is_active is False``  -> LAPSED        (tier preserved but subscription lapsed)
      2. ``validated is False``  -> NOT_ACTIVATED (never-validated key)
      3. ``tier == "free"``      -> FREE
      4. ``tier == "pro"`` + ``current_period_end is not None`` -> ACTIVE_PRO_RENEWAL
      5. ``tier == "pro"``       -> ACTIVE_PRO   (NULL period_end variant)
      6. otherwise               -> ACTIVE_INDIE

    The ACTIVE_PRO split (REVUE-409): a pro+active+validated seed that carries a
    ``current_period_end`` routes to the RENEWAL account (whose synthetic webhook
    stamps a fixed renewal date the page asserts), closing the old AC7 skip. The
    other ``current_period_end``-bearing seeds are ``is_active=False`` so they hit
    LAPSED at step 1 first and are unaffected by this split.
    """
    if is_active is False:
        return STATE_LAPSED
    if validated is False:
        return STATE_NOT_ACTIVATED
    if tier == "free":
        return STATE_FREE
    if tier == "pro":
        if current_period_end is not None:
            return STATE_ACTIVE_PRO_RENEWAL
        return STATE_ACTIVE_PRO
    return STATE_ACTIVE_INDIE


# Session-level memo of resolved staging accounts, keyed by STATE. Resolving an
# account logs in and reads its licence key over HTTP; the suite is
# function-scoped, so without this cache the 4 states would re-resolve once per
# test. The provision step makes the accounts stable for the whole run, so a
# single resolve per state is correct. Cleared between unit-test cases by the
# autouse fixture in test_staging_seeding_fixtures.py.
_STAGING_ACCOUNT_CACHE: "dict[str, dict]" = {}


def _staging_account(state: str) -> dict:
    """Resolve a STATE to its ensure-exists staging account.

    The e-mail is DERIVED (``e2e-<state>@<domain>``); the password is the ONE
    shared ``STAGING_E2E_PASSWORD`` secret; the licence KEY is read back at
    RUNTIME by logging in (``resolve_account_key``) — no per-state secrets.

    Raises a clear, actionable error naming ``STAGING_E2E_PASSWORD`` if it is
    unset, so a provisioning/config gap surfaces explicitly (AC7: gaps are
    logged, not hidden) rather than as an opaque login timeout. The resolved
    account is memoised per state for the session.
    """
    if state in _STAGING_ACCOUNT_CACHE:
        return _STAGING_ACCOUNT_CACHE[state]

    raw_password = os.environ.get("STAGING_E2E_PASSWORD")
    if not raw_password:
        raise RuntimeError(
            "Staging E2E password is not configured — set the shared "
            "STAGING_E2E_PASSWORD repository secret. "
            "See docs/runbooks/staging-e2e-account.md."
        )
    # Redact the shared password the same way as the key: it is a real secret that
    # would otherwise print verbatim in a locals/repr dump. Still a str subclass,
    # so login form-fills and equality assertions keep working on the real value.
    password = _RedactedPassword(raw_password)
    base_url = (os.environ.get("E2E_BASE_URL") or "").rstrip("/")
    if not base_url:
        raise RuntimeError(
            "Staging E2E requires E2E_BASE_URL to be set to resolve account "
            "licence keys at runtime. See docs/runbooks/staging-e2e-account.md."
        )
    domain = os.environ.get("STAGING_E2E_EMAIL_DOMAIN") or DEFAULT_EMAIL_DOMAIN
    email = email_for(state, domain)
    # LAPSED has NO readable licence key on any authenticated surface: /onboarding
    # + /dashboard filter is_active=1 (hiding the lapsed row) and the /account/plan
    # lapsed block renders the "subscription ended" CTAs WITHOUT the key. This is by
    # design — do NOT add a key-bearing page for lapsed. The lapsed E2E tests never
    # read the key (they log in via email + password), so carry an empty sentinel
    # key for that state instead of calling resolve_account_key (which would raise).
    if state == STATE_LAPSED:
        key = _RedactedKey("")
    else:
        # Wrap the real key in a repr-masked str subclass BEFORE it enters the memo
        # cache or any fixture return — so a pytest failure dumping locals shows
        # ``'lic_***'`` instead of the real staging key in the CI log. The value
        # still compares/str()s as the real key, so logins and key assertions are
        # unchanged.
        key = _RedactedKey(resolve_account_key(base_url, email, password))
    account = {"email": email, "password": password, "key": key}
    _STAGING_ACCOUNT_CACHE[state] = account
    return account


def _staging_enabled() -> bool:
    """True when the suite targets a deployed environment (E2E_BASE_URL set)."""
    return bool(os.environ.get("E2E_BASE_URL"))


def pytest_configure(config):
    """Set SECRET_KEY before any test module is collected or imported.

    auth.py binds SECRET_KEY at module import time
    (``SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-...")``)
    so the only safe place to force the test value is here — before pytest
    collection triggers the first import of any src/web module. Setting it in
    a fixture is too late: the module may already be cached with the default
    key. The uvicorn child inherits this value via os.environ, ensuring the
    test process and the server sign/verify cookies with the same key.

    REVUE-409: also set the Stripe WEBHOOK signing secret + the price ids the
    synthetic-webhook provisioning path relies on, via the SAME setdefault-here
    mechanism — the out-of-process uvicorn child inherits ``os.environ`` AT SPAWN,
    so setting them inside a test would be too late (the server is already up).
    ``construct_webhook_event`` reads ``STRIPE_WEBHOOK_SECRET`` and
    ``tier_from_price_id`` reads ``STRIPE_PRICE_*`` from the SERVER process env at
    request time; the local hard-gate test signs/builds events using these exact
    values so the bytes + price ids match. These are test-only literals — NOT real
    secrets — and are ignored on staging (E2E_BASE_URL targets the deployed app,
    whose own deployed values apply).
    """
    os.environ.setdefault("SECRET_KEY", "test-secret")
    os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_e2e_local_test_secret")
    os.environ.setdefault("STRIPE_PRICE_PRO_MONTHLY", "price_e2e_pro")
    os.environ.setdefault("STRIPE_PRICE_INDIE_MONTHLY", "price_e2e_indie")


@pytest.fixture(scope="session")
def _e2e_db():
    """Create a temporary SQLite DB that lives for the entire test session."""
    tmp_dir = tempfile.mkdtemp()
    db_path = os.path.join(tmp_dir, "e2e_test.db")

    os.environ["DATABASE_PATH"] = db_path

    import database
    database.init_db(db_path)

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


def _staging_base_url_or_none() -> "str | None":
    """Return the deployed base URL (E2E_BASE_URL, trailing slash stripped) when
    set, else None.

    Extracted so the staging-vs-local short-circuit can be unit-tested DIRECTLY
    (Edge F9) rather than reaching through the ``base_url`` fixture's
    ``__wrapped__`` generator: ``not None`` → the fixture yields it without
    spawning a server; ``None`` → the local subprocess path runs.
    """
    staging_url = os.environ.get("E2E_BASE_URL")
    return staging_url.rstrip("/") if staging_url else None


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
    staging_url = _staging_base_url_or_none()
    if staging_url:
        yield staging_url
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
    """SQL factory: create a user + workspace + licence row, return (key, email, password).

    REVUE-384: the e2e server runs out-of-process against the same SQLite file
    (``DATABASE_PATH`` == ``_e2e_db``), so a row written here from the test
    process is visible to the uvicorn child. Reuses the ``models`` layer
    (``create_user``/``create_workspace``/``create_license_key``) rather than
    raw SQL so the seed stays in lock-step with the schema. The returned key
    matches ``generate_license_key()`` (``lic_`` + 32 hex), which is exactly the
    shape ``/activate`` validates client-side.

    REVUE-382: extended with ``is_active`` and ``current_period_end`` /
    ``subscription_status`` params to cover the full state matrix
    (active/lapsed/free/not_activated, including the REVUE-413 NULL-columns
    variant). The old positional call ``seed_active_licence(tier="indie")``
    continues to work unchanged (``is_active`` defaults to True).

    Returns a namedtuple-like dict with ``key``, ``email``, ``password``.
    For backward compat, the factory is also directly callable with just
    ``tier`` and returns the key string (matching the original contract).

    REVUE-409 staging branch: when ``E2E_BASE_URL`` is set there is no DB to seed
    into, so instead of writing a row the factory classifies the requested state
    from its params and resolves the matching ensure-exists staging account
    (derived e-mail + shared ``STAGING_E2E_PASSWORD`` + runtime-read licence key).
    It sets ``_last_email`` / ``_last_password`` to that account's credentials and
    returns its real licence key — so the test bodies (which read those exact
    attributes and then log in via the UI) run unchanged.
    """
    if _staging_enabled():
        def _seed_staging(
            *,
            tier: str = "indie",
            is_active: bool = True,
            current_period_end: "str | None" = None,
            subscription_status: "str | None" = None,
            validated: bool = True,
            password: str = "testpass123",  # noqa: ARG001 — overridden by the real account
        ) -> str:
            state = _classify_state(
                tier=tier,
                is_active=is_active,
                validated=validated,
                current_period_end=current_period_end,
            )
            account = _staging_account(state)
            _seed_staging._last_email = account["email"]  # type: ignore[attr-defined]
            _seed_staging._last_password = account["password"]  # type: ignore[attr-defined]
            return account["key"]

        _seed_staging._last_email = ""  # type: ignore[attr-defined]
        _seed_staging._last_password = ""  # type: ignore[attr-defined]
        return _seed_staging

    import sqlite3
    import uuid

    from auth import hash_password
    from license import generate_license_key
    from models import (
        create_license_key,
        create_user,
        create_workspace,
        get_any_license_for_user,
        set_license_subscription_state,
        touch_license_validated,
        update_user_tier,
    )

    def _seed(
        *,
        tier: str = "indie",
        is_active: bool = True,
        current_period_end: "str | None" = None,
        subscription_status: "str | None" = None,
        validated: bool = True,
        password: str = "testpass123",
    ) -> str:
        """Create a user+workspace+licence row and return the licence key string.

        Args:
            tier: Plan tier (free / indie / pro).
            is_active: When False the row is lapsed (is_active=0, tier preserved).
            current_period_end: ISO-8601 UTC renewal date.  Pass None to leave
                it NULL — tests the REVUE-413 migration-reality NULL-columns case.
            subscription_status: Raw Stripe status string.  Pass None for NULL.
            validated: When True (default) stamp last_validated_at so the row
                resolves to active/free.  Pass False to model a never-validated
                key — the REVUE-382 not_activated state (AC5).
            password: Login password for the created user (used by e2e login).

        Returns:
            The generated licence key string (``lic_`` + 32 hex chars).
        """
        key = generate_license_key()
        conn = sqlite3.connect(_e2e_db)
        conn.row_factory = sqlite3.Row
        try:
            email = f"seed-{uuid.uuid4().hex[:8]}@test.com"
            user_id = create_user(
                conn,
                email=email,
                password_hash=hash_password(password),
            )
            ws_id = create_workspace(conn, user_id, "seed-ws")
            create_license_key(conn, ws_id, key, tier=tier)
            if tier != "free":
                update_user_tier(conn, user_id, tier)
            if not is_active or current_period_end is not None or subscription_status is not None:
                set_license_subscription_state(
                    conn,
                    user_id,
                    is_active=is_active,
                    current_period_end=current_period_end,
                    subscription_status=subscription_status,
                )
            if validated:
                lic = get_any_license_for_user(conn, user_id)
                if lic is not None:
                    touch_license_validated(conn, lic.id)
            conn.commit()
        finally:
            conn.close()
        # Store email/password on the callable so E2E tests can log in.
        _seed._last_email = email  # type: ignore[attr-defined]
        _seed._last_password = password  # type: ignore[attr-defined]
        return key

    # Expose last-seed credentials via attributes set by _seed().
    _seed._last_email = ""  # type: ignore[attr-defined]
    _seed._last_password = ""  # type: ignore[attr-defined]
    return _seed


@pytest.fixture(scope="function")
def seed_user_with_licence(_e2e_db):
    """Companion to ``seed_active_licence`` returning the full identity, not just
    the key (REVUE-361).

    ``seed_active_licence`` returns only the licence key — enough for the
    unauthenticated /activate flow, but the /billing/success and /onboarding
    pages render the *authenticated* user's key, so a test must also be able to
    mint that user's session cookie. Rather than change the existing factory's
    return type (its callers do ``key = seed_active_licence()``), this sibling
    returns a dict with ``user_id``/``email``/``tier``/``key`` — exactly the
    fields ``auth.create_session`` signs into the session cookie.

    Seeds the user with ``password_hash="x"`` (cannot log in via the UI — that is
    why the cookie is minted directly in ``auth_cookie``).

    REVUE-409 staging branch: when ``E2E_BASE_URL`` is set, return the matching
    ensure-exists account's identity — ``email`` / ``password`` / ``tier`` /
    ``key`` (runtime-read licence key). The ``password`` is added (absent from the
    local return) because the staging ``auth_cookie`` logs in through the UI rather
    than minting a cookie. No test body reads ``user_id`` (a local DB int with no
    staging meaning), so omitting it does not change any test.
    """
    if _staging_enabled():
        def _seed_staging(*, tier: str = "indie") -> dict:
            state = _classify_state(tier=tier, is_active=True, validated=True)
            account = _staging_account(state)
            return {
                "email": account["email"],
                "password": account["password"],
                "tier": tier,
                "key": account["key"],
            }

        return _seed_staging

    import sqlite3
    import uuid

    from license import generate_license_key
    from models import create_license_key, create_user, create_workspace

    def _seed(*, tier: str = "indie") -> dict:
        key = generate_license_key()
        email = f"seed-{uuid.uuid4().hex[:8]}@test.com"
        conn = sqlite3.connect(_e2e_db)
        conn.row_factory = sqlite3.Row
        try:
            user_id = create_user(conn, email=email, password_hash="x")
            ws_id = create_workspace(conn, user_id, "seed-ws")
            create_license_key(conn, ws_id, key, tier=tier)
            conn.commit()
        finally:
            conn.close()
        return {"user_id": user_id, "email": email, "tier": tier, "key": key}

    return _seed


@pytest.fixture(scope="function")
def auth_cookie(base_url):
    """Inject a signed session cookie for a seeded user into the Playwright page.

    The seeded user has ``password_hash="x"`` and cannot authenticate through the
    signup/login UI, so we mint the exact cookie ``auth.create_session`` would
    have set (``itsdangerous``-signed ``{"user_id","email","tier"}``) and add it
    to the browser context. The serializer is keyed on ``SECRET_KEY`` (set to
    ``test-secret`` by the ``_e2e_db`` fixture and inherited by the uvicorn
    child), so the server's ``get_session`` round-trips it.

    Returns a callable ``(page, identity) -> None`` taking the dict produced by
    ``seed_user_with_licence``. The cookie is scoped to the running server's host
    so it travels with same-host navigations.

    REVUE-409 staging branch: minting a cookie requires the server's SECRET_KEY,
    which staging does not share. Instead, when ``E2E_BASE_URL`` is set, establish
    the session by logging in through the real UI with the pre-provisioned
    account's email + password — the same login flow the suite already uses. The
    callable signature ``(page, identity)`` is unchanged, so test bodies that do
    ``auth_cookie(page, identity)`` run verbatim.
    """
    from urllib.parse import urlparse

    import auth

    if _staging_enabled():
        def _login_ui(page, identity: dict) -> None:
            page.goto(base_url + "/login")
            page.locator("input[name='email']").fill(identity["email"])
            page.locator("input[name='password']").fill(identity["password"])
            page.locator("button[type='submit']").click()
            page.wait_for_url("**/dashboard", timeout=10_000)

        return _login_ui

    def _inject(page, identity: dict) -> None:
        token = auth._get_serializer().dumps(
            {
                "user_id": identity["user_id"],
                "email": identity["email"],
                "tier": identity["tier"],
            }
        )
        host = urlparse(base_url).hostname or "127.0.0.1"
        page.context.add_cookies(
            [
                {
                    "name": auth.session_cookie_name(),
                    "value": token,
                    "domain": host,
                    "path": "/",
                    "httpOnly": True,
                    "sameSite": "Lax",
                }
            ]
        )

    return _inject


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
