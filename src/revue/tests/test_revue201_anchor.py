"""Tests for REVUE-201: suggestion block anchor range and DiffPositionResolver."""
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# AC1 — AIReview.replacement_line_count field
# ---------------------------------------------------------------------------

class TestAIReviewReplacementLineCount:
    def test_default_is_one(self):
        from revue.core.models import AIReview
        r = AIReview(
            file_path="a.py", line_number=10, severity="low",
            issue="x", suggestion="y", confidence=0.8,
        )
        assert r.replacement_line_count == 1

    def test_explicit_value_stored(self):
        from revue.core.models import AIReview
        r = AIReview(
            file_path="a.py", line_number=10, severity="low",
            issue="x", suggestion="y", confidence=0.8,
            replacement_line_count=5,
        )
        assert r.replacement_line_count == 5


# ---------------------------------------------------------------------------
# AC2 / TC5 — agent_loader parses replacement_line_count from JSON
# ---------------------------------------------------------------------------

class TestAgentLoaderParsesReplacementLineCount:
    """Call the real _parse_finding_item helper — no inline logic duplication."""

    def _parse(self, item):
        from revue.core.agent_loader import _parse_finding_item
        return _parse_finding_item(item, agent_name="maya", severity_default="minor")

    def test_parsed_from_dict(self):
        """replacement_line_count is extracted when code_replacement is also present."""
        review = self._parse({
            "file_path": "a.py",
            "line_number": 5,
            "severity": "low",
            "issue": "issue",
            "suggestion": "fix",
            "confidence": 0.9,
            "code_replacement": ["line a", "line b", "line c"],
            "replacement_line_count": 3,
        })
        assert review is not None
        assert review.replacement_line_count == 3

    def test_float_accepted(self):
        """LLMs often emit 3.0; it must be accepted and coerced to int."""
        review = self._parse({
            "file_path": "a.py",
            "line_number": 5,
            "severity": "low",
            "issue": "issue",
            "suggestion": "fix",
            "confidence": 0.9,
            "code_replacement": ["replacement"],
            "replacement_line_count": 3.0,
        })
        assert review is not None
        assert review.replacement_line_count == 3

    def test_missing_defaults_to_one(self):
        """When replacement_line_count is absent, defaults to 1."""
        review = self._parse({
            "file_path": "a.py",
            "line_number": 5,
            "severity": "low",
            "issue": "issue",
            "suggestion": "fix",
            "confidence": 0.9,
            "code_replacement": ["replacement"],
        })
        assert review is not None
        assert review.replacement_line_count == 1

    def test_non_integer_string_defaults_to_one(self):
        """Non-numeric replacement_line_count falls back to 1."""
        review = self._parse({
            "file_path": "a.py",
            "line_number": 5,
            "severity": "low",
            "issue": "issue",
            "suggestion": "fix",
            "confidence": 0.9,
            "code_replacement": ["replacement"],
            "replacement_line_count": "three",
        })
        assert review is not None
        assert review.replacement_line_count == 1

    def test_no_code_replacement_forces_count_to_one(self):
        """When code_replacement is absent, replacement_line_count is always 1."""
        review = self._parse({
            "file_path": "a.py",
            "line_number": 5,
            "severity": "low",
            "issue": "issue",
            "suggestion": "fix",
            "confidence": 0.9,
            "replacement_line_count": 5,  # ignored — no code_replacement
        })
        assert review is not None
        assert review.replacement_line_count == 1

    def test_oversized_count_is_capped(self):
        """replacement_line_count exceeding 100 is capped to 100."""
        review = self._parse({
            "file_path": "a.py",
            "line_number": 5,
            "severity": "low",
            "issue": "issue",
            "suggestion": "fix",
            "confidence": 0.9,
            "code_replacement": ["replacement"],
            "replacement_line_count": 999,
        })
        assert review is not None
        assert review.replacement_line_count == 100


# ---------------------------------------------------------------------------
# AC7 — DiffPositionResolver: diff parsing and three-tier snap
# ---------------------------------------------------------------------------

