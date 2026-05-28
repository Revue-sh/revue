"""Unit tests for ``revue_skill.skill.cache_paths`` (REVUE-280).

Covers the shared ``atomic_json_write`` helper that backs both the usage
counter and the licence cache. Extracted in response to code-review
finding #803516272 (duplicated atomic-write pattern) and the related
``except BaseException`` findings #803516267 / #803516280.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest


def test_atomic_json_write_round_trip(tmp_path):
    """Basic contract: write a dict, read it back unchanged."""
    from revue_skill.skill.cache_paths import atomic_json_write

    dest = tmp_path / "cache.json"
    payload = {"reviews_this_month": 7, "month_reset_at": "2026-05-01 00:00:00"}

    atomic_json_write(dest, payload)

    assert json.loads(dest.read_text()) == payload


def test_atomic_json_write_applies_file_mode(tmp_path):
    """``file_mode`` is applied to the temp file before the replace, so the
    destination ends up at the requested permission with no umask window.

    Skipped on Windows where POSIX permission bits are not meaningful.
    """
    if sys.platform.startswith("win"):
        pytest.skip("POSIX file modes do not apply on Windows")

    from revue_skill.skill.cache_paths import atomic_json_write

    dest = tmp_path / "secret.json"
    atomic_json_write(dest, {"k": "v"}, file_mode=0o600)

    mode = dest.stat().st_mode & 0o777
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


def test_atomic_json_write_no_partial_file_on_failure(tmp_path, monkeypatch):
    """If ``json.dump`` raises mid-write, the destination keeps its prior
    contents (atomic-rename contract) and no leftover ``.tmp`` files are
    left in the cache directory.
    """
    from revue_skill.skill import cache_paths

    dest = tmp_path / "cache.json"
    sentinel = {"original": True, "n": 42}
    dest.write_text(json.dumps(sentinel))

    def _boom(*_args, **_kwargs):
        raise IOError("disk full")

    monkeypatch.setattr(cache_paths.json, "dump", _boom)

    with pytest.raises(IOError):
        cache_paths.atomic_json_write(dest, {"new": "data"})

    # Prior contents preserved.
    assert json.loads(dest.read_text()) == sentinel

    # No leftover temp file.
    leftovers = [
        p for p in tmp_path.iterdir()
        if p.name.startswith(f".{dest.name}-") and p.name.endswith(".tmp")
    ]
    assert leftovers == [], f"temp files leaked: {leftovers}"


def test_atomic_json_write_overwrites_existing(tmp_path):
    """A second write replaces the prior contents (overwrite semantics)."""
    from revue_skill.skill.cache_paths import atomic_json_write

    dest = tmp_path / "cache.json"
    atomic_json_write(dest, {"v": 1})
    atomic_json_write(dest, {"v": 2})

    assert json.loads(dest.read_text()) == {"v": 2}


def test_atomic_json_write_does_not_swallow_keyboard_interrupt(tmp_path, monkeypatch):
    """``KeyboardInterrupt`` raised mid-write must propagate — the helper
    uses ``try/finally`` rather than ``except BaseException`` so signals
    can shut the process down cleanly (REVUE-280 code-review #803516267).
    Cleanup of the leftover temp file still runs.
    """
    from revue_skill.skill import cache_paths

    dest = tmp_path / "cache.json"

    def _interrupt(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cache_paths.json, "dump", _interrupt)

    with pytest.raises(KeyboardInterrupt):
        cache_paths.atomic_json_write(dest, {"k": "v"})

    # No leftover temp file even though KeyboardInterrupt blew through.
    leftovers = [
        p for p in tmp_path.iterdir()
        if p.name.startswith(f".{dest.name}-") and p.name.endswith(".tmp")
    ]
    assert leftovers == [], f"temp files leaked on KeyboardInterrupt: {leftovers}"
