#!/usr/bin/env python3
"""
AI Client protocol, concrete implementations, and provider factory.

Supports Anthropic, OpenAI, Azure OpenAI, OpenRouter, and custom gateways.
All clients conform to the AIClient protocol for interchangeable use.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Protocol

_log = logging.getLogger(__name__)

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
        system: "str | list[dict[str, Any]] | None" = None,
    ) -> str: ...


# ---------------------------------------------------------------------------
# Retry helper
# ---------------------------------------------------------------------------

def _extract_rate_limit_message(exc: Exception) -> str:
    """Pull the human-readable message out of a RateLimitError."""
    try:
        # anthropic / openai SDK errors expose .message or .body
        return str(exc.message)  # type: ignore[union-attr]
    except AttributeError:
        pass
    try:
        body = exc.body  # type: ignore[union-attr]
        if isinstance(body, dict):
            return body.get("error", {}).get("message", str(exc))
    except AttributeError:
        pass
    return str(exc)


def _rate_limit_wait(exc: Exception, base_delay: float, attempt: int) -> float:
    """Return seconds to wait before next retry, preferring the retry-after header."""
    try:
        headers = exc.response.headers  # type: ignore[union-attr]
        val = headers.get("retry-after") or headers.get("x-ratelimit-reset-tokens")
        if val:
            return float(val)
    except Exception:
        pass
    return base_delay * (2 ** attempt)


def _with_retry(
    fn: Callable[[], str],
    max_attempts: int = 1,
    base_delay: float = 60.0,
) -> str:
    """Call *fn*, surfacing rate-limit errors clearly.

    By default (``max_attempts=1``) this is fail-fast: a 429 is printed
    in a readable format and re-raised immediately so the pipeline log
    shows exactly why the agent failed.

    When ``max_attempts > 1`` (opt-in via ``retry_on_rate_limit: true``
    in .revue.yml) it retries with exponential back-off, preferring the
    ``retry-after`` response header.  60 s base delay covers the Anthropic
    30 k TPM per-minute reset window.

    TimeoutError is never caught and propagates immediately.
    """
    for attempt in range(max_attempts):
        try:
            return fn()
        except (openai.RateLimitError, anthropic.RateLimitError) as exc:
            msg = _extract_rate_limit_message(exc)
            if attempt == max_attempts - 1:
                print(
                    f"\n[revue] ❌ RATE LIMIT ERROR — agent failed.\n"
                    f"  Reason : {msg}\n"
                    f"  Action : reduce parallel agents, increase your API plan, or set\n"
                    f"           retry_on_rate_limit: true in .revue.yml to retry with backoff.\n",
                    flush=True,
                )
                raise
            wait = _rate_limit_wait(exc, base_delay, attempt)
            print(f"[revue] Rate limit hit — retrying in {wait:.0f}s (attempt {attempt + 1}/{max_attempts}): {msg}", flush=True)
            time.sleep(wait)
    raise RuntimeError("_with_retry: exhausted attempts")  # pragma: no cover


# ---------------------------------------------------------------------------
# Concrete clients
# ---------------------------------------------------------------------------

def _openai_messages(
    messages: list[dict[str, Any]],
    system: "str | list[dict[str, Any]] | None",
) -> list[dict[str, Any]]:
    """Prepend a system message and strip Anthropic-specific cache_control fields.

    OpenAI-compatible APIs don't accept ``cache_control`` inside content
    blocks.  We remove the key before sending so the SDK doesn't reject it.
    """
    out: list[dict[str, Any]] = []
    if system is not None:
        text = (
            " ".join(b.get("text", "") for b in system if isinstance(b, dict))
            if isinstance(system, list)
            else system
        )
        out.append({"role": "system", "content": text})
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            # Strip cache_control from every content block
            clean_blocks = [
                {k: v for k, v in block.items() if k != "cache_control"}
                for block in content
            ]
            # Flatten single-block arrays to a plain string for compatibility
            if len(clean_blocks) == 1 and clean_blocks[0].get("type") == "text":
                out.append({**msg, "content": clean_blocks[0]["text"]})
            else:
                out.append({**msg, "content": clean_blocks})
        else:
            out.append(msg)
    return out


class OpenAIClient:
    """OpenAI-compatible client (api.openai.com or custom base_url)."""

    def __init__(self, config: AIConfig) -> None:
        self._model = config.model
        self._max_attempts = 3 if config.retry_on_rate_limit else 1
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
        system: "str | list[dict[str, Any]] | None" = None,
    ) -> str:
        def _call() -> str:
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=_openai_messages(messages, system),
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return resp.choices[0].message.content or ""

        return _with_retry(_call, max_attempts=self._max_attempts)


class AnthropicClient:
    """Native Anthropic SDK client.

    Supports Anthropic Prompt Caching via the ``system`` parameter.  Pass a
    list of content blocks (with ``cache_control: {"type": "ephemeral"}`` on
    large static blocks) to cache system prompts and diff content between
    calls.  Cached token reads count at 10% of the normal TPM rate, which
    dramatically reduces rate-limit pressure when the same diff is reviewed
    by multiple parallel agents.
    """

    def __init__(self, config: AIConfig) -> None:
        self._model = config.model
        self._max_attempts = 3 if config.retry_on_rate_limit else 1
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
        system: "str | list[dict[str, Any]] | None" = None,
    ) -> str:
        def _call() -> str:
            kwargs: dict[str, Any] = {
                "model": self._model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                # Top-level cache_control: SDK ≥ 0.87.0 supports this as a native
                # parameter on messages.create(). The SDK auto-determines the last
                # cacheable content block — no per-block placement needed.
                # Minimum prefix: 1,024 tokens (Sonnet 4.6) / 4,096 (Haiku/Opus).
                # For small diffs where the full request is below that threshold,
                # Anthropic silently skips the cache write — this is expected.
                "cache_control": {"type": "ephemeral"},
            }
            if system is not None:
                kwargs["system"] = system
            resp = self._client.messages.create(**kwargs)
            usage = resp.usage
            _log.debug(
                "[anthropic] cache_creation=%s cache_read=%s input=%s output=%s",
                getattr(usage, "cache_creation_input_tokens", 0),
                getattr(usage, "cache_read_input_tokens", 0),
                usage.input_tokens,
                usage.output_tokens,
            )
            return resp.content[0].text  # type: ignore[union-attr]

        return _with_retry(_call, max_attempts=self._max_attempts)


class AzureOpenAIClient:
    """Azure OpenAI Service client."""

    def __init__(self, config: AIConfig) -> None:
        self._model = config.azure_deployment or config.model
        self._max_attempts = 3 if config.retry_on_rate_limit else 1
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
        system: "str | list[dict[str, Any]] | None" = None,
    ) -> str:
        def _call() -> str:
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=_openai_messages(messages, system),
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return resp.choices[0].message.content or ""

        return _with_retry(_call, max_attempts=self._max_attempts)


class OpenRouterClient:
    """OpenRouter (openrouter.ai) client — OpenAI-compatible with extra headers."""

    def __init__(self, config: AIConfig) -> None:
        self._model = config.model
        self._max_attempts = 3 if config.retry_on_rate_limit else 1
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
        system: "str | list[dict[str, Any]] | None" = None,
    ) -> str:
        def _call() -> str:
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=_openai_messages(messages, system),
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return resp.choices[0].message.content or ""

        return _with_retry(_call, max_attempts=self._max_attempts)


class CustomGatewayClient:
    """Generic OpenAI-compatible gateway at a user-supplied base_url."""

    def __init__(self, config: AIConfig) -> None:
        self._model = config.model
        self._max_attempts = 3 if config.retry_on_rate_limit else 1
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
        system: "str | list[dict[str, Any]] | None" = None,
    ) -> str:
        def _call() -> str:
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=_openai_messages(messages, system),
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return resp.choices[0].message.content or ""

        return _with_retry(_call, max_attempts=self._max_attempts)


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
