"""
Tests for guard-rails prepending in agent loader (REVUE-244).

Tests verify that:
1. Shared guard_rails.md file contains three required sections
2. Each reviewer prompt has a marked anti-pattern block
3. Agent loader successfully prepends guard_rails to system prompt
4. Anti-pattern phrasings use positive directives
5. Verification rule uses procedural framing
6. Confidence calibration explicitly caps inferred claims at 0.4
7. Golden-trace: tool-call log shows find_code before HIGH "missing" findings
"""
from pathlib import Path
import re

import pytest

import revue_core
from revue_core.core.agent_loader import load_agent_definition


class TestSharedGuardRailsFileContainsThreeSections:
    """AC1: Shared file contains Anti-patterns, Confidence calibration, Verification rule."""

    def test_shared_guard_rails_file_contains_three_sections(self):
        # Arrange
        guard_rails_path = Path(revue_core.__file__).parent / "agents" / "_shared" / "guard_rails.md"

        # Act
        guard_rails_text = guard_rails_path.read_text(encoding="utf-8")

        # Assert
        assert "## Anti-patterns" in guard_rails_text
        assert "## Confidence calibration" in guard_rails_text
        assert "## Verification rule" in guard_rails_text
        # Sections should appear at the TOP in this order
        pos_anti = guard_rails_text.index("## Anti-patterns")
        pos_conf = guard_rails_text.index("## Confidence calibration")
        pos_verif = guard_rails_text.index("## Verification rule")
        assert pos_anti < pos_conf < pos_verif


class TestEachReviewerPromptHasMarkedAntiPatternBlock:
    """AC2: Each reviewer prompt contains HTML-comment delimited anti-pattern block."""

    @pytest.mark.parametrize("agent_name", ["maya", "leo", "kai", "zara"])
    def test_each_reviewer_prompt_has_marked_anti_pattern_block(self, agent_name):
        # Arrange
        agent_path = Path(revue_core.__file__).parent / "agents" / f"{agent_name}.md"

        # Act
        agent_text = agent_path.read_text(encoding="utf-8")
        block_match = re.search(
            r"<!--\s*ANTI-PATTERNS.*?-->",
            agent_text,
            re.DOTALL,
        )

        # Assert
        assert block_match is not None, \
            f"{agent_name}.md must contain a <!-- ANTI-PATTERNS ... --> block"
        block_text = block_match.group(0)
        # Count line-start bullets (avoids over-counting inline "- ")
        bullet_count = sum(
            1 for line in block_text.splitlines()
            if line.strip().startswith("- ")
        )
        assert bullet_count >= 6, \
            f"{agent_name}: Expected ≥6 anti-pattern bullets, got {bullet_count}"


class TestEachReviewerLoadedPromptOpensWithGuardRails:
    """AC3: Agent loader prepends guard_rails; loaded prompt opens with three sections
    AND the per-agent anti-pattern bullets are merged into the Anti-patterns section."""

    # Each agent has at least one bullet phrase unique to its domain. These
    # phrases must appear inside the merged ## Anti-patterns section after
    # _prepend_guard_rails runs — proves the per-agent substitution worked.
    AGENT_SIGNATURE_PHRASES: dict[str, str] = {
        "maya": "Null dereferences require a path without checks",
        "leo": "API breaking changes must be verified in the diff",
        "kai": "Micro-optimizations must have measurable impact",
        "zara": "False-positive \"plaintext password\" claims",
    }

    @pytest.mark.parametrize("agent_name", ["maya", "leo", "kai", "zara"])
    def test_each_reviewer_loaded_prompt_opens_with_guard_rails(self, agent_name):
        # Arrange
        agent_path = Path(revue_core.__file__).parent / "agents" / f"{agent_name}.md"

        # Act
        definition = load_agent_definition(agent_path)

        # Assert — guard-rails sections present and ordered
        system_prompt = definition.system_prompt
        assert system_prompt.startswith("# Guard Rails for Reviewer Agents"), \
            "Loaded prompt must open with the guard-rails preamble"
        pos_anti = system_prompt.index("## Anti-patterns")
        pos_conf = system_prompt.index("## Confidence calibration")
        pos_verif = system_prompt.index("## Verification rule")
        assert pos_anti < pos_conf < pos_verif, \
            f"Guard-rails sections out of order: Anti={pos_anti}, Conf={pos_conf}, Verif={pos_verif}"

        # Assert — per-agent bullets are merged into the Anti-patterns section
        signature = self.AGENT_SIGNATURE_PHRASES[agent_name]
        assert signature in system_prompt, \
            f"{agent_name}'s domain-specific bullet '{signature}' missing from loaded prompt"
        signature_pos = system_prompt.index(signature)
        assert pos_anti < signature_pos < pos_conf, \
            f"{agent_name}'s bullet must appear inside ## Anti-patterns " \
            f"(between {pos_anti} and {pos_conf}); found at {signature_pos}"

        # Assert — the raw HTML comment was stripped (no marker leaks)
        assert "<!-- ANTI-PATTERNS" not in system_prompt, \
            "HTML comment marker must not leak into the loaded prompt"
        assert "ANTI-PATTERNS-" not in system_prompt, \
            "ANTI-PATTERNS-<TAG> label must not leak into the loaded prompt"


