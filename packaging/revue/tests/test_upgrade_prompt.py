"""Unit tests for ``revue_skill.skill.upgrade_prompt`` (REVUE-279 code-review).

Covers:
- Banner renders when cache is fresh AND paywall_state == "exhausted"
- Banner does NOT render when cache is stale (Fix 3 — freshness gate)
- Banner does NOT render when paywall_state is None / missing
- Banner copy no longer hardcodes the "25 reviews" magic number (Fix 8)
"""
from __future__ import annotations

import json
import time
from pathlib import Path


def _write_cache(cache_path: Path, *, paywall_state, fresh: bool = True) -> None:
    """Write a cache file shaped like the server's /validate response."""
    now = int(time.time())
    if fresh:
        refresh_after_ts = now + 3600
        cached_at = now
    else:
        # Stale: cached 25h ago and refresh horizon already past.
        refresh_after_ts = now - 3600
        cached_at = now - (25 * 3600)

    body = {
        "valid": True,
        "tier": "free",
        "reviews_remaining": 0 if paywall_state == "exhausted" else 10,
        "paywall_state": paywall_state,
        "refresh_after_ts": refresh_after_ts,
        "cached_at": cached_at,
        "refreshed_jwt": None,
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(body))


def _capture_cli(monkeypatch) -> list[str]:
    """Capture ``Log.cli.info`` messages into a list. Revue uses a custom
    log channel (revue_core.core.log) that does not route through stdlib
    ``logging``, so pytest ``caplog`` would not see them."""
    from revue_core.core.logging_channels import Log

    captured: list[str] = []

    def _capture(message, *args):
        try:
            captured.append(message % args if args else message)
        except (TypeError, ValueError):
            captured.append(f"{message} {args}")

    monkeypatch.setattr(Log.cli, "info", _capture)
    return captured


def test_renders_banner_when_fresh_cache_exhausted(monkeypatch, tmp_path):
    """Happy path: fresh cache + paywall_state==exhausted → banner shown."""
    cache_file = tmp_path / "cache.json"
    monkeypatch.setenv("REVUE_LICENCE_CACHE_PATH", str(cache_file))
    _write_cache(cache_file, paywall_state="exhausted", fresh=True)
    cli = _capture_cli(monkeypatch)

    from revue_skill.skill.upgrade_prompt import render_upgrade_prompt_if_exhausted
    render_upgrade_prompt_if_exhausted()

    text = "\n".join(cli)
    assert "FREE-TIER REVIEW LIMIT REACHED" in text
    assert "https://revue.sh/pricing" in text


def test_skips_banner_when_cache_is_stale(monkeypatch, tmp_path):
    """Fix 3: stale cache must be treated as 'do not render' — same freshness
    contract validate uses. A stale paywall_state may have been reset
    server-side already; do not show the banner."""
    cache_file = tmp_path / "cache.json"
    monkeypatch.setenv("REVUE_LICENCE_CACHE_PATH", str(cache_file))
    _write_cache(cache_file, paywall_state="exhausted", fresh=False)
    cli = _capture_cli(monkeypatch)

    from revue_skill.skill.upgrade_prompt import render_upgrade_prompt_if_exhausted
    render_upgrade_prompt_if_exhausted()

    text = "\n".join(cli)
    assert "FREE-TIER REVIEW LIMIT REACHED" not in text, (
        "stale cache must not render the upgrade banner — paywall_state may "
        "have been reset server-side"
    )


def test_skips_banner_when_paywall_state_is_none(monkeypatch, tmp_path):
    """Baseline: fresh cache with paywall_state=None means review headroom
    remains — banner must not render."""
    cache_file = tmp_path / "cache.json"
    monkeypatch.setenv("REVUE_LICENCE_CACHE_PATH", str(cache_file))
    _write_cache(cache_file, paywall_state=None, fresh=True)
    cli = _capture_cli(monkeypatch)

    from revue_skill.skill.upgrade_prompt import render_upgrade_prompt_if_exhausted
    render_upgrade_prompt_if_exhausted()

    text = "\n".join(cli)
    assert "FREE-TIER REVIEW LIMIT REACHED" not in text


