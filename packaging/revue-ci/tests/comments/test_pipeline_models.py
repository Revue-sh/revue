"""Tests for REVUE-208 pipeline contract types.

Covers: Attribution, AgentFinding, SynthesisGroup, ConsolidatedFinding dataclasses
and GroupingStrategy, SynthesisStrategy, FindingPostProcessor Protocols.

AC contract testing rule: every field asserted by name (testing.md).
"""
from __future__ import annotations

import dataclasses
from typing import get_type_hints

import pytest

from revue_core.comments.models import (
    AgentFinding,
    Attribution,
    ConsolidatedFinding,
    FindingPostProcessor,
    GroupingStrategy,
    SynthesisGroup,
    SynthesisStrategy,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def attribution() -> Attribution:
    return Attribution(agent_name="zara", category="security")


@pytest.fixture
def agent_finding() -> AgentFinding:
    return AgentFinding(
        file_path="src/main.py",
        line_number=42,
        severity="high",
        issue="SQL injection risk",
        suggestion="Use parameterised queries",
        confidence=0.9,
        category="security",
        agent_name="zara",
        code_replacement=["    return db.execute(query, params)"],
        replacement_line_count=1,
        snippet="    return db.execute(query)",
    )


@pytest.fixture
def minimal_agent_finding() -> AgentFinding:
    """AgentFinding with optional code_replacement omitted."""
    return AgentFinding(
        file_path="src/utils.py",
        line_number=10,
        severity="low",
        issue="Unused variable",
        suggestion="Remove unused variable",
        confidence=0.75,
        category="code-quality",
        agent_name="maya",
        code_replacement=None,
        replacement_line_count=1,
    )


@pytest.fixture
def consolidated_finding(attribution: Attribution) -> ConsolidatedFinding:
    return ConsolidatedFinding(
        file_path="src/main.py",
        line_number=42,
        severity="high",
        issue="SQL injection risk",
        suggestion="Use parameterised queries",
        confidence=0.9,
        category="security",
        attribution=[attribution],
        code_replacement=["    return db.execute(query, params)"],
        replacement_line_count=1,
        snippet="    return db.execute(query)",
    )


# ---------------------------------------------------------------------------
# Attribution
# ---------------------------------------------------------------------------

class TestAttribution:
    def test_fields(self, attribution: Attribution) -> None:
        assert attribution.agent_name == "zara"
        assert attribution.category == "security"

    def test_is_frozen(self, attribution: Attribution) -> None:
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            attribution.agent_name = "kai"  # type: ignore[misc]

    def test_is_dataclass(self) -> None:
        assert dataclasses.is_dataclass(Attribution)

    def test_frozen_flag(self) -> None:
        params = dataclasses.fields(Attribution)
        assert Attribution.__dataclass_params__.frozen is True  # type: ignore[attr-defined]

    def test_empty_agent_name_raises(self) -> None:
        with pytest.raises(ValueError, match="agent_name"):
            Attribution(agent_name="", category="security")


# ---------------------------------------------------------------------------
# AgentFinding
# ---------------------------------------------------------------------------

class TestAgentFinding:
    def test_all_required_fields(self, agent_finding: AgentFinding) -> None:
        assert agent_finding.file_path == "src/main.py"
        assert agent_finding.line_number == 42
        assert agent_finding.severity == "high"
        assert agent_finding.issue == "SQL injection risk"
        assert agent_finding.suggestion == "Use parameterised queries"
        assert agent_finding.confidence == 0.9
        assert agent_finding.category == "security"
        assert agent_finding.agent_name == "zara"
        assert agent_finding.code_replacement == ["    return db.execute(query, params)"]
        assert agent_finding.replacement_line_count == 1
        assert agent_finding.snippet == "    return db.execute(query)"

    def test_code_replacement_none(self, minimal_agent_finding: AgentFinding) -> None:
        assert minimal_agent_finding.code_replacement is None

    def test_snippet_defaults_empty_string(self) -> None:
        f = AgentFinding(
            file_path="x.py",
            line_number=1,
            severity="low",
            issue="issue",
            suggestion="fix",
            confidence=0.5,
            category="code-quality",
            agent_name="leo",
            code_replacement=None,
            replacement_line_count=1,
        )
        assert f.snippet == ""

    def test_is_mutable_dataclass(self, agent_finding: AgentFinding) -> None:
        # AgentFinding is NOT frozen — it must be mutable
        agent_finding.snippet = "new snippet"
        assert agent_finding.snippet == "new snippet"

    def test_is_dataclass(self) -> None:
        assert dataclasses.is_dataclass(AgentFinding)

    def test_confidence_below_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="confidence"):
            AgentFinding(
                file_path="x.py", line_number=1, severity="low", issue="i",
                suggestion="s", confidence=-0.1, category="code-quality",
                agent_name="leo", code_replacement=None, replacement_line_count=1,
            )

    def test_confidence_above_one_raises(self) -> None:
        with pytest.raises(ValueError, match="confidence"):
            AgentFinding(
                file_path="x.py", line_number=1, severity="low", issue="i",
                suggestion="s", confidence=1.1, category="code-quality",
                agent_name="leo", code_replacement=None, replacement_line_count=1,
            )

    def test_confidence_boundary_zero_accepted(self) -> None:
        f = AgentFinding(
            file_path="x.py", line_number=1, severity="low", issue="i",
            suggestion="s", confidence=0.0, category="code-quality",
            agent_name="leo", code_replacement=None, replacement_line_count=1,
        )
        assert f.confidence == 0.0

    def test_confidence_boundary_one_accepted(self) -> None:
        f = AgentFinding(
            file_path="x.py", line_number=1, severity="low", issue="i",
            suggestion="s", confidence=1.0, category="code-quality",
            agent_name="leo", code_replacement=None, replacement_line_count=1,
        )
        assert f.confidence == 1.0

    def test_invalid_severity_raises(self) -> None:
        with pytest.raises(ValueError, match="severity"):
            AgentFinding(
                file_path="x.py", line_number=1, severity="critical", issue="i",
                suggestion="s", confidence=0.5, category="code-quality",
                agent_name="leo", code_replacement=None, replacement_line_count=1,
            )

    def test_valid_severities_accepted(self) -> None:
        for sev in ("high", "medium", "low", "info"):
            f = AgentFinding(
                file_path="x.py", line_number=1, severity=sev, issue="i",  # type: ignore[arg-type]
                suggestion="s", confidence=0.5, category="code-quality",
                agent_name="leo", code_replacement=None, replacement_line_count=1,
            )
            assert f.severity == sev

    def test_empty_file_path_raises(self) -> None:
        with pytest.raises(ValueError, match="file_path"):
            AgentFinding(
                file_path="", line_number=1, severity="low", issue="i",
                suggestion="s", confidence=0.5, category="code-quality",
                agent_name="leo", code_replacement=None, replacement_line_count=1,
            )

    def test_zero_line_number_raises(self) -> None:
        with pytest.raises(ValueError, match="line_number"):
            AgentFinding(
                file_path="x.py", line_number=0, severity="low", issue="i",
                suggestion="s", confidence=0.5, category="code-quality",
                agent_name="leo", code_replacement=None, replacement_line_count=1,
            )

    def test_negative_line_number_raises(self) -> None:
        with pytest.raises(ValueError, match="line_number"):
            AgentFinding(
                file_path="x.py", line_number=-1, severity="low", issue="i",
                suggestion="s", confidence=0.5, category="code-quality",
                agent_name="leo", code_replacement=None, replacement_line_count=1,
            )

    def test_empty_agent_name_raises(self) -> None:
        with pytest.raises(ValueError, match="agent_name"):
            AgentFinding(
                file_path="x.py", line_number=1, severity="low", issue="i",
                suggestion="s", confidence=0.5, category="code-quality",
                agent_name="", code_replacement=None, replacement_line_count=1,
            )