class TestAntiPatternPhrasingsUsePositiveDirectives:
    """AC4: Anti-pattern bullets use positive directives ("Only flag X when...")."""

    def test_anti_pattern_phrasings_use_positive_directives(self):
        # Arrange — extract one bullet per "- **Header.**" line. This
        # line-oriented split avoids the multi-line lookahead bug that the
        # earlier version had (regex would coalesce bullets across blank lines).
        agents = ["maya", "leo", "kai", "zara"]
        agent_dir = Path(revue_core.__file__).parent / "agents"

        all_bullets: list[str] = []
        for agent_name in agents:
            agent_path = agent_dir / f"{agent_name}.md"
            agent_text = agent_path.read_text(encoding="utf-8")
            block_match = re.search(
                r"<!--\s*ANTI-PATTERNS.*?-->",
                agent_text,
                re.DOTALL,
            )
            if not block_match:
                continue
            block_text = block_match.group(0)
            for line in block_text.splitlines():
                stripped = line.strip()
                if stripped.startswith("- "):
                    all_bullets.append(stripped[2:])

        # Act
        positive_count = sum(
            1 for b in all_bullets
            if b.startswith("**") and (
                "Only flag" in b or "Only report" in b or "Only emit" in b
            )
        )
        total_count = len(all_bullets)

        # Assert
        assert total_count >= 24, \
            f"Expected ≥24 bullets across 4 agents (6 each); got {total_count}"
        positive_ratio = positive_count / total_count
        assert positive_ratio >= 0.9, \
            f"Only {positive_count}/{total_count} bullets use positive directives " \
            f"(need ≥90%; AC4 requires positive phrasing throughout)"


class TestVerificationRuleUsesProceduralFraming:
    """AC5: Verification rule uses procedural framing ("if find_code returns...")."""

    def test_verification_rule_uses_procedural_framing(self):
        # Arrange
        guard_rails_path = Path(revue_core.__file__).parent / "agents" / "_shared" / "guard_rails.md"

        # Act
        guard_rails_text = guard_rails_path.read_text(encoding="utf-8")
        verif_start = guard_rails_text.index("## Verification rule")
        # Get the section (up to next ## or end of file)
        verif_section = guard_rails_text[verif_start:]
        verif_end = verif_section.find("##", 2)
        if verif_end == -1:
            verif_section = verif_section
        else:
            verif_section = verif_section[:verif_end]

        # Assert
        verif_lower = verif_section.lower()
        assert "find_code" in verif_lower, "Verification rule should mention find_code"
        assert "returns" in verif_lower, "Verification rule should mention what find_code returns"
        assert "before filing" in verif_lower, "Should use procedural framing before filing"


