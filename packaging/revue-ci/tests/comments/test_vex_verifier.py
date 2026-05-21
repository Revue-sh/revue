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

from revue_core.comments._verifier import _DEFAULT_SYSTEM_PROMPT, VexVerdict, VexVerifier
from revue_core.comments.models import Attribution, ConsolidatedFinding
from revue_core.core.ai_client import CompletionResult, TokenUsage


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


# ---------------------------------------------------------------------------
# REVUE-249 — block-completeness subsection in the system prompt (D1, AC1–AC3)
# ---------------------------------------------------------------------------


def test_default_system_prompt_retains_blank_line_subsection_heading() -> None:
    """ADR §D1 (REVUE-248) — the blank-line subsection heading must remain
    intact. REVUE-249 appends a peer subsection; it must NEVER rewrite or
    remove REVUE-248's content. This guard fires if a future append accidentally
    edits the existing heading.
    """
    assert "## Corrected anchor — blank-line / context-line case" in _DEFAULT_SYSTEM_PROMPT


def test_default_system_prompt_includes_block_completeness_subsection() -> None:
    """ADR §D1 (REVUE-249) — a new subsection covering replacement-span
    completeness must be appended. It lives as a peer to the blank-line
    subsection (own heading) so future appends compose cleanly without
    reflowing previous content.
    """
    # Heading anchor — match on the load-bearing words so the exact wording
    # can drift slightly without breaking the test.
    prompt_lower = _DEFAULT_SYSTEM_PROMPT.lower()
    assert "## replacement-span completeness" in prompt_lower


def test_blank_line_and_block_completeness_subsections_are_distinct() -> None:
    """The two subsections must be distinct sections in the prompt — not merged,
    not nested. Guards against an accidental rewrite that folds REVUE-249's
    guidance into REVUE-248's subsection (which would re-open the
    'D1 ownership across REVUE-248/249' ambiguity the ADR Review-Notes resolved).
    """
    blank_idx = _DEFAULT_SYSTEM_PROMPT.find("## Corrected anchor — blank-line / context-line case")
    block_idx = _DEFAULT_SYSTEM_PROMPT.lower().find("## replacement-span completeness")
    assert blank_idx >= 0
    assert block_idx >= 0
    assert block_idx > blank_idx, (
        "block-completeness subsection must be APPENDED after the blank-line "
        "subsection — not inserted before it"
    )


def test_block_completeness_subsection_explains_widen_or_downgrade_options() -> None:
    """AC3 — the subsection must tell Vex its two valid outputs when the range
    under-reaches: widen the range via corrected_anchor, OR downgrade verdict
    to drop_cr_keep_prose. Both paths must be described so the LLM knows it has
    options.
    """
    prompt_lower = _DEFAULT_SYSTEM_PROMPT.lower()
    block_idx = prompt_lower.find("## replacement-span completeness")
    assert block_idx >= 0
    subsection = prompt_lower[block_idx:]
    # Both remediation paths must be documented within the subsection.
    assert "corrected_anchor" in subsection
    assert "drop_cr_keep_prose" in subsection


def test_block_completeness_subsection_has_language_agnostic_worked_example() -> None:
    """AC2 — the subsection must include a worked example, but the example must
    NOT use Python/JavaScript/Go-specific keywords. Per
    [[feedback_agent_prompts_language_agnostic]], reviewer prompts must use
    prose / abstract block descriptions, not concrete language syntax.

    Allowed: words like "function", "loop", "conditional", "return". Forbidden:
    syntax tokens that pin the example to one language (def, function(, =>,
    fn, func).
    """
    prompt_lower = _DEFAULT_SYSTEM_PROMPT.lower()
    block_idx = prompt_lower.find("## replacement-span completeness")
    assert block_idx >= 0
    # Subsection bounds: from this heading to either the next "##" heading or EOF.
    next_heading_idx = _DEFAULT_SYSTEM_PROMPT.find("\n##", block_idx + 2)
    subsection = (
        _DEFAULT_SYSTEM_PROMPT[block_idx:next_heading_idx]
        if next_heading_idx > 0
        else _DEFAULT_SYSTEM_PROMPT[block_idx:]
    )
    # Worked-example marker — match the style used in REVUE-248's blank-line
    # subsection ("Worked example") so both subsections share visual vocabulary.
    assert "worked example" in subsection.lower()
    # Language-specific syntax tokens that would violate the language-agnostic
    # rule. Any of these in the subsection is a failure.
    forbidden_tokens = ["def ", "function(", "=>", "fn ", "func "]
    for token in forbidden_tokens:
        assert token not in subsection.lower(), (
            f"block-completeness subsection contains language-specific token {token!r}; "
            "use language-agnostic prose per feedback_agent_prompts_language_agnostic"
        )


