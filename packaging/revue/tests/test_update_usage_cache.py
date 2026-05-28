"""Unit tests for ``revue_skill.skill.update_usage_cache`` (REVUE-280).

Covers:
- First invocation creates the file with reviews_this_month=1
- Subsequent invocations within the same month increment the counter
- A month rollover resets the counter to 1
- The write is atomic: the destination is never observed half-written
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def _read(cache_path: Path) -> dict:
    return json.loads(cache_path.read_text())


def test_first_invocation_creates_cache_with_count_one(monkeypatch, tmp_path):
    cache_path = tmp_path / "usage.json"
    monkeypatch.setenv("REVUE_USAGE_CACHE_PATH", str(cache_path))

    from revue_skill.skill.update_usage_cache import update_usage_cache

    update_usage_cache()

    data = _read(cache_path)
    assert data["reviews_this_month"] == 1
    assert "month_reset_at" in data
    assert "cached_at" in data


def test_second_invocation_in_same_month_increments(monkeypatch, tmp_path):
    cache_path = tmp_path / "usage.json"
    monkeypatch.setenv("REVUE_USAGE_CACHE_PATH", str(cache_path))

    from revue_skill.skill.update_usage_cache import update_usage_cache

    update_usage_cache()
    update_usage_cache()
    update_usage_cache()

    assert _read(cache_path)["reviews_this_month"] == 3


def test_month_rollover_resets_counter(monkeypatch, tmp_path):
    """A cache stamped with an old month_reset_at must reset to 1, not increment."""
    cache_path = tmp_path / "usage.json"
    monkeypatch.setenv("REVUE_USAGE_CACHE_PATH", str(cache_path))

    # Seed with last-month metadata; current code parses month_reset_at and
    # resets when it doesn't match this month's UTC boundary.
    cache_path.write_text(json.dumps({
        "reviews_this_month": 99,
        "cached_at": 1700000000,
        "month_reset_at": "2024-01-01 00:00:00",
    }))

    from revue_skill.skill.update_usage_cache import update_usage_cache

    update_usage_cache()

    assert _read(cache_path)["reviews_this_month"] == 1


def test_write_is_atomic_no_partial_file_on_failure(monkeypatch, tmp_path):
    """If json.dump raises mid-write, the destination must keep its prior
    contents — no partial/empty file survives. This is the contract that
    protects against torn writes from concurrent ``revue`` runs.
    """
    cache_path = tmp_path / "usage.json"
    monkeypatch.setenv("REVUE_USAGE_CACHE_PATH", str(cache_path))

    # Seed with a known-good prior state we can detect after the failure.
    sentinel = {
        "reviews_this_month": 42,
        "cached_at": 1700000000,
        "month_reset_at": "2026-05-01 00:00:00",
    }
    cache_path.write_text(json.dumps(sentinel))

    # Force the underlying json.dump to blow up mid-write. With atomic
    # rename, the destination is untouched; without it, the destination
    # would be truncated/empty before the exception.
    import revue_skill.skill.cache_paths as cache_paths_mod
    import revue_skill.skill.update_usage_cache as mod

    def _boom(*_args, **_kwargs):
        raise IOError("disk full")

    # The atomic write now lives in cache_paths.atomic_json_write; patch
    # json.dump there because that's where it's called.
    monkeypatch.setattr(cache_paths_mod.json, "dump", _boom)

    mod.update_usage_cache()  # best-effort, swallows exception internally

    # Prior contents must still be present and parseable.
    assert _read(cache_path) == sentinel

    # No temp files left behind in the cache dir. Helper now prefixes
    # temp files with ``.<destination-name>-``.
    leftovers = [
        p for p in tmp_path.iterdir()
        if p.name.startswith(f".{cache_path.name}-") and p.name.endswith(".tmp")
    ]
    assert leftovers == [], f"temp files leaked: {leftovers}"
