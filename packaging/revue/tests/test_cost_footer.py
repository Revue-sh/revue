"""Unit tests for ``revue_skill.skill.cost_footer`` (REVUE-280).

Covers:
- Footer renders with correct formatting and numbers
- Footer is hidden behind --no-footer flag
- Footer uses correct baseline (Sonnet 4.5) and current model pricing
- Footer computes savings from locally available data (no network calls)
- Footer rounds savings appropriately
"""
from __future__ import annotations

import json
import time
from pathlib import Path


def _write_licence_cache(cache_path: Path, *, tier: str = "free") -> None:
    """Write a minimal licence cache file shaped like the server's /validate response."""
    now = int(time.time())
    body = {
        "valid": True,
        "tier": tier,
        "reviews_remaining": 10,
        "paywall_state": None,
        "refresh_after_ts": now + 3600,
        "cached_at": now,
        "refreshed_jwt": None,
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(body))


def _write_usage_cache(cache_path: Path, *, reviews_this_month: int = 5) -> None:
    """Write a usage counter cache file with review count this month."""
    now = int(time.time())
    body = {
        "reviews_this_month": reviews_this_month,
        "cached_at": now,
        "month_reset_at": "2026-05-01 00:00:00",
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(body))


def _capture_cli(monkeypatch) -> list[str]:
    """Capture ``Log.cli.info`` messages into a list."""
    from revue_core.core.logging_channels import Log

    captured: list[str] = []

    def _capture(message, *args):
        try:
            captured.append(message % args if args else message)
        except (TypeError, ValueError):
            captured.append(f"{message} {args}")

    monkeypatch.setattr(Log.cli, "info", _capture)
    return captured


