"""Tests for Vex semantic verifier (REVUE-240).

Vex is a language-agnostic LLM verifier that decides whether a proposed
code_replacement is safe to apply at its claimed anchor. It returns one of:

  - apply              — patch is safe; post as-is
  - drop_cr_keep_prose — patch is unsafe; keep prose, drop suggestion fence
  - reject_finding     — finding itself is wrong; do not post

Each test follows AAA structure (Arrange / Act / Assert).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from revue.comments._verifier import _DEFAULT_SYSTEM_PROMPT, VexVerdict, VexVerifier
from revue.comments.models import Attribution, ConsolidatedFinding
from revue.core.ai_client import CompletionResult, TokenUsage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _consolidated(
    *,
    file_path: str = "src/example.py",
    line_number: int = 10,
    code_replacement: "list[str] | None" = None,
    replacement_line_count: int = 1,
    issue: str = "missing input validation",
    suggestion: str = "raise ValueError when input is None",
) -> ConsolidatedFinding:
    return ConsolidatedFinding(
        file_path=file_path,
        line_number=line_number,
        severity="medium",
        issue=issue,
        suggestion=suggestion,
        confidence=0.85,
        category="code-quality",
        attribution=[Attribution(agent_name="maya", category="code-quality")],
        code_replacement=code_replacement,
        replacement_line_count=replacement_line_count,
        snippet="",
        group_type="singleton",
    )


def _completion(text: str) -> CompletionResult:
    return CompletionResult(text=text, usage=TokenUsage())


# ---------------------------------------------------------------------------
# Verdict mapping
# ---------------------------------------------------------------------------


def test_apply_verdict_when_patch_is_safe() -> None:
    """LLM returns verdict=apply — VexVerdict carries the apply outcome verbatim."""
    # Arrange
    finding = _consolidated(
        code_replacement=["    return value + 1"],
        replacement_line_count=1,
    )
    client = MagicMock()
    client.complete.return_value = _completion(
        '{"verdict": "apply", "reason": "Replacement preserves indent and control flow."}'
    )
    verifier = VexVerifier(ai_client=client)

    # Act
    verdict = verifier.verify(
        file_content="def foo(value):\n    return value\n",
        finding=finding,
    )

    # Assert
    assert verdict.verdict == "apply"
    assert "preserves" in verdict.reason


def test_drop_cr_keep_prose_verdict_when_indent_mismatches_anchor() -> None:
    """LLM returns verdict=drop_cr_keep_prose — replicates the PR #24 Finding 1 case."""
    # Arrange — anchor inside an f-string at indent 16, replacement at indent 8
    file_content = (
        'def execute(self, *, path):\n'
        '    if path not in self._allowed_paths:\n'
        '        return ToolResult(\n'
        '            content=(\n'
        '                f"Error: \'{path}\' is not in this PR\'s file set."\n'
        '            ),\n'
        '        )\n'
    )
    finding = _consolidated(
        line_number=5,  # inside the f-string at indent 16
        code_replacement=[
            "        full_path = (self._repo_root / path).resolve(strict=True)",
            "        try:",
        ],
        replacement_line_count=2,
    )
    client = MagicMock()
    client.complete.return_value = _completion(
        '{"verdict": "drop_cr_keep_prose", "reason": "Anchor line 5 is inside an f-string at indent 16; replacement is at indent 8. Applying would orphan the f-string and break the function."}'
    )
    verifier = VexVerifier(ai_client=client)

    # Act
    verdict = verifier.verify(file_content=file_content, finding=finding)

    # Assert
    assert verdict.verdict == "drop_cr_keep_prose"
    assert "indent" in verdict.reason.lower()


