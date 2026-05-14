"""REVUE-246 AC4: ``LoadedAgent.analyse`` emits a typed ``AgentVerdict``.

Before REVUE-246 the method returned ``list[AIReview]`` and the empty list
was a triple-overloaded sentinel: clean, error, and no-tools-success all
collapsed into the same value. The typed verdict surfaces those states.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from revue.core.agent_loader import AgentDefinition, LoadedAgent
from revue.core.ai_client import CompletionResult, TokenUsage
from revue.core.models import FileChange


def _fc(path: str = "app.py") -> FileChange:
    return FileChange(
        file_path=path, change_type="modified",
        additions=5, deletions=2, diff="@@ -1 +1 @@\n-old\n+new",
    )


def _client_returning(text: str) -> MagicMock:
    c = MagicMock()
    c.complete.return_value = CompletionResult(text=text, usage=TokenUsage())
    return c


_FINDING = {
    "file_path": "app.py", "line_number": 5,
    "severity": "high", "issue": "issue",
    "suggestion": "fix it", "confidence": 0.85,
    "category": "security",
}


def _defn(name: str = "zara") -> AgentDefinition:
    return AgentDefinition(
        name=name, display_name=name.title(), role="role",
        system_prompt="prompt",
    )


# ---------------------------------------------------------------------------
# Findings verdict
# ---------------------------------------------------------------------------


def test_analyse_returns_findings_verdict_for_findings_response() -> None:
    """A status=findings response surfaces as ``AgentVerdict`` with
    status='findings' and findings populated."""
    # Arrange
    raw = json.dumps({"status": "findings", "findings": [_FINDING]})
    agent = LoadedAgent(_defn(), _client_returning(raw), 4096)

    # Act
    verdict = agent.analyse([_fc()])

    # Assert
    assert verdict.status == "findings"
    assert len(verdict.findings) == 1
    assert verdict.findings[0].issue == "issue"


# ---------------------------------------------------------------------------
# Clean verdict
# ---------------------------------------------------------------------------


def test_analyse_returns_clean_verdict_for_clean_response() -> None:
    """A status=clean response surfaces as ``AgentVerdict`` with status='clean'
    and findings=[]; the summary + confidence are preserved on the verdict."""
    # Arrange
    raw = json.dumps({
        "status": "clean",
        "summary": "no security issues found in the diff",
        "confidence": 0.92,
    })
    agent = LoadedAgent(_defn(), _client_returning(raw), 4096)

    # Act
    verdict = agent.analyse([_fc()])

    # Assert
    assert verdict.status == "clean"
    assert verdict.findings == []
    assert verdict.summary == "no security issues found in the diff"
    assert verdict.confidence == 0.92


def test_clean_verdict_does_not_smuggle_findings() -> None:
    """AC5 invariant: a clean verdict must NEVER carry findings — the
    consolidator relies on this to keep clean agents separate from findings
    agents in the per-agent breakdown."""
    # Arrange
    raw = json.dumps({
        "status": "clean",
        "summary": "ok",
        "confidence": 1.0,
    })
    agent = LoadedAgent(_defn(), _client_returning(raw), 4096)

    # Act
    verdict = agent.analyse([_fc()])

    # Assert
    assert verdict.status == "clean"
    assert verdict.findings == [], "clean must never carry findings"


# ---------------------------------------------------------------------------
# Error verdict
# ---------------------------------------------------------------------------


def test_analyse_returns_error_verdict_for_legacy_array_response() -> None:
    """The legacy ``[{...}]`` array shape — what reviewers used to emit
    pre-REVUE-246 — no longer parses as findings. AC8: no shim, the old
    shape must surface as an explicit error so the prompt gets fixed."""
    # Arrange
    legacy = json.dumps([_FINDING])
    agent = LoadedAgent(_defn(), _client_returning(legacy), 4096)

    # Act
    verdict = agent.analyse([_fc()])

    # Assert
    assert verdict.status == "error"
    assert verdict.error_code == "invalid_response_schema"


def test_analyse_returns_error_verdict_for_empty_text() -> None:
    """The 14K-line silent bail-out — the exact failure mode REVUE-246 was
    written to surface — produces a parseable error verdict, not an empty
    list."""
    # Arrange
    agent = LoadedAgent(_defn(), _client_returning(""), 4096)

    # Act
    verdict = agent.analyse([_fc()])

    # Assert
    assert verdict.status == "error"
    assert verdict.error_code == "invalid_response_schema"
    assert verdict.findings == []


def test_analyse_preserves_self_declared_tool_unavailable() -> None:
    """An agent that fell back to diff-only after read_file failed can
    self-declare error(tool_unavailable). The verdict must preserve that
    code; the classifier must not relabel it."""
    # Arrange
    raw = json.dumps({
        "status": "error",
        "error": {"code": "tool_unavailable", "message": "all reads failed"},
    })
    agent = LoadedAgent(_defn(), _client_returning(raw), 4096)

    # Act
    verdict = agent.analyse([_fc()])

    # Assert
    assert verdict.status == "error"
    assert verdict.error_code == "tool_unavailable"


# ---------------------------------------------------------------------------
# Iteration compatibility — old callers using `for f in verdict` still work
# ---------------------------------------------------------------------------


def test_verdict_iterates_as_findings_for_backcompat() -> None:
    """Pre-REVUE-246, ``analyse()`` returned ``list[AIReview]`` and callers
    iterated directly. The typed verdict preserves that ergonomics so the
    agent_runner / consolidator migration can be staged without rewriting
    every iteration site at once."""
    # Arrange
    raw = json.dumps({"status": "findings", "findings": [_FINDING, _FINDING]})
    agent = LoadedAgent(_defn(), _client_returning(raw), 4096)

    # Act
    verdict = agent.analyse([_fc()])
    items = list(verdict)

    # Assert
    assert len(items) == 2
    assert all(item.issue == "issue" for item in items)
    assert verdict[0].file_path == "app.py"
