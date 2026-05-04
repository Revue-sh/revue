"""Tests for Consolidator pipeline stage (REVUE-210).

Covers:
- Consolidator constructor injection and pass-order guarantee
- ProximityAndCountGroupingStrategy boundary conditions (N, K thresholds)
- NovaSingleShotStrategy deterministic fallback on Nova failure
- NoOpSuggestionDropper — no-op code_replacement detection
- UnanchoredFindingExtractor — unanchored finding demotion
- Pipeline import contract (no dedup_consolidator import after migration)
- dedup_consolidator retains NovaConsolidator after migration
- Consolidator output sort order (severity → confidence desc)
- .revue.yml consolidation stanza override support

Finding-consolidation tests migrated from conceptual test_dedup_consolidator.py:
- test_empty_findings
- test_no_duplicates_unchanged
- test_sorted_by_severity_then_confidence
- test_consolidator_output_sorted (AC11)

Nova synthesis tests:
- test_nova_strategy_fallback
"""
from __future__ import annotations

import importlib
import inspect
from unittest.mock import MagicMock, patch

import pytest

from revue.comments.models import (
    AgentFinding,
    Attribution,
    ConsolidatedFinding,
    FindingPostProcessor,
    GroupingStrategy,
    SynthesisGroup,
    SynthesisStrategy,
)
from revue.comments.consolidator import (
    Consolidator,
    NoOpSuggestionDropper,
    NovaSingleShotStrategy,
    ProximityAndCountGroupingStrategy,
    UnanchoredFindingExtractor,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_finding(
    file_path: str = "a.py",
    line_number: int = 10,
    severity: str = "medium",
    issue: str = "test issue",
    suggestion: str = "fix it",
    confidence: float = 0.8,
    category: str = "quality",
    agent_name: str = "leo",
    code_replacement: list[str] | None = None,
    replacement_line_count: int = 1,
    snippet: str = "",
) -> AgentFinding:
    return AgentFinding(
        file_path=file_path,
        line_number=line_number,
        severity=severity,
        issue=issue,
        suggestion=suggestion,
        confidence=confidence,
        category=category,
        agent_name=agent_name,
        code_replacement=code_replacement,
        replacement_line_count=replacement_line_count,
        snippet=snippet,
    )


def _make_consolidated(
    file_path: str = "a.py",
    line_number: int = 10,
    severity: str = "medium",
    issue: str = "test issue",
    suggestion: str = "fix it",
    confidence: float = 0.8,
    category: str = "quality",
    agent_name: str = "leo",
    code_replacement: list[str] | None = None,
    replacement_line_count: int = 1,
    snippet: str = "",
    group_type: str = "singleton",
) -> ConsolidatedFinding:
    return ConsolidatedFinding(
        file_path=file_path,
        line_number=line_number,
        severity=severity,
        issue=issue,
        suggestion=suggestion,
        confidence=confidence,
        category=category,
        attribution=[Attribution(agent_name=agent_name, category=category)],
        code_replacement=code_replacement,
        replacement_line_count=replacement_line_count,
        snippet=snippet,
        group_type=group_type,
    )


class _StubGroupingStrategy:
    """Returns each finding as its own singleton SynthesisGroup."""

    def __init__(self) -> None:
        self.call_count = 0

    def group(self, findings: list[AgentFinding]) -> list[SynthesisGroup]:
        self.call_count += 1
        return [
            SynthesisGroup(
                findings=[f],
                file_path=f.file_path,
                line_range=(f.line_number, f.line_number),
                group_type="singleton",
            )
            for f in findings
        ]


class _StubSynthesisStrategy:
    """Passes each SynthesisGroup through as a ConsolidatedFinding."""

    def __init__(self) -> None:
        self.call_count = 0

    def synthesise(self, group: SynthesisGroup) -> ConsolidatedFinding:
        self.call_count += 1
        f = group.findings[0]
        return ConsolidatedFinding(
            file_path=f.file_path,
            line_number=f.line_number,
            severity=f.severity,
            issue=f.issue,
            suggestion=f.suggestion,
            confidence=f.confidence,
            category=f.category,
            attribution=[Attribution(agent_name=f.agent_name, category=f.category)],
            code_replacement=f.code_replacement,
            replacement_line_count=f.replacement_line_count,
            snippet=f.snippet,
            group_type=group.group_type,
        )


# ---------------------------------------------------------------------------
# AC1 — Consolidator constructor injection
# ---------------------------------------------------------------------------


def test_consolidator_constructor_injection():
    """Consolidator invokes grouping (Pass A) then synthesis (Pass B) in order."""
    grouping = _StubGroupingStrategy()
    synthesis = _StubSynthesisStrategy()
    consolidator = Consolidator(grouping=grouping, synthesis=synthesis)

    findings = [_make_finding(line_number=1), _make_finding(line_number=2)]
    result = consolidator.consolidate(findings)

    assert grouping.call_count == 1
    assert synthesis.call_count == 2  # one per group (both singletons)
    assert len(result) == 2


def test_consolidator_empty_findings():
    """Consolidator handles empty input without error."""
    consolidator = Consolidator(
        grouping=_StubGroupingStrategy(),
        synthesis=_StubSynthesisStrategy(),
    )
    assert consolidator.consolidate([]) == []


def test_consolidator_post_processor_called():
    """Post-processor chain is applied to each synthesised finding."""
    call_log: list[str] = []

    class _LoggingProcessor:
        def process(self, finding: ConsolidatedFinding) -> ConsolidatedFinding | None:
            call_log.append(finding.file_path)
            return finding

    consolidator = Consolidator(
        grouping=_StubGroupingStrategy(),
        synthesis=_StubSynthesisStrategy(),
        post_processors=[_LoggingProcessor()],
    )
    findings = [_make_finding(file_path="x.py"), _make_finding(file_path="y.py")]
    result = consolidator.consolidate(findings)

    assert call_log == ["x.py", "y.py"]
    assert len(result) == 2


def test_consolidator_post_processor_can_drop():
    """Post-processor returning None removes finding from output."""

    class _DropAll:
        def process(self, finding: ConsolidatedFinding) -> ConsolidatedFinding | None:
            return None

    consolidator = Consolidator(
        grouping=_StubGroupingStrategy(),
        synthesis=_StubSynthesisStrategy(),
        post_processors=[_DropAll()],
    )
    result = consolidator.consolidate([_make_finding()])
    assert result == []


# ---------------------------------------------------------------------------
# AC2 — ProximityAndCountGroupingStrategy
# ---------------------------------------------------------------------------


def test_proximity_grouping_within_bounds():
    """Two findings at line_distance ≤ N and count ≤ K produce one SynthesisGroup."""
    strategy = ProximityAndCountGroupingStrategy(n=3, k=3)
    findings = [
        _make_finding(line_number=10),
        _make_finding(line_number=12),  # distance = 2 ≤ 3
    ]
    groups = strategy.group(findings)
    assert len(groups) == 1
    assert groups[0].group_type in ("proximity", "same_line")
    assert len(groups[0].findings) == 2


def test_proximity_grouping_exceeds_distance():
    """Two findings at line_distance > N produce two singleton groups."""
    strategy = ProximityAndCountGroupingStrategy(n=3, k=3)
    findings = [
        _make_finding(line_number=10),
        _make_finding(line_number=15),  # distance = 5 > 3
    ]
    groups = strategy.group(findings)
    assert len(groups) == 2
    assert all(g.group_type == "singleton" for g in groups)


def test_proximity_grouping_exceeds_count():
    """(K+1) findings within N lines produce multiple groups; no group exceeds K."""
    k = 3
    strategy = ProximityAndCountGroupingStrategy(n=5, k=k)
    # 4 findings all within 1 line of each other
    findings = [_make_finding(line_number=10 + i) for i in range(k + 1)]
    groups = strategy.group(findings)
    assert all(len(g.findings) <= k for g in groups)
    assert sum(len(g.findings) for g in groups) == k + 1


def test_proximity_grouping_same_line():
    """Two findings on the exact same line produce a same_line group."""
    strategy = ProximityAndCountGroupingStrategy(n=3, k=3)
    findings = [
        _make_finding(line_number=10, agent_name="leo"),
        _make_finding(line_number=10, agent_name="maya"),
    ]
    groups = strategy.group(findings)
    assert len(groups) == 1
    assert groups[0].group_type == "same_line"


def test_proximity_grouping_different_files():
    """Findings in different files are never grouped together."""
    strategy = ProximityAndCountGroupingStrategy(n=3, k=3)
    findings = [
        _make_finding(file_path="a.py", line_number=10),
        _make_finding(file_path="b.py", line_number=11),  # different file
    ]
    groups = strategy.group(findings)
    assert len(groups) == 2
    file_paths = {g.findings[0].file_path for g in groups}
    assert file_paths == {"a.py", "b.py"}


def test_proximity_grouping_at_exact_boundary():
    """Findings exactly N lines apart are grouped (boundary is inclusive)."""
    strategy = ProximityAndCountGroupingStrategy(n=3, k=3)
    findings = [
        _make_finding(line_number=10),
        _make_finding(line_number=13),  # distance = 3 = N, should group
    ]
    groups = strategy.group(findings)
    assert len(groups) == 1


# ---------------------------------------------------------------------------
# AC3 — NovaSingleShotStrategy deterministic fallback
# ---------------------------------------------------------------------------


def test_nova_strategy_fallback():
    """NovaSingleShotStrategy returns deterministic concatenation when Nova returns invalid JSON."""
    mock_client = MagicMock()
    mock_result = MagicMock()
    mock_result.text = "not valid json at all"
    mock_client.complete.return_value = mock_result

    strategy = NovaSingleShotStrategy(ai_client=mock_client)
    group = SynthesisGroup(
        findings=[
            _make_finding(agent_name="leo", issue="Issue A"),
            _make_finding(agent_name="maya", issue="Issue B"),
        ],
        file_path="a.py",
        line_range=(10, 10),
        group_type="same_line",
    )

    result = strategy.synthesise(group)

    assert isinstance(result, ConsolidatedFinding)
    assert len(result.attribution) >= 1
    # Fallback path preserves all agents in attribution
    agent_names = {a.agent_name for a in result.attribution}
    assert "leo" in agent_names
    assert "maya" in agent_names


def test_nova_strategy_singleton_passthrough():
    """NovaSingleShotStrategy passes singleton groups through without an AI call."""
    mock_client = MagicMock()
    strategy = NovaSingleShotStrategy(ai_client=mock_client)

    finding = _make_finding(agent_name="leo", issue="Solo finding")
    group = SynthesisGroup(
        findings=[finding],
        file_path="a.py",
        line_range=(10, 10),
        group_type="singleton",
    )

    result = strategy.synthesise(group)

    mock_client.complete.assert_not_called()
    assert isinstance(result, ConsolidatedFinding)
    assert result.issue == "Solo finding"


def test_nova_strategy_fallback_on_network_error():
    """NovaSingleShotStrategy falls back gracefully when AI client raises."""
    mock_client = MagicMock()
    mock_client.complete.side_effect = RuntimeError("network error")

    strategy = NovaSingleShotStrategy(ai_client=mock_client)
    group = SynthesisGroup(
        findings=[
            _make_finding(agent_name="leo"),
            _make_finding(agent_name="zara"),
        ],
        file_path="a.py",
        line_range=(10, 10),
        group_type="same_line",
    )

    result = strategy.synthesise(group)

    assert isinstance(result, ConsolidatedFinding)
    # Callers cannot observe which path was taken — result is always ConsolidatedFinding


def test_nova_strategy_preserves_code_replacement():
    """Deterministic fallback preserves code_replacement from highest-confidence finding."""
    mock_client = MagicMock()
    mock_result = MagicMock()
    mock_result.text = "bad json"
    mock_client.complete.return_value = mock_result

    strategy = NovaSingleShotStrategy(ai_client=mock_client)
    group = SynthesisGroup(
        findings=[
            _make_finding(agent_name="leo", confidence=0.6, code_replacement=["old_line"]),
            _make_finding(agent_name="maya", confidence=0.9, code_replacement=["better_line"]),
        ],
        file_path="a.py",
        line_range=(10, 10),
        group_type="same_line",
    )

    result = strategy.synthesise(group)
    # Highest-confidence finding's code_replacement is preserved
    assert result.code_replacement == ["better_line"]


# ---------------------------------------------------------------------------
# AC4 — NoOpSuggestionDropper
# ---------------------------------------------------------------------------


def test_noop_suggestion_dropper_strips():
    """Finding where code_replacement equals snippet (sigils stripped) gets code_replacement=None."""
    dropper = NoOpSuggestionDropper()
    # snippet is the raw code; code_replacement has diff sigils stripped
    finding = _make_consolidated(
        snippet="    x = 1",
        code_replacement=["    x = 1"],  # identical after stripping sigils
    )
    result = dropper.process(finding)

    assert result is not None
    assert result.code_replacement is None


def test_noop_suggestion_dropper_strips_with_sigils():
    """code_replacement with leading diff sigils that match snippet is treated as no-op."""
    dropper = NoOpSuggestionDropper()
    finding = _make_consolidated(
        snippet="x = 1",
        code_replacement=[" x = 1"],  # leading space sigil stripped → "x = 1" == snippet
    )
    result = dropper.process(finding)
    assert result is not None
    assert result.code_replacement is None


def test_noop_suggestion_dropper_preserves_real():
    """Finding where code_replacement differs from snippet is unchanged."""
    dropper = NoOpSuggestionDropper()
    finding = _make_consolidated(
        snippet="x = 1",
        code_replacement=["x = 2"],  # different — real suggestion
    )
    result = dropper.process(finding)

    assert result is not None
    assert result.code_replacement == ["x = 2"]


def test_noop_suggestion_dropper_no_replacement():
    """Finding with no code_replacement is returned unchanged."""
    dropper = NoOpSuggestionDropper()
    finding = _make_consolidated(code_replacement=None)
    result = dropper.process(finding)
    assert result is not None
    assert result.code_replacement is None


# ---------------------------------------------------------------------------
# AC5 — UnanchoredFindingExtractor
# ---------------------------------------------------------------------------


def test_unanchored_extractor_demotes():
    """Finding with no snippet and no code_replacement is removed from inline stream."""
    sink: list[ConsolidatedFinding] = []
    extractor = UnanchoredFindingExtractor(summary_sink=sink)

    finding = _make_consolidated(snippet="", code_replacement=None)
    result = extractor.process(finding)

    assert result is None
    assert len(sink) == 1
    assert sink[0] is finding


def test_unanchored_extractor_keeps_anchored():
    """Finding with snippet is kept in inline stream, not added to sink."""
    sink: list[ConsolidatedFinding] = []
    extractor = UnanchoredFindingExtractor(summary_sink=sink)

    finding = _make_consolidated(snippet="some code", code_replacement=None)
    result = extractor.process(finding)

    assert result is finding
    assert len(sink) == 0


def test_unanchored_after_noop():
    """Finding with only a no-op code_replacement and no snippet is unanchored after NoOpSuggestionDropper."""
    dropper = NoOpSuggestionDropper()
    sink: list[ConsolidatedFinding] = []
    extractor = UnanchoredFindingExtractor(summary_sink=sink)

    # Finding with real code_replacement and no snippet — dropper keeps it, extractor keeps it (anchored)
    finding = _make_consolidated(
        snippet="",
        code_replacement=["x = 1"],  # non-empty, doesn't match empty snippet → kept by dropper
    )
    after_dropper = dropper.process(finding)
    assert after_dropper is not None
    result_anchored = extractor.process(after_dropper)
    assert result_anchored is not None  # code_replacement present → anchored

    # Finding where code_replacement is a no-op (empty line matches empty snippet)
    finding2 = _make_consolidated(
        snippet="",
        code_replacement=[""],  # stripped empty == snippet "" → dropper sets code_replacement=None
    )
    after_dropper2 = dropper.process(finding2)
    assert after_dropper2 is not None
    assert after_dropper2.code_replacement is None  # dropper nullified the no-op replacement
    # Extractor sees neither snippet nor code_replacement → unanchored → sent to sink
    result = extractor.process(after_dropper2)
    assert result is None
    assert len(sink) == 1


# ---------------------------------------------------------------------------
# AC6/AC9 — Pipeline contract tests
# ---------------------------------------------------------------------------


def test_pipeline_no_longer_imports_dedup_consolidator():
    """pipeline.py source does not contain an import from core.dedup_consolidator."""
    import os
    pipeline_path = os.path.join(
        os.path.dirname(__file__),
        "../../src/revue/core/pipeline.py",
    )
    with open(os.path.normpath(pipeline_path)) as fh:
        source = fh.read()

    assert "from revue.core.dedup_consolidator" not in source
    assert "from .dedup_consolidator import" not in source
    # Import via orchestration modules is allowed but must not reference consolidate/AIContradictionSynthesiser
    assert "from revue.core.dedup_consolidator import" not in source


def test_dedup_consolidator_retains_nova_consolidator():
    """from core.dedup_consolidator import NovaConsolidator succeeds after migration."""
    from revue.core.dedup_consolidator import NovaConsolidator  # noqa: F401
    assert NovaConsolidator is not None


# ---------------------------------------------------------------------------
# AC11 — Sort order
# ---------------------------------------------------------------------------


def test_consolidator_output_sorted():
    """Consolidator.consolidate() returns findings sorted high → medium → low → info, then confidence desc."""
    findings = [
        _make_finding(severity="low", confidence=0.9, line_number=1),
        _make_finding(severity="high", confidence=0.7, line_number=2),
        _make_finding(severity="medium", confidence=0.8, line_number=3),
        _make_finding(severity="info", confidence=1.0, line_number=4),
        _make_finding(severity="high", confidence=0.9, line_number=5),
    ]

    consolidator = Consolidator(
        grouping=_StubGroupingStrategy(),
        synthesis=_StubSynthesisStrategy(),
    )
    result = consolidator.consolidate(findings)

    severities = [f.severity for f in result]
    _SEV_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}
    for i in range(len(severities) - 1):
        a_ord = _SEV_ORDER[severities[i]]
        b_ord = _SEV_ORDER[severities[i + 1]]
        if a_ord == b_ord:
            assert result[i].confidence >= result[i + 1].confidence
        else:
            assert a_ord <= b_ord


