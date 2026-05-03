"""Integration tests for REVUE-209: BodyBuilder wiring into _run_per_issue_dedup.

These tests verify the NEW behaviors introduced by BodyBuilder — they fail against
the old inline rendering and pass only after BodyBuilder is wired in.

Specific new contracts:
- Brand footer (— 🤖 Revue) appears in every inline comment
- Vocabulary labels use Action/Suggest/Note instead of "Recommendation"
- C3 regression #1 fixed: attribution is preserved in grouped (multi-attribution) comments
- Fingerprint sentinel is still embedded correctly
- AC6: unanchored findings (position=0) returned in summary_sink, not silently discarded
"""
import json
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class _FakeReviewResult:
    file_path: str
    response: str
    error: str = ""


def _make_response(findings: list[dict]) -> str:
    return json.dumps({"findings": findings, "summary": "ok"})


def _run_dedup(platform: str, findings: list[dict], tmp_path) -> MagicMock:
    from revue.cli import _run_per_issue_dedup
    from revue.comments.json_store import PerPRCommentStore

    results = [_FakeReviewResult("app.py", _make_response(findings))]
    adapter = MagicMock()
    adapter.get_existing_comments.return_value = []
    adapter.post_review_comment.return_value = "cmt-1"
    store = PerPRCommentStore(tmp_path)
    _run_per_issue_dedup(adapter, 1, platform, results, {}, store)
    return adapter


# ---------------------------------------------------------------------------
# Brand footer (new BodyBuilder contract — does NOT exist in old inline code)
# ---------------------------------------------------------------------------

def test_brand_footer_present_in_single_finding_comment(tmp_path) -> None:
    """BodyBuilder.build() appends '— 🤖 Revue' to every inline comment.

    This is a new requirement from AC2 / UX D3. The old inline code never added
    the brand footer — this test fails against the pre-REVUE-209 cli.py.
    """
    finding = {
        "severity": "high",
        "issue": "SQL injection vulnerability",
        "line": 10,
        "recommendation": "Use parameterised queries",
        "agent_name": "zara",
        "category": "security",
    }
    adapter = _run_dedup("github", [finding], tmp_path)

    body = adapter.post_review_comment.call_args[1]["body"]
    assert "— 🤖 Revue" in body, "Brand footer must be present in every inline comment"


def test_brand_footer_present_with_code_replacement(tmp_path) -> None:
    """Brand footer appears even when a suggestion fence is rendered."""
    finding = {
        "severity": "medium",
        "issue": "Unsafe concat",
        "line": 5,
        "recommendation": "Use parameterised query",
        "code_replacement": ["    cursor.execute(q, (uid,))"],
        "agent_name": "kai",
        "category": "performance",
    }
    adapter = _run_dedup("github", [finding], tmp_path)

    body = adapter.post_review_comment.call_args[1]["body"]
    assert "— 🤖 Revue" in body


def test_brand_footer_present_in_merged_comment(tmp_path) -> None:
    """Brand footer appears in merged (multi-finding) comments too."""
    findings = [
        {"severity": "high", "issue": "Issue A", "line": 42, "recommendation": "Fix A", "agent_name": "zara"},
        {"severity": "medium", "issue": "Issue B", "line": 42, "recommendation": "Fix B", "agent_name": "kai"},
    ]
    adapter = _run_dedup("bitbucket", findings, tmp_path)

    body = adapter.post_review_comment.call_args[1]["body"]
    assert "— 🤖 Revue" in body, "Brand footer must appear in merged comment bodies"


# ---------------------------------------------------------------------------
# Vocabulary labels (Action / Suggest / Note instead of old "Recommendation")
# ---------------------------------------------------------------------------

def test_vocabulary_label_suggest_when_no_code_replacement(tmp_path) -> None:
    """Medium/high/low with no code_replacement uses '💡 **Suggest:**' not 'Recommendation'.

    The old code always used '> 💡 **Recommendation:**'. BodyBuilder distinguishes:
    - code_replacement present → '> 💡 **Action:**'
    - code_replacement absent → '> 💡 **Suggest:**'
    This test fails against the pre-REVUE-209 implementation.
    """
    finding = {
        "severity": "medium",
        "issue": "Missing type annotation",
        "line": 8,
        "recommendation": "Add return type hint",
        "agent_name": "maya",
        "category": "code-quality",
    }
    adapter = _run_dedup("github", [finding], tmp_path)

    body = adapter.post_review_comment.call_args[1]["body"]
    assert "Suggest" in body, "Vocab label should be 'Suggest' when no code_replacement"
    assert "Recommendation" not in body, "Old 'Recommendation' label must not appear"


