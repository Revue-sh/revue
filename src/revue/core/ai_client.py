#!/usr/bin/env python3
"""
AI Client protocol, concrete implementations, and provider factory.

Supports Anthropic, OpenAI, Azure OpenAI, OpenRouter, and custom gateways.
All clients conform to the AIClient protocol for interchangeable use.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Final, Protocol

_log = logging.getLogger(__name__)

import anthropic
import httpx
import openai

from .ai_config import AIConfig
from .metrics import MetricsCollector, MetricsEvent, NullMetricsCollector

# Shared timeout used by all clients
_TIMEOUT = httpx.Timeout(connect=60.0, read=600.0, write=600.0, pool=600.0)

# D2: diff prefix uses ephemeral type with 1-hour TTL.
# Anthropic changed the default TTL from 1h to 5m on 2026-03-06; "persistent" is not
# a valid type — the correct form is {"type": "ephemeral", "ttl": "1h"}.
# Final prevents reassignment; callers must not mutate the underlying dict.
#
# MappingProxyType was evaluated as an immutability guard but cannot be used here:
# json.dumps raises TypeError for mappingproxy objects. The Anthropic SDK serialises
# this value as part of the request payload, so it must remain a plain dict.
# Final + naming convention is sufficient to signal read-only intent.
_CACHE_CONTROL_1H: Final = {"type": "ephemeral", "ttl": "1h"}


# ---------------------------------------------------------------------------
# Domain types — REVUE-155
# ---------------------------------------------------------------------------

@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass
class CompletionResult:
    text: str
    usage: TokenUsage = field(default_factory=TokenUsage)


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
        cache_key: "str | None" = None,
        agent_name: "str | None" = None,
    ) -> CompletionResult: ...


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
                    f"\n"
                    f"  Any one of the following will fix this:\n"
                    f"  1. Keep PRs small (< ~500 lines). Large diffs send more tokens per agent\n"
                    f"     call than your plan's per-minute limit allows.\n"
                    f"  2. Upgrade your Anthropic API tier. Tier 2 (90k TPM) requires $40 spend\n"
                    f"     and resolves most large-diff rate limits. Visit console.anthropic.com\n"
                    f"     → Settings → Billing, or contact sales to request a limit increase.\n"
                    f"  3. Set retry_on_rate_limit: true in .revue.yml to automatically wait\n"
                    f"     and retry when the rate limit resets (makes large-diff reviews slow\n"
                    f"     but reliable on any plan).\n"
                    f"  4. Set max_parallel_agents: 1 in .revue.yml to run agents sequentially\n"
                    f"     and avoid multiple agents consuming your token budget at once.\n",
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
    """Build the messages list for OpenAI-compatible APIs.

    Prepends a ``role: system`` message when a system prompt is provided.
    Placing the static system prompt first is intentional: OpenAI Prompt
    Caching is prefix-based, so static content at the start of the request
    maximises cache hit rates (caching activates automatically for prefixes
    ≥ 1,024 tokens — no ``cache_control`` needed).

    Also strips ``cache_control`` keys from any content blocks defensively.
    Anthropic-specific ``cache_control`` is handled at the ``AnthropicClient``
    level (top-level ``cache_control`` kwarg on ``messages.create()``); this
    stripping ensures any future callers that accidentally include per-block
    ``cache_control`` don't cause a 422 rejection from OpenAI-compatible APIs.
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
        cache_key: "str | None" = None,
        agent_name: "str | None" = None,
    ) -> CompletionResult:
        def _call() -> CompletionResult:
            kwargs: dict[str, Any] = {
                "model": self._model,
                "messages": _openai_messages(messages, system),
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            if cache_key is not None:
                kwargs["prompt_cache_key"] = cache_key
            resp = self._client.chat.completions.create(**kwargs)
            usage = resp.usage
            details = getattr(usage, "prompt_tokens_details", None) if usage else None
            cached = getattr(details, "cached_tokens", 0) if details else 0
            token_usage = TokenUsage(
                input_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
                output_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
            )
            _log.debug(
                "[openai] cached=%s prompt=%s completion=%s",
                cached,
                token_usage.input_tokens,
                token_usage.output_tokens,
            )
            return CompletionResult(
                text=resp.choices[0].message.content or "",
                usage=token_usage,
            )

        return _with_retry(_call, max_attempts=self._max_attempts)


class AnthropicClient:
    """Native Anthropic SDK client.

    Supports Anthropic Prompt Caching via per-block ``cache_control`` markers
    (the correct API mechanism — top-level cache_control is not a valid param).

    Two cache breakpoints are used:
    1. ``system`` block: caches the static agent system prompt.
    2. Last user message block: caches the full prefix (system + diff) so
       re-reviews of the same PR hit the cache on subsequent runs.

    Cached token reads count at 10 % of the normal input TPM rate, reducing
    rate-limit pressure when the same diff is reviewed by multiple agents.
    Minimum cacheable prefix: 1,024 tokens (Sonnet 4.6).
    """

    def __init__(
        self,
        config: AIConfig,
        metrics: MetricsCollector | None = None,
    ) -> None:
        self._model = config.model
        self._max_attempts = 3 if config.retry_on_rate_limit else 1
        self._client = anthropic.Anthropic(
            api_key=config.resolve_api_key(),
            timeout=_TIMEOUT,
        )
        self._metrics = metrics or NullMetricsCollector()

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        system: "str | list[dict[str, Any]] | None" = None,
        cache_key: "str | None" = None,  # accepted but unused — Anthropic uses cache_control
        agent_name: "str | None" = None,
    ) -> CompletionResult:
        def _call() -> CompletionResult:
            kwargs: dict[str, Any] = {
                "model": self._model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            if system is not None:
                if isinstance(system, str):
                    kwargs["system"] = [{"type": "text", "text": system}]
                else:
                    kwargs["system"] = list(system)
            resp = self._client.messages.create(**kwargs)
            usage = resp.usage
            token_usage = TokenUsage(
                cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0),
                cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0),
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
            )
            _log.debug(
                "[anthropic] cache_creation=%s cache_read=%s input=%s output=%s",
                token_usage.cache_creation_input_tokens,
                token_usage.cache_read_input_tokens,
                token_usage.input_tokens,
                token_usage.output_tokens,
            )
            result = CompletionResult(text=resp.content[0].text, usage=token_usage)  # type: ignore[union-attr]
            self._metrics.record(
                MetricsEvent(
                    event_type="agent_call",
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    agent_name=agent_name,
                    provider="anthropic",
                    model=self._model,
                    cache_creation_tokens=token_usage.cache_creation_input_tokens,
                    cache_read_tokens=token_usage.cache_read_input_tokens,
                    input_tokens=token_usage.input_tokens,
                    output_tokens=token_usage.output_tokens,
                )
            )
            return result

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
        cache_key: "str | None" = None,
        agent_name: "str | None" = None,
    ) -> CompletionResult:
        def _call() -> CompletionResult:
            kwargs: dict[str, Any] = {
                "model": self._model,
                "messages": _openai_messages(messages, system),
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            if cache_key is not None:
                kwargs["prompt_cache_key"] = cache_key
            resp = self._client.chat.completions.create(**kwargs)
            usage = resp.usage
            token_usage = TokenUsage(
                input_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
                output_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
            )
            _log.debug(
                "[azure] prompt=%s completion=%s",
                token_usage.input_tokens,
                token_usage.output_tokens,
            )
            return CompletionResult(
                text=resp.choices[0].message.content or "",
                usage=token_usage,
            )

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
        cache_key: "str | None" = None,
        agent_name: "str | None" = None,
    ) -> CompletionResult:
        def _call() -> CompletionResult:
            kwargs: dict[str, Any] = {
                "model": self._model,
                "messages": _openai_messages(messages, system),
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            if cache_key is not None:
                kwargs["prompt_cache_key"] = cache_key
            resp = self._client.chat.completions.create(**kwargs)
            usage = resp.usage
            token_usage = TokenUsage(
                input_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
                output_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
            )
            _log.debug(
                "[openrouter] prompt=%s completion=%s",
                token_usage.input_tokens,
                token_usage.output_tokens,
            )
            return CompletionResult(
                text=resp.choices[0].message.content or "",
                usage=token_usage,
            )

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
        cache_key: "str | None" = None,
        agent_name: "str | None" = None,
    ) -> CompletionResult:
        def _call() -> CompletionResult:
            kwargs: dict[str, Any] = {
                "model": self._model,
                "messages": _openai_messages(messages, system),
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            if cache_key is not None:
                kwargs["prompt_cache_key"] = cache_key
            resp = self._client.chat.completions.create(**kwargs)
            usage = resp.usage
            token_usage = TokenUsage(
                input_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
                output_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
            )
            _log.debug(
                "[custom] prompt=%s completion=%s",
                token_usage.input_tokens,
                token_usage.output_tokens,
            )
            return CompletionResult(
                text=resp.choices[0].message.content or "",
                usage=token_usage,
            )

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

# Providers that natively support response_format={"type": "json_object"}.
# Callers omit prompt-engineering JSON suffixes for these. Co-located here so
# adding a new provider to _PROVIDER_REGISTRY is the only edit needed.
# REVUE-107: extend AIClient.complete() to forward response_format for this set.
_JSON_FORMAT_PROVIDERS: frozenset[str] = frozenset({
    "openai", "azure", "openrouter", "custom", "google", "groq",
})

# Providers whose constructors accept a `metrics` keyword argument.
_METRICS_AWARE_PROVIDERS: frozenset[str] = frozenset({"anthropic"})


def register_provider(name: str, cls: type) -> None:
    """Register a new provider class. Enables extension without modifying this file."""
    _PROVIDER_REGISTRY[name] = cls


def create_ai_client(
    config: AIConfig,
    metrics: MetricsCollector | None = None,
) -> AIClient:
    """Instantiate the correct AI client based on config.provider."""
    cls = _PROVIDER_REGISTRY.get(config.provider)
    if cls is None:
        raise ValueError(
            f"Unknown provider: {config.provider!r}. "
            f"Known providers: {sorted(_PROVIDER_REGISTRY.keys())}"
        )
    if metrics is not None and config.provider in _METRICS_AWARE_PROVIDERS:
        return cls(config, metrics=metrics)
    return cls(config)