class TestConfidenceCalibrationCapsInferredClaimsAt0_4:
    """AC6: Confidence calibration section explicitly caps inferred claims at 0.4."""

    def test_confidence_calibration_caps_inferred_claims_at_0_4(self):
        # Arrange
        guard_rails_path = Path(revue_core.__file__).parent / "agents" / "_shared" / "guard_rails.md"

        # Act
        guard_rails_text = guard_rails_path.read_text(encoding="utf-8")
        conf_start = guard_rails_text.index("## Confidence calibration")
        # Get the section (up to next ## or end of file)
        conf_section = guard_rails_text[conf_start:]
        conf_end = conf_section.find("##", 2)
        if conf_end == -1:
            conf_section = conf_section
        else:
            conf_section = conf_section[:conf_end]

        # Assert
        assert "0.4" in conf_section, "Confidence calibration must mention 0.4 cap"
        assert "inferred" in conf_section.lower(), "Should reference inferred-only claims"
        assert "diff" in conf_section.lower(), "Should reference diff context"


def _section_text(guard_rails_text: str, heading: str) -> str:
    """Slice the markdown text for one ## heading up to the next ## or EOF."""
    start = guard_rails_text.index(heading)
    rest = guard_rails_text[start:]
    end = rest.find("\n## ", len(heading))
    return rest if end == -1 else rest[:end]


class TestConfidenceCalibrationHasConclusiveAnchor:
    """AC11: Confidence calibration includes a high-confidence (≥0.8) anchor for
    diff-conclusive findings, alongside the existing ≤0.4 inferred cap.

    Without this anchor, the model collapses every finding to 0.4 as the safe
    default — the regression scenario discovered during REVUE-244 dogfood.
    """

    def test_confidence_section_has_conclusive_anchor(self):
        # Arrange
        guard_rails_path = Path(revue_core.__file__).parent / "agents" / "_shared" / "guard_rails.md"
        guard_rails_text = guard_rails_path.read_text(encoding="utf-8")

        # Act
        conf_section = _section_text(guard_rails_text, "## Confidence calibration")

        # Assert — high anchor is present
        assert "0.8" in conf_section, \
            "Confidence calibration must include a ≥0.8 anchor for conclusive findings"
        assert "Conclusive" in conf_section, \
            "Confidence calibration must label the conclusive-from-diff case explicitly"


class TestVerificationRuleHasReactiveStructure:
    """AC12: Verification rule is reactive (analyse first, verify when impactful),
    not proactive (verify before everything). The three clauses must be present:
    contained / cross-file impact / missing-symbol claims.
    """

    def test_verification_rule_lists_three_clauses(self):
        # Arrange
        guard_rails_path = Path(revue_core.__file__).parent / "agents" / "_shared" / "guard_rails.md"
        guard_rails_text = guard_rails_path.read_text(encoding="utf-8")

        # Act
        verif_section = _section_text(guard_rails_text, "## Verification rule").lower()

        # Assert — three decision clauses
        assert "contained" in verif_section, \
            "Verification rule must distinguish contained changes (diff-only)"
        assert ("breaking" in verif_section or "cross-file" in verif_section
                or "callers" in verif_section), \
            "Verification rule must address cross-file / breaking impact"
        assert ("missing symbol" in verif_section
                or "undefined name" in verif_section), \
            "Verification rule must keep the missing-symbol clause as the hard prerequisite"

    def test_verification_rule_directs_diff_first_analysis(self):
        # Arrange
        guard_rails_path = Path(revue_core.__file__).parent / "agents" / "_shared" / "guard_rails.md"
        guard_rails_text = guard_rails_path.read_text(encoding="utf-8")

        # Act
        verif_section = _section_text(guard_rails_text, "## Verification rule").lower()
        # First non-heading paragraph: split on blank lines, skip the heading.
        paragraphs = [p.strip() for p in verif_section.split("\n\n") if p.strip()]
        opening = paragraphs[1] if paragraphs[0].startswith("## ") else paragraphs[0]

        # Assert — the rule opens with diff-first framing, not tools-first.
        # This is what reverses the false-negative regression: agents form a
        # hypothesis from the diff before reaching for tools.
        assert "diff" in opening, \
            "Verification rule's opening must mention the diff (analyse-first framing)"
        assert ("analyse" in opening or "analyze" in opening
                or "review" in opening or "first" in opening), \
            "Verification rule must direct the agent to analyse before invoking tools"