def test_vocabulary_label_action_when_code_replacement_present(tmp_path) -> None:
    """Medium/high/low with code_replacement uses '💡 **Action:**'."""
    finding = {
        "severity": "high",
        "issue": "XSS vulnerability",
        "line": 15,
        "recommendation": "Escape output",
        "code_replacement": ["    return html.escape(value)"],
        "agent_name": "zara",
        "category": "security",
    }
    adapter = _run_dedup("github", [finding], tmp_path)

    body = adapter.post_review_comment.call_args[1]["body"]
    assert "Action" in body, "Vocab label should be 'Action' when code_replacement present"
    assert "Recommendation" not in body


def test_vocabulary_label_note_for_info_severity(tmp_path) -> None:
    """Info severity uses 'ℹ️ Note:' regardless of code_replacement presence."""
    finding = {
        "severity": "info",
        "issue": "Consider adding docstring",
        "line": 20,
        "recommendation": "Add a brief description",
        "agent_name": "leo",
        "category": "architecture",
    }
    adapter = _run_dedup("github", [finding], tmp_path)

    body = adapter.post_review_comment.call_args[1]["body"]
    assert "Note" in body, "Info severity must use 'Note' label"
    assert "Recommendation" not in body


# ---------------------------------------------------------------------------
# C3 regression #1 — attribution preserved in grouped / multi-attribution comments
# ---------------------------------------------------------------------------

def test_attribution_present_in_single_finding_comment(tmp_path) -> None:
    """Agent name and category appear in the posted comment body.

    In cli.py before REVUE-209, attribution was only shown when synthesised_from was
    present. For a plain single-agent finding, only category was shown (no agent name).
    BodyBuilder always renders attribution as '*AgentName · Category*'.
    """
    finding = {
        "severity": "high",
        "issue": "Security vulnerability",
        "line": 10,
        "recommendation": "Fix it",
        "agent_name": "zara",
        "category": "security",
    }
    adapter = _run_dedup("github", [finding], tmp_path)

    body = adapter.post_review_comment.call_args[1]["body"]
    assert "Zara" in body, "Agent display name should appear in comment body"
    assert "Security" in body, "Category should appear in comment body"


def test_c3_regression_attribution_preserved_grouped(tmp_path) -> None:
    """AC8: C3 regression #1 — multi-finding grouped comment shows agent attribution.

    The MR !22 regression: when multiple agents found issues on the same line,
    the merged comment body lost all attribution (no agent names visible).
    BodyBuilder ensures every Attribution is rendered.
    """
    findings = [
        {
            "severity": "high",
            "issue": "SQL injection",
            "line": 42,
            "recommendation": "Use parameterised queries",
            "agent_name": "zara",
            "category": "security",
        },
        {
            "severity": "medium",
            "issue": "Performance issue in loop",
            "line": 42,
            "recommendation": "Batch the query",
            "agent_name": "kai",
            "category": "performance",
        },
    ]
    adapter = _run_dedup("github", findings, tmp_path)

    body = adapter.post_review_comment.call_args[1]["body"]
    # Both agents must be represented in the comment
    assert "zara" in body.lower() or "Zara" in body, "Zara's attribution must be in grouped comment"
    assert "kai" in body.lower() or "Kai" in body, "Kai's attribution must be in grouped comment"


# ---------------------------------------------------------------------------
# AC6 — unanchored findings returned in summary_sink (not silently discarded)
# ---------------------------------------------------------------------------

def _run_dedup_full(platform: str, findings: list[dict], tmp_path, position_zero: bool = False):
    """Like _run_dedup but returns the full tuple from _run_per_issue_dedup."""
    from revue.cli import _run_per_issue_dedup
    from revue.comments.json_store import PerPRCommentStore
    from revue.core.vcs_adapter import DiffPosition

    results = [_FakeReviewResult("app.py", _make_response(findings))]
    adapter = MagicMock()
    adapter.get_existing_comments.return_value = []
    adapter.post_review_comment.return_value = "cmt-1"

    if position_zero:
        adapter.resolve_position.return_value = DiffPosition(
            file_path="app.py",
            line_number=findings[0]["line"],
            position=0,  # outside diff hunk sentinel
        )

    store = PerPRCommentStore(tmp_path)
    return _run_per_issue_dedup(adapter, 1, platform, results, {}, store)


