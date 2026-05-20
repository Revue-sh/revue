"""REVUE-263: OpenAI-compatible clients apply per-model registry knobs.

REVUE-262 introduced the per-model registry but only wired the dispatcher
gate. REVUE-263 makes the knobs actually do something on the OpenAI-
compatible path:

- ``tool_choice_first_turn`` is forwarded to ``openai_tool_loop`` so
  qwen / deepseek models get the ``required`` nudge the registry advertises.
- ``max_tokens_default`` becomes the fallback when the caller leaves
  ``max_tokens`` unspecified — caller-provided values still win.
- Customer-added models that aren't in the *built-in* registry fall back
  gracefully (``auto`` + 4096) and log an INFO note. They're already
  accepted by ``validate_selected_model`` once user overrides are merged
  (REVUE-262); ``ai_client`` only sees the built-in view.

The Anthropic client is explicitly out of scope and must remain untouched.
"""
from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from revue_core.core.ai_client import (
    AnthropicClient,
    AzureOpenAIClient,
    CustomGatewayClient,
    OpenAIClient,
    OpenRouterClient,
)
from revue_core.core.ai_config import AIConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides: Any) -> AIConfig:
    defaults: dict[str, Any] = dict(
        gitlab_url="https://gitlab.example.com",
        gitlab_token="glpat-test",
        gitlab_project_id="42",
        gitlab_project_path="org/repo",
        gitlab_project_url="https://gitlab.example.com/org/repo",
        genai_gateway_url="https://gateway.example.com/v1",
        openai_api_key="sk-test",
        gen_ai_gateway_model="claude-sonnet-4-5-20250929",
        ai_temp=0.3,
        ai_confidence=70,
        ai_max_tokens=50000,
        provider="openai",
        api_key="sk-test",
        api_key_env="",
        base_url="",
        model="gpt-4o",
        azure_endpoint="",
        azure_deployment="",
        azure_api_version="2024-02-01",
    )
    defaults.update(overrides)
    return AIConfig(**defaults)


def _openai_text_response(text: str = "done") -> MagicMock:
    resp = MagicMock()
    choice = MagicMock()
    choice.message.content = text
    choice.message.tool_calls = None
    choice.finish_reason = "stop"
    resp.choices = [choice]
    resp.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
    return resp