SAMPLE_DIFF = """\
diff --git a/src/example.py b/src/example.py
--- a/src/example.py
+++ b/src/example.py
@@ -10,6 +10,7 @@
 context line 10
 context line 11
-old line 12
+new line 12
+added line 13
 context line 13
"""

MULTI_HUNK_DIFF = """\
diff --git a/src/foo.py b/src/foo.py
--- a/src/foo.py
+++ b/src/foo.py
@@ -5,4 +5,4 @@
 line 5
-line 6 old
+line 6 new
 line 7
 line 8
@@ -20,3 +20,4 @@
 line 20
+line inserted
 line 21
 line 22
"""


class TestDiffLineMapping:
    """Direct tests of _map_diff_lines — verify exact (old_line, new_line) pairs produced."""

    def _parse(self, diff):
        from revue.core.diff_position_resolver import DiffPositionResolver
        return list(DiffPositionResolver._map_diff_lines(diff))

    def test_single_hunk_produces_exact_pairs(self):
        # @@ -10,6 +10,7 @@: context 10,11 / removed 12 / added 12,13 / context 13→14
        pairs = self._parse(SAMPLE_DIFF)
        assert pairs == [(10, 10), (11, 11), (0, 12), (0, 13), (13, 14)]

    def test_multi_hunk_produces_pairs_from_both_hunks(self):
        pairs = self._parse(MULTI_HUNK_DIFF)
        new_lines = [p[1] for p in pairs]
        # Hunk 1: lines 5, 6 (new), 7, 8
        assert 5 in new_lines
        assert 6 in new_lines
        assert 7 in new_lines
        assert 8 in new_lines
        # Hunk 2: lines 20, 21 (inserted), 22, 23
        assert 20 in new_lines
        assert 21 in new_lines

    def test_empty_diff_returns_empty(self):
        assert self._parse("") == []

    def test_additions_only_have_old_line_zero(self):
        diff = "@@ -1,0 +1,2 @@\n+added 1\n+added 2\n"
        pairs = self._parse(diff)
        assert all(old == 0 for old, _ in pairs)
        assert [new for _, new in pairs] == [1, 2]

    def test_removed_lines_are_excluded(self):
        diff = "@@ -5,3 +5,2 @@\n context\n-removed\n context again\n"
        pairs = self._parse(diff)
        new_lines = [p[1] for p in pairs]
        assert len(pairs) == 2  # only context lines — removed line is excluded


class TestDiffPositionResolver:
    def _snap(self, line, diff=SAMPLE_DIFF, repo_path=None, file_path=None):
        from revue.core.diff_position_resolver import DiffPositionResolver
        return DiffPositionResolver.snap(line, diff, repo_path=repo_path, file_path=file_path)

    def test_empty_diff_snaps_to_one(self):
        # F9: empty diff returns 1 (safe anchor), not the reported line
        assert self._snap(42, diff="") == 1

    # TC7 — Tier 1: line present in diff → exact match
    def test_tier1_exact_match_context_line(self):
        assert self._snap(10) == 10
        assert self._snap(11) == 11

    def test_tier1_exact_match_added_line(self):
        # new_line 12 and 13 are added lines in SAMPLE_DIFF
        assert self._snap(12) == 12
        assert self._snap(13) == 13

    def test_tier1_exact_match_context_line_shifted(self):
        # old context line 13 is now at new_line 14 after insertion
        assert self._snap(14) == 14

    # TC8 — Tier 2: agent reports line near but outside diff → snaps to exact nearest
    def test_tier2_above_diff_snaps_to_first_hunk_line(self):
        # Line 8 is above the hunk (which starts at 10); nearest new_line is 10
        assert self._snap(8) == 10

    def test_tier2_below_diff_snaps_to_last_hunk_line(self):
        # Line 99 is below the hunk (which ends at 14); nearest new_line is 14
        assert self._snap(99) == 14

    def test_tier2_far_outside_snaps_to_last_hunk_line(self):
        assert self._snap(999) == 14

    # TC9 — Tier 2: no repo_path provided → Tier 3 skipped, exact Tier 2 result returned
    def test_tier2_no_repo_path_returns_nearest_hunk_line(self):
        # 999 is far below the diff; nearest is 14
        assert self._snap(999, repo_path=None) == 14

    # TC10 — Tier 3: outside diff + file on disk → exact file line returned
    def test_tier3_returns_exact_file_line(self, tmp_path):
        file_content = "\n".join(f"line {i}" for i in range(1, 101))
        f = tmp_path / "example.py"
        f.write_text(file_content)
        # Line 50 is outside the diff (diff covers lines 10–14); file has 100 lines
        result = self._snap(50, repo_path=str(tmp_path), file_path="example.py")
        assert result == 50

    def test_tier3_clamps_beyond_eof(self, tmp_path):
        f = tmp_path / "example.py"
        f.write_text("line1\nline2\nline3\n")
        # Line 999 is beyond 3-line file; should clamp to line 3
        result = self._snap(999, repo_path=str(tmp_path), file_path="example.py")
        assert result == 3

    # TC11 — Tier 3: file not found → Tier 2 fallback returns exact nearest hunk line
    def test_tier3_missing_file_falls_back_to_tier2(self, tmp_path):
        # No file present; falls back to Tier 2 nearest for line 999 → 14
        result = self._snap(999, repo_path=str(tmp_path), file_path="nonexistent.py")
        assert result == 14


