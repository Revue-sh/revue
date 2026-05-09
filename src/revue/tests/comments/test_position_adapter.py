"""Tests for position_adapter.py — TC1-TC16 from REVUE-236.

Bitbucket (TC9, TC10, TC13) is deferred to a follow-up ticket.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from revue.comments.position_adapter import (
    GitHubPositionAdapter,
    GitLabPositionAdapter,
    PlatformPosition,
    get_position_adapter,
)
from revue.core.models import PRContext

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures/positioning"

# ---------------------------------------------------------------------------
# Shared diff snippets for TC1-TC5
# ---------------------------------------------------------------------------

# @@ -1,3 +1,5 @@   context  +new_line  +another  context
# new-file line numbers: context=1, new_line=2, another=3, context=4
_ADDED_DIFF = "@@ -1,3 +1,5 @@\n context\n+new_line\n+another\n context\n"
# @@ -1,3 +1,2 @@   -old_line  context  context
# old-file line 1 removed; new-file lines: context=1, context=2
_REMOVED_DIFF = "@@ -1,3 +1,2 @@\n-old_line\n context\n context\n"


# ---------------------------------------------------------------------------
# TC1-TC5 — resolve() changed-line rule
# ---------------------------------------------------------------------------

def test_tc1_changed_line_returns_platform_position():
    """A '+' line in the diff produces a PlatformPosition with the correct anchor."""
    adapter = GitHubPositionAdapter()
    result = adapter.resolve(2, _ADDED_DIFF, "file.py", None)
    assert result is not None
    assert result["file_path"] == "file.py"
    assert result["start_line"] == 2
    assert result["end_line"] == 2


def test_tc2_context_line_returns_none():
    """A context (' ') line produces None — not anchorable."""
    adapter = GitHubPositionAdapter()
    result = adapter.resolve(1, _ADDED_DIFF, "file.py", None)  # line 1 is context
    assert result is None


def test_tc3_removed_line_returns_none():
    """A removed ('-') line produces None — no longer in the new file."""
    adapter = GitHubPositionAdapter()
    result = adapter.resolve(1, _REMOVED_DIFF, "file.py", None)  # line 1 was removed
    assert result is None


def test_tc4_line_not_in_diff_at_all_returns_none():
    """A line outside all diff hunks produces None."""
    adapter = GitHubPositionAdapter()
    result = adapter.resolve(999, _ADDED_DIFF, "file.py", None)
    assert result is None


def test_tc5_multi_line_end_equals_start_plus_rlc_minus_one():
    """Multi-line anchor: end_line == start_line + replacement_line_count - 1."""
    adapter = GitHubPositionAdapter()
    result = adapter.resolve(2, _ADDED_DIFF, "file.py", None, replacement_line_count=3)
    assert result is not None
    assert result["start_line"] == 2
    assert result["end_line"] == 4  # 2 + 3 - 1


# ---------------------------------------------------------------------------
# TC6-TC7 — GitHub to_api_params
# ---------------------------------------------------------------------------

def test_tc6_github_single_line_no_start_fields():
    """Single-line comment: only path + side + line; no start_line or start_side."""
    adapter = GitHubPositionAdapter()
    pp = PlatformPosition(file_path="a.py", start_line=10, end_line=10)
    params = adapter.to_api_params(pp)
    assert params == {"path": "a.py", "side": "RIGHT", "line": 10}
    assert "start_line" not in params
    assert "start_side" not in params


def test_tc7_github_multi_line_includes_start_fields():
    """Multi-line comment: includes start_line and start_side."""
    adapter = GitHubPositionAdapter()
    pp = PlatformPosition(file_path="a.py", start_line=10, end_line=12)
    params = adapter.to_api_params(pp)
    assert params == {
        "path": "a.py",
        "side": "RIGHT",
        "line": 12,
        "start_line": 10,
        "start_side": "RIGHT",
    }


# ---------------------------------------------------------------------------
# TC8 — GitLab to_api_params
# ---------------------------------------------------------------------------

def test_tc8_gitlab_produces_all_sha_fields_and_new_line():
    """GitLab position: all SHA fields + new_line == start_line; no range fields."""
    adapter = GitLabPositionAdapter(base_sha="base", head_sha="head", start_sha="start")
    pp = PlatformPosition(file_path="b.py", start_line=5, end_line=7)
    params = adapter.to_api_params(pp)
    assert params == {
        "position_type": "text",
        "base_sha": "base",
        "head_sha": "head",
        "start_sha": "start",
        "new_path": "b.py",
        "old_path": "b.py",
        "new_line": 5,  # always start_line; end expressed via suggestion fence in body
    }
    assert "start_line" not in params
    assert "end_line" not in params


# ---------------------------------------------------------------------------
# TC11 — GitHub fixture-driven tests
# ---------------------------------------------------------------------------

def _load_fixtures(platform: str) -> list[Path]:
    return sorted((FIXTURES_DIR / platform).glob("*.json"))


@pytest.mark.parametrize("fixture_path", _load_fixtures("github"), ids=lambda p: p.name)
def test_tc11_github_fixture(fixture_path: Path) -> None:
    f = json.loads(fixture_path.read_text())
    adapter = GitHubPositionAdapter()
    pp = adapter.resolve(
        f["reported_line"],
        f["diff_snippet"],
        f["file_path"],
        None,
        f.get("replacement_line_count", 1),
    )

    exp_pos = f.get("expected_position")
    if exp_pos is None:
        assert pp is None, f"Expected None for unanchored fixture {fixture_path.name}"
        return

    assert pp is not None, f"Expected anchor for {fixture_path.name}"
    assert pp["start_line"] == exp_pos["start_line"]
    assert pp["end_line"] == exp_pos["end_line"]

    exp_params = f.get("expected_api_params")
    if exp_params:
        assert adapter.to_api_params(pp) == exp_params


# ---------------------------------------------------------------------------
# TC12 — GitLab fixture-driven tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fixture_path", _load_fixtures("gitlab"), ids=lambda p: p.name)
def test_tc12_gitlab_fixture(fixture_path: Path) -> None:
    f = json.loads(fixture_path.read_text())
    adapter = GitLabPositionAdapter(
        base_sha=f.get("posted_base_sha", ""),
        head_sha=f.get("posted_head_sha", ""),
        start_sha=f.get("posted_start_sha", ""),
    )
    pp = adapter.resolve(
        f["reported_line"],
        f["diff_snippet"],
        f["file_path"],
        None,
        f.get("replacement_line_count", 1),
    )

    exp_pos = f.get("expected_position")
    if exp_pos is None:
        assert pp is None, f"Expected None for unanchored fixture {fixture_path.name}"
        return

    assert pp is not None, f"Expected anchor for {fixture_path.name}"
    assert pp["start_line"] == exp_pos["start_line"]
    assert pp["end_line"] == exp_pos["end_line"]

    exp_params = f.get("expected_api_params")
    if exp_params:
        assert adapter.to_api_params(pp) == exp_params


# ---------------------------------------------------------------------------
# TC15 — Registry: correct types, no elif chain
# ---------------------------------------------------------------------------

def _make_ctx(platform: str) -> PRContext:
    return PRContext(
        platform=platform, pr_number=1, repo_owner="o", repo_name="r", repo_path="."
    )


def test_tc15_registry_github_returns_github_adapter():
    adapter = get_position_adapter("github", _make_ctx("github"))
    assert isinstance(adapter, GitHubPositionAdapter)


def test_tc15_registry_gitlab_returns_gitlab_adapter():
    adapter = get_position_adapter("gitlab", _make_ctx("gitlab"))
    assert isinstance(adapter, GitLabPositionAdapter)


def test_tc15_registry_unknown_platform_raises():
    with pytest.raises((KeyError, ValueError)):
        get_position_adapter("unknown", _make_ctx("unknown"))