_READ_FILE_TOOL_DEF: dict[str, Any] = {
    "name": "read_file",
    "description": "Read a PR file.",
    "input_schema": {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
}


# ---------------------------------------------------------------------------
# tool_choice_first_turn — sourced from registry
# ---------------------------------------------------------------------------


@patch("revue_core.core.ai_client.openai.OpenAI")
def test_openrouter_client_passes_tool_choice_first_turn_from_registry(
    mock_openai_cls: MagicMock,
) -> None:
    """qwen/qwen3-coder-next is registered with ``tool_choice_first_turn: required``.
    The OpenRouter client must surface that value on the first turn of the
    tool loop — otherwise the model misses tools and emits 0 findings."""
    # Arrange
    mock_openai_cls.return_value.chat.completions.create.return_value = (
        _openai_text_response("ok")
    )
    client = OpenRouterClient(
        _make_config(provider="openrouter", model="qwen/qwen3-coder-next")
    )

    # Act
    client.complete_with_tools(
        messages=[{"role": "user", "content": "x"}],
        tools=[_READ_FILE_TOOL_DEF],
        tool_handlers={"read_file": MagicMock()},
    )

    # Assert
    first_kwargs = (
        mock_openai_cls.return_value.chat.completions.create.call_args_list[0].kwargs
    )
    assert first_kwargs.get("tool_choice") == "required"


# ---------------------------------------------------------------------------
# max_tokens — registry default is the fallback, caller value wins
# ---------------------------------------------------------------------------


@patch("revue_core.core.ai_client.openai.OpenAI")
def test_openrouter_client_uses_max_tokens_default_from_registry_when_caller_omits(
    mock_openai_cls: MagicMock,
) -> None:
    """qwen/qwen3-coder-next has ``max_tokens_default: 2048`` in the registry.
    When the caller doesn't specify ``max_tokens``, the loop must use 2048
    rather than the hard-coded 4096 — that's the whole point of having the
    knob in the registry."""
    # Arrange
    mock_openai_cls.return_value.chat.completions.create.return_value = (
        _openai_text_response("ok")
    )
    client = OpenRouterClient(
        _make_config(provider="openrouter", model="qwen/qwen3-coder-next")
    )

    # Act — caller omits max_tokens
    client.complete_with_tools(
        messages=[{"role": "user", "content": "x"}],
        tools=[_READ_FILE_TOOL_DEF],
        tool_handlers={"read_file": MagicMock()},
    )

    # Assert
    first_kwargs = (
        mock_openai_cls.return_value.chat.completions.create.call_args_list[0].kwargs
    )
    assert first_kwargs.get("max_tokens") == 2048


@patch("revue_core.core.ai_client.openai.OpenAI")
def test_openrouter_client_caller_explicit_max_tokens_wins_over_registry_default(
    mock_openai_cls: MagicMock,
) -> None:
    """Caller-supplied ``max_tokens`` always wins. The registry default is a
    floor for callers that don't care; it must NEVER override an explicit
    value (e.g. a reviewer that knows it needs 8k tokens for a long-context
    response)."""
    # Arrange
    mock_openai_cls.return_value.chat.completions.create.return_value = (
        _openai_text_response("ok")
    )
    client = OpenRouterClient(
        _make_config(provider="openrouter", model="qwen/qwen3-coder-next")
    )

    # Act — caller passes explicit 8192
    client.complete_with_tools(
        messages=[{"role": "user", "content": "x"}],
        tools=[_READ_FILE_TOOL_DEF],
        tool_handlers={"read_file": MagicMock()},
        max_tokens=8192,
    )

    # Assert — caller's value, not the registry's 2048
    first_kwargs = (
        mock_openai_cls.return_value.chat.completions.create.call_args_list[0].kwargs
    )
    assert first_kwargs.get("max_tokens") == 8192


# ---------------------------------------------------------------------------
# Unsupported / customer-added model — graceful fallback + INFO log
# ---------------------------------------------------------------------------


@patch("revue_core.core.ai_client.openai.OpenAI")
def test_openai_client_unsupported_model_falls_back_to_auto(
    mock_openai_cls: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A customer-added model not in the *built-in* registry must NOT crash
    the client. ``ai_client`` has no access to the merged user-override
    registry (that's plumbed by ``config_loader`` at startup, then dropped),
    so the fallback path is the safety net.

    Contract:
    - no ``tool_choice`` injected on the wire (implicit ``auto``);
    - max_tokens defaults to 4096 (matching the existing signature default);
    - one INFO log line so operators can see they're on the fallback path.
    """
    # Arrange
    mock_openai_cls.return_value.chat.completions.create.return_value = (
        _openai_text_response("ok")
    )
    caplog.set_level(logging.INFO, logger="revue_core.core.ai_client")
    client = OpenAIClient(_make_config(provider="openai", model="acme-custom-99"))

    # Act
    client.complete_with_tools(
        messages=[{"role": "user", "content": "x"}],
        tools=[_READ_FILE_TOOL_DEF],
        tool_handlers={"read_file": MagicMock()},
    )

    # Assert — wire-level: no tool_choice, sane max_tokens default
    first_kwargs = (
        mock_openai_cls.return_value.chat.completions.create.call_args_list[0].kwargs
    )
    assert "tool_choice" not in first_kwargs
    assert first_kwargs.get("max_tokens") == 4096
    # Operator-visible note about the fallback. Match by model id so we
    # don't pin the exact phrasing.
    fallback_log = [r for r in caplog.records if "acme-custom-99" in r.message]
    assert fallback_log, "expected an INFO log about the unsupported-model fallback"
    assert fallback_log[0].levelno == logging.INFO


# ---------------------------------------------------------------------------
# Anthropic — regression: registry knobs MUST NOT bleed in
# ---------------------------------------------------------------------------


@patch("revue_core.core.ai_client.anthropic.Anthropic")
def test_anthropic_client_unaffected_by_tool_choice_knob(
    mock_anthropic_cls: MagicMock,
) -> None:
    """Anthropic has its own tool semantics (``tool_use`` content blocks).
    REVUE-263 is explicitly scoped to the OpenAI-compatible loop, so the
    Anthropic client's request kwargs must not pick up ``tool_choice`` or
    have their ``max_tokens`` rewritten by the registry knob.

    This guards against a future refactor that "helpfully" generalises the
    knob to both paths and silently changes Anthropic behaviour.
    """
    # Arrange — single end_turn response so the loop exits after one call.
    msg = MagicMock()
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "no tools needed"
    msg.content = [text_block]
    msg.stop_reason = "end_turn"
    msg.usage = MagicMock(
        input_tokens=10, output_tokens=5,
        cache_creation_input_tokens=0, cache_read_input_tokens=0,
    )
    mock_anthropic_cls.return_value.messages.create.return_value = msg
    # Use a supported Anthropic model id so the registry lookup (if any
    # were to happen) would find ``max_tokens_default: 2048`` for Haiku —
    # making it observable if a regression rewrites the caller's value.
    client = AnthropicClient(
        _make_config(provider="anthropic", model="claude-haiku-4-5-20251001")
    )

    # Act — caller passes its own 4096 explicitly
    client.complete_with_tools(
        messages=[{"role": "user", "content": "x"}],
        tools=[_READ_FILE_TOOL_DEF],
        tool_handlers={"read_file": MagicMock()},
        max_tokens=4096,
    )

    # Assert
    first_kwargs = (
        mock_anthropic_cls.return_value.messages.create.call_args_list[0].kwargs
    )
    assert "tool_choice" not in first_kwargs, (
        "Anthropic loop must NOT carry the OpenAI tool_choice knob"
    )
    assert first_kwargs.get("max_tokens") == 4096, (
        "Anthropic max_tokens must come from the caller, not the registry"
    )


# ---------------------------------------------------------------------------
# Coverage for the other OpenAI-compatible siblings
# ---------------------------------------------------------------------------


@patch("revue_core.core.ai_client.openai.AzureOpenAI")
def test_azure_client_passes_tool_choice_first_turn_from_registry(
    mock_azure_cls: MagicMock,
) -> None:
    """Azure shares the OpenAI-compatible code path. The registry lookup
    keys off ``config.model`` (the model id), not ``azure_deployment``
    (the deployment name)."""
    # Arrange
    mock_azure_cls.return_value.chat.completions.create.return_value = (
        _openai_text_response("ok")
    )
    client = AzureOpenAIClient(
        _make_config(
            provider="azure",
            model="qwen/qwen3-coder-next",
            azure_endpoint="https://example.openai.azure.com",
            azure_deployment="prod-qwen",
        )
    )

    # Act
    client.complete_with_tools(
        messages=[{"role": "user", "content": "x"}],
        tools=[_READ_FILE_TOOL_DEF],
        tool_handlers={"read_file": MagicMock()},
    )

    # Assert
    first_kwargs = (
        mock_azure_cls.return_value.chat.completions.create.call_args_list[0].kwargs
    )
    assert first_kwargs.get("tool_choice") == "required"


@patch("revue_core.core.ai_client.openai.OpenAI")
def test_custom_gateway_client_passes_tool_choice_first_turn_from_registry(
    mock_openai_cls: MagicMock,
) -> None:
    """Custom gateways speak OpenAI's dialect; they inherit the same knob."""
    # Arrange
    mock_openai_cls.return_value.chat.completions.create.return_value = (
        _openai_text_response("ok")
    )
    client = CustomGatewayClient(
        _make_config(
            provider="custom",
            model="deepseek/deepseek-v4-pro",
            base_url="https://custom.example.com/v1",
        )
    )

    # Act
    client.complete_with_tools(
        messages=[{"role": "user", "content": "x"}],
        tools=[_READ_FILE_TOOL_DEF],
        tool_handlers={"read_file": MagicMock()},
    )

    # Assert
    first_kwargs = (
        mock_openai_cls.return_value.chat.completions.create.call_args_list[0].kwargs
    )
    assert first_kwargs.get("tool_choice") == "required"