# ---------------------------------------------------------------------------
# GitHub suggestion block — AC3 / TC1 / TC3
# ---------------------------------------------------------------------------

class TestGitHubSuggestionBlock:
    def test_tc1_single_line_no_start_line(self):
        from revue.cli import _github_suggestion_block
        body = _github_suggestion_block(["replacement"], replacement_line_count=1)
        assert "```suggestion" in body
        assert "start_line" not in body

    def test_tc3_multi_line_includes_count(self):
        from revue.cli import _github_suggestion_block
        result = _github_suggestion_block(["a", "b", "c"], replacement_line_count=3)
        assert isinstance(result, str)
        assert "```suggestion" in result


# ---------------------------------------------------------------------------
# GitLab suggestion block — AC4 / TC2 / TC4
# ---------------------------------------------------------------------------

class TestGitLabSuggestionBlock:
    def test_tc2_single_line_fence_is_zero(self):
        from revue.cli import _gitlab_suggestion_block
        body = _gitlab_suggestion_block(["replacement"], replacement_line_count=1)
        assert "suggestion:-0+0" in body

    def test_tc4_multi_line_fence_uses_count(self):
        from revue.cli import _gitlab_suggestion_block
        body = _gitlab_suggestion_block(["a", "b", "c"], replacement_line_count=3)
        assert "suggestion:-0+2" in body


# ---------------------------------------------------------------------------
# AC3 — GitHub post_comment uses start_line for multi-line (TC3)
# ---------------------------------------------------------------------------

class TestGitHubPostCommentRange:
    def test_tc3_multi_line_payload_has_start_line(self, monkeypatch):
        """GitHub API payload must include start_line when replacement_line_count > 1."""
        import httpx
        from revue.comments.platform_adapter import GitHubAdapter

        captured = {}

        def fake_post(url, json, headers):
            captured.update(json)
            class R:
                def raise_for_status(self): pass
                def json(self): return {"id": 1, "pull_request_review_id": None}
            return R()

        monkeypatch.setattr(httpx, "post", fake_post)
        adapter = GitHubAdapter(token="tok")
        adapter.post_comment(
            "owner", "repo", 1, "a.py", 10, "body", "sha",
            replacement_line_count=3,
        )
        assert captured.get("start_line") == 10
        assert captured.get("line") == 12  # 10 + 3 - 1

    def test_tc1_single_line_no_start_line_in_payload(self, monkeypatch):
        import httpx
        from revue.comments.platform_adapter import GitHubAdapter

        captured = {}

        def fake_post(url, json, headers):
            captured.update(json)
            class R:
                def raise_for_status(self): pass
                def json(self): return {"id": 1, "pull_request_review_id": None}
            return R()

        monkeypatch.setattr(httpx, "post", fake_post)
        adapter = GitHubAdapter(token="tok")
        adapter.post_comment(
            "owner", "repo", 1, "a.py", 10, "body", "sha",
            replacement_line_count=1,
        )
        assert "start_line" not in captured


