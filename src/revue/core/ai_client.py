#!/usr/bin/env python3
"""
AI Client protocol, concrete implementations, and provider factory.

Supports Anthropic, OpenAI, Azure OpenAI, OpenRouter, and custom gateways.
All clients conform to the AIClient protocol for interchangeable use.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Protocol

import anthropic
import httpx
import openai

from .ai_config import AIConfig

# Shared timeout used by all clients
_TIMEOUT = httpx.Timeout(connect=60.0, read=600.0, write=600.0, pool=600.0)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

class AIClient(Protocol):
    """Protocol that all AI provider clients must satisfy."""

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> str: ...


# ---------------------------------------------------------------------------
# Retry helper
# ---------------------------------------------------------------------------

def _with_retry(
    fn: Callable[[], str],
    max_attempts: int = 3,
    base_delay: float = 1.0,
) -> str:
    """Call *fn* with exponential back-off on 429 / RateLimitError.

    TimeoutError is **not** caught and propagates immediately.
    """
    for attempt in range(max_attempts):
        try:
            return fn()
        except (openai.RateLimitError, anthropic.RateLimitError):
            if attempt == max_attempts - 1:
                raise
            time.sleep(base_delay * (2 ** attempt))
    # Unreachable, but keeps mypy happy
    raise RuntimeError("_with_retry: exhausted attempts")  # pragma: no cover


# ---------------------------------------------------------------------------
# Concrete clients
# ---------------------------------------------------------------------------

class OpenAIClient:
    """OpenAI-compatible client (api.openai.com or custom base_url)."""

    def __init__(self, config: AIConfig) -> None:
        self._model = config.model
        self._client = openai.OpenAI(
            api_key=config.resolve_api_key(),
            base_url=config.base_url or None,
            timeout=_TIMEOUT,
        )

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> str:
        def _call() -> str:
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return resp.choices[0].message.content or ""

        return _with_retry(_call)


class AnthropicClient:
    """Native Anthropic SDK client."""

    def __init__(self, config: AIConfig) -> None:
        self._model = config.model
        self._client = anthropic.Anthropic(
            api_key=config.resolve_api_key(),
            timeout=_TIMEOUT,
        )

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> str:
        def _call() -> str:
            resp = self._client.messages.create(
                model=self._model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return resp.content[0].text  # type: ignore[union-attr]

        return _with_retry(_call)


class AzureOpenAIClient:
    """Azure OpenAI Service client."""

    def __init__(self, config: AIConfig) -> None:
        self._model = config.azure_deployment or config.model
        self._client = openai.AzureOpenAI(
            api_key=config.resolve_api_key(),
            azure_endpoint=config.azure_endpoint,
            api_version=config.azure_api_version,
            timeout=_TIMEOUT,
        )

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> str:
        def _call() -> str:
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return resp.choices[0].message.content or ""

        return _with_retry(_call)


class OpenRouterClient:
    """OpenRouter (openrouter.ai) client — OpenAI-compatible with extra headers."""

    def __init__(self, config: AIConfig) -> None:
        self._model = config.model
        self._client = openai.OpenAI(
            api_key=config.resolve_api_key(),
            base_url="https://openrouter.ai/api/v1",
            timeout=_TIMEOUT,
            default_headers={
                "HTTP-Referer": "https://revue.io",
                "X-Title": "Revue AI Code Review",
            },
        )

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> str:
        def _call() -> str:
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return resp.choices[0].message.content or ""

        return _with_retry(_call)


class CustomGatewayClient:
    """Generic OpenAI-compatible gateway at a user-supplied base_url."""

    def __init__(self, config: AIConfig) -> None:
        self._model = config.model
        self._client = openai.OpenAI(
            api_key=config.resolve_api_key(),
            base_url=config.base_url,
            timeout=_TIMEOUT,
        )

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> str:
        def _call() -> str:
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return resp.choices[0].message.content or ""

        return _with_retry(_call)


# ---------------------------------------------------------------------------
# Factory (OCP: registry-based — extend via register_provider, no edits needed)
# ---------------------------------------------------------------------------

_PROVIDER_REGISTRY: dict[str, type] = {
    "openai": OpenAIClient,
    "anthropic": AnthropicClient,
    "azure": AzureOpenAIClient,
    "openrouter": OpenRouterClient,
    "custom": CustomGatewayClient,
}


def register_provider(name: str, cls: type) -> None:
    """Register a new provider class. Enables extension without modifying this file."""
    _PROVIDER_REGISTRY[name] = cls


def create_ai_client(config: AIConfig) -> AIClient:
    """Instantiate the correct AI client based on config.provider."""
    cls = _PROVIDER_REGISTRY.get(config.provider)
    if cls is None:
        raise ValueError(
            f"Unknown provider: {config.provider!r}. "
            f"Known providers: {sorted(_PROVIDER_REGISTRY.keys())}"
        )
    return cls(config)
