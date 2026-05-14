"""Robustness + corrected-anchor tests for VexVerifier (REVUE-240 review patches).

Covers:

  * P5 — ``_parse_verdict`` tolerates ``null``, capitalised values, array values,
    and other LLM weirdness (defaults to ``apply``).
  * P6 — Fence regex extracts JSON object anywhere in the response, even when
    the model adds trailing prose despite "no prose around it" instructions.
  * P11 — ``VexVerdict`` carries an optional ``corrected_anchor``; the contract
    lets Vex propose a corrected ``line`` and ``replacement_line_count`` along
    with any verdict (D4 option d + D5 option c, merged).

Each test follows AAA structure (Arrange / Act / Assert).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from revue.comments._verifier import CorrectedAnchor, VexVerdict, VexVerifier
from revue.comments.models import Attribution, ConsolidatedFinding
from revue.core.ai_client import CompletionResult, TokenUsage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _consolidated(*, code_replacement="hint", line_number=10) -> ConsolidatedFinding:
    return ConsolidatedFinding(
        file_path="src/example.py",
        line_number=line_number,
        severity="medium",
        issue="x",
        suggestion="y",
        confidence=0.85,
        category="code-quality",
        attribution=[Attribution(agent_name="maya", category="code-quality")],
        code_replacement=["x = 1"] if code_replacement == "hint" else code_replacement,
        replacement_line_count=1,
        snippet="",
        group_type="singleton",
    )


def _completion(text: str) -> CompletionResult:
    return CompletionResult(text=text, usage=TokenUsage())


def _verifier_with(response_text: str) -> VexVerifier:
    client = MagicMock()
    client.complete.return_value = _completion(response_text)
    return VexVerifier(ai_client=client)


# ---------------------------------------------------------------------------
# P5 — parser robustness
# ---------------------------------------------------------------------------


def test_parses_capitalised_verdict_case_insensitively() -> None:
    """LLM returns 'Apply' (capital A): treat as 'apply', not as unknown."""
    # Arrange
    verifier = _verifier_with('{"verdict": "Apply", "reason": "ok"}')

    # Act
    verdict = verifier.verify(file_content="x\n", finding=_consolidated())

    # Assert
    assert verdict.verdict == "apply"


def test_treats_null_verdict_as_unknown_and_fails_open_to_apply() -> None:
    """LLM returns verdict: null — defaults to apply (fail open), not crash."""
    # Arrange
    verifier = _verifier_with('{"verdict": null, "reason": "shrug"}')

    # Act
    verdict = verifier.verify(file_content="x\n", finding=_consolidated())

    # Assert
    assert verdict.verdict == "apply"


def test_treats_array_verdict_as_unknown_and_fails_open() -> None:
    """LLM returns verdict: ['apply'] — defaults to apply."""
    # Arrange
    verifier = _verifier_with('{"verdict": ["apply"], "reason": "shrug"}')

    # Act
    verdict = verifier.verify(file_content="x\n", finding=_consolidated())

    # Assert
    assert verdict.verdict == "apply"


def test_handles_verdict_with_surrounding_whitespace() -> None:
    """LLM returns '  drop_cr_keep_prose  ' — whitespace stripped before validation."""
    # Arrange
    verifier = _verifier_with('{"verdict": "  drop_cr_keep_prose  ", "reason": "wrong indent"}')

    # Act
    verdict = verifier.verify(file_content="x\n", finding=_consolidated())

    # Assert
    assert verdict.verdict == "drop_cr_keep_prose"


# ---------------------------------------------------------------------------
# P6 — fence regex / JSON extraction
# ---------------------------------------------------------------------------


def test_extracts_json_object_when_model_adds_trailing_prose_after_fence() -> None:
    """LLM emits fence + JSON + trailing prose: still parse the JSON."""
    # Arrange
    response = (
        "Here is my verdict.\n"
        "```json\n"
        '{"verdict": "reject_finding", "reason": "already addressed"}\n'
        "```\n"
        "\n"
        "Let me know if you need anything else!"
    )
    verifier = _verifier_with(response)

    # Act
    verdict = verifier.verify(file_content="x\n", finding=_consolidated())

    # Assert
    assert verdict.verdict == "reject_finding"


def test_extracts_json_object_with_no_fence_no_prose() -> None:
    """Bare JSON object on one line."""
    # Arrange
    verifier = _verifier_with('{"verdict":"apply","reason":"ok"}')

    # Act
    verdict = verifier.verify(file_content="x\n", finding=_consolidated())

    # Assert
    assert verdict.verdict == "apply"


def test_extracts_json_when_response_starts_with_explanation_paragraph() -> None:
    """Model violates 'JSON only' rule and prefixes with explanation: still parse."""
    # Arrange
    response = (
        "Looking at this carefully — the patch replaces line 5 which is the function "
        "header, not the body. This is a wrong-anchor case.\n\n"
        '{"verdict": "drop_cr_keep_prose", "reason": "Line 5 is the function header; '
        'replacement is at indent 4 (body)."}'
    )
    verifier = _verifier_with(response)

    # Act
    verdict = verifier.verify(file_content="x\n", finding=_consolidated())

    # Assert
    assert verdict.verdict == "drop_cr_keep_prose"


# ---------------------------------------------------------------------------
# P11 — corrected_anchor field
# ---------------------------------------------------------------------------


def test_parses_corrected_anchor_when_provided() -> None:
    """Vex returns verdict + corrected_anchor: both arrive on the VexVerdict."""
    # Arrange
    response = (
        '{"verdict": "apply", "reason": "Replacement is sound but span starts at 91, not 86.", '
        '"corrected_anchor": {"line": 91, "replacement_line_count": 4}}'
    )
    verifier = _verifier_with(response)

    # Act
    verdict = verifier.verify(file_content="x\n", finding=_consolidated())

    # Assert
    assert verdict.verdict == "apply"
    assert verdict.corrected_anchor is not None
    assert verdict.corrected_anchor.line == 91
    assert verdict.corrected_anchor.replacement_line_count == 4


def test_corrected_anchor_defaults_to_none_when_omitted() -> None:
    """Omitted field is interpreted as 'no correction needed'."""
    # Arrange
    verifier = _verifier_with('{"verdict": "apply", "reason": "ok"}')

    # Act
    verdict = verifier.verify(file_content="x\n", finding=_consolidated())

    # Assert
    assert verdict.corrected_anchor is None


def test_corrected_anchor_with_invalid_line_falls_back_to_none() -> None:
    """corrected_anchor with non-positive line: ignore the correction entirely."""
    # Arrange
    response = (
        '{"verdict": "apply", "reason": "ok", '
        '"corrected_anchor": {"line": 0, "replacement_line_count": 4}}'
    )
    verifier = _verifier_with(response)

    # Act
    verdict = verifier.verify(file_content="x\n", finding=_consolidated())

    # Assert — invalid correction silently dropped; verdict still respected
    assert verdict.corrected_anchor is None


def test_corrected_anchor_paired_with_drop_cr_keep_prose_is_repositioning_hint() -> None:
    """Vex says 'patch unsafe at 86, but if you want to keep prose, anchor it at 91'."""
    # Arrange
    response = (
        '{"verdict": "drop_cr_keep_prose", "reason": "Wrong anchor; correct is line 91.", '
        '"corrected_anchor": {"line": 91, "replacement_line_count": 1}}'
    )
    verifier = _verifier_with(response)

    # Act
    verdict = verifier.verify(file_content="x\n", finding=_consolidated())

    # Assert
    assert verdict.verdict == "drop_cr_keep_prose"
    assert verdict.corrected_anchor is not None
    assert verdict.corrected_anchor.line == 91


def test_corrected_anchor_dataclass_rejects_zero_or_negative_line() -> None:
    """CorrectedAnchor is a typed value object: invalid line raises at construction."""
    # Arrange / Act / Assert
    with pytest.raises(ValueError):
        CorrectedAnchor(line=0, replacement_line_count=4)
    with pytest.raises(ValueError):
        CorrectedAnchor(line=-1, replacement_line_count=4)


def test_corrected_anchor_dataclass_rejects_zero_or_negative_rlc() -> None:
    """CorrectedAnchor rejects rlc < 1."""
    # Arrange / Act / Assert
    with pytest.raises(ValueError):
        CorrectedAnchor(line=10, replacement_line_count=0)
    with pytest.raises(ValueError):
        CorrectedAnchor(line=10, replacement_line_count=-2)


# ---------------------------------------------------------------------------
# P1 — file content presented with 1-based line numbers
# ---------------------------------------------------------------------------


def test_prompt_prefixes_each_file_content_line_with_its_one_based_number() -> None:
    """The LLM must be able to correlate 'Anchor line: N' with a concrete line."""
    # Arrange
    finding = _consolidated(line_number=2)
    client = MagicMock()
    client.complete.return_value = _completion('{"verdict": "apply", "reason": "ok"}')
    verifier = VexVerifier(ai_client=client)

    # Act
    verifier.verify(
        file_content="def foo():\n    return 1\n",
        finding=finding,
    )

    # Assert — line numbers appear in the prompt with a 1-based start
    prompt = client.complete.call_args[0][0][0]["content"]
    assert "   1 | def foo():" in prompt
    assert "   2 |     return 1" in prompt


# ---------------------------------------------------------------------------
# P2 — file content wrapped in unique sentinel, not triple-backtick fence
# ---------------------------------------------------------------------------


def test_prompt_uses_unique_sentinels_so_files_containing_triple_backticks_dont_corrupt() -> None:
    """File content with embedded ``` (markdown / docstrings) must not break the wrapper."""
    # Arrange — file content contains a triple-backtick block (e.g. a Python docstring)
    file_with_fence = (
        'def explain():\n'
        '    """\n'
        '    Example usage:\n'
        '\n'
        '    ```python\n'
        '    explain()\n'
        '    ```\n'
        '    """\n'
        '    return 1\n'
    )
    finding = _consolidated(line_number=9)
    client = MagicMock()
    client.complete.return_value = _completion('{"verdict": "apply", "reason": "ok"}')
    verifier = VexVerifier(ai_client=client)

    # Act
    verifier.verify(file_content=file_with_fence, finding=finding)

    # Assert — sentinels frame the file content; the inner triple-backticks
    # appear verbatim inside (not collapsed, not escaped, not truncated).
    prompt = client.complete.call_args[0][0][0]["content"]
    assert "===VEX_FILE_BEGIN===" in prompt
    assert "===VEX_FILE_END===" in prompt
    # The inner triple-backticks survive without prematurely closing any block
    file_section = prompt.split("===VEX_FILE_BEGIN===", 1)[1].split("===VEX_FILE_END===", 1)[0]
    assert "```python" in file_section
    assert "```" in file_section