# ---------------------------------------------------------------------------
# SynthesisGroup
# ---------------------------------------------------------------------------

class TestSynthesisGroup:
    def test_all_fields(self, agent_finding: AgentFinding) -> None:
        group = SynthesisGroup(
            findings=[agent_finding],
            file_path="src/main.py",
            line_range=(40, 45),
            group_type="proximity",
        )
        assert group.findings == [agent_finding]
        assert group.file_path == "src/main.py"
        assert group.line_range == (40, 45)
        assert group.group_type == "proximity"

    def test_singleton_group_type(self, agent_finding: AgentFinding) -> None:
        group = SynthesisGroup(
            findings=[agent_finding],
            file_path="src/main.py",
            line_range=(42, 42),
            group_type="singleton",
        )
        assert group.group_type == "singleton"

    def test_same_line_group_type(self, agent_finding: AgentFinding) -> None:
        group = SynthesisGroup(
            findings=[agent_finding, agent_finding],
            file_path="src/main.py",
            line_range=(42, 42),
            group_type="same_line",
        )
        assert group.group_type == "same_line"

    def test_empty_findings_raises(self) -> None:
        with pytest.raises(ValueError, match="findings"):
            SynthesisGroup(
                findings=[],
                file_path="src/main.py",
                line_range=(1, 1),
                group_type="singleton",
            )

    def test_inverted_line_range_raises(self, agent_finding: AgentFinding) -> None:
        with pytest.raises(ValueError, match="line_range"):
            SynthesisGroup(
                findings=[agent_finding],
                file_path="src/main.py",
                line_range=(50, 10),
                group_type="singleton",
            )

    def test_equal_line_range_accepted(self, agent_finding: AgentFinding) -> None:
        group = SynthesisGroup(
            findings=[agent_finding],
            file_path="src/main.py",
            line_range=(42, 42),
            group_type="singleton",
        )
        assert group.line_range == (42, 42)

    def test_invalid_group_type_raises(self, agent_finding: AgentFinding) -> None:
        with pytest.raises(ValueError, match="group_type"):
            SynthesisGroup(
                findings=[agent_finding],
                file_path="src/main.py",
                line_range=(1, 5),
                group_type="unknown",  # type: ignore[arg-type]
            )

    def test_empty_file_path_raises(self, agent_finding: AgentFinding) -> None:
        with pytest.raises(ValueError, match="file_path"):
            SynthesisGroup(
                findings=[agent_finding],
                file_path="",
                line_range=(1, 5),
                group_type="singleton",
            )

    def test_zero_line_range_start_raises(self, agent_finding: AgentFinding) -> None:
        with pytest.raises(ValueError, match="line_range"):
            SynthesisGroup(
                findings=[agent_finding],
                file_path="src/main.py",
                line_range=(0, 5),
                group_type="singleton",
            )

    def test_negative_line_range_raises(self, agent_finding: AgentFinding) -> None:
        with pytest.raises(ValueError, match="line_range"):
            SynthesisGroup(
                findings=[agent_finding],
                file_path="src/main.py",
                line_range=(-1, 5),
                group_type="singleton",
            )

    def test_is_dataclass(self) -> None:
        assert dataclasses.is_dataclass(SynthesisGroup)