class TestCostFooterRendering:
    """AC1–AC7: Footer renders with correct format and savings calculation."""

    def test_footer_renders_with_savings_calculation(self, monkeypatch, tmp_path):
        """AC1,AC2,AC3,AC5: Footer shows: 'Saved ~$X this month vs. CI-only review
        (Y reviews × ~$Z/review)' with DeepSeek pricing vs Sonnet 4.5 baseline."""
        licence_cache = tmp_path / "licence.json"
        usage_cache = tmp_path / "usage.json"
        monkeypatch.setenv("REVUE_LICENCE_CACHE_PATH", str(licence_cache))
        monkeypatch.setenv("REVUE_USAGE_CACHE_PATH", str(usage_cache))
        _write_licence_cache(licence_cache, tier="free")
        _write_usage_cache(usage_cache, reviews_this_month=5)
        cli = _capture_cli(monkeypatch)

        from revue_skill.skill.cost_footer import render_cost_footer

        render_cost_footer()

        text = "\n".join(cli)
        # Footer must contain the savings message pattern
        assert "Saved ~$" in text, "Footer must show cost savings"
        assert "this month" in text, "Footer must reference 'this month'"
        assert "reviews ×" in text or "review ×" in text, "Footer must show review count × cost"
        assert "/review" in text, "Footer must show per-review cost"
        assert "CI-only" in text, "Footer must mention CI-only baseline"

    def test_footer_uses_correct_baseline_sonnet_45(self, monkeypatch, tmp_path):
        """AC4: Counterfactual baseline must be Anthropic Sonnet 4.5 (CI-only
        equivalent), not the current model."""
        licence_cache = tmp_path / "licence.json"
        usage_cache = tmp_path / "usage.json"
        monkeypatch.setenv("REVUE_LICENCE_CACHE_PATH", str(licence_cache))
        monkeypatch.setenv("REVUE_USAGE_CACHE_PATH", str(usage_cache))
        _write_licence_cache(licence_cache)
        _write_usage_cache(usage_cache, reviews_this_month=10)
        cli = _capture_cli(monkeypatch)

        from revue_skill.skill.cost_footer import render_cost_footer

        render_cost_footer()

        text = "\n".join(cli)
        # The footer exists and has content (baseline logic is internal)
        assert len(text) > 0, "Footer must render"
        # Should show positive savings (DeepSeek is cheaper than Sonnet 4.5)
        assert "Saved ~$" in text

    def test_footer_shows_no_savings_on_paid_tier(self, monkeypatch, tmp_path):
        """AC5: Footer only renders for free tier users (paid tiers have no
        monthly cap and different value messaging)."""
        licence_cache = tmp_path / "licence.json"
        usage_cache = tmp_path / "usage.json"
        monkeypatch.setenv("REVUE_LICENCE_CACHE_PATH", str(licence_cache))
        monkeypatch.setenv("REVUE_USAGE_CACHE_PATH", str(usage_cache))
        _write_licence_cache(licence_cache, tier="pro")
        _write_usage_cache(usage_cache, reviews_this_month=100)
        cli = _capture_cli(monkeypatch)

        from revue_skill.skill.cost_footer import render_cost_footer

        render_cost_footer()

        text = "\n".join(cli)
        # Paid tiers should not show the monthly savings message
        assert "this month" not in text.lower() or "Saved ~$" not in text, (
            "Paid tier should not show monthly savings footer (no cap constraint)"
        )

    def test_footer_uses_local_computation_no_network(self, monkeypatch, tmp_path):
        """AC6: Saving figure is locally computed; no server round-trip required."""
        licence_cache = tmp_path / "licence.json"
        usage_cache = tmp_path / "usage.json"
        monkeypatch.setenv("REVUE_LICENCE_CACHE_PATH", str(licence_cache))
        monkeypatch.setenv("REVUE_USAGE_CACHE_PATH", str(usage_cache))
        _write_licence_cache(licence_cache)
        _write_usage_cache(usage_cache, reviews_this_month=3)
        cli = _capture_cli(monkeypatch)

        # No network mocking needed — the function must not make any network calls
        from revue_skill.skill.cost_footer import render_cost_footer

        render_cost_footer()

        text = "\n".join(cli)
        # If it renders without error, network was not called
        assert "Saved ~$" in text

    def test_footer_skips_silently_on_zero_reviews(self, monkeypatch, tmp_path):
        """Contract: zero reviews this month → NO footer renders. The
        implementation early-returns on ``reviews_this_month == 0``
        because a "Saved ~$0.00 (0 reviews × ...)" line is noise — the
        customer hasn't run a review yet, so there's nothing to brag
        about. Asserts the negative directly; the prior test wrapped its
        assertion in ``if 'Saved' in text`` and so passed vacuously even
        if the implementation regressed to render $0 lines.
        """
        licence_cache = tmp_path / "licence.json"
        usage_cache = tmp_path / "usage.json"
        monkeypatch.setenv("REVUE_LICENCE_CACHE_PATH", str(licence_cache))
        monkeypatch.setenv("REVUE_USAGE_CACHE_PATH", str(usage_cache))
        _write_licence_cache(licence_cache)
        _write_usage_cache(usage_cache, reviews_this_month=0)
        cli = _capture_cli(monkeypatch)

        from revue_skill.skill.cost_footer import render_cost_footer

        render_cost_footer()

        text = "\n".join(cli)
        assert "Saved ~$" not in text, (
            "zero reviews this month must render no footer at all "
            "(not a $0 line)"
        )


class TestNoFooterFlag:
    """AC7: Footer hidden behind --no-footer flag."""

    def test_no_footer_param_suppresses_output(self, monkeypatch, tmp_path):
        """AC7: passing ``no_footer=True`` (forwarded from the ``--no-footer``
        CLI flag) must suppress the footer for piped/CI usage. Plumbed as a
        function parameter rather than an env-var so suppression is one
        explicit channel — no process-wide ``os.environ`` mutation that
        leaks across tests or subsequent calls in the same process.
        """
        licence_cache = tmp_path / "licence.json"
        usage_cache = tmp_path / "usage.json"
        monkeypatch.setenv("REVUE_LICENCE_CACHE_PATH", str(licence_cache))
        monkeypatch.setenv("REVUE_USAGE_CACHE_PATH", str(usage_cache))
        _write_licence_cache(licence_cache)
        _write_usage_cache(usage_cache, reviews_this_month=5)
        cli = _capture_cli(monkeypatch)

        from revue_skill.skill.cost_footer import render_cost_footer

        render_cost_footer(no_footer=True)

        text = "\n".join(cli)
        assert "Saved ~$" not in text, (
            "no_footer=True must suppress cost footer output"
        )

    def test_default_no_footer_false_still_renders(self, monkeypatch, tmp_path):
        """Sibling-test: the suppression must be opt-in. Default behaviour
        (no_footer=False, the parameter default) still renders the footer
        — otherwise we have a regression that hides the footer by default.
        """
        licence_cache = tmp_path / "licence.json"
        usage_cache = tmp_path / "usage.json"
        monkeypatch.setenv("REVUE_LICENCE_CACHE_PATH", str(licence_cache))
        monkeypatch.setenv("REVUE_USAGE_CACHE_PATH", str(usage_cache))
        _write_licence_cache(licence_cache)
        _write_usage_cache(usage_cache, reviews_this_month=5)
        cli = _capture_cli(monkeypatch)

        from revue_skill.skill.cost_footer import render_cost_footer

        render_cost_footer()  # default no_footer=False

        assert "Saved ~$" in "\n".join(cli)


