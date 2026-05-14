"""Tests for UnanchoredFindingExtractor.

After REVUE-239 Phase 1, Nova owns ``line_number`` as the authoritative anchor
and the ConsolidatedFinding dataclass invariants enforce ``line_number > 0``.
The genuine "line not in diff hunk" case is handled downstream by the
position adapter and poster, which feed their own ``summary_sink``.

These tests pin the post-processor's behaviour so prose-only findings with
valid line numbers reach the poster instead of being silently demoted to
the PR-level summary.
"""
from __future__ import annotations

from revue.comments.consolidator import UnanchoredFindingExtractor
from revue.comments.models import Attribution, ConsolidatedFinding


def _make_finding(
    *,
    snippet: str = "",
    code_replacement: list[str] | None = None,
) -> ConsolidatedFinding:
    """Build a valid ConsolidatedFinding with controllable snippet/replacement."""
    return ConsolidatedFinding(
        file_path="src/example.py",
        line_number=42,
        severity="medium",
        issue="function is too long",
        suggestion="extract the validation block into its own helper",
        confidence=0.8,
        category="code-quality",
        attribution=[Attribution(agent_name="maya", category="code-quality")],
        code_replacement=code_replacement,
        replacement_line_count=1,
        snippet=snippet,
    )


class TestUnanchoredExtractorKeepsValidFindings:
    """Prose-only findings with a valid anchor must reach the poster.

    Before this fix the extractor demoted any finding with empty snippet and
    no code_replacement, even though the dataclass already guarantees
    ``line_number > 0`` so the poster can anchor it. The demotion fed every
    consolidated prose finding to the PR-level summary and zero inline
    comments were posted to the PR.
    """

    def test_prose_only_finding_is_kept_inline(self) -> None:
        finding = _make_finding(snippet="", code_replacement=None)
        sink: list[ConsolidatedFinding] = []
        extractor = UnanchoredFindingExtractor(sink)

        result = extractor.process(finding)

        assert result is finding
        assert sink == []

    def test_finding_with_snippet_only_is_kept_inline(self) -> None:
        finding = _make_finding(snippet="def f(): ...", code_replacement=None)
        sink: list[ConsolidatedFinding] = []
        extractor = UnanchoredFindingExtractor(sink)

        result = extractor.process(finding)

        assert result is finding
        assert sink == []

    def test_finding_with_code_replacement_only_is_kept_inline(self) -> None:
        finding = _make_finding(snippet="", code_replacement=["def f(): pass"])
        sink: list[ConsolidatedFinding] = []
        extractor = UnanchoredFindingExtractor(sink)

        result = extractor.process(finding)

        assert result is finding
        assert sink == []