def test_block_completeness_subsection_directs_vex_to_check_natural_terminators() -> None:
    """AC3 — the subsection must direct Vex to verify the range extends to the
    natural block terminator (final return, post-loop statement, terminal
    else-branch). This is the core heuristic that catches the PR #29 case.
    """
    prompt_lower = _DEFAULT_SYSTEM_PROMPT.lower()
    block_idx = prompt_lower.find("## replacement-span completeness")
    assert block_idx >= 0
    subsection = prompt_lower[block_idx:]
    # Heuristic vocabulary — match the conceptual words, not exact phrasing.
    assert "block" in subsection
    # The fix must hint at end-of-block reasoning: terminator, return, end of
    # the function/loop. Any of these is acceptable.
    terminator_hints = ["terminator", "final return", "post-loop", "end of", "whole block"]
    assert any(hint in subsection for hint in terminator_hints), (
        "block-completeness subsection must direct Vex to inspect the natural "
        "block terminator (final return, post-loop statement, end-of-block); "
        f"none of {terminator_hints} found in the subsection"
    )


# ---------------------------------------------------------------------------
# REVUE-324 — Reasoning channel (Vex Option C)
# ---------------------------------------------------------------------------


def _completion_with_reasoning(
    text: str,
    reasoning_details: "list[dict] | None" = None,
) -> CompletionResult:
    return CompletionResult(
        text=text,
        usage=TokenUsage(),
        reasoning_details=reasoning_details,
    )


def test_verify_passes_reasoning_enabled_true_to_complete() -> None:
    """REVUE-324 TC13 / AC2-via-Vex: Vex always opts into the reasoning
    channel at the call site. The OpenRouterClient consults the model
    registry to decide what (if anything) the kwarg actually does — for
    non-DeepSeek entries it's a no-op (assembler not named).
    """
    # Arrange
    finding = _consolidated(
        code_replacement=["    return value + 1"],
        replacement_line_count=1,
    )
    client = MagicMock()
    client.complete.return_value = _completion('{"verdict": "apply", "reason": "ok"}')
    verifier = VexVerifier(ai_client=client)

    # Act
    verifier.verify(
        file_content="def foo(value):\n    return value\n",
        finding=finding,
    )

    # Assert — every complete() call on the retry loop must carry reasoning_enabled=True
    for call in client.complete.call_args_list:
        assert call.kwargs.get("reasoning_enabled") is True


def test_empty_content_with_reasoning_details_falls_open_to_apply() -> None:
    """REVUE-324 TC14 / AC6: when ``content`` is empty but ``reasoning_details``
    is populated, the verdict still falls back to ``apply`` — the existing
    fail-open contract is preserved. The reasoning channel is NEVER mined
    for a verdict.
    """
    # Arrange
    finding = _consolidated(
        code_replacement=["    return cached_value"],
        replacement_line_count=1,
    )
    client = MagicMock()
    # Both retry attempts return empty content + populated reasoning_details.
    client.complete.return_value = _completion_with_reasoning(
        text="",
        reasoning_details=[
            {"type": "reasoning", "text": "The patch looks fine — verdict apply."}
        ],
    )
    verifier = VexVerifier(ai_client=client)

    # Act
    verdict = verifier.verify(
        file_content="def foo():\n    return None\n",
        finding=finding,
    )

    # Assert — fail-open to apply, reason mentions the parse failure
    assert verdict.verdict == "apply"
    assert verdict.reason  # non-empty fallback reason
    # Reasoning channel text MUST NOT leak into the verdict reason.
    assert "verdict apply" not in verdict.reason.lower()