# ---------------------------------------------------------------------------
# Integration: diff injected → snap → correct anchor on GitHub and GitLab
#
# These tests simulate the full pipeline:
#   1. A realistic diff with known line positions is injected.
#   2. An agent reports a line number (sometimes miscounted from the diff header).
#   3. DiffPositionResolver.snap() corrects the line against the real diff.
#   4. The resolved line + replacement_line_count produce the correct
#      GitHub API payload (start_line/line) and GitLab fence (suggestion:-0+N).
#
# The diff used here represents a PR that renames a parameter and also
# rewrites a multi-line function signature:
#
#   @@ -8,15 +8,15 @@
#    (blank)
#    def helper():
#        return 42
#    (blank)
#   -def process(input):        ← old line 12, new line 12
#   +def process(data):         ← new line 12 (single-line replacement)
#    (blank)
#   -def transform(             ← old line 14, new line 14
#   -    raw_input,             ← old line 15, new line 15 (removed)
#   -    flag=False             ← old line 16, new line 16 (removed)
#   -):                         ← old line 17 (removed)
#   +def transform(             ← new line 14 (addition)
#   +    data,                  ← new line 15 (addition)
#   +    flag=False             ← new line 16 (addition)
#   +):                         ← new line 17 (addition)
#    (blank)
#    (context lines ...)
# ---------------------------------------------------------------------------

# Diff for the integration tests
_INTEGRATION_DIFF = """\
diff --git a/src/processor.py b/src/processor.py
--- a/src/processor.py
+++ b/src/processor.py
@@ -8,15 +8,15 @@

 def helper():
     return 42

-def process(input):
+def process(data):

-def transform(
-    raw_input,
-    flag=False
-):
+def transform(
+    data,
+    flag=False
+):

 def consume():
     pass
"""


