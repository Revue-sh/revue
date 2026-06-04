"""Tests for licence activation rate limiting (REVUE-325).

AC1: Per-IP rate limit (5 req / 10 min / IP) → HTTP 429 with Retry-After
AC2: Per-key rate limit (10 successful / 24h / key) → HTTP 429 regardless of IP
AC3: Log all attempts with hashed key and fingerprint
AC4: Emit structured log on flood (11+ attempts in 24h)
AC5: Reject missing/malformed headers (User-Agent, Content-Type)
AC6: Rate-limit state survives restart (persisted to DB)
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# These exercise the FastAPI web app (src/web). The root ``tests/`` CI suite
# runs against requirements-ci.txt, which intentionally omits the web-app deps
# (fastapi/uvicorn) — so skip the whole module there rather than erroring at
# collection. The suite still runs in full locally (the repo .venv has fastapi)
# and via the run-tests web suite.
pytest.importorskip("fastapi", reason="web-app deps (fastapi) not installed in this env")
from fastapi.testclient import TestClient  # noqa: E402

# Add src/web to path so we can import web modules
sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "web"))

from main import create_app  # noqa: E402
from database import get_db, init_db  # noqa: E402


@pytest.fixture
def db_memory(tmp_path):
    """In-memory SQLite database for tests."""
    db_path = tmp_path / "test.db"
    init_db(str(db_path))
    return str(db_path)


@pytest.fixture
def client(db_memory):
    """FastAPI test client with isolated database."""
    # Patch DATABASE_PATH to use test database
    with patch.dict(os.environ, {"DATABASE_PATH": db_memory}):
        app = create_app()
        yield TestClient(app)


@pytest.fixture
def auth_header():
    """Valid authorization headers."""
    return {
        "User-Agent": "revue-cli/1.0",
        "Content-Type": "application/json",
    }


def _setup_test_user(conn, user_id=1):
    """Helper to create test user and workspace."""
    conn.execute(
        "INSERT INTO users (id, email, password_hash) VALUES (?, ?, ?)",
        (user_id, f"user{user_id}@test.com", "fake_hash"),
    )
    conn.execute(
        "INSERT INTO workspaces (user_id, name) VALUES (?, ?)",
        (user_id, f"workspace{user_id}"),
    )
    return user_id


# Mock JWT signing for tests to avoid needing JWT_SIGNING_KEY
@pytest.fixture(autouse=True)
def mock_jwt_signing(monkeypatch):
    """Mock JWT signing so tests don't need the signing key."""
    def fake_sign_licence_jwt(workspace_id, tier, machine_fingerprint):
        return f"fake.jwt.token.{tier}"

    # Patch in the routes module where it's imported
    monkeypatch.setattr("routes.api_routes.sign_licence_jwt", fake_sign_licence_jwt)


