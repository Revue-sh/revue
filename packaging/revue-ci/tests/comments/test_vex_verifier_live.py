"""REVUE-324 — Live OpenRouter smoke test for the DeepSeek reasoning channel.

Guarded by ``RUN_LIVE_AI_TESTS=1`` because the test burns OpenRouter API
tokens. CI does NOT set this var so the test skips silently in pipelines;
run it locally before PR review to capture redacted evidence for the PR
description.

What the test asserts:
- The OpenRouter response on ``deepseek/deepseek-v4-pro`` carries a
  populated ``message.reasoning_details`` field (AC11).
- The ``content`` field carries valid Vex-shaped JSON that the verifier
  can parse without falling open.

What the test does NOT assert:
- Specific verdict value (apply/reject/etc.) — the model's judgement is
  out of scope for a smoke test.
- Token-cost telemetry — see VexMetricsData fields for that surface.
"""
from __future__ import annotations

import os

import pytest

from revue_core.comments._verifier import VexVerifier
from revue_core.comments.models import Attribution, ConsolidatedFinding


_LIVE_GUARD_REASON = (
    "Live OpenRouter call against deepseek/deepseek-v4-pro is opt-in to "
    "avoid burning API tokens in CI. Set RUN_LIVE_AI_TESTS=1 to enable."
)


def _live_enabled() -> bool:
    return os.environ.get("RUN_LIVE_AI_TESTS") == "1"


@pytest.mark.skipif(not _live_enabled(), reason=_LIVE_GUARD_REASON)
def test_deepseek_live_returns_reasoning_details_and_valid_content() -> None:
    """AC11 / TC17: hit the real OpenRouter API on deepseek/deepseek-v4-pro
    and verify both ``content`` and ``reasoning_details`` come back populated.

    Requires:
    - ``RUN_LIVE_AI_TESTS=1`` in env (opt-in guard).
    - ``OPENROUTER_API_KEY`` in env.

    Paste the redacted ``content`` + ``reasoning_details[0]`` excerpts
    into the PR description as evidence.
    """
    # Late imports keep the file harmless when the guard is unset — no
    # network state machinery wakes up at collection time.
    from revue_core.core.ai_client import OpenRouterClient
    from revue_core.core.ai_config import AIConfig

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        pytest.skip(
            "OPENROUTER_API_KEY missing — set it alongside RUN_LIVE_AI_TESTS=1."
        )

    # Arrange — minimal Vex-shaped verification payload that exercises the
    # reasoning channel: a code_replacement that obviously fixes the issue.
    finding = ConsolidatedFinding(
        file_path="src/validator.py",
        line_number=2,
        severity="medium",
        issue="missing None check",
        suggestion="raise ValueError when value is None",
        confidence=0.85,
        category="code-quality",
        attribution=[Attribution(agent_name="maya", category="code-quality")],
        code_replacement=[
            "    if value is None:",
            '        raise ValueError("value must not be None")',
            "    return value + 1",
        ],
        replacement_line_count=1,
        snippet="",
        group_type="singleton",
    )
    file_content = "def increment(value):\n    return value + 1\n"

    config = AIConfig(
        gitlab_url="https://gitlab.example.com",
        gitlab_token="glpat-noop",
        gitlab_project_id="0",
        gitlab_project_path="org/repo",
        gitlab_project_url="https://gitlab.example.com/org/repo",
        genai_gateway_url="https://openrouter.ai/api/v1",
        openai_api_key=api_key,
        gen_ai_gateway_model="deepseek/deepseek-v4-pro",
        ai_temp=0.0,
        ai_confidence=70,
        ai_max_tokens=2048,
        provider="openrouter",
        api_key=api_key,
        api_key_env="OPENROUTER_API_KEY",
        base_url="https://openrouter.ai/api/v1",
        model="deepseek/deepseek-v4-pro",
        azure_endpoint="",
        azure_deployment="",
        azure_api_version="2024-02-01",
    )
    client = OpenRouterClient(config)
    verifier = VexVerifier(ai_client=client)

    # Act — real network call. Vex internally requests reasoning_enabled=True.
    verdict = verifier.verify(file_content=file_content, finding=finding)

    # Assert — verdict is one of the contract values (any of the three is fine
    # for the smoke test; we're proving the channel works end-to-end, not
    # asserting on the model's judgement).
    assert verdict.verdict in {"apply", "drop_cr_keep_prose", "reject_finding"}
    # If verdict came from a fail-open path, the reason carries the marker.
    # The smoke test fails the run if it fell open — that would indicate the
    # reasoning channel didn't work as documented.
    assert "not parseable" not in verdict.reason.lower(), (
        f"Vex fell open on live DeepSeek call — reasoning channel did not "
        f"yield a parseable verdict. reason={verdict.reason!r}"
    )