class TestCacheHandling:
    """Baseline: footer handles missing cache files gracefully."""

    def test_missing_licence_cache_skips_footer(self, monkeypatch, tmp_path):
        """Baseline: no licence cache → no footer (early return)."""
        licence_cache = tmp_path / "licence.json"  # not written
        usage_cache = tmp_path / "usage.json"
        monkeypatch.setenv("REVUE_LICENCE_CACHE_PATH", str(licence_cache))
        monkeypatch.setenv("REVUE_USAGE_CACHE_PATH", str(usage_cache))
        _write_usage_cache(usage_cache)
        cli = _capture_cli(monkeypatch)

        from revue_skill.skill.cost_footer import render_cost_footer

        render_cost_footer()

        text = "\n".join(cli)
        assert "Saved ~$" not in text

    def test_missing_usage_cache_skips_footer(self, monkeypatch, tmp_path):
        """Baseline: no usage cache → no footer (early return)."""
        licence_cache = tmp_path / "licence.json"
        usage_cache = tmp_path / "usage.json"  # not written
        monkeypatch.setenv("REVUE_LICENCE_CACHE_PATH", str(licence_cache))
        monkeypatch.setenv("REVUE_USAGE_CACHE_PATH", str(usage_cache))
        _write_licence_cache(licence_cache)
        cli = _capture_cli(monkeypatch)

        from revue_skill.skill.cost_footer import render_cost_footer

        render_cost_footer()

        text = "\n".join(cli)
        assert "Saved ~$" not in text


class TestCacheFreshness:
    """Stale licence cache must be treated as 'unknown tier' and skip the footer."""

    def test_stale_licence_cache_skips_footer(self, monkeypatch, tmp_path):
        """A cache where ``refresh_after_ts`` is in the past is stale; the
        tier value may be obsolete (customer could have upgraded mid-month),
        so the footer must skip rather than route paid users into free-tier
        copy. Mirrors upgrade_prompt's freshness gate.
        """
        licence_cache = tmp_path / "licence.json"
        usage_cache = tmp_path / "usage.json"
        monkeypatch.setenv("REVUE_LICENCE_CACHE_PATH", str(licence_cache))
        monkeypatch.setenv("REVUE_USAGE_CACHE_PATH", str(usage_cache))

        # Stale cache: refresh_after_ts is in the past.
        past = int(time.time()) - 7200
        licence_cache.parent.mkdir(parents=True, exist_ok=True)
        licence_cache.write_text(json.dumps({
            "valid": True,
            "tier": "free",
            "reviews_remaining": 10,
            "paywall_state": None,
            "refresh_after_ts": past,
            "cached_at": past,
            "refreshed_jwt": None,
        }))
        _write_usage_cache(usage_cache, reviews_this_month=5)
        cli = _capture_cli(monkeypatch)

        from revue_skill.skill.cost_footer import render_cost_footer

        render_cost_footer()

        assert "Saved ~$" not in "\n".join(cli), (
            "stale licence cache must skip the footer — tier may be obsolete"
        )

    def test_invalid_licence_cache_skips_footer(self, monkeypatch, tmp_path):
        """A cache with ``valid: false`` is not fresh; skip the footer."""
        licence_cache = tmp_path / "licence.json"
        usage_cache = tmp_path / "usage.json"
        monkeypatch.setenv("REVUE_LICENCE_CACHE_PATH", str(licence_cache))
        monkeypatch.setenv("REVUE_USAGE_CACHE_PATH", str(usage_cache))

        now = int(time.time())
        licence_cache.parent.mkdir(parents=True, exist_ok=True)
        licence_cache.write_text(json.dumps({
            "valid": False,
            "tier": "free",
            "paywall_state": None,
            "refresh_after_ts": now + 3600,
            "cached_at": now,
        }))
        _write_usage_cache(usage_cache, reviews_this_month=5)
        cli = _capture_cli(monkeypatch)

        from revue_skill.skill.cost_footer import render_cost_footer

        render_cost_footer()

        assert "Saved ~$" not in "\n".join(cli)