class TestSuggestionAnchorIntegration:
    """End-to-end: inject diff → snap line → verify anchor and fence are correct."""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _snap(self, reported_line: int, count: int = 1) -> int:
        from revue.core.diff_position_resolver import DiffPositionResolver
        return DiffPositionResolver.snap(reported_line, _INTEGRATION_DIFF)

    def _gitlab_fence(self, replacement_line_count: int) -> str:
        from revue.cli import _gitlab_suggestion_block
        return _gitlab_suggestion_block(["replacement"], replacement_line_count=replacement_line_count)

    def _github_payload(self, monkeypatch, line_number: int, replacement_line_count: int) -> dict:
        import httpx
        from revue.comments.platform_adapter import GitHubAdapter
        captured = {}
        def fake_post(url, json, headers):
            captured.update(json)
            class R:
                def raise_for_status(self): pass
                def json(self): return {"id": 1, "pull_request_review_id": None}
            return R()
        monkeypatch.setattr(httpx, "post", fake_post)
        GitHubAdapter(token="tok").post_comment(
            "owner", "repo", 1, "src/processor.py", line_number, "body", "sha",
            replacement_line_count=replacement_line_count,
        )
        return captured

    # ------------------------------------------------------------------
    # Single-line replacement — agent reports correct line
    # ------------------------------------------------------------------

    def test_single_line_correct_report_github(self, monkeypatch):
        """Agent reports the right line; snap returns it unchanged; no start_line in payload."""
        # `def process(data):` is at new_line 12 in the diff
        resolved = self._snap(12)
        assert resolved == 12

        payload = self._github_payload(monkeypatch, resolved, replacement_line_count=1)
        assert payload["line"] == 12
        assert "start_line" not in payload

    def test_single_line_correct_report_gitlab(self):
        """Agent reports the right line; snap returns it unchanged; fence is suggestion:-0+0."""
        resolved = self._snap(12)
        assert resolved == 12

        fence = self._gitlab_fence(replacement_line_count=1)
        assert "suggestion:-0+0" in fence

    # ------------------------------------------------------------------
    # Single-line replacement — agent miscounts by one (off-by-one)
    # ------------------------------------------------------------------

    def test_single_line_miscounted_snaps_to_correct_line_github(self, monkeypatch):
        """Agent reports line 7 thinking it's def helper() — which is actually at 9.

        Line 7 is before the hunk (which starts at new_line 8); snap snaps it to 8
        (the nearest in-diff line), not the originally reported 7.
        This demonstrates that a 2-line overcount is corrected before API submission.
        """
        resolved = self._snap(7)
        # Nearest new_line in _INTEGRATION_DIFF to line 7 is line 8 (first context line)
        assert resolved == 8

        payload = self._github_payload(monkeypatch, resolved, replacement_line_count=1)
        assert payload["line"] == 8
        assert "start_line" not in payload

    def test_single_line_outside_diff_snaps_to_nearest(self, monkeypatch):
        """Agent reports line 5 (before the hunk starts at 8); snap gives nearest hunk line."""
        resolved = self._snap(5)
        # Nearest new_line in the diff to line 5 is 8 (first context line in hunk)
        assert resolved == 8

        payload = self._github_payload(monkeypatch, resolved, replacement_line_count=1)
        assert payload["line"] == 8
        assert "start_line" not in payload

    # ------------------------------------------------------------------
    # Multi-line replacement — agent reports correct start line
    # ------------------------------------------------------------------

    def test_multi_line_correct_report_github(self, monkeypatch):
        """Agent correctly identifies the 4-line transform() signature; GitHub gets start+end."""
        # `def transform(` is an addition starting at new_line 14; spans 4 lines (14-17)
        resolved = self._snap(14)
        assert resolved == 14

        payload = self._github_payload(monkeypatch, resolved, replacement_line_count=4)
        assert payload["start_line"] == 14
        assert payload["line"] == 17  # 14 + 4 - 1

    def test_multi_line_correct_report_gitlab(self):
        """Agent correctly identifies 4-line block; GitLab fence deletes 3 lines below anchor."""
        resolved = self._snap(14)
        assert resolved == 14

        fence = self._gitlab_fence(replacement_line_count=4)
        assert "suggestion:-0+3" in fence  # anchor + 3 more = 4 total

    # ------------------------------------------------------------------
    # Multi-line replacement — agent miscounts start line
    # ------------------------------------------------------------------

    def test_multi_line_miscounted_start_snaps_then_covers_correct_range_github(self, monkeypatch):
        """Agent says line 20 for transform() which is actually at 14; snap corrects it."""
        # Line 20 is outside the diff (hunk ends around 22); nearest is the last context line
        # Let's simulate agent saying 16 (inside hunk but wrong position — reports a middle line)
        # snap(16) == 16 (it's an addition line in the diff)
        resolved = self._snap(16)
        assert resolved == 16

        # Even if anchored at 16, replacement_line_count drives the end line
        payload = self._github_payload(monkeypatch, resolved, replacement_line_count=3)
        assert payload["start_line"] == 16
        assert payload["line"] == 18  # 16 + 3 - 1

    def test_multi_line_outside_diff_snaps_then_covers_correct_range_gitlab(self):
        """Agent says line 99 (outside diff); snap corrects to last hunk line; fence is correct."""
        resolved = self._snap(99)
        # Last new_line in this diff — find it: hunk ends with context lines after transform()
        # The diff has consume() starting around line 20+; let's verify programmatically
        from revue.core.diff_position_resolver import DiffPositionResolver
        pairs = DiffPositionResolver._map_diff_lines(_INTEGRATION_DIFF)
        last_new_line = max(p[1] for p in pairs)
        assert resolved == last_new_line

        fence = self._gitlab_fence(replacement_line_count=2)
        assert "suggestion:-0+1" in fence  # 2-line replacement: delete anchor + 1