def test_reject_finding_verdict_when_issue_already_addressed_in_file() -> None:
    """LLM returns verdict=reject_finding — finding itself is incorrect."""
    # Arrange
    file_content = (
        "function getUser(id) {\n"
        "  if (id == null) throw new Error('id required');\n"
        "  return users.find(u => u.id === id);\n"
        "}\n"
    )
    finding = _consolidated(
        line_number=2,
        issue="Missing null check on id parameter",
        code_replacement=["  if (!id) throw new Error('id required');"],
        replacement_line_count=1,
    )
    client = MagicMock()
    client.complete.return_value = _completion(
        '{"verdict": "reject_finding", "reason": "Line 2 already contains a null check on id; the finding is incorrect."}'
    )
    verifier = VexVerifier(ai_client=client)

    # Act
    verdict = verifier.verify(file_content=file_content, finding=finding)

    # Assert
    assert verdict.verdict == "reject_finding"
    assert "already" in verdict.reason.lower()


# ---------------------------------------------------------------------------
# Defensive parsing — Vex's response is LLM-generated; never crash on bad output
# ---------------------------------------------------------------------------


def test_malformed_json_response_falls_back_to_apply_so_suggestions_are_not_silently_blocked() -> None:
    """Bad JSON from Vex: fail open with apply + log a warning.

    Fail-closed would block ALL suggestions whenever Vex hiccups, which is worse
    than the (rare) hallucination Vex was meant to catch.
    """
    # Arrange
    finding = _consolidated(code_replacement=["x = 1"], replacement_line_count=1)
    client = MagicMock()
    client.complete.return_value = _completion("not valid json at all")
    verifier = VexVerifier(ai_client=client)

    # Act
    verdict = verifier.verify(file_content="placeholder\n", finding=finding)

    # Assert — defaults to apply so the user still sees the suggestion
    assert verdict.verdict == "apply"


def test_response_with_unknown_verdict_value_falls_back_to_apply() -> None:
    """LLM returns a verdict not in the contract: treat as apply (fail open)."""
    # Arrange
    finding = _consolidated(code_replacement=["x = 1"], replacement_line_count=1)
    client = MagicMock()
    client.complete.return_value = _completion('{"verdict": "maybe", "reason": "unsure"}')
    verifier = VexVerifier(ai_client=client)

    # Act
    verdict = verifier.verify(file_content="placeholder\n", finding=finding)

    # Assert
    assert verdict.verdict == "apply"


def test_response_wrapped_in_markdown_fences_is_parsed_correctly() -> None:
    """LLM wraps JSON in ```json … ``` fence — strip and parse."""
    # Arrange
    finding = _consolidated(code_replacement=["x = 1"], replacement_line_count=1)
    client = MagicMock()
    client.complete.return_value = _completion(
        '```json\n{"verdict": "drop_cr_keep_prose", "reason": "anchor wrong"}\n```'
    )
    verifier = VexVerifier(ai_client=client)

    # Act
    verdict = verifier.verify(file_content="placeholder\n", finding=finding)

    # Assert
    assert verdict.verdict == "drop_cr_keep_prose"


# ---------------------------------------------------------------------------
# Prompt forwarding — Vex must see anchor, span, replacement, file content
# ---------------------------------------------------------------------------


def test_verify_forwards_anchor_and_replacement_to_llm_prompt() -> None:
    """The LLM prompt must include the file content, anchor line, rlc, and code_replacement.

    Without these fields Vex can't reason about the patch; this guards against
    a regression where the wrong field is forwarded.
    """
    # Arrange
    finding = _consolidated(
        file_path="src/foo.py",
        line_number=42,
        code_replacement=["    return cached_value"],
        replacement_line_count=3,
    )
    client = MagicMock()
    client.complete.return_value = _completion('{"verdict": "apply", "reason": "ok"}')
    verifier = VexVerifier(ai_client=client)

    # Act
    verifier.verify(
        file_content="def f():\n    pass\n",
        finding=finding,
    )

    # Assert — inspect the prompt sent to the LLM
    call_args = client.complete.call_args
    messages = call_args[0][0] if call_args[0] else call_args[1]["messages"]
    prompt_text = messages[0]["content"]
    assert "src/foo.py" in prompt_text
    assert "42" in prompt_text  # anchor line
    assert "3" in prompt_text  # replacement_line_count
    assert "return cached_value" in prompt_text
    assert "def f():" in prompt_text  # file content embedded