class TestActivationRateLimitPerIP:
    """AC1: Per-IP rate limit (5 requests / 10 minutes / IP)."""

    def test_accepts_first_5_attempts_from_same_ip(self, client, db_memory, auth_header):
        """First 5 attempts from same IP should succeed (or fail for other reasons)."""
        # Setup: Create valid license key
        with get_db(db_memory) as conn:
            user_id = _setup_test_user(conn, 1)
            workspace_id = conn.execute("SELECT id FROM workspaces WHERE user_id = ?", (user_id,)).fetchone()[0]
            conn.execute(
                """INSERT INTO license_keys
                   (workspace_id, key, tier, is_active)
                   VALUES (?, 'test-key-001', 'pro', 1)""",
                (workspace_id,)
            )

        payload = {
            "key": "test-key-001",
            "machine_fingerprint": "abc123def456",
        }

        # Simulate 5 requests from same IP
        for i in range(5):
            headers_with_ip = {**auth_header, "Fly-Client-IP": "192.168.1.100"}
            response = client.post(
                "/api/v2/licence/activate",
                json=payload,
                headers=headers_with_ip,
            )
            # Should NOT be 429 (rate limited)
            assert response.status_code != 429, f"Request {i+1} was rate limited"

    def test_rejects_6th_attempt_from_same_ip_with_429(self, client, db_memory, auth_header):
        """6th attempt from same IP within 10 minutes returns HTTP 429."""
        # Setup: Create valid license key
        with get_db(db_memory) as conn:
            user_id = _setup_test_user(conn, 1)
            workspace_id = conn.execute("SELECT id FROM workspaces WHERE user_id = ?", (user_id,)).fetchone()[0]
            conn.execute(
                """INSERT INTO license_keys
                   (workspace_id, key, tier, is_active)
                   VALUES (?, 'test-key-002', 'pro', 1)""",
                (workspace_id,)
            )

        payload = {
            "key": "test-key-002",
            "machine_fingerprint": "abc123def456",
        }

        # Make 6 requests from same IP
        ip_addr = "192.168.1.101"
        for i in range(6):
            response = client.post(
                "/api/v2/licence/activate",
                json=payload,
                headers={**auth_header, "Fly-Client-IP": ip_addr},
            )
            if i < 5:
                assert response.status_code != 429, f"Request {i+1} should not be rate limited"
            else:
                # 6th request should be 429
                assert response.status_code == 429, f"6th request should return 429, got {response.status_code}"
                assert "Retry-After" in response.headers, "429 response should include Retry-After header"

    def test_retry_after_header_on_429(self, client, db_memory, auth_header):
        """429 response includes Retry-After header."""
        with get_db(db_memory) as conn:
            user_id = _setup_test_user(conn, 1)
            workspace_id = conn.execute("SELECT id FROM workspaces WHERE user_id = ?", (user_id,)).fetchone()[0]
            conn.execute(
                """INSERT INTO license_keys
                   (workspace_id, key, tier, is_active)
                   VALUES (?, 'test-key-003', 'pro', 1)""",
                (workspace_id,)
            )

        payload = {
            "key": "test-key-003",
            "machine_fingerprint": "abc123def456",
        }

        ip_addr = "192.168.1.102"
        for i in range(6):
            response = client.post(
                "/api/v2/licence/activate",
                json=payload,
                headers={**auth_header, "Fly-Client-IP": ip_addr},
            )

        assert response.status_code == 429
        assert "Retry-After" in response.headers
        retry_after = response.headers.get("Retry-After")
        assert retry_after is not None
        # Retry-After should be a reasonable number of seconds
        assert int(retry_after) > 0


class TestActivationRateLimitPerKey:
    """AC2: Per-key rate limit (10 successful activations / 24 hours / key)."""

    def test_accepts_10_successful_activations_from_different_ips(self, client, db_memory, auth_header):
        """Key can be successfully activated 10 times in 24h from different IPs."""
        with get_db(db_memory) as conn:
            user_id = _setup_test_user(conn, 2)
            workspace_id = conn.execute("SELECT id FROM workspaces WHERE user_id = ?", (user_id,)).fetchone()[0]
            conn.execute(
                """INSERT INTO license_keys
                   (workspace_id, key, tier, is_active)
                   VALUES (?, 'test-key-004', 'pro', 1)""",
                (workspace_id,)
            )

        payload = {
            "key": "test-key-004",
            "machine_fingerprint": "abc123def456",
        }

        # Activate from 10 different IPs to bypass per-IP limit
        for i in range(10):
            ip = f"192.168.1.{100 + i}"
            fingerprint = f"fingerprint-{i}"
            response = client.post(
                "/api/v2/licence/activate",
                json={**payload, "machine_fingerprint": fingerprint},
                headers={**auth_header, "Fly-Client-IP": ip},
            )
            assert response.status_code != 429, f"Activation {i+1} should not be rate limited"

    def test_rejects_11th_activation_with_429(self, client, db_memory, auth_header):
        """11th successful activation in 24h returns HTTP 429."""
        with get_db(db_memory) as conn:
            user_id = _setup_test_user(conn, 3)
            workspace_id = conn.execute("SELECT id FROM workspaces WHERE user_id = ?", (user_id,)).fetchone()[0]
            conn.execute(
                """INSERT INTO license_keys
                   (workspace_id, key, tier, is_active)
                   VALUES (?, 'test-key-005', 'pro', 1)""",
                (workspace_id,)
            )

        payload = {
            "key": "test-key-005",
            "machine_fingerprint": "abc123def456",
        }

        # Activate 11 times from different IPs
        for i in range(11):
            ip = f"192.168.2.{100 + i}"
            fingerprint = f"fingerprint-{i}"
            response = client.post(
                "/api/v2/licence/activate",
                json={**payload, "machine_fingerprint": fingerprint},
                headers={**auth_header, "Fly-Client-IP": ip},
            )
            if i < 10:
                assert response.status_code != 429, f"Activation {i+1} should not be rate limited"
            else:
                assert response.status_code == 429, f"11th activation should return 429"


