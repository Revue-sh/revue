"""Display upgrade prompt when free-tier paywall is exhausted (REVUE-279 Task 3).

Renders a soft-cap notice when paywall_state == "exhausted", directing users to
upgrade to a paid tier. The notice appears after findings but never blocks the
review (soft cap, not hard cap).
"""
from __future__ import annotations

import json
from pathlib import Path


def _get_cache_path() -> Path:
    """Return the path to the licence cache file."""
    from revue_skill.validate import _get_cache_path as validate_get_cache_path
    return validate_get_cache_path()


def _read_fresh_paywall_state() -> str | None:
    """Return ``paywall_state`` from the licence cache, or ``None`` if the
    cache is missing, stale, malformed, or has no paywall_state field.

    Pure decision logic — no I/O on the caller's path beyond reading the cache
    file. Stale cache is treated as "unknown state" because a paywall_state
    that's hours/days old may have been reset server-side.
    """
    from revue_core.core.logging_channels import Log
    from revue_skill.validate import _is_cache_fresh

    try:
        cache_path = _get_cache_path()
        if not cache_path.exists():
            return None

        with open(cache_path) as f:
            cache = json.load(f)

        if not _is_cache_fresh(cache):
            return None

        return cache.get("paywall_state")

    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        Log.cli.debug(
            "[revue] upgrade_prompt: failed to read cache (best-effort): %s", exc
        )
        return None


def _render_upgrade_banner() -> None:
    """Print the tier-agnostic upgrade banner. Pure rendering — no I/O, no
    decisions. Kept separate from the decision logic so a future caller can
    render the banner directly (e.g. from a CLI ``revue status`` command)
    without re-reading the cache.

    Tier-agnostic copy: the cap value is server-owned (REVIEWS_LIMIT_BY_TIER)
    and not stored in the cache, so we cannot derive it client-side without
    drift risk.
    """
    from revue_core.core.logging_channels import Log
    Log.cli.info("")
    Log.cli.info("%s", "=" * 64)
    Log.cli.info("  ⚠️  FREE-TIER REVIEW LIMIT REACHED")
    Log.cli.info("%s", "=" * 64)
    Log.cli.info("")
    Log.cli.info(
        "  You've reached your monthly review limit on the free tier."
    )
    Log.cli.info("  Upgrade to a paid plan for unlimited monthly reviews:")
    Log.cli.info("")
    Log.cli.info("  → Visit: https://revue.sh/pricing")
    Log.cli.info("  → Contact: hello@revue.sh")
    Log.cli.info("")
    Log.cli.info(
        "  Your review still ran and findings are shown above. This is a"
    )
    Log.cli.info("  soft limit — you can continue using Revue while on the")
    Log.cli.info("  free tier, with monthly reset on the 1st (UTC).")
    Log.cli.info("")
    Log.cli.info("%s", "=" * 64)
    Log.cli.info("")


def render_upgrade_prompt_if_exhausted() -> None:
    """Display the upgrade banner if the cache reports the free tier as
    ``exhausted``. Soft cap: the review already ran and findings are
    displayed — this prompt is informational only and never blocks.

    Orchestration only: decision logic is in ``_read_fresh_paywall_state``,
    rendering is in ``_render_upgrade_banner``.
    """
    if _read_fresh_paywall_state() == "exhausted":
        _render_upgrade_banner()
