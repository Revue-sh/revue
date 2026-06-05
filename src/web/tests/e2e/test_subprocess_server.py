"""REVUE-332 — the e2e server runs out-of-process via subprocess uvicorn.

Hitting the server over real HTTP proves the readiness probe yielded only
once the subprocess was actually serving, and that the out-of-process server
boots against the test DB (passed by env, since the subprocess sees none of
the test process's monkeypatches).

NOTE: this module deliberately contains no pytest-asyncio test. An async test
running after a sync-Playwright test in the same session hits
"Runner.run() cannot be called from a running event loop" — a pytest-playwright
vs pytest-asyncio conflict tracked in REVUE-411, which is why the e2e-last
collection-reorder hook (src/web/tests/conftest.py) is retained for now.
"""
from __future__ import annotations

import httpx


def test_server_runs_out_of_process(base_url):
    """The session server answers /health 200 over real HTTP (TC1/TC2)."""
    assert httpx.get(f"{base_url}/health", timeout=5.0).status_code == 200