class TestActivationLogging:
    """AC3: Log all attempts with hashed key and fingerprint."""

    def test_logs_attempt_with_hashed_key_and_fingerprint(self, client, db_memory, auth_header):
        """Every activation attempt logged with hashed (not raw) key + fingerprint."""
        with get_db(db_memory) as conn:
            user_id = _setup_test_user(conn, 4)
            workspace_id = conn.execute("SELECT id FROM workspaces WHERE user_id = ?", (user_id,)).fetchone()[0]
            conn.execute(
                """INSERT INTO license_keys
                   (workspace_id, key, tier, is_active)
                   VALUES (?, 'test-key-006', 'pro', 1)""",
                (workspace_id,)
            )

        payload = {
            "key": "test-key-006",
            "machine_fingerprint": "fingerprint-abc123",
        }

        headers_with_ip = {**auth_header, "Fly-Client-IP": "192.168.3.100"}
        response = client.post(
            "/api/v2/licence/activate",
            json=payload,
            headers=headers_with_ip,
        )

        # Check that log row exists with hashed values
        with get_db(db_memory) as conn:
            row = conn.execute(
                "SELECT * FROM activation_attempts WHERE ip_address = '192.168.3.100'"
            ).fetchone()
            assert row is not None, "Log entry should exist"

            # Key and fingerprint should be hashed (not raw)
            expected_key_hash = hashlib.sha256(b"test-key-006").hexdigest()
            assert row["key_hash"] == expected_key_hash, "Key should be hashed"

            expected_fp_hash = hashlib.sha256(b"fingerprint-abc123").hexdigest()
            assert row["fingerprint_hash"] == expected_fp_hash, "Fingerprint should be hashed"

            # Should have timestamp
            assert row["attempted_at"] is not None


class TestActivationFloodEvent:
    """AC4: Emit structured log on flood (11+ attempts in 24h on same key)."""

    def test_emits_flood_event_on_threshold_breach(self, client, db_memory, auth_header):
        """When key crosses 10 activations in 24h, emits structured flood event."""
        with get_db(db_memory) as conn:
            user_id = _setup_test_user(conn, 5)
            workspace_id = conn.execute("SELECT id FROM workspaces WHERE user_id = ?", (user_id,)).fetchone()[0]
            conn.execute(
                """INSERT INTO license_keys
                   (workspace_id, key, tier, is_active)
                   VALUES (?, 'test-key-007', 'pro', 1)""",
                (workspace_id,)
            )

        payload = {
            "key": "test-key-007",
            "machine_fingerprint": "abc123def456",
        }

        # Trigger 11 activations to breach threshold
        for i in range(11):
            ip = f"192.168.4.{100 + i}"
            fingerprint = f"fingerprint-{i}"
            response = client.post(
                "/api/v2/licence/activate",
                json={**payload, "machine_fingerprint": fingerprint},
                headers={**auth_header, "Fly-Client-IP": ip},
            )

        # Check that flood event was logged
        with get_db(db_memory) as conn:
            flood_log = conn.execute(
                "SELECT * FROM activation_flood_events WHERE key_hash = ?",
                (hashlib.sha256(b"test-key-007").hexdigest(),)
            ).fetchone()
            assert flood_log is not None, "Flood event should be logged"