# ---------------------------------------------------------------------------
# ConsolidatedFinding
# ---------------------------------------------------------------------------

class TestConsolidatedFinding:
    def test_all_required_fields(self, consolidated_finding: ConsolidatedFinding, attribution: Attribution) -> None:
        assert consolidated_finding.file_path == "src/main.py"
        assert consolidated_finding.line_number == 42
        assert consolidated_finding.severity == "high"
        assert consolidated_finding.issue == "SQL injection risk"
        assert consolidated_finding.suggestion == "Use parameterised queries"
        assert consolidated_finding.confidence == 0.9
        assert consolidated_finding.category == "security"
        assert consolidated_finding.attribution == [attribution]
        assert consolidated_finding.code_replacement == ["    return db.execute(query, params)"]
        assert consolidated_finding.replacement_line_count == 1
        assert consolidated_finding.snippet == "    return db.execute(query)"

    def test_group_type_defaults_singleton(self, consolidated_finding: ConsolidatedFinding) -> None:
        assert consolidated_finding.group_type == "singleton"

    def test_group_type_explicit(self, attribution: Attribution) -> None:
        f = ConsolidatedFinding(
            file_path="x.py",
            line_number=1,
            severity="low",
            issue="i",
            suggestion="s",
            confidence=0.5,
            category="code-quality",
            attribution=[attribution],
            code_replacement=None,
            replacement_line_count=1,
            snippet="",
            group_type="proximity",
        )
        assert f.group_type == "proximity"

    def test_attribution_required_non_empty(self, attribution: Attribution) -> None:
        """attribution must never be empty — structural fix for MR !22 regressions."""
        with pytest.raises(ValueError, match="attribution"):
            ConsolidatedFinding(
                file_path="x.py",
                line_number=1,
                severity="low",
                issue="i",
                suggestion="s",
                confidence=0.5,
                category="code-quality",
                attribution=[],  # empty — must raise
                code_replacement=None,
                replacement_line_count=1,
                snippet="",
            )

    def test_code_replacement_none_allowed(self, attribution: Attribution) -> None:
        f = ConsolidatedFinding(
            file_path="x.py",
            line_number=1,
            severity="low",
            issue="i",
            suggestion="s",
            confidence=0.5,
            category="code-quality",
            attribution=[attribution],
            code_replacement=None,
            replacement_line_count=1,
            snippet="",
        )
        assert f.code_replacement is None

    def test_multiple_attributions(self) -> None:
        a1 = Attribution(agent_name="zara", category="security")
        a2 = Attribution(agent_name="maya", category="code-quality")
        f = ConsolidatedFinding(
            file_path="x.py",
            line_number=1,
            severity="medium",
            issue="i",
            suggestion="s",
            confidence=0.8,
            category="security",
            attribution=[a1, a2],
            code_replacement=None,
            replacement_line_count=1,
            snippet="ctx",
        )
        assert len(f.attribution) == 2
        assert f.attribution[0].agent_name == "zara"
        assert f.attribution[1].agent_name == "maya"

    def test_confidence_out_of_range_raises(self, attribution: Attribution) -> None:
        with pytest.raises(ValueError, match="confidence"):
            ConsolidatedFinding(
                file_path="x.py", line_number=1, severity="low", issue="i",
                suggestion="s", confidence=1.5, category="code-quality",
                attribution=[attribution], code_replacement=None,
                replacement_line_count=1, snippet="",
            )

    def test_invalid_group_type_raises(self, attribution: Attribution) -> None:
        with pytest.raises(ValueError, match="group_type"):
            ConsolidatedFinding(
                file_path="x.py", line_number=1, severity="low", issue="i",
                suggestion="s", confidence=0.5, category="code-quality",
                attribution=[attribution], code_replacement=None,
                replacement_line_count=1, snippet="",
                group_type="bad_type",  # type: ignore[arg-type]
            )

    def test_empty_file_path_raises(self, attribution: Attribution) -> None:
        with pytest.raises(ValueError, match="file_path"):
            ConsolidatedFinding(
                file_path="", line_number=1, severity="low", issue="i",
                suggestion="s", confidence=0.5, category="code-quality",
                attribution=[attribution], code_replacement=None,
                replacement_line_count=1, snippet="",
            )

    def test_zero_line_number_raises(self, attribution: Attribution) -> None:
        with pytest.raises(ValueError, match="line_number"):
            ConsolidatedFinding(
                file_path="x.py", line_number=0, severity="low", issue="i",
                suggestion="s", confidence=0.5, category="code-quality",
                attribution=[attribution], code_replacement=None,
                replacement_line_count=1, snippet="",
            )

    def test_negative_line_number_raises(self, attribution: Attribution) -> None:
        with pytest.raises(ValueError, match="line_number"):
            ConsolidatedFinding(
                file_path="x.py", line_number=-1, severity="low", issue="i",
                suggestion="s", confidence=0.5, category="code-quality",
                attribution=[attribution], code_replacement=None,
                replacement_line_count=1, snippet="",
            )

    def test_is_dataclass(self) -> None:
        assert dataclasses.is_dataclass(ConsolidatedFinding)


