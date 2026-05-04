"""Tests for reply-thread analysis (REVUE-112).

Finding-consolidation tests migrated to tests/comments/test_consolidator.py.
This module retains only the NovaConsolidator reply-thread analysis tests.
"""
from __future__ import annotations

import pytest
from revue.core.dedup_consolidator import _parse_thread_decisions, _REPLY_THREAD_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Reply-thread decision parsing tests
# ---------------------------------------------------------------------------


def test_parse_thread_decisions_plain_json():
    """Plain JSON array with no fences parses correctly."""
    raw = '[{"fingerprint": "fp1", "decision": "allowed_pattern", "pattern": "p1", "rationale": "r1"}]'
    result = _parse_thread_decisions(raw)
    assert len(result) == 1
    assert result[0]["fingerprint"] == "fp1"
    assert result[0]["decision"] == "allowed_pattern"


def test_parse_thread_decisions_fenced_json():
    """JSON with markdown code fences (```json ... ```) parses correctly."""
    raw = """```json
[{"fingerprint": "fp1", "decision": "not_acknowledged"}]
```"""
    result = _parse_thread_decisions(raw)
    assert len(result) == 1
    assert result[0]["fingerprint"] == "fp1"


def test_parse_thread_decisions_fence_with_trailing_rationale():
    """JSON response with trailing prose after fence is parsed (fence removed, rationale ignored)."""
    raw = """```json
[{"fingerprint": "fp1", "decision": "acknowledged_fixed"}]
```

Some trailing explanation text. Nova output contains rationale."""
    result = _parse_thread_decisions(raw)
    assert len(result) == 1
    assert result[0]["fingerprint"] == "fp1"


def test_parse_thread_decisions_missing_closing_fence():
    """JSON response with opening fence but no closing fence still parses."""
    raw = """```json
[{"fingerprint": "fp1", "decision": "reason_missing"}]"""
    result = _parse_thread_decisions(raw)
    assert len(result) == 1
    assert result[0]["fingerprint"] == "fp1"


def test_prompt_contains_invariant_prose_guidance():
    """System prompt contains guidance on invariant-prose pattern format."""
    assert "DESIGN INVARIANT" in _REPLY_THREAD_SYSTEM_PROMPT
    assert "DO NOT include file paths, line numbers, function names" in _REPLY_THREAD_SYSTEM_PROMPT


def test_prompt_prohibits_file_paths_and_line_numbers():
    """System prompt explicitly forbids file paths and line numbers in pattern/rationale."""
    assert "line numbers" in _REPLY_THREAD_SYSTEM_PROMPT
    assert "file paths" in _REPLY_THREAD_SYSTEM_PROMPT


def test_prompt_contains_specificity_rule():
    """System prompt includes specificity rule for patterns."""
    assert "Specificity rule" in _REPLY_THREAD_SYSTEM_PROMPT


def test_prompt_applies_rules_to_rationale():
    """System prompt applies invariant-prose and no-specifics rules to rationale."""
    assert "rationale" in _REPLY_THREAD_SYSTEM_PROMPT.lower()
    assert "remains valid after a refactor" in _REPLY_THREAD_SYSTEM_PROMPT