class TestActivationHeaderValidation:
    """AC5: Reject requests missing or with malformed headers."""

    def _seed_key(self, db_memory, user_id, key):
        with get_db(db_memory) as conn:
            uid = _setup_test_user(conn, user_id)
            wid = conn.execute("SELECT id FROM workspaces WHERE user_id = ?", (uid,)).fetchone()[0]
            conn.execute(
                "INSERT INTO license_keys (workspace_id, key, tier, is_active) VALUES (?, ?, 'pro', 1)",
                (wid, key),
            )

    def test_activate_rejects_missing_user_agent(self, client, db_memory):
        """HTTP-level: empty/missing User-Agent → 400 from the endpoint."""
        self._seed_key(db_memory, 20, "test-key-ua")
        payload = {"key": "test-key-ua", "machine_fingerprint": "abc123def456"}
        # Empty User-Agent is falsy → validate_activation_headers rejects it.
        response = client.post(
            "/api/v2/licence/activate",
            content=json.dumps(payload),
            headers={
                "User-Agent": "",
                "Content-Type": "application/json",
                "Fly-Client-IP": "10.0.0.1",
            },
        )
        assert response.status_code == 400, response.text
        assert response.json()["error"] == "invalid_request"

    def test_activate_rejects_wrong_content_type(self, client, db_memory):
        """HTTP-level: non-JSON Content-Type → 400 from the endpoint (not 422)."""
        self._seed_key(db_memory, 21, "test-key-ct")
        payload = {"key": "test-key-ct", "machine_fingerprint": "abc123def456"}
        response = client.post(
            "/api/v2/licence/activate",
            content=json.dumps(payload),
            headers={
                "User-Agent": "revue-cli/1.0",
                "Content-Type": "text/plain",
                "Fly-Client-IP": "10.0.0.2",
            },
        )
        assert response.status_code == 400, response.text
        assert response.json()["error"] == "invalid_request"

    def test_validates_headers_helper(self):
        """Unit-level guard so the validation rule is pinned independent of wiring."""
        from rate_limiter import validate_activation_headers

        with pytest.raises(ValueError, match="Missing required User-Agent"):
            validate_activation_headers(user_agent=None, content_type="application/json")
        with pytest.raises(ValueError, match="Content-Type must be application/json"):
            validate_activation_headers(user_agent="revue-cli/1.0", content_type="text/plain")
        # Accepts application/json with or without a charset parameter.
        validate_activation_headers(user_agent="revue-cli/1.0", content_type="application/json")
        validate_activation_headers(user_agent="revue-cli/1.0", content_type="application/json; charset=utf-8")