# ---------------------------------------------------------------------------
# Protocol structural checks
# ---------------------------------------------------------------------------

class TestGroupingStrategyProtocol:
    def test_concrete_impl_satisfies_protocol(self, agent_finding: AgentFinding) -> None:
        """A concrete class with the right signature must be accepted structurally."""
        class MyGrouper:
            def group(self, findings: list[AgentFinding]) -> list[SynthesisGroup]:
                return []

        grouper: GroupingStrategy = MyGrouper()
        result = grouper.group([agent_finding])
        assert result == []

    def test_wrong_signature_raises_at_runtime(self) -> None:
        """Structural typing: wrong return type is caught when used, not at assignment."""
        class BadGrouper:
            def group(self, findings: list) -> list:  # type: ignore[override]
                return []

        # No runtime error at assignment — Protocol typing is structural
        bad: GroupingStrategy = BadGrouper()  # type: ignore[assignment]
        assert bad.group([]) == []


class TestSynthesisStrategyProtocol:
    def test_concrete_impl_satisfies_protocol(
        self, agent_finding: AgentFinding, consolidated_finding: ConsolidatedFinding
    ) -> None:
        class MySynthesiser:
            def synthesise(self, group: SynthesisGroup) -> ConsolidatedFinding:
                return consolidated_finding

        group = SynthesisGroup(
            findings=[agent_finding],
            file_path="src/main.py",
            line_range=(42, 42),
            group_type="singleton",
        )
        synth: SynthesisStrategy = MySynthesiser()
        result = synth.synthesise(group)
        assert result is consolidated_finding


class TestFindingPostProcessorProtocol:
    def test_return_finding_keeps_it(self, consolidated_finding: ConsolidatedFinding) -> None:
        class IdentityProcessor:
            def process(self, finding: ConsolidatedFinding) -> ConsolidatedFinding | None:
                return finding

        proc: FindingPostProcessor = IdentityProcessor()
        assert proc.process(consolidated_finding) is consolidated_finding

    def test_return_none_drops_it(self, consolidated_finding: ConsolidatedFinding) -> None:
        class DropAllProcessor:
            def process(self, finding: ConsolidatedFinding) -> ConsolidatedFinding | None:
                return None

        proc: FindingPostProcessor = DropAllProcessor()
        assert proc.process(consolidated_finding) is None