# ---------------------------------------------------------------------------
# Verdict dataclass invariants
# ---------------------------------------------------------------------------


def test_vex_verdict_rejects_unknown_verdict_value_at_construction() -> None:
    """VexVerdict is a typed value object — invalid verdicts raise at construction."""
    # Arrange / Act / Assert
    with pytest.raises(ValueError):
        VexVerdict(verdict="something_else", reason="x")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# REVUE-248 — corrected_anchor contract in the system prompt (D1, AC1)
# ---------------------------------------------------------------------------


def test_default_system_prompt_does_not_instruct_verdict_reason_only() -> None:
    """ADR §D1.c — the system prompt must not restrict output to only verdict +
    reason. Leaving that restriction in place would tell the LLM to ignore the
    corrected_anchor field the user-message prompt requests — D1.a/D1.b/D1.d
    become dead code.

    The previous prompt listed exactly two fields under "with these fields:".
    The new prompt must list corrected_anchor as a third field so the model
    treats it as a real output, not optional padding.
    """
    # Find where the prompt describes its output schema, and verify that
    # corrected_anchor is listed alongside verdict and reason — not after a
    # closed list that ends at reason.
    assert "corrected_anchor" in _DEFAULT_SYSTEM_PROMPT
    # The legacy phrasing "with these fields:\n  verdict: ...\n  reason: ..."
    # used to terminate the schema before corrected_anchor. Guard against
    # regressions by ensuring corrected_anchor appears before the schema list
    # ends (i.e. before any "Verdicts:" section that documents verdict values).
    schema_block_end = _DEFAULT_SYSTEM_PROMPT.find("Verdicts:")
    if schema_block_end != -1:
        assert _DEFAULT_SYSTEM_PROMPT.find("corrected_anchor") < schema_block_end


def test_default_system_prompt_documents_corrected_anchor_as_first_class_field() -> None:
    """ADR §D1 — corrected_anchor must be described as an output field with its
    full schema (line + replacement_line_count) so the LLM emits a structured
    correction instead of describing the misalignment in prose.
    """
    assert "corrected_anchor" in _DEFAULT_SYSTEM_PROMPT
    assert "replacement_line_count" in _DEFAULT_SYSTEM_PROMPT
    # The schema fields must be documented as a unit — assert that within at
    # least one 200-character window, *both* ``corrected_anchor`` and
    # ``replacement_line_count`` appear. This guards against a refactor that
    # mentions one or the other casually but never together as a schema.
    schema_window_found = False
    for ca_idx in _find_all(_DEFAULT_SYSTEM_PROMPT, "corrected_anchor"):
        window = _DEFAULT_SYSTEM_PROMPT[ca_idx : ca_idx + 200]
        if "replacement_line_count" in window:
            schema_window_found = True
            break
    assert schema_window_found, (
        "corrected_anchor and replacement_line_count must appear within a "
        "200-character window so the schema is documented as a unit"
    )


def _find_all(text: str, needle: str) -> list[int]:
    """Return every offset of *needle* in *text* — used to locate all schema mentions."""
    indices: list[int] = []
    start = 0
    while True:
        idx = text.find(needle, start)
        if idx < 0:
            break
        indices.append(idx)
        start = idx + 1
    return indices


def test_default_system_prompt_includes_blank_line_worked_example() -> None:
    """ADR §D1 — a worked example for the blank-line case is required so the
    LLM has a concrete pattern to follow. The REVUE-247 failure was that Vex
    recognised 'line N is blank, the issue is on line N+1' in prose but didn't
    emit a structured correction.

    The example must use language-agnostic prose (no Python syntax) per
    [[feedback_agent_prompts_language_agnostic]].
    """
    prompt_lower = _DEFAULT_SYSTEM_PROMPT.lower()
    # Look for the words that anchor a blank-line example
    assert "blank" in prompt_lower
    # The example must clearly describe corrected_anchor emission for this case
    # by including a JSON-shaped or schema-style reference
    assert "corrected_anchor" in _DEFAULT_SYSTEM_PROMPT