class TestActivationRateLimitPersistence:
    """AC6: Rate-limit state survives machine restart (persisted to DB)."""

    def test_rate_limit_survives_restart(self, client, db_memory, auth_header):
        """Rate-limit state persists across process restarts."""
        with get_db(db_memory) as conn:
            user_id = _setup_test_user(conn, 8)
            workspace_id = conn.execute("SELECT id FROM workspaces WHERE user_id = ?", (user_id,)).fetchone()[0]
            conn.execute(
                """INSERT INTO license_keys
                   (workspace_id, key, tier, is_active)
                   VALUES (?, 'test-key-010', 'pro', 1)""",
                (workspace_id,)
            )

        payload = {
            "key": "test-key-010",
            "machine_fingerprint": "abc123def456",
        }

        ip = "192.168.6.100"

        # Make 5 requests
        for i in range(5):
            response = client.post(
                "/api/v2/licence/activate",
                json=payload,
                headers={**auth_header, "Fly-Client-IP": ip},
            )
            assert response.status_code != 429

        # Verify state persists in DB (simulating restart by reading it back).
        # Use an isoformat ('T'-separated) window bound so the comparison matches
        # how the app stores attempted_at — a space-separated datetime('now',...)
        # bound would sort after every 'T' row and make the window filter inert.
        window_start = (
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=11)
        ).isoformat()
        with get_db(db_memory) as conn:
            count = conn.execute(
                "SELECT COUNT(*) as c FROM activation_attempts "
                "WHERE ip_address = ? AND blocked = 0 AND attempted_at >= ?",
                (ip, window_start),
            ).fetchone()["c"]
            assert count == 5, f"Database should have 5 recent attempts, found {count}"

        # 6th request should be rate limited (reading from persisted DB state)
        response = client.post(
            "/api/v2/licence/activate",
            json=payload,
            headers={**auth_header, "Fly-Client-IP": ip},
        )
        assert response.status_code == 429, "Rate limit should persist (6th request should be blocked)"

    def test_rate_limit_constants_not_env_overridable(self, client, db_memory, auth_header):
        """Rate limit constants are hardcoded, not env-var overridable."""
        with get_db(db_memory) as conn:
            user_id = _setup_test_user(conn, 9)
            workspace_id = conn.execute("SELECT id FROM workspaces WHERE user_id = ?", (user_id,)).fetchone()[0]
            conn.execute(
                """INSERT INTO license_keys
                   (workspace_id, key, tier, is_active)
                   VALUES (?, 'test-key-011', 'pro', 1)""",
                (workspace_id,)
            )

        payload = {
            "key": "test-key-011",
            "machine_fingerprint": "abc123def456",
        }

        # Patch env var to try to override (should be ignored)
        with patch.dict("os.environ", {"ACTIVATE_RATE_LIMIT": "999999"}):
            for i in range(6):
                headers_with_ip = {**auth_header, "Fly-Client-IP": "192.168.7.100"}
                response = client.post(
                    "/api/v2/licence/activate",
                    json=payload,
                    headers=headers_with_ip,
                )
                if i < 5:
                    assert response.status_code != 429
                else:
                    # 6th should still be 429 despite env var (hardcoded wins)
                    assert response.status_code == 429


class TestActivationBruteForceThrottling:
    """H1 (REVUE-325): brute-forcing non-existent keys must be throttled AND logged."""

    def test_invalid_key_attempts_are_logged_and_throttled_per_ip(self, client, db_memory, auth_header):
        """6 probes of unknown keys from one IP: first 5 → 404, 6th → 429.

        The per-IP limit must apply BEFORE the key lookup, otherwise an attacker
        can brute-force keys from a single IP without ever tripping the limit.
        Every probe must also be logged (with a NULL license_key_id).
        """
        ip = "203.0.113.7"
        for i in range(6):
            response = client.post(
                "/api/v2/licence/activate",
                json={"key": f"bogus-key-{i}", "machine_fingerprint": "abc123def456"},
                headers={**auth_header, "Fly-Client-IP": ip},
            )
            if i < 5:
                assert response.status_code == 404, f"Probe {i+1} should be 404, got {response.status_code}"
            else:
                assert response.status_code == 429, "6th unknown-key probe must be rate limited"

        # All five genuine (non-blocked) probes were logged with a NULL key id.
        with get_db(db_memory) as conn:
            rows = conn.execute(
                "SELECT license_key_id, blocked FROM activation_attempts WHERE ip_address = ?",
                (ip,),
            ).fetchall()
            assert len(rows) == 6, f"All 6 probes should be logged, found {len(rows)}"
            assert all(r["license_key_id"] is None for r in rows), "Unknown-key rows must have NULL key id"
            non_blocked = [r for r in rows if r["blocked"] == 0]
            assert len(non_blocked) == 5, "Five genuine probes should count; the 429 row is marked blocked"