def test_ac6_unanchored_finding_in_summary_sink(tmp_path) -> None:
    """AC6: findings outside diff hunks (position=0) go into summary_sink.

    On GitHub and Bitbucket, position=0 means the line falls outside all diff
    hunks and cannot be posted as an inline comment. Before this fix these
    findings were silently discarded (skipped += 1, continue). After this fix
    they are collected in a summary_sink list returned as the 6th tuple element
    so BodyBuilder.build_summary() can include them in the PR-level summary.

    This test FAILS against the pre-AC6 implementation (5-tuple return).
    """
    finding = {
        "severity": "high",
        "issue": "SQL injection in unchanged context",
        "line": 99,
        "recommendation": "Use parameterised queries",
        "agent_name": "zara",
        "category": "security",
    }
    result = _run_dedup_full("github", [finding], tmp_path, position_zero=True)

    assert len(result) == 6, (
        "_run_per_issue_dedup must return a 6-tuple: "
        "(posted, skipped, total_findings, previously_tracked, failed, summary_sink)"
    )
    posted, skipped, total_findings, previously_tracked, failed, summary_sink = result

    assert len(summary_sink) == 1, (
        "Unanchored finding must be collected in summary_sink, not silently discarded"
    )
    cf = summary_sink[0]
    assert cf.issue == "SQL injection in unchanged context"
    assert cf.severity == "high"
    assert cf.attribution[0].agent_name == "zara"


def test_ac6_anchored_finding_not_in_summary_sink(tmp_path) -> None:
    """Findings that resolve to a real diff position must NOT appear in summary_sink."""
    finding = {
        "severity": "medium",
        "issue": "Missing type hint",
        "line": 5,
        "recommendation": "Add return annotation",
        "agent_name": "kai",
        "category": "code-quality",
    }
    result = _run_dedup_full("github", [finding], tmp_path, position_zero=False)

    assert len(result) == 6, "_run_per_issue_dedup must return a 6-tuple"
    *_, summary_sink = result

    assert summary_sink == [], (
        "Anchored finding (position > 0) must not appear in summary_sink"
    )


def test_ac6_summary_sink_empty_for_gitlab(tmp_path) -> None:
    """GitLab uses line_code not position — no summary_sink diversion for gitlab platform."""
    finding = {
        "severity": "low",
        "issue": "Unused variable",
        "line": 7,
        "recommendation": "Remove it",
        "agent_name": "maya",
        "category": "code-quality",
    }
    # GitLab path does NOT check position=0, it uses line_code.
    # Even if resolve_position returns position=0, gitlab findings must not be diverted.
    result = _run_dedup_full("gitlab", [finding], tmp_path, position_zero=False)

    assert len(result) == 6, "_run_per_issue_dedup must return a 6-tuple"
    *_, summary_sink = result
    assert summary_sink == [], "GitLab findings must not appear in summary_sink"


def test_ac6_build_enhanced_summary_renders_unanchored_section(tmp_path) -> None:
    """AC6 wiring: _build_enhanced_summary calls BodyBuilder.build_summary for unanchored findings.

    When summary_sink is non-empty, the enhanced summary must include an
    'Unanchored Findings' section rendered via BodyBuilder.build_summary().

    This test FAILS until _build_enhanced_summary accepts and uses summary_sink.
    """
    from revue.cli import _build_enhanced_summary
    from revue.comments.models import Attribution, ConsolidatedFinding

    unanchored = ConsolidatedFinding(
        file_path="src/service.py",
        line_number=42,
        severity="high",
        issue="Timing attack in auth check",
        suggestion="Use hmac.compare_digest",
        confidence=0.9,
        category="security",
        attribution=[Attribution(agent_name="zara", category="security")],
        code_replacement=None,
        replacement_line_count=1,
        snippet="",
    )

    body = _build_enhanced_summary(
        review_results=[],
        total_findings={"high": 0, "medium": 0, "low": 0, "info": 0},
        revision=1,
        last_updated_at="just now",
        summary_sink=[unanchored],
    )

    assert "Unanchored Findings" in body, (
        "_build_enhanced_summary must render the Unanchored Findings section "
        "from BodyBuilder.build_summary() when summary_sink is non-empty"
    )
    assert "Timing attack in auth check" in body, (
        "Issue text from unanchored finding must appear in summary"
    )


# ---------------------------------------------------------------------------
# Debug logging — synthesised_from attribution (lazy evaluation guard)
# ---------------------------------------------------------------------------

def test_synthesised_from_debug_log_emits_attribution_names(tmp_path, caplog) -> None:
    """When DEBUG logging is enabled, synthesised_from attribution names appear in logs.

    This validates that the isEnabledFor(DEBUG) guard does not suppress the log
    message when DEBUG is actually active. A guard that always skips the log would
    cause this test to fail.
    """
    import logging

    finding = {
        "severity": "high",
        "issue": "SQL injection",
        "line": 10,
        "recommendation": "Use parameterised queries",
        "synthesised_from": [["zara", "security"], ["kai", "performance"]],
    }
    with caplog.at_level(logging.DEBUG):
        _run_dedup("github", [finding], tmp_path)

    log_text = " ".join(r.message for r in caplog.records)
    assert "zara/security" in log_text, (
        "synthesised_from debug log must include agent/category pairs when DEBUG enabled"
    )
    assert "kai/performance" in log_text
