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
            raise openai.RateLimitError(
                message="rate limited",
                response=MagicMock(status_code=429),
                body=None,
            )
        return "success"

    result = _with_retry(_flaky)
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
