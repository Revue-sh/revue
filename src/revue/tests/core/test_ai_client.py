#!/usr/bin/env python3
"""Tests for AIClient protocol, concrete implementations, and factory."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import openai
import pytest

from revue.core.ai_client import (
    AnthropicClient,
    AzureOpenAIClient,
    CustomGatewayClient,
    OpenAIClient,
    OpenRouterClient,
    _with_retry,
    create_ai_client,
    register_provider,
)
from revue.core.ai_config import AIConfig


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

@patch("revue.core.ai_client.openai.OpenAI")
def test_openai_client_instantiates(mock_openai_cls: MagicMock) -> None:
    config = _make_config(provider="openai")
    client = OpenAIClient(config)
    mock_openai_cls.assert_called_once()
    assert client._model == config.model


@patch("revue.core.ai_client.anthropic.Anthropic")
def test_anthropic_client_instantiates(mock_anthropic_cls: MagicMock) -> None:
    config = _make_config(provider="anthropic")
    client = AnthropicClient(config)
    mock_anthropic_cls.assert_called_once()
    assert client._model == config.model


@patch("revue.core.ai_client.openai.AzureOpenAI")
def test_azure_client_instantiates(mock_azure_cls: MagicMock) -> None:
    config = _make_config(
        provider="azure",
        azure_endpoint="https://myazure.openai.azure.com",
        azure_deployment="gpt-4o-deploy",
    )
    client = AzureOpenAIClient(config)
    mock_azure_cls.assert_called_once()
    assert client._model == "gpt-4o-deploy"


@patch("revue.core.ai_client.openai.OpenAI")
def test_openrouter_client_instantiates(mock_openai_cls: MagicMock) -> None:
    config = _make_config(provider="openrouter")
    client = OpenRouterClient(config)
    mock_openai_cls.assert_called_once()
    call_kwargs = mock_openai_cls.call_args[1]
    assert call_kwargs["base_url"] == "https://openrouter.ai/api/v1"
    assert "HTTP-Referer" in call_kwargs["default_headers"]
    assert client._model == config.model


@patch("revue.core.ai_client.openai.OpenAI")
def test_custom_gateway_client_instantiates(mock_openai_cls: MagicMock) -> None:
    config = _make_config(provider="custom", base_url="https://my-gateway.internal/v1")
    client = CustomGatewayClient(config)
    mock_openai_cls.assert_called_once()
    assert client._model == config.model


# ---------------------------------------------------------------------------
# Factory routing tests (6-11)
# ---------------------------------------------------------------------------

@patch("revue.core.ai_client.openai.OpenAI")
def test_factory_routes_openai(mock_cls: MagicMock) -> None:
    config = _make_config(provider="openai")
    client = create_ai_client(config)
    assert isinstance(client, OpenAIClient)


@patch("revue.core.ai_client.anthropic.Anthropic")
def test_factory_routes_anthropic(mock_cls: MagicMock) -> None:
    config = _make_config(provider="anthropic")
    client = create_ai_client(config)
    assert isinstance(client, AnthropicClient)


@patch("revue.core.ai_client.openai.AzureOpenAI")
def test_factory_routes_azure(mock_cls: MagicMock) -> None:
    config = _make_config(provider="azure", azure_endpoint="https://x.openai.azure.com")
    client = create_ai_client(config)
    assert isinstance(client, AzureOpenAIClient)


@patch("revue.core.ai_client.openai.OpenAI")
def test_factory_routes_openrouter(mock_cls: MagicMock) -> None:
    config = _make_config(provider="openrouter")
    client = create_ai_client(config)
    assert isinstance(client, OpenRouterClient)


@patch("revue.core.ai_client.openai.OpenAI")
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


@patch("revue.core.ai_client.openai.OpenAI")
def test_register_provider(mock_openai_cls: MagicMock) -> None:
    register_provider("gemini", OpenAIClient)
    config = _make_config(provider="gemini")
    client = create_ai_client(config)
    assert isinstance(client, OpenAIClient)


# ---------------------------------------------------------------------------
# Retry / timeout tests (12-13)
# ---------------------------------------------------------------------------

@patch("revue.core.ai_client.time.sleep", return_value=None)
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

@patch("revue.core.ai_client.openai.OpenAI")
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

    with patch("revue.core.ai_client._log") as mock_log:
        result = client.complete([{"role": "user", "content": "test"}])
        assert result == "result"
        mock_log.debug.assert_called_once()
        log_args = mock_log.debug.call_args[0]
        assert "cached" in log_args[0]
        # 512 should appear in the log args
        assert 512 in log_args


@patch("revue.core.ai_client.openai.OpenAI")
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


@patch("revue.core.ai_client.openai.OpenAI")
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


@patch("revue.core.ai_client.anthropic.Anthropic")
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

@patch("revue.core.ai_client.anthropic.Anthropic")
def test_anthropic_complete_passes_top_level_cache_control(mock_anthropic_cls: MagicMock) -> None:
    """TC1: messages.create() receives cache_control at the top level, not inside content blocks."""
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
    result = client.complete([{"role": "user", "content": "review this diff"}])

    assert result == "result"
    call_kwargs = mock_anthropic_cls.return_value.messages.create.call_args[1]
    # Top-level cache_control must be present
    assert call_kwargs.get("cache_control") == {"type": "ephemeral"}
    # No cache_control inside any content block
    for msg in call_kwargs.get("messages", []):
        content = msg.get("content", "")
        if isinstance(content, list):
            for block in content:
                assert "cache_control" not in block, f"Unexpected cache_control in block: {block}"


@patch("revue.core.ai_client.anthropic.Anthropic")
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
    with patch("revue.core.ai_client._log") as mock_log:
        client.complete([{"role": "user", "content": "test"}])
        mock_log.debug.assert_called_once()
        log_args = mock_log.debug.call_args[0]
        assert "cache_creation" in log_args[0]
        assert "cache_read" in log_args[0]