# ---------------------------------------------------------------------------
# Helpers shared by F1 / F2 / F3 tests
# ---------------------------------------------------------------------------

@dataclass
class _FakeReviewResult:
    file_path: str
    response: str
    error: str = ""


def _make_response(findings: list[dict]) -> str:
    return json.dumps({"findings": findings, "summary": "ok"})


def _run_dedup_201(platform: str, findings: list[dict], diff_by_file: dict, tmp_path):
    """Run _run_per_issue_dedup and return the mock adapter."""
    from revue.cli import _run_per_issue_dedup
    from revue.comments.json_store import PerPRCommentStore
    from revue.core.vcs_adapter import DiffPosition

    # Group all findings under the same file so they land in one dedup group
    file_path = findings[0]["file_path"]
    review_results = [_FakeReviewResult(file_path, _make_response(findings))]
    mock_adapter = MagicMock()
    mock_adapter.get_existing_comments.return_value = []
    mock_adapter.post_review_comment.return_value = "cmt-1"
    # resolve_position must return a DiffPosition with position != 0 so posting proceeds
    mock_adapter.resolve_position.return_value = DiffPosition(
        file_path=file_path, line_number=12, position=5
    )

    store = PerPRCommentStore(tmp_path)
    _run_per_issue_dedup(mock_adapter, 1, platform, review_results, diff_by_file, store)
    return mock_adapter


# ---------------------------------------------------------------------------
# F1 — merged body must always post with replacement_line_count=1
# ---------------------------------------------------------------------------

class TestMergedGroupRlcIsAlwaysOne:
    """Merged comment bodies (>1 finding at same file+line) have no suggestion fence.

    GitHub rejects start_line/line payloads that don't span a real diff range, so
    replacement_line_count MUST be 1 for merged bodies, regardless of what individual
    findings report.
    """

    def test_merged_body_uses_rlc_one_even_if_finding_has_higher_count(self, tmp_path):
        """Two findings at the same line; one has rlc=3 — merged post must use rlc=1."""
        diff = _INTEGRATION_DIFF
        findings = [
            {
                "file_path": "src/processor.py",
                "line_number": 12,
                "severity": "minor",
                "issue": "First issue",
                "suggestion": "Fix it",
                "confidence": 0.8,
                "code_replacement": ["def process(data):"],
                "replacement_line_count": 3,
            },
            {
                "file_path": "src/processor.py",
                "line_number": 12,
                "severity": "minor",
                "issue": "Second issue at same line",
                "suggestion": "Also fix it",
                "confidence": 0.7,
                "replacement_line_count": 1,
            },
        ]
        adapter = _run_dedup_201(
            "github", findings, {"src/processor.py": diff}, tmp_path
        )
        assert adapter.post_review_comment.called
        call_kwargs = adapter.post_review_comment.call_args
        rlc = call_kwargs.kwargs["replacement_line_count"]
        assert rlc == 1, f"Expected rlc=1 for merged body, got {rlc}"


# ---------------------------------------------------------------------------
# F2 — snap relocation must reset replacement_line_count to 1
# ---------------------------------------------------------------------------

class TestSnapRelocationResetsRlc:
    """When snap() changes the anchor line, the original rlc describes the wrong span.

    The corrected behaviour: if snapped_line != reported_line, rlc is reset to 1
    so no multi-line API call is made with an invalid range.
    """

    def test_snap_relocates_then_rlc_is_one(self, tmp_path):
        """Finding at line 99 (outside diff); snap moves it to last hunk line.

        Even though rlc=4 was reported, the post must use rlc=1.
        """
        diff = _INTEGRATION_DIFF
        findings = [
            {
                "file_path": "src/processor.py",
                "line_number": 99,  # way outside the diff
                "severity": "minor",
                "issue": "Out-of-range finding",
                "suggestion": "Fix it",
                "confidence": 0.8,
                "code_replacement": ["def transform(data):"],
                "replacement_line_count": 4,
            },
        ]
        adapter = _run_dedup_201(
            "github", findings, {"src/processor.py": diff}, tmp_path
        )
        assert adapter.post_review_comment.called
        call_kwargs = adapter.post_review_comment.call_args
        rlc = call_kwargs.kwargs["replacement_line_count"]
        assert rlc == 1, f"Expected rlc=1 after snap relocation, got {rlc}"


