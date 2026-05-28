"""Cost-saving footer renderer (REVUE-280).

Computes and displays monthly savings from using Revue vs. CI-only review
workflow. Footer appears below findings when running /revue-local.

Key design decisions:
- Pricing is hardcoded (not fetched from server) to avoid network calls
- Baseline = Anthropic Sonnet 4.5 (industry CI standard)
- Current model = DeepSeek v4 Pro (default from models_registry)
- Savings = (baseline_cost - current_cost) × reviews_this_month
- Footer only renders for free tier (paid tiers have no cap messaging)
- Suppression is via the ``no_footer`` parameter (passed in from the
  ``--no-footer`` CLI flag) — never via env-var mutation
- Both caches must be fresh; missing cache = no footer
"""
from __future__ import annotations

import json

from revue_skill.skill.cache_paths import (
    get_licence_cache_path,
    get_usage_cache_path,
)


# Pricing data (USD per review, as of 2026-05-28)
# These hardcoded values represent the costs we would pay to the provider
# per single review run using each model. Estimates assume ~6000 input
# tokens + ~1000 output tokens per review (typical /revue-local payload).
#
# Sonnet 4.5 baseline (CI-only equivalent):
#   Input:  6000 tokens × $3 / 1M tokens = $0.018
#   Output: 1000 tokens × $15 / 1M tokens = $0.015
#   Total ≈ $0.033 per review
BASELINE_COST_PER_REVIEW = 0.033  # Sonnet 4.5 (anthropic)

# DeepSeek v4 Pro via OpenRouter (current default):
#   OpenRouter list price: $0.14 / 1M input tokens, $0.28 / 1M output tokens
#   Input:  6000 tokens × $0.14 / 1M = $0.00084
#   Output: 1000 tokens × $0.28 / 1M = $0.00028
#   Computed total ≈ $0.00112 per review
#
# The constant is rounded UP to $0.009 — deliberately conservative:
#   - overstates our cost → understates the displayed savings → never
#     promises more value than we deliver.
#   - absorbs token-estimate variance (longer diffs, retries, OpenRouter
#     surcharges) without flipping savings negative.
# If/when we have a real per-tenant rate-card we should replace this with
# observed cost from emit_usage telemetry rather than tightening the
# estimate further.
CURRENT_COST_PER_REVIEW = 0.009   # DeepSeek v4 Pro (openrouter), conservative


def render_cost_footer(no_footer: bool = False) -> None:
    """Render the cost-saving footer if all conditions are met.

    Args:
        no_footer: If True, suppress the footer (forwarded from the
            ``--no-footer`` CLI flag). Explicit parameter rather than
            env-var lookup so the suppression channel is one-and-only
            and visible at every call site.

    Conditions:
    1. ``no_footer`` is False
    2. Licence cache file exists, is readable, AND is fresh per
       ``validate.is_cache_fresh`` (same staleness contract as the upgrade
       prompt — a stale ``tier`` could be hours/days out of date and would
       silently show the wrong message to a customer who upgraded mid-month)
    3. Tier must be 'free' (paid tiers don't have monthly cap messaging)
    4. Usage cache file exists and is readable
    5. reviews_this_month > 0
    6. Computed savings > 0 (a pricing regression that makes us more
       expensive than the baseline must NEVER render a negative-savings
       footer — silently skip instead)

    Does not raise; silently returns if any condition is not met.
    """
    from revue_core.core.logging_channels import Log
    from revue_skill.validate import is_cache_fresh

    if no_footer:
        return

    # Load licence cache
    licence_cache_path = get_licence_cache_path()
    if not licence_cache_path.exists():
        return
    try:
        licence_data = json.loads(licence_cache_path.read_text())
    except (json.JSONDecodeError, OSError):
        return

    # Apply the same freshness contract as upgrade_prompt — a stale tier
    # value would route paid-tier customers into free-tier footer copy (or
    # vice versa). is_cache_fresh also defends against cache tampering via
    # the cached_at + CACHE_WINDOW_SECONDS upper-bound cap.
    if not is_cache_fresh(licence_data):
        return

    # Only render for free tier users
    if licence_data.get("tier") != "free":
        return

    # Load usage cache
    usage_cache_path = get_usage_cache_path()
    if not usage_cache_path.exists():
        return
    try:
        usage_data = json.loads(usage_cache_path.read_text())
    except (json.JSONDecodeError, OSError):
        return

    reviews_this_month = usage_data.get("reviews_this_month", 0)
    if reviews_this_month == 0:
        return

    # Compute savings — bail out on non-positive results. If a future
    # pricing update flips the inequality (baseline ≤ current), the
    # honest behaviour is to render nothing rather than print a
    # negative or zero "Saved" line that breaks the marketing promise.
    savings = (BASELINE_COST_PER_REVIEW - CURRENT_COST_PER_REVIEW) * reviews_this_month
    if savings <= 0:
        return

    # Format: Saved ~$X this month vs. CI-only review (Y reviews × ~$Z/review)
    review_word = "review" if reviews_this_month == 1 else "reviews"
    footer_msg = (
        f"Saved ~${savings:.2f} this month vs. CI-only review "
        f"({reviews_this_month} {review_word} × ~${CURRENT_COST_PER_REVIEW:.4f}/review)"
    )

    Log.cli.info("")
    Log.cli.info("%s", "=" * 64)
    Log.cli.info("  %s", footer_msg)
    Log.cli.info("%s", "=" * 64)


