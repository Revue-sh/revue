"""Tests for position_adapter.py — TC1-TC16 from REVUE-236.

Bitbucket (TC9, TC10, TC13) added by REVUE-238.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from revue.comments.position_adapter import (
    BitbucketPositionAdapter,
    GitHubPositionAdapter,
    GitLabPositionAdapter,
    PlatformPosition,
    PositionStatus,
    calculate,
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


# ---------------------------------------------------------------------------
# Pure calculate() function tests
# ---------------------------------------------------------------------------

def test_calculate_anchored_on_plus_line():
    """calculate() returns ANCHORED when reported_line is a '+' line in the diff."""
    # Arrange: _ADDED_DIFF — new-file line 2 is '+new_line'
    # Act
    result = calculate(_ADDED_DIFF, 2, "src/foo.py")
    # Assert
    assert result.status == PositionStatus.ANCHORED
    assert result.file_path == "src/foo.py"
    assert result.start_line == 2
    assert result.end_line == 2


def test_calculate_context_line():
    """calculate() returns CONTEXT_LINE when reported_line is a space-prefixed line."""
    # Arrange: _ADDED_DIFF — new-file line 1 is context
    # Act
    result = calculate(_ADDED_DIFF, 1, "src/foo.py")
    # Assert
    assert result.status == PositionStatus.CONTEXT_LINE
    assert result.start_line is None
    assert result.end_line is None


def test_calculate_removed_line():
    """calculate() returns REMOVED_LINE when reported_line is an old-file '-' line
    with no corresponding new-file entry (pure-deletion diff)."""
    # Arrange: pure deletion — old lines 1 and 2 removed, no new lines added
    pure_delete_diff = "@@ -1,2 +1,0 @@\n-removed_first\n-removed_second\n"
    # Act
    result = calculate(pure_delete_diff, 1, "src/foo.py")
    # Assert
    assert result.status == PositionStatus.REMOVED_LINE
    assert result.start_line is None
    assert result.end_line is None


def test_calculate_out_of_hunk():
    """calculate() returns OUT_OF_HUNK when reported_line is outside all diff hunks."""
    # Arrange: _ADDED_DIFF covers new-file lines 1-4; line 999 is absent
    # Act
    result = calculate(_ADDED_DIFF, 999, "src/foo.py")
    # Assert
    assert result.status == PositionStatus.OUT_OF_HUNK
    assert result.start_line is None
    assert result.end_line is None


def test_calculate_multi_line_replacement():
    """calculate() sets end_line = start_line + replacement_line_count - 1."""
    # Arrange: new-file line 2 is '+new_line' in _ADDED_DIFF
    # Act
    result = calculate(_ADDED_DIFF, 2, "src/foo.py", replacement_line_count=3)
    # Assert
    assert result.status == PositionStatus.ANCHORED
    assert result.start_line == 2
    assert result.end_line == 4  # 2 + 3 - 1


def test_calculate_anchored_inferred_from_pure_addition_hunk():
    """calculate() infers ANCHORED from pure-addition hunk header when body is truncated
    (diff API returned only first two lines of a 5-line added block)."""
    # Arrange: @@ -0,0 +10,5 @@ — new lines 10-14; body only has lines 10-11
    pure_add_diff = "@@ -0,0 +10,5 @@\n+line10\n+line11\n"
    # Act: report line 13 (in range, not in truncated body)
    result = calculate(pure_add_diff, 13, "src/foo.py")
    # Assert
    assert result.status == PositionStatus.ANCHORED
    assert result.start_line == 13
    assert result.end_line == 13


def test_calculate_empty_diff():
    """calculate() returns OUT_OF_HUNK when given an empty diff string."""
    # Act
    result = calculate("", 1, "src/foo.py")
    # Assert
    assert result.status == PositionStatus.OUT_OF_HUNK
    assert result.start_line is None
    assert result.end_line is None


# ---------------------------------------------------------------------------
# TC5 — Offset hunk: absolute new-file line_number required (AC4)
# ---------------------------------------------------------------------------

# @@ -10,5 +50,5 @@ — old_start=10, new_start=50; large divergence
# new-file line numbers: context=50, added=51, context=52, context=53, context=54
_OFFSET_HUNK_DIFF = "@@ -10,5 +50,5 @@\n context_line_A\n+added_feature\n context_line_B\n context_line_C\n context_line_D\n"


def test_tc5_offset_hunk_absolute_line_anchors():
    """Absolute new-file line (51) anchors in offset hunk @@ -10,5 +50,5 @@ — AC4."""
    # Arrange: agent correctly reports absolute new-file line 51 (the '+' line)
    # Act
    result = calculate(_OFFSET_HUNK_DIFF, 51, "src/example/offset_hunk_stub.py")
    # Assert — absolute line anchors correctly
    assert result.status == PositionStatus.ANCHORED
    assert result.start_line == 51
    assert result.end_line == 51


def test_tc5_offset_hunk_relative_offset_misses():
    """Relative offset 2 does not anchor in @@ -10,5 +50,5 @@ — demonstrates the bug
    this gap fixes: naive agents counting from the diff top report out_of_hunk."""
    # Arrange: naive agent reports relative position 2 (second diff line = added)
    # Act
    result = calculate(_OFFSET_HUNK_DIFF, 2, "src/example/offset_hunk_stub.py")
    # Assert — relative offset fails to anchor (line 2 not in plus_new or context_new)
    assert result.status == PositionStatus.OUT_OF_HUNK


def test_tc5_resolve_offset_hunk_absolute_line_returns_platform_position():
    """resolve() returns ANCHORED PlatformPosition for absolute new-file line in offset hunk — AC4."""
    # Arrange
    adapter = GitHubPositionAdapter()
    # Act
    pp = adapter.resolve(51, _OFFSET_HUNK_DIFF, "src/example/offset_hunk_stub.py", None)
    # Assert
    assert pp["status"] == PositionStatus.ANCHORED
    assert pp["start_line"] == 51
    assert pp["end_line"] == 51


# ---------------------------------------------------------------------------
# AC6 — resolve() delegates to calculate() (single classification path)
# ---------------------------------------------------------------------------

def test_resolve_delegates_to_calculate_anchored():
    """resolve() outcome matches calculate() for an anchored line — AC6."""
    # Arrange
    adapter = GitHubPositionAdapter()
    # Act
    calc_result = calculate(_ADDED_DIFF, 2, "src/auth.py")
    resolve_result = adapter.resolve(2, _ADDED_DIFF, "src/auth.py", None)
    # Assert — both paths agree on anchor
    assert calc_result.status == PositionStatus.ANCHORED
    assert resolve_result["status"] == PositionStatus.ANCHORED
    assert resolve_result["start_line"] == calc_result.start_line
    assert resolve_result["end_line"] == calc_result.end_line


def test_resolve_delegates_to_calculate_unanchored():
    """resolve() status matches calculate() for a non-ANCHORED line — AC6."""
    # Arrange
    adapter = GitHubPositionAdapter()
    # Act — line 1 is context in _ADDED_DIFF → calculate() gives CONTEXT_LINE
    calc_result = calculate(_ADDED_DIFF, 1, "src/auth.py")
    resolve_result = adapter.resolve(1, _ADDED_DIFF, "src/auth.py", None)
    # Assert — both agree: not anchored
    assert calc_result.status == PositionStatus.CONTEXT_LINE
    assert resolve_result["status"] == PositionStatus.CONTEXT_LINE


# ---------------------------------------------------------------------------
# TC1-TC5 — resolve() changed-line rule
# ---------------------------------------------------------------------------

def test_tc1_changed_line_returns_platform_position():
    """A '+' line in the diff produces a PlatformPosition with ANCHORED status."""
    adapter = GitHubPositionAdapter()
    result = adapter.resolve(2, _ADDED_DIFF, "file.py", None)
    assert result["status"] == PositionStatus.ANCHORED
    assert result["file_path"] == "file.py"
    assert result["start_line"] == 2
    assert result["end_line"] == 2


def test_tc2_context_line_returns_unanchored_position():
    """A context (' ') line produces CONTEXT_LINE status — not anchorable."""
    adapter = GitHubPositionAdapter()
    result = adapter.resolve(1, _ADDED_DIFF, "file.py", None)  # line 1 is context
    assert result["status"] == PositionStatus.CONTEXT_LINE
    assert result["start_line"] is None
    assert result["end_line"] is None


def test_tc3_removed_line_returns_unanchored_position():
    """A removed ('-') line produces REMOVED_LINE status."""
    # Arrange: pure deletion — old lines 1-2 removed, no new lines added
    pure_delete_diff = "@@ -1,2 +1,0 @@\n-removed_first\n-removed_second\n"
    adapter = GitHubPositionAdapter()
    result = adapter.resolve(1, pure_delete_diff, "file.py", None)
    assert result["status"] == PositionStatus.REMOVED_LINE
    assert result["start_line"] is None


def test_tc4_line_not_in_diff_at_all_returns_out_of_hunk():
    """A line outside all diff hunks produces OUT_OF_HUNK status."""
    adapter = GitHubPositionAdapter()
    result = adapter.resolve(999, _ADDED_DIFF, "file.py", None)
    assert result["status"] == PositionStatus.OUT_OF_HUNK
    assert result["start_line"] is None


def test_tc5_multi_line_end_equals_start_plus_rlc_minus_one():
    """Multi-line anchor: end_line == start_line + replacement_line_count - 1."""
    adapter = GitHubPositionAdapter()
    result = adapter.resolve(2, _ADDED_DIFF, "file.py", None, replacement_line_count=3)
    assert result["status"] == PositionStatus.ANCHORED
    assert result["start_line"] == 2
    assert result["end_line"] == 4  # 2 + 3 - 1


# ---------------------------------------------------------------------------
# TC6-TC7 — GitHub to_api_params
# ---------------------------------------------------------------------------

def _anchored_pp(file_path: str, start_line: int, end_line: int) -> PlatformPosition:
    """Build a minimal ANCHORED PlatformPosition for to_api_params() tests."""
    return PlatformPosition(
        file_path=file_path,
        status=PositionStatus.ANCHORED,
        reason="test fixture",
        start_line=start_line,
        end_line=end_line,
    )


def test_tc6_github_single_line_no_start_fields():
    """Single-line comment: only path + side + line; no start_line or start_side."""
    adapter = GitHubPositionAdapter()
    params = adapter.to_api_params(_anchored_pp("a.py", 10, 10))
    assert params == {"path": "a.py", "side": "RIGHT", "line": 10}
    assert "start_line" not in params
    assert "start_side" not in params


def test_tc7_github_multi_line_includes_start_fields():
    """Multi-line comment: includes start_line and start_side."""
    adapter = GitHubPositionAdapter()
    params = adapter.to_api_params(_anchored_pp("a.py", 10, 12))
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
    params = adapter.to_api_params(_anchored_pp("b.py", 5, 7))
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
# TC9, TC10 — Bitbucket to_api_params (REVUE-238)
# ---------------------------------------------------------------------------

def test_tc9_bitbucket_single_line_no_from_field():
    """Single-line comment: inline.path + inline.to only; no inline.from."""
    adapter = BitbucketPositionAdapter()
    params = adapter.to_api_params(_anchored_pp("a.py", 10, 10))
    assert params == {"inline": {"path": "a.py", "to": 10}}
    assert "from" not in params["inline"]


def test_tc10_bitbucket_multi_line_includes_from_field():
    """Multi-line comment: inline.path + inline.to + inline.from."""
    adapter = BitbucketPositionAdapter()
    params = adapter.to_api_params(_anchored_pp("a.py", 10, 12))
    assert params == {"inline": {"path": "a.py", "to": 12, "from": 10}}


# ---------------------------------------------------------------------------
# Centralised preconditions — all adapters must reject malformed PlatformPosition
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("adapter_factory", [
    lambda: GitHubPositionAdapter(),
    lambda: GitLabPositionAdapter(base_sha="b", head_sha="h", start_sha="s"),
    lambda: BitbucketPositionAdapter(),
], ids=["github", "gitlab", "bitbucket"])
def test_to_api_params_rejects_end_line_less_than_start_line(adapter_factory):
    """All adapters must reject PlatformPosition where end_line < start_line."""
    adapter = adapter_factory()
    bad_position = PlatformPosition(
        file_path="a.py",
        status=PositionStatus.ANCHORED,
        reason="test",
        start_line=10,
        end_line=5,  # invalid: end before start
    )
    with pytest.raises(AssertionError, match="end_line"):
        adapter.to_api_params(bad_position)


@pytest.mark.parametrize("adapter_factory", [
    lambda: GitHubPositionAdapter(),
    lambda: GitLabPositionAdapter(base_sha="b", head_sha="h", start_sha="s"),
    lambda: BitbucketPositionAdapter(),
], ids=["github", "gitlab", "bitbucket"])
def test_to_api_params_rejects_none_lines(adapter_factory):
    """All adapters must reject PlatformPosition where start_line or end_line is None."""
    adapter = adapter_factory()
    bad_position = PlatformPosition(
        file_path="a.py",
        status=PositionStatus.ANCHORED,
        reason="test",
        start_line=None,
        end_line=None,
    )
    with pytest.raises(AssertionError):
        adapter.to_api_params(bad_position)


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
        assert pp["status"] != PositionStatus.ANCHORED, (
            f"Expected unanchored fixture {fixture_path.name} but got ANCHORED"
        )
        return

    assert pp["status"] == PositionStatus.ANCHORED, (
        f"Expected ANCHORED for {fixture_path.name}, got {pp['status']}"
    )
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
        assert pp["status"] != PositionStatus.ANCHORED, (
            f"Expected unanchored fixture {fixture_path.name} but got ANCHORED"
        )
        return

    assert pp["status"] == PositionStatus.ANCHORED, (
        f"Expected ANCHORED for {fixture_path.name}, got {pp['status']}"
    )
    assert pp["start_line"] == exp_pos["start_line"]
    assert pp["end_line"] == exp_pos["end_line"]

    exp_params = f.get("expected_api_params")
    if exp_params:
        assert adapter.to_api_params(pp) == exp_params


# ---------------------------------------------------------------------------
# TC13 — Bitbucket fixture-driven tests (REVUE-238)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fixture_path", _load_fixtures("bitbucket"), ids=lambda p: p.name)
def test_tc13_bitbucket_fixture(fixture_path: Path) -> None:
    f = json.loads(fixture_path.read_text())
    adapter = BitbucketPositionAdapter()
    pp = adapter.resolve(
        f["reported_line"],
        f["diff_snippet"],
        f["file_path"],
        None,
        f.get("replacement_line_count", 1),
    )

    exp_pos = f.get("expected_position")
    if exp_pos is None:
        assert pp["status"] != PositionStatus.ANCHORED, (
            f"Expected unanchored fixture {fixture_path.name} but got ANCHORED"
        )
        return

    assert pp["status"] == PositionStatus.ANCHORED, (
        f"Expected ANCHORED for {fixture_path.name}, got {pp['status']}"
    )
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


def test_tc15_registry_bitbucket_returns_bitbucket_adapter():
    adapter = get_position_adapter("bitbucket", _make_ctx("bitbucket"))
    assert isinstance(adapter, BitbucketPositionAdapter)


def test_tc15_registry_unknown_platform_raises():
    with pytest.raises((KeyError, ValueError)):
        get_position_adapter("unknown", _make_ctx("unknown"))


def test_stateless_factory_returns_correct_adapter_type():
    """The _stateless_factory helper wraps a stateless adapter class as a factory."""
    from revue.comments.position_adapter import _stateless_factory

    factory = _stateless_factory(GitHubPositionAdapter)
    adapter = factory(_make_ctx("github"), None)
    assert isinstance(adapter, GitHubPositionAdapter)

    factory = _stateless_factory(BitbucketPositionAdapter)
    adapter = factory(_make_ctx("bitbucket"), None)
    assert isinstance(adapter, BitbucketPositionAdapter)


# ---------------------------------------------------------------------------
# AC5 — PlatformPosition carries status and reason (Gap 3)
# ---------------------------------------------------------------------------

def test_ac5_resolve_always_returns_platform_position_never_none():
    """resolve() never returns None — status replaces the None sentinel — AC5."""
    adapter = GitHubPositionAdapter()
    for line in [1, 2, 999]:  # context, anchored, out_of_hunk
        result = adapter.resolve(line, _ADDED_DIFF, "src/auth.py", None)
        assert result is not None, f"resolve() returned None for line {line}"
        assert isinstance(result, dict)


def test_ac5_resolve_anchored_carries_status_and_reason():
    """ANCHORED PlatformPosition carries status=ANCHORED and a non-empty reason — AC5."""
    # Arrange: line 2 is a '+' line in _ADDED_DIFF
    adapter = GitHubPositionAdapter()
    # Act
    pp = adapter.resolve(2, _ADDED_DIFF, "src/auth.py", None)
    # Assert
    assert pp["status"] == PositionStatus.ANCHORED
    assert pp["reason"]
    assert pp["start_line"] == 2
    assert pp["end_line"] == 2


def test_ac5_resolve_context_line_carries_context_status():
    """Context line produces status=CONTEXT_LINE with no anchor — AC5."""
    # Arrange: line 1 is context in _ADDED_DIFF
    adapter = GitHubPositionAdapter()
    # Act
    pp = adapter.resolve(1, _ADDED_DIFF, "src/auth.py", None)
    # Assert
    assert pp["status"] == PositionStatus.CONTEXT_LINE
    assert pp["start_line"] is None
    assert pp["end_line"] is None
    assert pp["reason"]


def test_ac5_resolve_removed_line_carries_removed_status():
    """Removed line produces status=REMOVED_LINE — Nova can flag it as stale — AC5."""
    # Arrange: pure-deletion diff — old lines 1-2 removed, no new lines
    pure_delete_diff = "@@ -1,2 +1,0 @@\n-removed_first\n-removed_second\n"
    adapter = GitHubPositionAdapter()
    # Act
    pp = adapter.resolve(1, pure_delete_diff, "src/auth.py", None)
    # Assert
    assert pp["status"] == PositionStatus.REMOVED_LINE
    assert pp["start_line"] is None
    assert pp["end_line"] is None


def test_ac5_resolve_out_of_hunk_carries_out_of_hunk_status():
    """Line outside all hunks produces status=OUT_OF_HUNK — AC5."""
    # Arrange: _ADDED_DIFF covers lines 1-4; line 999 is absent
    adapter = GitHubPositionAdapter()
    # Act
    pp = adapter.resolve(999, _ADDED_DIFF, "src/auth.py", None)
    # Assert
    assert pp["status"] == PositionStatus.OUT_OF_HUNK
    assert pp["start_line"] is None
    assert pp["end_line"] is None


def test_ac5_resolve_empty_diff_returns_out_of_hunk():
    """Empty diff produces OUT_OF_HUNK (not None) — AC5."""
    adapter = GitHubPositionAdapter()
    pp = adapter.resolve(1, "", "src/auth.py", None)
    assert pp["status"] == PositionStatus.OUT_OF_HUNK
    assert pp["start_line"] is None
    assert pp["end_line"] is None