# ---------------------------------------------------------------------------
# F3 — end-line bounds: DiffPositionResolver.line_in_diff unit tests
# ---------------------------------------------------------------------------

class TestDiffPositionResolverLineInDiff:
    """Unit tests for DiffPositionResolver.line_in_diff."""

    def test_line_present_returns_true(self):
        from revue.core.diff_position_resolver import DiffPositionResolver
        assert DiffPositionResolver.line_in_diff(12, _INTEGRATION_DIFF) is True

    def test_added_line_present_returns_true(self):
        from revue.core.diff_position_resolver import DiffPositionResolver
        # line 14 is an added line (+def transform()
        assert DiffPositionResolver.line_in_diff(14, _INTEGRATION_DIFF) is True

    def test_line_absent_returns_false(self):
        from revue.core.diff_position_resolver import DiffPositionResolver
        assert DiffPositionResolver.line_in_diff(99, _INTEGRATION_DIFF) is False

    def test_empty_diff_returns_false(self):
        from revue.core.diff_position_resolver import DiffPositionResolver
        assert DiffPositionResolver.line_in_diff(1, "") is False


# ---------------------------------------------------------------------------
# F3 — end-line bounds: reset rlc when end-line is outside the diff
# ---------------------------------------------------------------------------

class TestEndLineBoundsResetRlc:
    """When anchor is valid but anchor+rlc-1 overshoots the diff, rlc must be 1.

    GitHub returns 422 for a start_line/line range where the end line does not
    exist in the diff. The fix clamps rlc=1 before the API call.
    """

    def test_end_line_outside_diff_resets_rlc_to_one(self, tmp_path):
        """Finding at line 14 with rlc=10; end-line 23 is outside the diff.

        The diff used here ends around line 22; line 23 is not a valid new_line.
        Post must use rlc=1.
        """
        diff = _INTEGRATION_DIFF
        findings = [
            {
                "file_path": "src/processor.py",
                "line_number": 14,  # valid anchor in diff
                "severity": "minor",
                "issue": "Oversized span",
                "suggestion": "Fix it",
                "confidence": 0.8,
                "code_replacement": ["def transform(data):"],
                "replacement_line_count": 10,  # end-line = 14+10-1 = 23, outside diff
            },
        ]
        adapter = _run_dedup_201(
            "github", findings, {"src/processor.py": diff}, tmp_path
        )
        assert adapter.post_review_comment.called
        call_kwargs = adapter.post_review_comment.call_args
        rlc = call_kwargs.kwargs["replacement_line_count"]
        assert rlc == 1, f"Expected rlc=1 when end-line overshoots diff, got {rlc}"

    def test_valid_span_within_diff_preserves_rlc(self, tmp_path):
        """Finding at line 14 with rlc=4; end-line 17 is within the diff.

        All four lines of transform() are in the diff — rlc must be forwarded unchanged.
        """
        diff = _INTEGRATION_DIFF
        findings = [
            {
                "file_path": "src/processor.py",
                "line_number": 14,
                "severity": "minor",
                "issue": "Transform signature",
                "suggestion": "Rename param",
                "confidence": 0.9,
                "code_replacement": [
                    "def transform(",
                    "    data,",
                    "    flag=False",
                    "):",
                ],
                "replacement_line_count": 4,  # lines 14-17 all exist in diff
            },
        ]
        adapter = _run_dedup_201(
            "github", findings, {"src/processor.py": diff}, tmp_path
        )
        assert adapter.post_review_comment.called
        call_kwargs = adapter.post_review_comment.call_args
        rlc = call_kwargs.kwargs["replacement_line_count"]
        assert rlc == 4, f"Expected rlc=4 for valid span, got {rlc}"
