"""REVUE-325 — concurrency regression for the activation rate limiter.

Fires many requests *truly concurrently* (real threads against a live uvicorn
server) from a single IP and asserts two invariants that unit tests with an
in-process TestClient cannot exercise:

1. The per-IP cap (5) is **not overshot** under parallel load — i.e. no
   check-then-write (TOCTOU) race lets extra requests slip through.
2. **No request fails with a "database is locked" 500** — i.e. there is no
   write-lock contention.

Together these guard the single-worker atomicity assumption that lets the
limiter run without explicit DB locking (see the comment in
``activate_licence``). Any future change that reintroduces a TOCTOU race, or
that scales the web tier to multiple workers without moving rate-limit state
to Postgres, will trip this test.

Slow (boots a real server), so gated behind the ``slow`` marker::

    pytest tests/integration/test_activation_concurrency.py -m slow
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = REPO_ROOT / "src" / "web"

PER_IP_CAP = 5  # mirrors rate_limiter.PER_IP_LIMIT
CONCURRENT_REQUESTS = 40


def _free_port() -> int:
    """Reserve an ephemeral localhost port so parallel test runs don't collide."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.slow
def test_per_ip_cap_holds_under_concurrent_load(tmp_path: Path) -> None:
    """40 simultaneous requests from one IP must not overshoot the 5-req cap."""
    db_path = tmp_path / "conc.db"
    port = _free_port()
    env = {
        **os.environ,
        "DATABASE_PATH": str(db_path),
        "PYTHONPATH": str(WEB_DIR),
    }
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "main:app",
            "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning",
        ],
        cwd=str(WEB_DIR),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        # Wait for the server to accept requests (≤ ~10s).
        for _ in range(50):
            try:
                if httpx.get(base + "/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        else:
            pytest.fail("uvicorn did not become ready in time")

        headers = {
            "User-Agent": "revue-cli/1.0",
            "Content-Type": "application/json",
            "Fly-Client-IP": "203.0.113.222",
        }

        def fire(i: int) -> int:
            # Unknown key → 404 path, which counts toward the per-IP limit until
            # the cap, then 429. No valid key / JWT signing is needed because the
            # request never reaches the success path.
            return httpx.post(
                base + "/api/v2/licence/activate",
                json={"key": f"GUESS-{i}", "machine_fingerprint": "fp"},
                headers=headers,
                timeout=10,
            ).status_code

        with ThreadPoolExecutor(max_workers=CONCURRENT_REQUESTS) as pool:
            codes = list(pool.map(fire, range(CONCURRENT_REQUESTS)))

        tally = Counter(codes)
        reached_lookup = tally.get(404, 0) + tally.get(200, 0)
        lock_errors = tally.get(500, 0)

        # Invariant 1: the per-IP cap is not overshot under true concurrency.
        assert reached_lookup <= PER_IP_CAP, (
            f"per-IP cap overshot under concurrency: {reached_lookup} requests "
            f"reached the key lookup (cap {PER_IP_CAP}). Tally={dict(tally)}"
        )
        # Invariant 2: no 'database is locked' 500s.
        assert lock_errors == 0, f"server errors under concurrency: {dict(tally)}"
        # Sanity: the limiter actually engaged (the bulk were throttled).
        assert tally.get(429, 0) >= 1, f"limiter did not engage: {dict(tally)}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