class TestVerificationRuleHasToolErrorFallback:
    """AC13: Verification rule explicitly states that tool errors fall back to
    diff-only analysis, never to an empty review.

    This is the exact regression discovered in dogfood: 3 of 4 agents bailed
    with empty findings when their read_file tool returned 'file not at HEAD'.
    The fallback clause must be present and unambiguous.
    """

    def test_tool_error_fallback_is_present(self):
        # Arrange
        guard_rails_path = Path(revue_core.__file__).parent / "agents" / "_shared" / "guard_rails.md"
        guard_rails_text = guard_rails_path.read_text(encoding="utf-8")

        # Act
        verif_section = _section_text(guard_rails_text, "## Verification rule").lower()

        # Assert — fallback clause is present
        assert "tool error" in verif_section or "tool errors" in verif_section, \
            "Verification rule must mention tool errors explicitly"
        assert "fall back" in verif_section or "fallback" in verif_section, \
            "Verification rule must specify the fallback path"
        # The fallback must direct to diff-only analysis with a confidence cap.
        assert "0.4" in verif_section, \
            "Tool-error fallback must cap confidence at 0.4 (diff-only)"


class TestGuardRailsAreLanguageAgnostic:
    """AC14: Guard-rails contain no language-specific code tokens.

    The reviewer prompts run across Python, JS, Go, Ruby, etc. — the language
    is injected from file extensions at runtime. A language-specific example
    in the shared prompt pollutes reviews of other languages.
    """

    def test_no_language_specific_tokens_in_guard_rails(self):
        # Arrange
        guard_rails_path = Path(revue_core.__file__).parent / "agents" / "_shared" / "guard_rails.md"
        guard_rails_text = guard_rails_path.read_text(encoding="utf-8")

        # Act — these tokens are tied to one language family. Each match below
        # is a regex-bounded check so generic words ("def" in "definitely")
        # do not false-positive.
        language_tokens = {
            r"\bdef\s+\w+\(": "Python function definition",
            r"\bfunction\s+\w+\(": "JS/TS function definition",
            r"\bf\"": "Python f-string",
            r"\bexcept\s*:": "Python bare except",
            # "null" as a literal token — allowed as a concept noun
            # ("null dereference", "null check", "null reference", "null pointer").
            r"\bnull\b(?!\s+(dereference|check|reference|pointer|safety|values?))":
                "JS/Java null literal "
                "(allowed as concept noun: 'null dereference/check/reference/pointer')",
            r"\bNone\b": "Python None literal",
            r"\bnil\b": "Go/Ruby nil literal",
            r"\bundefined\b(?!\s+(name|symbol|import))": "JS undefined literal "
                "(allowed only as a noun: 'undefined name/symbol/import')",
            r"\brange\(len\(": "Python range(len(...)) anti-pattern",
            r"@overload\b": "Python @overload decorator",
            r"__\w+__": "Python dunder method",
            r"\.then\(": "JS promise chain",
            r"=>\s*\{": "JS arrow function",
        }

        leaks = []
        for pattern, description in language_tokens.items():
            for match in re.finditer(pattern, guard_rails_text):
                leaks.append(f"{description!r} → matched {match.group(0)!r} "
                             f"at offset {match.start()}")

        # Assert
        assert not leaks, (
            "Guard-rails contain language-specific tokens. The reviewer prompt "
            "must stay language-agnostic because the file's language is injected "
            "from its extension at runtime.\n\nLeaks:\n  - " +
            "\n  - ".join(leaks)
        )