class TestActivationServerErrorAccounting:
    """REVUE-325 review follow-up: a server-side 500 must not burn the per-IP quota."""

    def test_signing_failure_is_blocked_and_does_not_consume_ip_budget(
        self, client, db_memory, auth_header, monkeypatch
    ):
        """6 consecutive signing failures stay 500 (never 429) and log blocked=1.

        A JWTSigningKeyMissing is the server's fault. If those attempts counted
        toward the per-IP limit, a user retrying after the outage would lock
        themselves out — so they are logged for IR but excluded from the count.
        """
        from jwt_signing import JWTSigningKeyMissing
        import routes.api_routes as api_routes

        def _raise_missing(*args, **kwargs):
            raise JWTSigningKeyMissing("JWT_SIGNING_KEY is unset")

        monkeypatch.setattr(api_routes, "sign_licence_jwt", _raise_missing)

        with get_db(db_memory) as conn:
            uid = _setup_test_user(conn, 24)
            wid = conn.execute("SELECT id FROM workspaces WHERE user_id = ?", (uid,)).fetchone()[0]
            conn.execute(
                "INSERT INTO license_keys (workspace_id, key, tier, is_active) VALUES (?, 'test-key-500', 'pro', 1)",
                (wid,),
            )
        payload = {"key": "test-key-500", "machine_fingerprint": "abc123def456"}
        ip = "203.0.113.50"

        for i in range(6):
            response = client.post(
                "/api/v2/licence/activate",
                json=payload,
                headers={**auth_header, "Fly-Client-IP": ip},
            )
            # Never throttled — a server error must not consume the caller's budget.
            assert response.status_code == 500, f"req {i+1} should stay 500, got {response.status_code}"

        with get_db(db_memory) as conn:
            rows = conn.execute(
                "SELECT blocked FROM activation_attempts WHERE ip_address = ?", (ip,)
            ).fetchall()
            assert len(rows) == 6, f"All 6 server-error attempts should be logged, found {len(rows)}"
            assert all(r["blocked"] == 1 for r in rows), "500-path attempts must be blocked=1 (excluded from count)"


class TestActivationTrustedClientIP:
    """H2 (REVUE-325): the per-IP limit must use a non-spoofable client IP."""

    def test_x_forwarded_for_is_not_trusted(self, client, db_memory, auth_header):
        """Rotating X-Forwarded-For must NOT let a single real client dodge the limit.

        Fly-Client-IP is set by Fly's edge and cannot be forged; X-Forwarded-For
        is client-supplied. Six requests with a constant Fly-Client-IP but a
        rotating X-Forwarded-For must still trip the per-IP limit on the 6th.
        """
        with get_db(db_memory) as conn:
            uid = _setup_test_user(conn, 22)
            wid = conn.execute("SELECT id FROM workspaces WHERE user_id = ?", (uid,)).fetchone()[0]
            conn.execute(
                "INSERT INTO license_keys (workspace_id, key, tier, is_active) VALUES (?, 'test-key-xff', 'pro', 1)",
                (wid,),
            )
        payload = {"key": "test-key-xff", "machine_fingerprint": "abc123def456"}

        last = None
        for i in range(6):
            last = client.post(
                "/api/v2/licence/activate",
                json=payload,
                headers={
                    **auth_header,
                    "Fly-Client-IP": "198.51.100.9",
                    "X-Forwarded-For": f"1.2.3.{i}",  # spoof attempt — must be ignored
                },
            )
        assert last.status_code == 429, "Spoofed X-Forwarded-For must not bypass the per-IP limit"

    def test_falls_back_to_socket_peer_when_no_fly_header(self, client, db_memory, auth_header):
        """Off-Fly (no Fly-Client-IP) the endpoint still works via the socket peer.

        TestClient always supplies a socket peer, so the request proceeds rather
        than failing closed — confirming the fallback path is wired and the
        endpoint does not require the Fly header in dev.
        """
        with get_db(db_memory) as conn:
            uid = _setup_test_user(conn, 23)
            wid = conn.execute("SELECT id FROM workspaces WHERE user_id = ?", (uid,)).fetchone()[0]
            conn.execute(
                "INSERT INTO license_keys (workspace_id, key, tier, is_active) VALUES (?, 'test-key-noip', 'pro', 1)",
                (wid,),
            )
        payload = {"key": "test-key-noip", "machine_fingerprint": "abc123def456"}
        response = client.post(
            "/api/v2/licence/activate",
            json=payload,
            headers={**auth_header},  # no Fly-Client-IP; TestClient supplies a peer
        )
        # Not a "could not determine client address" failure.
        assert not (
            response.status_code == 400
            and response.json().get("message") == "Could not determine client address."
        )
