#!/usr/bin/env python3
"""Tests for AIClient protocol, concrete implementations, and factory."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import openai
import pytest

from revue_core.core.ai_client import (
    AnthropicClient,
    AzureOpenAIClient,
    CompletionResult,
    CustomGatewayClient,
    OpenAIClient,
    OpenRouterClient,
    TokenUsage,
    _CACHE_CONTROL_1H,
    _openai_messages,
    _with_retry,
    create_ai_client,
    register_provider,
)
from revue_core.core.ai_config import AIConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(**overrides: Any) -> AIConfig:
    """Return an AIConfig with sensible test defaults, overridden by *overrides*."""
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
        provider="anthropic",
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


# ---------------------------------------------------------------------------
# Instantiation tests (1-5)
# ---------------------------------------------------------------------------

@patch("revue_core.core.ai_client.openai.OpenAI")
def test_openai_client_instantiates(mock_openai_cls: MagicMock) -> None:
    config = _make_config(provider="openai")
    client = OpenAIClient(config)
    mock_openai_cls.assert_called_once()
    assert client._model == config.model


@patch("revue_core.core.ai_client.anthropic.Anthropic")
def test_anthropic_client_instantiates(mock_anthropic_cls: MagicMock) -> None:
    config = _make_config(provider="anthropic")
    client = AnthropicClient(config)
    mock_anthropic_cls.assert_called_once()
    assert client._model == config.model


@patch("revue_core.core.ai_client.openai.AzureOpenAI")
def test_azure_client_instantiates(mock_azure_cls: MagicMock) -> None:
    config = _make_config(
        provider="azure",
        azure_endpoint="https://myazure.openai.azure.com",
        azure_deployment="gpt-4o-deploy",
    )
    client = AzureOpenAIClient(config)
    mock_azure_cls.assert_called_once()
    assert client._model == "gpt-4o-deploy"


@patch("revue_core.core.ai_client.openai.OpenAI")
def test_openrouter_client_instantiates(mock_openai_cls: MagicMock) -> None:
    config = _make_config(provider="openrouter")
    client = OpenRouterClient(config)
    mock_openai_cls.assert_called_once()
    call_kwargs = mock_openai_cls.call_args[1]
    assert call_kwargs["base_url"] == "https://openrouter.ai/api/v1"
    assert "HTTP-Referer" in call_kwargs["default_headers"]
    assert client._model == config.model


@patch("revue_core.core.ai_client.openai.OpenAI")
def test_custom_gateway_client_instantiates(mock_openai_cls: MagicMock) -> None:
    config = _make_config(provider="custom", base_url="https://my-gateway.internal/v1")
    client = CustomGatewayClient(config)
    mock_openai_cls.assert_called_once()
    assert client._model == config.model


# ---------------------------------------------------------------------------
# Factory routing tests (6-11)
# ---------------------------------------------------------------------------

@patch("revue_core.core.ai_client.openai.OpenAI")
def test_factory_routes_openai(mock_cls: MagicMock) -> None:
    config = _make_config(provider="openai")
    client = create_ai_client(config)
    assert isinstance(client, OpenAIClient)


@patch("revue_core.core.ai_client.anthropic.Anthropic")
def test_factory_routes_anthropic(mock_cls: MagicMock) -> None:
    config = _make_config(provider="anthropic")
    client = create_ai_client(config)
    assert isinstance(client, AnthropicClient)


@patch("revue_core.core.ai_client.openai.AzureOpenAI")
def test_factory_routes_azure(mock_cls: MagicMock) -> None:
    config = _make_config(provider="azure", azure_endpoint="https://x.openai.azure.com")
    client = create_ai_client(config)
    assert isinstance(client, AzureOpenAIClient)


@patch("revue_core.core.ai_client.openai.OpenAI")
def test_factory_routes_openrouter(mock_cls: MagicMock) -> None:
    config = _make_config(provider="openrouter")
    client = create_ai_client(config)
    assert isinstance(client, OpenRouterClient)


@patch("revue_core.core.ai_client.openai.OpenAI")
def test_factory_routes_custom(mock_cls: MagicMock) -> None:
    config = _make_config(provider="custom", base_url="https://gw.internal/v1")
    client = create_ai_client(config)
    assert isinstance(client, CustomGatewayClient)


def test_factory_raises_unknown_provider() -> None:
    config = _make_config()
    # Force an invalid provider value
    object.__setattr__(config, "provider", "deepseek")
    with pytest.raises(ValueError, match=r"Unknown provider.*Known providers"):
        create_ai_client(config)


@patch("revue_core.core.ai_client.openai.OpenAI")
def test_register_provider(mock_openai_cls: MagicMock) -> None:
    register_provider("gemini", OpenAIClient)
    config = _make_config(provider="gemini")
    client = create_ai_client(config)
    assert isinstance(client, OpenAIClient)


# ---------------------------------------------------------------------------
# Retry / timeout tests (12-13)
# ---------------------------------------------------------------------------

@patch("revue_core.core.ai_client.time.sleep", return_value=None)
def test_retry_on_429(mock_sleep: MagicMock) -> None:
    call_count = 0

    def _flaky() -> str:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            mock_response = MagicMock(status_code=429)
            mock_response.headers.get.return_value = None  # force exponential backoff
            raise openai.RateLimitError(
                message="rate limited",
                response=mock_response,
                body=None,
            )
        return "success"

    result = _with_retry(_flaky, max_attempts=3, base_delay=1.0)
    assert result == "success"
    assert call_count == 3
    assert mock_sleep.call_count == 2
    # Verify exponential back-off delays: 1s, 2s
    mock_sleep.assert_any_call(1.0)
    mock_sleep.assert_any_call(2.0)


def test_timeout_raises() -> None:
    def _timeout() -> str:
        raise TimeoutError("connection timed out")

    with pytest.raises(TimeoutError, match="connection timed out"):
        _with_retry(_timeout)


# ---------------------------------------------------------------------------
# REVUE-115: Anthropic top-level cache_control + observability
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# REVUE-116: OpenAI cached_tokens logging + prompt_cache_key forwarding
# ---------------------------------------------------------------------------

@patch("revue_core.core.ai_client.openai.OpenAI")
def test_openai_complete_logs_cached_tokens(mock_openai_cls: MagicMock) -> None:
    """TC1: OpenAIClient logs cached_tokens from usage.prompt_tokens_details."""
    mock_details = MagicMock(cached_tokens=512)
    mock_usage = MagicMock(
        prompt_tokens=2000,
        completion_tokens=100,
        prompt_tokens_details=mock_details,
    )
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = "result"
    mock_resp.usage = mock_usage
    mock_openai_cls.return_value.chat.completions.create.return_value = mock_resp

    config = _make_config(provider="openai")
    client = OpenAIClient(config)

    with patch("revue_core.core.log.Log.nova") as mock_log:
        result = client.complete([{"role": "user", "content": "test"}])
        assert result.text == "result"
        mock_log.debug.assert_called_once()
        log_args = mock_log.debug.call_args[0]
        assert "cached" in log_args[0]
        # 512 should appear in the log args
        assert 512 in log_args


@patch("revue_core.core.ai_client.openai.OpenAI")
def test_openai_complete_forwards_cache_key(mock_openai_cls: MagicMock) -> None:
    """TC2: OpenAIClient passes cache_key as prompt_cache_key when provided."""
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = "ok"
    mock_resp.usage = None
    mock_openai_cls.return_value.chat.completions.create.return_value = mock_resp

    config = _make_config(provider="openai")
    client = OpenAIClient(config)
    client.complete([{"role": "user", "content": "test"}], cache_key="abc123def456789a")

    call_kwargs = mock_openai_cls.return_value.chat.completions.create.call_args[1]
    assert call_kwargs.get("prompt_cache_key") == "abc123def456789a"


@patch("revue_core.core.ai_client.openai.OpenAI")
def test_openai_complete_omits_cache_key_when_none(mock_openai_cls: MagicMock) -> None:
    """TC3: OpenAIClient does not pass prompt_cache_key when cache_key=None."""
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = "ok"
    mock_resp.usage = None
    mock_openai_cls.return_value.chat.completions.create.return_value = mock_resp

    config = _make_config(provider="openai")
    client = OpenAIClient(config)
    client.complete([{"role": "user", "content": "test"}], cache_key=None)

    call_kwargs = mock_openai_cls.return_value.chat.completions.create.call_args[1]
    assert "prompt_cache_key" not in call_kwargs


@patch("revue_core.core.ai_client.anthropic.Anthropic")
def test_anthropic_complete_ignores_cache_key(mock_anthropic_cls: MagicMock) -> None:
    """TC4: AnthropicClient does not forward cache_key as prompt_cache_key."""
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="result")]
    mock_msg.usage = MagicMock(
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        input_tokens=500,
        output_tokens=50,
    )
    mock_anthropic_cls.return_value.messages.create.return_value = mock_msg

    config = _make_config(provider="anthropic")
    client = AnthropicClient(config)
    client.complete([{"role": "user", "content": "test"}], cache_key="should-be-ignored")

    call_kwargs = mock_anthropic_cls.return_value.messages.create.call_args[1]
    assert "prompt_cache_key" not in call_kwargs


# ---------------------------------------------------------------------------
# REVUE-115: Anthropic top-level cache_control + observability
# ---------------------------------------------------------------------------

@patch("revue_core.core.ai_client.anthropic.Anthropic")
def test_anthropic_complete_caches_via_content_blocks(mock_anthropic_cls: MagicMock) -> None:
    """TC1 (D1): client is a transparent passthrough — caller owns cache_control placement.

    D1 contract: AnthropicClient.complete() must NOT mutate the system list or add
    cache_control anywhere.  The caller (LoadedAgent.analyse / run_shared_analysis)
    is responsible for placing cache_control on system[0] (the diff block).

    - system[0] carries cache_control (placed by the caller, passed through unchanged)
    - system[1] has NO cache_control (agent instructions — uncached)
    - User message content has NO cache_control (shared_context is not byte-stable)
    """
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="result")]
    mock_msg.usage = MagicMock(
        cache_creation_input_tokens=100,
        cache_read_input_tokens=0,
        input_tokens=500,
        output_tokens=50,
    )
    mock_anthropic_cls.return_value.messages.create.return_value = mock_msg

    config = _make_config(provider="anthropic")
    client = AnthropicClient(config)
    caller_system = [
        {"type": "text", "text": "diff content here", "cache_control": _CACHE_CONTROL_1H},
        {"type": "text", "text": "You are a security expert."},
    ]
    result = client.complete(
        [{"role": "user", "content": "review this diff"}],
        system=caller_system,
    )

    assert result.text == "result"
    call_kwargs = mock_anthropic_cls.return_value.messages.create.call_args[1]
    # No invalid top-level cache_control kwarg
    assert "cache_control" not in call_kwargs
    # system[0] (diff block) must carry the caller-provided cache_control
    system_blocks = call_kwargs.get("system", [])
    assert isinstance(system_blocks, list) and len(system_blocks) == 2
    assert system_blocks[0].get("cache_control") == _CACHE_CONTROL_1H
    # system[1] (agent instructions) must NOT have cache_control added by the client
    assert "cache_control" not in system_blocks[1]
    # User message content must NOT have cache_control (no _anthropic_messages_with_cache)
    messages = call_kwargs.get("messages", [])
    last_content = messages[-1].get("content")
    if isinstance(last_content, list):
        for block in last_content:
            assert "cache_control" not in block
    # plain string content is fine — no cache_control either way


# ---------------------------------------------------------------------------
# REVUE-151: D1 — client is a transparent passthrough (TC_D1_1, TC_D1_2)
# ---------------------------------------------------------------------------

@patch("revue_core.core.ai_client.anthropic.Anthropic")
def test_anthropic_does_not_mutate_caller_system_list(mock_anthropic_cls: MagicMock) -> None:
    """TC_D1_1: complete() passes the system list through unchanged — no mutation.

    The caller provides cache_control on system[0].  The client must not add,
    remove, or reorder cache_control on any block.
    """
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="ok")]
    mock_msg.usage = MagicMock(
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        input_tokens=100,
        output_tokens=10,
    )
    mock_anthropic_cls.return_value.messages.create.return_value = mock_msg

    config = _make_config(provider="anthropic")
    client = AnthropicClient(config)
    caller_system = [
        {"type": "text", "text": "diff", "cache_control": _CACHE_CONTROL_1H},
        {"type": "text", "text": "instructions"},
    ]
    client.complete([{"role": "user", "content": "go"}], system=caller_system)

    call_kwargs = mock_anthropic_cls.return_value.messages.create.call_args[1]
    sent_system = call_kwargs.get("system", [])
    # Exact passthrough — same two blocks, same order, no extra cache_control
    assert sent_system == caller_system


@patch("revue_core.core.ai_client.anthropic.Anthropic")
def test_anthropic_no_user_message_cache_breakpoint(mock_anthropic_cls: MagicMock) -> None:
    """TC_D1_2: complete() does NOT add cache_control to the user message content.

    After D1, _anthropic_messages_with_cache() must not be called.  User message
    content is passed through as-is (string or list) with no cache_control injected.
    """
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="ok")]
    mock_msg.usage = MagicMock(
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        input_tokens=100,
        output_tokens=10,
    )
    mock_anthropic_cls.return_value.messages.create.return_value = mock_msg

    config = _make_config(provider="anthropic")
    client = AnthropicClient(config)
    client.complete([{"role": "user", "content": "plain string content"}])

    call_kwargs = mock_anthropic_cls.return_value.messages.create.call_args[1]
    messages = call_kwargs.get("messages", [])
    last_content = messages[-1].get("content")
    # Must not have been converted to a list with cache_control
    if isinstance(last_content, list):
        for block in last_content:
            assert "cache_control" not in block, (
                f"client must not inject cache_control into user message: {block}"
            )


@patch("revue_core.core.ai_client.anthropic.Anthropic")
def test_anthropic_complete_logs_cache_usage(mock_anthropic_cls: MagicMock) -> None:
    """TC4: cache_creation_input_tokens and cache_read_input_tokens are logged without raising."""
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="ok")]
    mock_msg.usage = MagicMock(
        cache_creation_input_tokens=1500,
        cache_read_input_tokens=0,
        input_tokens=2000,
        output_tokens=80,
    )
    mock_anthropic_cls.return_value.messages.create.return_value = mock_msg

    config = _make_config(provider="anthropic")
    client = AnthropicClient(config)

    import logging
    with patch("revue_core.core.log.Log.nova") as mock_log:
        client.complete([{"role": "user", "content": "test"}])
        mock_log.debug.assert_called_once()
        log_args = mock_log.debug.call_args[0]
        assert "cache_creation" in log_args[0]
        assert "cache_read" in log_args[0]


# ---------------------------------------------------------------------------
# REVUE-157: D2 cache tier verification — guard against accidental reversion
# ---------------------------------------------------------------------------

def test_cache_control_1h_value() -> None:
    """_CACHE_CONTROL_1H must be exactly {"type": "ephemeral", "ttl": "1h"}.

    Billing evidence (2026-04-20): cache_read_tokens=74034 after a 9-minute gap
    with cache_creation=0 confirms the 1h tier is active server-side.  This test
    guards the constant against accidental reversion to the old {"type": "ephemeral"}
    (5-minute default) which would silently drop the TTL without an API error.
    """
    assert _CACHE_CONTROL_1H == {"type": "ephemeral", "ttl": "1h"}


# ---------------------------------------------------------------------------
# REVUE-155: CompletionResult + TokenUsage (RED — TokenUsage/CompletionResult
# do not exist yet; these tests drive the implementation)
# ---------------------------------------------------------------------------

def test_token_usage_defaults_to_zero() -> None:
    """All four TokenUsage fields default to 0 when not supplied."""
    from revue_core.core.ai_client import TokenUsage
    u = TokenUsage()
    assert u.input_tokens == 0
    assert u.output_tokens == 0
    assert u.cache_creation_input_tokens == 0
    assert u.cache_read_input_tokens == 0


def test_completion_result_fields() -> None:
    """CompletionResult exposes .text and .usage correctly."""
    from revue_core.core.ai_client import CompletionResult, TokenUsage
    r = CompletionResult(text="hello", usage=TokenUsage())
    assert r.text == "hello"
    assert isinstance(r.usage, TokenUsage)


@patch("revue_core.core.ai_client.anthropic.Anthropic")
def test_anthropic_returns_completion_result(mock_anthropic_cls: MagicMock) -> None:
    """AnthropicClient.complete() returns a CompletionResult instance."""
    from revue_core.core.ai_client import CompletionResult
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="answer")]
    mock_msg.usage = MagicMock(
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        input_tokens=100,
        output_tokens=10,
    )
    mock_anthropic_cls.return_value.messages.create.return_value = mock_msg
    client = AnthropicClient(_make_config(provider="anthropic"))
    result = client.complete([{"role": "user", "content": "hi"}])
    assert isinstance(result, CompletionResult)
    assert result.text == "answer"


@patch("revue_core.core.ai_client.openai.OpenAI")
def test_openai_returns_completion_result(mock_openai_cls: MagicMock) -> None:
    """OpenAIClient.complete() returns a CompletionResult instance."""
    from revue_core.core.ai_client import CompletionResult
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = "answer"
    mock_resp.usage = MagicMock(prompt_tokens=50, completion_tokens=10)
    mock_openai_cls.return_value.chat.completions.create.return_value = mock_resp
    client = OpenAIClient(_make_config(provider="openai"))
    result = client.complete([{"role": "user", "content": "hi"}])
    assert isinstance(result, CompletionResult)
    assert result.text == "answer"


@patch("revue_core.core.ai_client.openai.AzureOpenAI")
def test_azure_returns_completion_result(mock_azure_cls: MagicMock) -> None:
    """AzureOpenAIClient.complete() returns a CompletionResult instance."""
    from revue_core.core.ai_client import CompletionResult
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = "answer"
    mock_resp.usage = MagicMock(prompt_tokens=50, completion_tokens=10)
    mock_azure_cls.return_value.chat.completions.create.return_value = mock_resp
    client = AzureOpenAIClient(_make_config(provider="azure", azure_endpoint="https://x.openai.azure.com"))
    result = client.complete([{"role": "user", "content": "hi"}])
    assert isinstance(result, CompletionResult)
    assert result.text == "answer"


@patch("revue_core.core.ai_client.openai.OpenAI")
def test_openrouter_returns_completion_result(mock_openai_cls: MagicMock) -> None:
    """OpenRouterClient.complete() returns a CompletionResult instance."""
    from revue_core.core.ai_client import CompletionResult
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = "answer"
    mock_resp.usage = MagicMock(prompt_tokens=50, completion_tokens=10)
    mock_openai_cls.return_value.chat.completions.create.return_value = mock_resp
    client = OpenRouterClient(_make_config(provider="openrouter"))
    result = client.complete([{"role": "user", "content": "hi"}])
    assert isinstance(result, CompletionResult)
    assert result.text == "answer"


@patch("revue_core.core.ai_client.openai.OpenAI")
def test_custom_returns_completion_result(mock_openai_cls: MagicMock) -> None:
    """CustomGatewayClient.complete() returns a CompletionResult instance."""
    from revue_core.core.ai_client import CompletionResult
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = "answer"
    mock_resp.usage = MagicMock(prompt_tokens=50, completion_tokens=10)
    mock_openai_cls.return_value.chat.completions.create.return_value = mock_resp
    client = CustomGatewayClient(_make_config(provider="custom", base_url="https://gw.example.com"))
    result = client.complete([{"role": "user", "content": "hi"}])
    assert isinstance(result, CompletionResult)
    assert result.text == "answer"


@patch("revue_core.core.ai_client.anthropic.Anthropic")
def test_anthropic_populates_all_usage_fields(mock_anthropic_cls: MagicMock) -> None:
    """AnthropicClient populates all four TokenUsage fields from resp.usage."""
    from revue_core.core.ai_client import TokenUsage
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="ok")]
    mock_msg.usage = MagicMock(
        cache_creation_input_tokens=1234,
        cache_read_input_tokens=567,
        input_tokens=2000,
        output_tokens=80,
    )
    mock_anthropic_cls.return_value.messages.create.return_value = mock_msg
    client = AnthropicClient(_make_config(provider="anthropic"))
    result = client.complete([{"role": "user", "content": "hi"}])
    assert result.usage.cache_creation_input_tokens == 1234
    assert result.usage.cache_read_input_tokens == 567
    assert result.usage.input_tokens == 2000
    assert result.usage.output_tokens == 80


@patch("revue_core.core.ai_client.openai.OpenAI")
def test_openai_maps_prompt_tokens_to_input_tokens(mock_openai_cls: MagicMock) -> None:
    """OpenAIClient maps prompt_tokens→input_tokens, completion_tokens→output_tokens; cache fields=0."""
    from revue_core.core.ai_client import TokenUsage
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = "ok"
    mock_resp.usage = MagicMock(prompt_tokens=100, completion_tokens=50)
    mock_openai_cls.return_value.chat.completions.create.return_value = mock_resp
    client = OpenAIClient(_make_config(provider="openai"))
    result = client.complete([{"role": "user", "content": "hi"}])
    assert result.usage.input_tokens == 100
    assert result.usage.output_tokens == 50
    assert result.usage.cache_creation_input_tokens == 0
    assert result.usage.cache_read_input_tokens == 0


# ---------------------------------------------------------------------------
# REVUE-151: D1 — OpenAI path with D1-style system list (TC_D1_openai)
# ---------------------------------------------------------------------------

def test_openai_messages_flattens_d1_system_list() -> None:
    """TC_D1_openai: _openai_messages() correctly flattens a D1-style system list.

    When callers pass a list with cache_control markers (Anthropic D1 structure),
    the OpenAI path must flatten to a plain string system message with no
    cache_control artifacts in the content.
    """
    d1_system = [
        {"type": "text", "text": "diff content", "cache_control": _CACHE_CONTROL_1H},
        {"type": "text", "text": "agent instructions"},
    ]
    messages = [{"role": "user", "content": "review this"}]
    result = _openai_messages(messages, d1_system)

    assert result[0]["role"] == "system"
    system_content = result[0]["content"]
    assert isinstance(system_content, str), "system content must be a plain string for OpenAI"
    assert "diff content" in system_content
    assert "agent instructions" in system_content
    assert "cache_control" not in system_content
    assert "ephemeral" not in system_content

# ---------------------------------------------------------------------------
# REVUE-154: MetricsCollector integration
# ---------------------------------------------------------------------------


@patch("revue_core.core.ai_client.anthropic.Anthropic")
def test_anthropic_client_records_usage_after_complete(mock_anthropic_cls: MagicMock) -> None:
    """AnthropicClient records MetricsEvent after complete() using token data from usage."""

    def _test():
        from revue_core.core.metrics import CapturingMetricsCollector, MetricsEvent
        from revue_core.core.ai_client import CompletionResult, TokenUsage

        # Setup: Mock Anthropic response with usage data
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(text="response text")]
        mock_resp.usage.input_tokens = 100
        mock_resp.usage.output_tokens = 50
        mock_resp.usage.cache_creation_input_tokens = 0
        mock_resp.usage.cache_read_input_tokens = 0

        mock_anthropic_instance = MagicMock()
        mock_anthropic_instance.messages.create.return_value = mock_resp
        mock_anthropic_cls.return_value = mock_anthropic_instance

        # Create client with metrics collector
        collector = CapturingMetricsCollector()
        config = _make_config(provider="anthropic")
        client = AnthropicClient(config, metrics=collector)

        # Call complete()
        result = client.complete(
            messages=[{"role": "user", "content": "test"}],
            system="test system",
        )

        # Verify result is CompletionResult with text
        assert isinstance(result, CompletionResult)
        assert result.text == "response text"

        # Verify metrics event was recorded
        assert len(collector.events) == 1
        event = collector.events[0]
        assert event.event_type == "agent_call"
        assert event.provider == "anthropic"
        assert event.input_tokens == 100
        assert event.output_tokens == 50
        assert event.cache_creation_tokens == 0
        assert event.cache_read_tokens == 0
        # agent_name must flow through — this is the AC5 schema contract
        assert event.agent_name is None  # not passed → None

    _test()


@patch("revue_core.core.ai_client.anthropic.Anthropic")
def test_anthropic_client_records_agent_name_when_passed(mock_anthropic_cls: MagicMock) -> None:
    """agent_name kwarg is stored in the MetricsEvent — drives per-agent breakdown in metrics.jsonl."""

    def _test():
        from revue_core.core.metrics import CapturingMetricsCollector
        from revue_core.core.ai_client import AnthropicClient

        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(text="ok")]
        mock_resp.usage.input_tokens = 10
        mock_resp.usage.output_tokens = 5
        mock_resp.usage.cache_creation_input_tokens = 0
        mock_resp.usage.cache_read_input_tokens = 0
        mock_anthropic_cls.return_value.messages.create.return_value = mock_resp

        collector = CapturingMetricsCollector()
        client = AnthropicClient(_make_config(provider="anthropic"), metrics=collector)

        client.complete([{"role": "user", "content": "x"}], agent_name="kai")

        assert len(collector.events) == 1
        assert collector.events[0].agent_name == "kai"

    _test()


# ---------------------------------------------------------------------------
# REVUE-160: TokenUsage validation + consistency of cached_tokens extraction
# ---------------------------------------------------------------------------

def test_extract_cached_tokens_raises_on_negative_api_value() -> None:
    """_extract_cached_tokens raises ValueError when the API returns negative cached_tokens."""
    from revue_core.core.ai_client import _extract_cached_tokens
    mock_usage = MagicMock()
    mock_usage.prompt_tokens_details.cached_tokens = -1
    with pytest.raises(ValueError, match="negative cached_tokens"):
        _extract_cached_tokens(mock_usage)


@patch("revue_core.core.ai_client.openai.OpenAI")
def test_openai_warns_when_usage_is_none(mock_openai_cls: MagicMock) -> None:
    """OpenAIClient logs a warning when the API response omits the usage block."""
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = "ok"
    mock_resp.usage = None
    mock_openai_cls.return_value.chat.completions.create.return_value = mock_resp

    config = _make_config(provider="openai")
    client = OpenAIClient(config)

    with patch("revue_core.core.log.Log.nova") as mock_log:
        result = client.complete([{"role": "user", "content": "test"}])
        assert result.text == "ok"
        assert result.usage.input_tokens == 0
        mock_log.warning.assert_called_once()
        assert "missing usage" in mock_log.warning.call_args[0][0]


def test_token_usage_rejects_negative_input_tokens() -> None:
    """TokenUsage raises ValueError naming the failing field."""
    with pytest.raises(ValueError, match="input_tokens.*non-negative"):
        TokenUsage(input_tokens=-1)


def test_token_usage_rejects_negative_output_tokens() -> None:
    """TokenUsage raises ValueError naming the failing field."""
    with pytest.raises(ValueError, match="output_tokens.*non-negative"):
        TokenUsage(output_tokens=-1)


def test_token_usage_rejects_negative_cache_creation() -> None:
    """TokenUsage raises ValueError naming the failing field."""
    with pytest.raises(ValueError, match="cache_creation_input_tokens.*non-negative"):
        TokenUsage(cache_creation_input_tokens=-1)


def test_token_usage_rejects_negative_cache_read() -> None:
    """TokenUsage raises ValueError naming the failing field."""
    with pytest.raises(ValueError, match="cache_read_input_tokens.*non-negative"):
        TokenUsage(cache_read_input_tokens=-1)


def test_token_usage_accepts_zero_values() -> None:
    """TokenUsage accepts zero for all fields (boundary condition)."""
    usage = TokenUsage(
        input_tokens=0,
        output_tokens=0,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )
    assert usage.input_tokens == 0
    assert usage.output_tokens == 0
    assert usage.cache_creation_input_tokens == 0
    assert usage.cache_read_input_tokens == 0


def test_token_usage_accepts_positive_values() -> None:
    """TokenUsage accepts positive values for all fields."""
    usage = TokenUsage(
        input_tokens=100,
        output_tokens=50,
        cache_creation_input_tokens=25,
        cache_read_input_tokens=10,
    )
    assert usage.input_tokens == 100
    assert usage.output_tokens == 50
    assert usage.cache_creation_input_tokens == 25
    assert usage.cache_read_input_tokens == 10


@patch("revue_core.core.ai_client.openai.AzureOpenAI")
def test_azure_logs_cached_tokens(mock_azure_cls: MagicMock) -> None:
    """AzureOpenAIClient extracts and logs cached_tokens from prompt_tokens_details."""
    mock_details = MagicMock(cached_tokens=512)
    mock_usage = MagicMock(
        prompt_tokens=2000,
        completion_tokens=100,
        prompt_tokens_details=mock_details,
    )
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = "result"
    mock_resp.usage = mock_usage
    mock_azure_cls.return_value.chat.completions.create.return_value = mock_resp

    config = _make_config(
        provider="azure",
        azure_endpoint="https://myazure.openai.azure.com",
        azure_deployment="gpt-4o-deploy",
    )
    client = AzureOpenAIClient(config)

    with patch("revue_core.core.log.Log.nova") as mock_log:
        result = client.complete([{"role": "user", "content": "test"}])
        assert result.text == "result"
        assert result.usage.cache_read_input_tokens == 512
        mock_log.debug.assert_called_once()
        log_args = mock_log.debug.call_args[0]
        assert "cached" in log_args[0]
        assert 512 in log_args


@patch("revue_core.core.ai_client.openai.OpenAI")
def test_openrouter_logs_cached_tokens(mock_openai_cls: MagicMock) -> None:
    """OpenRouterClient extracts and logs cached_tokens from prompt_tokens_details."""
    mock_details = MagicMock(cached_tokens=768)
    mock_usage = MagicMock(
        prompt_tokens=1500,
        completion_tokens=200,
        prompt_tokens_details=mock_details,
    )
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = "result"
    mock_resp.usage = mock_usage
    mock_openai_cls.return_value.chat.completions.create.return_value = mock_resp

    config = _make_config(provider="openrouter")
    client = OpenRouterClient(config)

    with patch("revue_core.core.log.Log.nova") as mock_log:
        result = client.complete([{"role": "user", "content": "test"}])
        assert result.text == "result"
        assert result.usage.cache_read_input_tokens == 768
        mock_log.debug.assert_called_once()
        log_args = mock_log.debug.call_args[0]
        assert "cached" in log_args[0]
        assert 768 in log_args


@patch("revue_core.core.ai_client.openai.OpenAI")
def test_custom_logs_cached_tokens(mock_openai_cls: MagicMock) -> None:
    """CustomGatewayClient extracts and logs cached_tokens from prompt_tokens_details."""
    mock_details = MagicMock(cached_tokens=256)
    mock_usage = MagicMock(
        prompt_tokens=1000,
        completion_tokens=150,
        prompt_tokens_details=mock_details,
    )
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = "result"
    mock_resp.usage = mock_usage
    mock_openai_cls.return_value.chat.completions.create.return_value = mock_resp

    config = _make_config(provider="custom", base_url="https://gateway.internal/v1")
    client = CustomGatewayClient(config)

    with patch("revue_core.core.log.Log.nova") as mock_log:
        result = client.complete([{"role": "user", "content": "test"}])
        assert result.text == "result"
        assert result.usage.cache_read_input_tokens == 256
        mock_log.debug.assert_called_once()
        log_args = mock_log.debug.call_args[0]
        assert "cached" in log_args[0]
        assert 256 in log_args


@pytest.mark.parametrize("client_cls,mock_target,extra_config", [
    (OpenAIClient, "revue_core.core.ai_client.openai.OpenAI", {}),
    (AzureOpenAIClient, "revue_core.core.ai_client.openai.AzureOpenAI",
     {"azure_endpoint": "https://x.openai.azure.com", "azure_deployment": "gpt-4o"}),
    (OpenRouterClient, "revue_core.core.ai_client.openai.OpenAI", {}),
    (CustomGatewayClient, "revue_core.core.ai_client.openai.OpenAI",
     {"base_url": "https://gw.internal/v1"}),
])
def test_openai_compatible_cache_creation_tokens_always_zero(
    client_cls: type, mock_target: str, extra_config: dict
) -> None:
    """OpenAI-compatible clients never populate cache_creation_input_tokens.

    OpenAI's API has no equivalent of Anthropic's cache-creation cost; only
    cache reads are reported. This test pins the zero so future changes to
    extraction logic don't accidentally produce non-zero values.
    """
    with patch(mock_target) as mock_cls:
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "ok"
        mock_resp.usage.prompt_tokens = 100
        mock_resp.usage.completion_tokens = 20
        mock_resp.usage.prompt_tokens_details.cached_tokens = 50
        mock_cls.return_value.chat.completions.create.return_value = mock_resp

        config = _make_config(**extra_config)
        client = client_cls(config)
        result = client.complete([{"role": "user", "content": "test"}])

        assert result.usage.cache_creation_input_tokens == 0
        assert result.usage.cache_read_input_tokens == 50


def test_all_call_sites_extract_text() -> None:
    """Integration test: verify that call sites that use CompletionResult correctly extract .text.

    Specifically tests run_shared_analysis(), which calls client.complete() and extracts
    .text from the result. Also verifies the CompletionResult protocol itself.
    """
    from revue_core.core.shared_analysis import run_shared_analysis, SharedAnalysisResult
    from revue_core.core.models import FileChange

    # Helper: create a mock AIClient that returns CompletionResult (real protocol)
    def _mock_client(text: str) -> MagicMock:
        client = MagicMock()
        client.complete.return_value = CompletionResult(text=text, usage=TokenUsage())
        return client

    def _file_change(filename: str) -> FileChange:
        return FileChange(
            file_path=filename,
            change_type="modified",
            additions=10,
            deletions=5,
            diff="--- a/test\n+++ b/test\n@@ -1 +1 @@\n-old\n+new",
        )

    # --- Call site 1: run_shared_analysis() ---
    # run_shared_analysis calls client.complete() and extracts .text for JSON parsing
    changes = [_file_change("test.py")]
    json_response = '{"risk_areas": [], "suggested_agents": ["kai"], "summary": "test"}'
    client = _mock_client(json_response)
    shared_result = run_shared_analysis(changes, client)
    # Verify the real function extracted .text and parsed it correctly
    assert isinstance(shared_result, SharedAnalysisResult)
    assert shared_result.success
    assert shared_result.summary == "test"
    # Verify client.complete() was actually called with .text extracted from result
    assert client.complete.called

    # --- Call site 2: CompletionResult protocol ---
    # Verify CompletionResult.text is accessible as an attribute (not a method or property)
    result = CompletionResult(text="# Findings\nCode review comment", usage=TokenUsage())
    assert isinstance(result.text, str)
    assert result.text == "# Findings\nCode review comment"
    assert isinstance(result.usage, TokenUsage)


# ---------------------------------------------------------------------------
# REVUE-241 P1: complete_with_tools must use _with_retry on the rate-limit path
# ---------------------------------------------------------------------------

@patch("revue_core.core.ai_client.time.sleep", return_value=None)
def test_anthropic_complete_with_tools_retries_on_rate_limit(mock_sleep: MagicMock) -> None:
    """complete_with_tools must wrap the tool loop in _with_retry so a single
    transient 429 does not collapse a reviewer agent that previously got 3 attempts."""
    import anthropic as _anthropic
    from revue_core.core.tool_loop import DEFAULT_MAX_TOOL_ITERATIONS  # noqa: F401

    config = _make_config(provider="anthropic", retry_on_rate_limit=True)
    client = AnthropicClient(config)

    call_count = 0

    def _fake_loop(*args: Any, **kwargs: Any) -> CompletionResult:
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            mock_response = MagicMock(status_code=429)
            mock_response.headers.get.return_value = None
            raise _anthropic.RateLimitError(
                message="rate limited",
                response=mock_response,
                body=None,
            )
        return CompletionResult(text="ok", usage=TokenUsage())

    with patch("revue_core.core.tool_loop.anthropic_tool_loop", side_effect=_fake_loop):
        result = client.complete_with_tools(
            [{"role": "user", "content": "x"}],
            tools=[],
            tool_handlers={},
        )

    assert result.text == "ok"
    assert call_count == 2, "complete_with_tools should retry on 429"


@patch("revue_core.core.ai_client.time.sleep", return_value=None)
def test_openai_complete_with_tools_retries_on_rate_limit(mock_sleep: MagicMock) -> None:
    """OpenAIClient.complete_with_tools must also retry — same contract as Anthropic path."""
    config = _make_config(provider="openai", retry_on_rate_limit=True)
    client = OpenAIClient(config)

    call_count = 0

    def _fake_loop(*args: Any, **kwargs: Any) -> CompletionResult:
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            mock_response = MagicMock(status_code=429)
            mock_response.headers.get.return_value = None
            raise openai.RateLimitError(
                message="rate limited",
                response=mock_response,
                body=None,
            )
        return CompletionResult(text="ok", usage=TokenUsage())

    with patch("revue_core.core.tool_loop.openai_tool_loop", side_effect=_fake_loop):
        result = client.complete_with_tools(
            [{"role": "user", "content": "x"}],
            tools=[],
            tool_handlers={},
        )

    assert result.text == "ok"
    assert call_count == 2, "complete_with_tools should retry on 429"


def test_complete_with_tools_fail_fast_when_retry_disabled() -> None:
    """When retry_on_rate_limit=False, complete_with_tools fails fast on the first 429."""
    import anthropic as _anthropic

    config = _make_config(provider="anthropic", retry_on_rate_limit=False)
    client = AnthropicClient(config)

    def _fake_loop(*args: Any, **kwargs: Any) -> CompletionResult:
        mock_response = MagicMock(status_code=429)
        mock_response.headers.get.return_value = None
        raise _anthropic.RateLimitError(
            message="rate limited", response=mock_response, body=None,
        )

    with patch("revue_core.core.tool_loop.anthropic_tool_loop", side_effect=_fake_loop):
        with pytest.raises(_anthropic.RateLimitError):
            client.complete_with_tools(
                [{"role": "user", "content": "x"}],
                tools=[],
                tool_handlers={},
            )


# ---------------------------------------------------------------------------
# REVUE-241 P2: anthropic_tool_loop must record MetricsEvent for per-agent telemetry
# ---------------------------------------------------------------------------

def test_anthropic_complete_with_tools_records_metrics_event() -> None:
    """When AnthropicClient.complete_with_tools is invoked, a MetricsEvent must
    be recorded — without this, reviewer-agent token usage vanishes from
    metrics.jsonl whenever the tool-use path is active."""
    from revue_core.core.metrics import CapturingMetricsCollector
    from types import SimpleNamespace

    collector = CapturingMetricsCollector()
    config = _make_config(provider="anthropic", retry_on_rate_limit=False)
    client = AnthropicClient(config, metrics=collector)

    # Patch the underlying SDK so the loop runs end-to-end without HTTP.
    fake_text_block = SimpleNamespace(type="text", text="done")
    fake_usage = SimpleNamespace(
        input_tokens=100,
        output_tokens=20,
        cache_creation_input_tokens=50,
        cache_read_input_tokens=30,
    )
    fake_resp = SimpleNamespace(
        content=[fake_text_block],
        stop_reason="end_turn",
        usage=fake_usage,
    )

    with patch.object(client._client.messages, "create", return_value=fake_resp):
        client.complete_with_tools(
            [{"role": "user", "content": "review"}],
            tools=[],
            tool_handlers={},
            agent_name="maya",
        )

    assert len(collector.events) == 1, "MetricsEvent should be recorded for tool-use call"
    event = collector.events[0]
    assert event.event_type == "agent_call"
    assert event.agent_name == "maya"
    assert event.provider == "anthropic"
    assert event.input_tokens == 100
    assert event.output_tokens == 20
    assert event.cache_creation_tokens == 50
    assert event.cache_read_tokens == 30


# ---------------------------------------------------------------------------
# REVUE-241 P2 (OpenAI path): MetricsEvent recording on the tool-use path
# ---------------------------------------------------------------------------

@patch("revue_core.core.ai_client.openai.OpenAI")
def test_openai_complete_with_tools_records_metrics_event(mock_openai_cls: MagicMock) -> None:
    """OpenAIClient.complete_with_tools must record a MetricsEvent — without
    this, every tool-use review on the OpenAI path leaves no trace in
    metrics.jsonl while the Anthropic path persists usage correctly.

    Asserts provider=="openai" so OpenAI / Azure / OpenRouter / Custom remain
    distinguishable in metrics rather than collapsing under a shared tag.
    """
    from revue_core.core.metrics import CapturingMetricsCollector
    from types import SimpleNamespace

    collector = CapturingMetricsCollector()
    config = _make_config(provider="openai", retry_on_rate_limit=False)
    client = OpenAIClient(config, metrics=collector)

    fake_resp = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="done", tool_calls=[]))],
        usage=SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=120,
            prompt_tokens_details=SimpleNamespace(cached_tokens=30),
        ),
    )

    with patch.object(client._client.chat.completions, "create", return_value=fake_resp):
        client.complete_with_tools(
            [{"role": "user", "content": "review"}],
            tools=[],
            tool_handlers={},
            agent_name="maya",
        )

    assert len(collector.events) == 1, (
        "OpenAIClient.complete_with_tools must record a MetricsEvent — "
        "reviewer-agent token usage vanishes from metrics.jsonl otherwise"
    )
    event = collector.events[0]
    assert event.event_type == "agent_call"
    assert event.agent_name == "maya"
    assert event.provider == "openai"
    assert event.input_tokens == 100
    assert event.output_tokens == 20
    assert event.cache_read_tokens == 30


@patch("revue_core.core.ai_client.openai.OpenAI")
def test_openrouter_complete_with_tools_records_provider_label(
    mock_openai_cls: MagicMock,
) -> None:
    """OpenRouterClient must tag MetricsEvent with provider="openrouter" so
    cost analytics can distinguish OpenRouter spend from direct-OpenAI spend
    on the same underlying SDK."""
    from revue_core.core.metrics import CapturingMetricsCollector
    from types import SimpleNamespace

    collector = CapturingMetricsCollector()
    config = _make_config(provider="openrouter", retry_on_rate_limit=False)
    client = OpenRouterClient(config, metrics=collector)

    fake_resp = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="done", tool_calls=[]))],
        usage=SimpleNamespace(
            prompt_tokens=42, completion_tokens=17, total_tokens=59,
            prompt_tokens_details=None,
        ),
    )

    with patch.object(client._client.chat.completions, "create", return_value=fake_resp):
        client.complete_with_tools(
            [{"role": "user", "content": "review"}],
            tools=[],
            tool_handlers={},
            agent_name="leo",
        )

    assert len(collector.events) == 1
    assert collector.events[0].provider == "openrouter"
    assert collector.events[0].agent_name == "leo"


def test_create_ai_client_forwards_metrics_to_openai_compatible_providers() -> None:
    """``create_ai_client`` must forward the metrics collector to every
    OpenAI-compatible provider — not only Anthropic. The factory is the only
    place that knows whether the caller supplied a collector; downstream
    classes can't bootstrap one themselves.
    """
    from revue_core.core.ai_client import (
        _METRICS_AWARE_PROVIDERS,
        create_ai_client,
    )
    from revue_core.core.metrics import CapturingMetricsCollector

    # The frozenset is the contract — if a provider is missing here, the
    # factory will silently drop the collector at construction time.
    for required in ("openai", "azure", "openrouter", "custom", "anthropic"):
        assert required in _METRICS_AWARE_PROVIDERS, (
            f"provider {required!r} missing from _METRICS_AWARE_PROVIDERS — "
            f"create_ai_client will drop the metrics collector for this provider"
        )

    # Spot-check: the OpenAI client actually receives the collector through
    # the factory. Patch the SDK so no live HTTP happens during construction.
    collector = CapturingMetricsCollector()
    config = _make_config(provider="openai", retry_on_rate_limit=False)
    with patch("revue_core.core.ai_client.openai.OpenAI"):
        client = create_ai_client(config, metrics=collector)
    assert client._metrics is collector, (  # type: ignore[attr-defined]
        "create_ai_client did not forward the metrics collector to OpenAIClient"
    )