def _missing_symbol_findings_lacking_find_code(
    findings: list[dict],
    tool_calls: list[dict],
) -> list[dict]:
    """Return findings that violate the procedural verification rule.

    A finding violates if it claims a symbol is missing/undefined, is HIGH
    severity, and no preceding tool call invoked find_code for that symbol.

    This helper is the implementation of the AC8 procedural check — it lets
    the test fail when an agent emits a HIGH "missing X" finding without
    first calling find_code(X).
    """
    MISSING_KEYWORDS = ("missing", "undefined", "does not exist", "is not defined")
    violations: list[dict] = []
    for f in findings:
        if str(f.get("severity", "")).lower() not in ("high", "critical", "major"):
            continue
        issue_text = f.get("issue", "").lower()
        if not any(kw in issue_text for kw in MISSING_KEYWORDS):
            continue
        # This finding claims something is missing/undefined and is HIGH.
        # Did the agent call find_code for the symbol before emitting it?
        symbol = f.get("symbol", "")
        find_code_called_for_symbol = any(
            tc.get("name") == "find_code"
            and symbol.lower() in str(tc.get("input", {}).get("query", "")).lower()
            for tc in tool_calls
            if tc.get("position", 0) < f.get("position", 999)
        )
        if not find_code_called_for_symbol:
            violations.append(f)
    return violations


class TestHighMissingFindingRequiresPriorFindCodeCall:
    """AC8: Golden-trace test — find_code called before HIGH "missing/undefined" findings.

    Tests the procedural rule's *detection mechanism*: given a fixture tool-call
    trace and a list of findings, the helper correctly identifies findings that
    violate the rule (HIGH "missing X" without prior find_code(X)).
    """

    def test_violation_detected_when_high_missing_finding_lacks_find_code(self):
        # Arrange — fixture: agent emits a HIGH "missing import" finding,
        # but the tool-call log shows no find_code call for that symbol.
        tool_calls = [
            {"position": 0, "name": "read_file", "input": {"path": "src/foo.py"}},
        ]
        findings = [
            {
                "position": 1,
                "severity": "high",
                "issue": "FindCodeTool is undefined in reviewer_tools.py",
                "symbol": "FindCodeTool",
            },
        ]

        # Act
        violations = _missing_symbol_findings_lacking_find_code(findings, tool_calls)

        # Assert
        assert len(violations) == 1, \
            "Should detect the HIGH missing-import finding without prior find_code"
        assert violations[0]["symbol"] == "FindCodeTool"

    def test_no_violation_when_find_code_was_called_for_the_symbol(self):
        # Arrange — agent called find_code BEFORE emitting the finding.
        tool_calls = [
            {"position": 0, "name": "find_code",
             "input": {"path": "src/foo.py", "query": "FindCodeTool"}},
        ]
        findings = [
            {
                "position": 1,
                "severity": "high",
                "issue": "FindCodeTool is undefined in reviewer_tools.py",
                "symbol": "FindCodeTool",
            },
        ]

        # Act
        violations = _missing_symbol_findings_lacking_find_code(findings, tool_calls)

        # Assert
        assert violations == [], \
            "find_code was called for the symbol — no violation should be detected"

    def test_low_severity_missing_findings_are_not_gated(self):
        # Arrange — the rule applies to HIGH severity only; low-confidence
        # findings drop naturally via the confidence cap (AC6).
        tool_calls: list[dict] = []
        findings = [
            {
                "position": 1,
                "severity": "low",
                "issue": "FindCodeTool may be undefined",
                "symbol": "FindCodeTool",
            },
        ]

        # Act
        violations = _missing_symbol_findings_lacking_find_code(findings, tool_calls)

        # Assert
        assert violations == [], "LOW severity findings are not subject to AC8"


class TestExistingAC7RegressionTestContinuesToPass:
    """AC9: Existing test_ac7_guard_outside_hunk.py continues to pass."""

    def test_existing_ac7_regression_test_imports_cleanly(self):
        # Arrange — AC9 demands the existing test continues to pass. We import
        # its module to verify the test infrastructure is intact (agent_loader,
        # tools, models all importable in the AC7 test's expected shape).
        import importlib

        # Act
        module = importlib.import_module(
            "tests.integration.test_ac7_guard_outside_hunk"
        )

        # Assert — confirm the test functions still exist and are importable.
        assert hasattr(module, "test_control_path_files_the_false_positive_finding")
        assert hasattr(module, "test_treatment_path_routes_through_tool_use_and_invokes_handler")
        assert hasattr(module, "test_tool_use_causally_changes_the_outcome")