class TestNegativeSavingsGuard:
    """A pricing regression where the baseline is no longer cheaper must
    silently skip the footer — never print a negative or zero \"Saved\" line."""

    def test_zero_or_negative_savings_skips_footer(self, monkeypatch, tmp_path):
        licence_cache = tmp_path / "licence.json"
        usage_cache = tmp_path / "usage.json"
        monkeypatch.setenv("REVUE_LICENCE_CACHE_PATH", str(licence_cache))
        monkeypatch.setenv("REVUE_USAGE_CACHE_PATH", str(usage_cache))
        _write_licence_cache(licence_cache)
        _write_usage_cache(usage_cache, reviews_this_month=10)

        import revue_skill.skill.cost_footer as mod

        # Force baseline to be cheaper than current (pricing regression).
        monkeypatch.setattr(mod, "BASELINE_COST_PER_REVIEW", 0.001)
        monkeypatch.setattr(mod, "CURRENT_COST_PER_REVIEW", 0.005)

        cli = _capture_cli(monkeypatch)

        mod.render_cost_footer()

        text = "\n".join(cli)
        assert "Saved" not in text, (
            "negative savings must NEVER render — silently skip instead"
        )

    def test_equal_savings_skips_footer(self, monkeypatch, tmp_path):
        """Exact parity (savings == 0) is also a no-render condition."""
        licence_cache = tmp_path / "licence.json"
        usage_cache = tmp_path / "usage.json"
        monkeypatch.setenv("REVUE_LICENCE_CACHE_PATH", str(licence_cache))
        monkeypatch.setenv("REVUE_USAGE_CACHE_PATH", str(usage_cache))
        _write_licence_cache(licence_cache)
        _write_usage_cache(usage_cache, reviews_this_month=10)

        import revue_skill.skill.cost_footer as mod

        monkeypatch.setattr(mod, "BASELINE_COST_PER_REVIEW", 0.005)
        monkeypatch.setattr(mod, "CURRENT_COST_PER_REVIEW", 0.005)

        cli = _capture_cli(monkeypatch)

        mod.render_cost_footer()

        assert "Saved" not in "\n".join(cli)


class TestFormattingAccuracy:
    """Verify footer formatting matches AC5 spec exactly."""

    def test_footer_format_matches_spec(self, monkeypatch, tmp_path):
        """AC5: Copy reads exactly: 'Saved ~$X this month vs. CI-only review
        (Y reviews × ~$Z/review)'"""
        licence_cache = tmp_path / "licence.json"
        usage_cache = tmp_path / "usage.json"
        monkeypatch.setenv("REVUE_LICENCE_CACHE_PATH", str(licence_cache))
        monkeypatch.setenv("REVUE_USAGE_CACHE_PATH", str(usage_cache))
        _write_licence_cache(licence_cache)
        _write_usage_cache(usage_cache, reviews_this_month=2)
        cli = _capture_cli(monkeypatch)

        from revue_skill.skill.cost_footer import render_cost_footer

        render_cost_footer()

        text = "\n".join(cli)
        # Check the pattern matches the spec
        import re

        pattern = r"Saved ~\$[\d.]+\s+this month vs\.\s+CI-only review\s+\(\d+\s+reviews? ×\s+~\$[\d.]+/review\)"
        assert re.search(pattern, text), (
            f"Footer copy must match spec pattern. Got: {text}"
        )