def test_verify_forwards_cacheable_system_prompt_and_per_file_cache_key() -> None:
    """P9: system prompt is sent as a cache_control-tagged block; cache_key is file-scoped."""
    # Arrange
    finding = _consolidated(line_number=10)
    client = MagicMock()
    client.complete.return_value = _completion('{"verdict": "apply", "reason": "ok"}')
    verifier = VexVerifier(ai_client=client, system_prompt="STATIC PROMPT")

    # Act
    verifier.verify(file_content="x = 1\n", finding=finding)

    # Assert — system passed as a list of blocks with cache_control set
    kwargs = client.complete.call_args.kwargs
    system = kwargs["system"]
    assert isinstance(system, list)
    assert system[0]["type"] == "text"
    assert system[0]["text"] == "STATIC PROMPT"
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    # cache_key is scoped to the finding's file so multi-finding reviews
    # of the same file share the cached prefix.
    assert kwargs["cache_key"] == "vex-src/example.py"


def test_prompt_normalises_file_without_trailing_newline() -> None:
    """File content missing a final newline must not corrupt the closing sentinel."""
    # Arrange — note: no trailing newline
    finding = _consolidated(line_number=1)
    client = MagicMock()
    client.complete.return_value = _completion('{"verdict": "apply", "reason": "ok"}')
    verifier = VexVerifier(ai_client=client)

    # Act
    verifier.verify(file_content="single_line = 42", finding=finding)

    # Assert — closing sentinel must appear on its own line, not concatenated
    prompt = client.complete.call_args[0][0][0]["content"]
    assert "single_line = 42===VEX_FILE_END===" not in prompt
    assert "\n===VEX_FILE_END===" in prompt