def test_no_duplicates_unchanged():
    """Distinct findings (different files or lines) all pass through."""
    findings = [
        _make_finding(file_path="a.py", line_number=1),
        _make_finding(file_path="b.py", line_number=1),
    ]
    consolidator = Consolidator(
        grouping=ProximityAndCountGroupingStrategy(n=3, k=3),
        synthesis=_StubSynthesisStrategy(),
    )
    result = consolidator.consolidate(findings)
    assert len(result) == 2


# ---------------------------------------------------------------------------
# AC8 — .revue.yml consolidation stanza read by ProximityAndCountGroupingStrategy
# ---------------------------------------------------------------------------


def test_revue_yml_consolidation_overrides():
    """ProximityAndCountGroupingStrategy respects n and k passed at construction."""
    # n=5, k=2 — only 2 findings per group; line distance up to 5
    strategy = ProximityAndCountGroupingStrategy(n=5, k=2)
    # 3 findings within distance 5 — group should split (k=2)
    findings = [
        _make_finding(line_number=1),
        _make_finding(line_number=2),
        _make_finding(line_number=3),
    ]
    groups = strategy.group(findings)
    assert all(len(g.findings) <= 2 for g in groups)
    assert sum(len(g.findings) for g in groups) == 3


# ---------------------------------------------------------------------------
# B1 regression — sorted output preserved across consolidation
# ---------------------------------------------------------------------------