def test_skips_banner_when_cache_missing(monkeypatch, tmp_path):
    """Baseline: no cache file at all → no banner (early return)."""
    cache_file = tmp_path / "cache.json"  # not written
    monkeypatch.setenv("REVUE_LICENCE_CACHE_PATH", str(cache_file))
    cli = _capture_cli(monkeypatch)

    from revue_skill.skill.upgrade_prompt import render_upgrade_prompt_if_exhausted
    render_upgrade_prompt_if_exhausted()

    text = "\n".join(cli)
    assert "FREE-TIER REVIEW LIMIT REACHED" not in text


def test_banner_does_not_hardcode_review_count(monkeypatch, tmp_path):
    """Fix 8: banner copy must NOT contain the magic number ``25 reviews``;
    the cap is server-owned (REVIEWS_LIMIT_BY_TIER) and not derivable from
    the cache without drift risk."""
    cache_file = tmp_path / "cache.json"
    monkeypatch.setenv("REVUE_LICENCE_CACHE_PATH", str(cache_file))
    _write_cache(cache_file, paywall_state="exhausted", fresh=True)
    cli = _capture_cli(monkeypatch)

    from revue_skill.skill.upgrade_prompt import render_upgrade_prompt_if_exhausted
    render_upgrade_prompt_if_exhausted()

    text = "\n".join(cli)
    assert "25 reviews" not in text, (
        "upgrade prompt must not hardcode '25 reviews' — would drift if the "
        "server-side cap in REVIEWS_LIMIT_BY_TIER changes"
    )
    # And the new generic message is present.
    assert "monthly review limit" in text


# --- Fix 1: zero-findings path must call render_upgrade_prompt_if_exhausted ---


def test_zero_findings_branch_calls_post_review_signals():
    """Fix 1: the early-return branch in ``cmd_consolidate`` (no findings
    from any agent) must trigger the upgrade banner before returning.
    Otherwise the upgrade banner is silently skipped whenever the AI agents
    produce zero findings (which happens on small/clean diffs).

    Since the call is now routed through ``_emit_post_review_signals``
    (REVUE-279 review fix: extract duplicated orchestration), the AST check
    accepts either the legacy direct call to ``render_upgrade_prompt_if_exhausted``
    or the new ``_emit_post_review_signals`` helper that wraps it. The helper's
    own contract is verified separately (see ``_emit_post_review_signals``
    body — it always calls the upgrade prompt before telemetry).
    """
    import ast
    import inspect

    from revue_skill.skill import local_run

    src = inspect.getsource(local_run.cmd_consolidate)
    tree = ast.parse(src)

    # Find the function def and walk its body looking for the
    # `if not all_reviews:` early-return branch.
    func = next(
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
        and node.name == "cmd_consolidate"
    )

    branch_body: list[ast.stmt] | None = None
    for node in ast.walk(func):
        if (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.UnaryOp)
            and isinstance(node.test.op, ast.Not)
            and isinstance(node.test.operand, ast.Name)
            and node.test.operand.id == "all_reviews"
        ):
            branch_body = node.body
            break

    assert branch_body is not None, (
        "could not find the `if not all_reviews:` branch in cmd_consolidate"
    )

    # Accept either the direct call (legacy) or the helper wrapper (current).
    expected_callers = {
        "render_upgrade_prompt_if_exhausted",
        "_emit_post_review_signals",
    }
    found_call = False
    for stmt in branch_body:
        for node in ast.walk(stmt):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in expected_callers
            ):
                found_call = True
                break
        if found_call:
            break

    assert found_call, (
        "zero-findings branch in cmd_consolidate must call one of "
        f"{sorted(expected_callers)} — without it, users hit the free-tier "
        "cap silently when reviews return zero findings"
    )