def test_empty_content_with_reasoning_details_increments_missing_counter() -> None:
    """REVUE-324 AC6: the empty-content-with-reasoning case increments
    the ``reasoning_missing_count`` counter exactly once per verify()
    call, even when both retry attempts land in the empty-path. Counting
    per attempt would double-count single findings and break dashboards.
    """
    # Arrange
    finding = _consolidated(
        code_replacement=["    return value"],
        replacement_line_count=1,
    )
    client = MagicMock()
    client.complete.return_value = _completion_with_reasoning(
        text="",
        reasoning_details=[{"type": "reasoning", "text": "thinking"}],
    )
    verifier = VexVerifier(ai_client=client)

    # Act — one verify() call; both retries internally hit the empty path.
    verifier.verify(file_content="def f():\n    pass\n", finding=finding)

    # Assert — exactly one increment per verify(), not per attempt.
    assert verifier.reasoning_missing_count == 1


def test_empty_content_without_reasoning_details_does_not_increment_missing_counter() -> None:
    """REVUE-324: empty content with NO reasoning_details is the pre-existing
    failure mode (provider-side filter or routing). The new counter only
    fires when reasoning was captured — so this case must NOT increment it.
    """
    # Arrange
    finding = _consolidated(
        code_replacement=["    return value"],
        replacement_line_count=1,
    )
    client = MagicMock()
    client.complete.return_value = _completion_with_reasoning(
        text="",
        reasoning_details=None,
    )
    verifier = VexVerifier(ai_client=client)

    # Act
    verifier.verify(file_content="def f():\n    pass\n", finding=finding)

    # Assert
    assert verifier.reasoning_missing_count == 0


def test_reasoning_details_present_with_valid_content_does_not_trigger_warning() -> None:
    """REVUE-324: the happy path — content parses AND reasoning_details is
    populated — must NOT increment the warning counter. Reasoning is
    consumed for debug/telemetry only when content parsing fails.
    """
    # Arrange
    finding = _consolidated(
        code_replacement=["    return value + 1"],
        replacement_line_count=1,
    )
    client = MagicMock()
    client.complete.return_value = _completion_with_reasoning(
        text='{"verdict": "apply", "reason": "Safe replacement."}',
        reasoning_details=[{"type": "reasoning", "text": "checking indent"}],
    )
    verifier = VexVerifier(ai_client=client)

    # Act
    verdict = verifier.verify(
        file_content="def foo(value):\n    return value\n",
        finding=finding,
    )

    # Assert — happy path: counter stays at zero, verdict from content
    assert verdict.verdict == "apply"
    assert verifier.reasoning_missing_count == 0


def test_reasoning_missing_counter_is_thread_safe_across_concurrent_calls() -> None:
    """REVUE-324: process_all runs verify() concurrently up to max_workers.
    The counter increment is a read-modify-write that races without a
    lock. This test exercises the lock by hammering verify() in a thread
    pool and asserting the final count equals the call count exactly.

    The per-finding contract (one increment per verify(), not per attempt)
    means a missed lock-protected increment would land BELOW iterations,
    and an unlikely over-count would land above. Either failure mode is
    detected by an exact equality assertion.
    """
    # Arrange
    from concurrent.futures import ThreadPoolExecutor

    finding = _consolidated(
        code_replacement=["    return value"],
        replacement_line_count=1,
    )
    client = MagicMock()
    client.complete.return_value = _completion_with_reasoning(
        text="",
        reasoning_details=[{"type": "reasoning", "text": "."}],
    )
    verifier = VexVerifier(ai_client=client)

    # Act — 32 parallel verify() calls; each lands one increment on the
    # final fail-open path. Futures are collected and resolved so that
    # any exception inside verify() (e.g. a deadlock or logic error)
    # surfaces instead of being silently dropped.
    iterations = 32
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(
                verifier.verify,
                file_content="def f():\n    pass\n",
                finding=finding,
            )
            for _ in range(iterations)
        ]
        for future in futures:
            future.result()

    # Assert — exactly one increment per call, race-free under the lock.
    assert verifier.reasoning_missing_count == iterations