def test_sorted_by_severity_then_confidence():
    """Output from Consolidator is ordered high → medium → low → info, confidence desc within tier."""
    findings = [
        _make_finding(severity="info",   confidence=0.9, line_number=1),
        _make_finding(severity="high",   confidence=0.6, line_number=2),
        _make_finding(severity="medium", confidence=0.7, line_number=3),
        _make_finding(severity="low",    confidence=0.8, line_number=4),
        _make_finding(severity="high",   confidence=0.9, line_number=5),
    ]
    consolidator = Consolidator(
        grouping=_StubGroupingStrategy(),
        synthesis=_StubSynthesisStrategy(),
    )
    result = consolidator.consolidate(findings)
    _SEV_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}
    for i in range(len(result) - 1):
        a, b = _SEV_ORDER[result[i].severity], _SEV_ORDER[result[i + 1].severity]
        if a == b:
            assert result[i].confidence >= result[i + 1].confidence, (
                f"Confidence out of order at index {i}: {result[i].confidence} < {result[i+1].confidence}"
            )
        else:
            assert a <= b, f"Severity out of order at index {i}: {result[i].severity} > {result[i+1].severity}"


def test_attribution_always_non_empty_on_fallback():
    """NovaSingleShotStrategy deterministic fallback always produces non-empty attribution."""
    mock_client = MagicMock()
    mock_client.complete.side_effect = RuntimeError("fail")
    strategy = NovaSingleShotStrategy(ai_client=mock_client)

    group = SynthesisGroup(
        findings=[
            _make_finding(agent_name="leo",  issue="Issue A"),
            _make_finding(agent_name="maya", issue="Issue B"),
        ],
        file_path="a.py",
        line_range=(10, 10),
        group_type="same_line",
    )
    result = strategy.synthesise(group)
    assert isinstance(result, ConsolidatedFinding)
    assert len(result.attribution) >= 1  # ConsolidatedFinding invariant


def test_deterministic_fallback_preserves_per_finding_category():
    """_deterministic_fallback uses each finding's own category in attribution, not the first finding's."""
    mock_client = MagicMock()
    mock_client.complete.side_effect = RuntimeError("fail")
    strategy = NovaSingleShotStrategy(ai_client=mock_client)

    group = SynthesisGroup(
        findings=[
            _make_finding(agent_name="leo",  category="security"),
            _make_finding(agent_name="maya", category="performance"),
        ],
        file_path="a.py",
        line_range=(10, 10),
        group_type="same_line",
    )
    result = strategy.synthesise(group)
    categories = {a.agent_name: a.category for a in result.attribution}
    assert categories.get("leo")  == "security"
    assert categories.get("maya") == "performance"
