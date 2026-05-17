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
from typing import Any, Callable, Final, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from .tool_loop import ToolHandler

from revue.core.logging_channels import Log

import anthropic
import httpx
import openai

from .ai_config import AIConfig
from .metrics import MetricsCollector, MetricsEvent, NullMetricsCollector
from .models_registry import ModelConfig, load_builtin_registry

_log = logging.getLogger(__name__)

# REVUE-263 fallback knobs for customer-added models that aren't in the
# built-in registry. ``ai_client`` doesn't see the merged user-override
# view (config_loader plumbs that at startup, then drops it), so we keep
# the safety net minimal and explicit. ``auto`` matches the OpenAI wire
# default; 4096 matches the long-standing client signature default — both
# preserve today's behaviour exactly for users on the fallback path.
_FALLBACK_TOOL_CHOICE_FIRST_TURN: Final[str] = "auto"
_FALLBACK_MAX_TOKENS: Final[int] = 4096


def _resolve_model_config(model_id: str) -> "ModelConfig | None":
    """Look up a model's registry entry; return ``None`` for fallback path.

    Used only by the OpenAI-compatible clients (REVUE-263). The Anthropic
    client deliberately ignores the registry knobs — its tool semantics
    differ and the spec scopes the knob to the OpenAI loop.

    Customer-added entries live in the merged registry built by
    ``config_loader._load_and_validate_model_registry``, but the merged
    view is not threaded into ``ai_client``. Falling back to the built-in
    view keeps this layer dependency-free; missing entries trigger a
    one-shot INFO log so operators can see they're on the fallback path
    rather than the engineered defaults.
    """
    registry = load_builtin_registry()
    cfg = registry.get(model_id)
    if cfg is None:
        _log.info(
            "[ai-client] model %r not in built-in registry; "
            "using fallback tool_choice_first_turn=%s and max_tokens_default=%d",
            model_id,
            _FALLBACK_TOOL_CHOICE_FIRST_TURN,
            _FALLBACK_MAX_TOKENS,
        )
    return cfg


def _apply_model_knobs(
    cfg: "ModelConfig | None",
    caller_max_tokens: "int | None",
) -> tuple[int, str]:
    """Resolve per-call values from the registry snapshot + caller overrides.

    Returns ``(max_tokens, tool_choice_first_turn)``. Caller-supplied
    ``max_tokens`` always wins; ``None`` means "use the registry default
    (or the fallback if this model isn't registered)". This is the only
    point where the registry knobs influence the wire shape, which keeps
    the policy auditable — clients themselves stay dumb.
    """
    if cfg is None:
        max_tokens = (
            caller_max_tokens
            if caller_max_tokens is not None
            else _FALLBACK_MAX_TOKENS
        )
        return max_tokens, _FALLBACK_TOOL_CHOICE_FIRST_TURN
    max_tokens = (
        caller_max_tokens
        if caller_max_tokens is not None
        else cfg.max_tokens_default
    )
    return max_tokens, cfg.tool_choice_first_turn

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

    def __post_init__(self) -> None:
        for field_name, value in (
            ("input_tokens", self.input_tokens),
            ("output_tokens", self.output_tokens),
            ("cache_creation_input_tokens", self.cache_creation_input_tokens),
            ("cache_read_input_tokens", self.cache_read_input_tokens),
        ):
            if value < 0:
                raise ValueError(
                    f"TokenUsage.{field_name} must be non-negative, got {value}"
                )


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

def _extract_cached_tokens(usage: Any) -> int:
    """Extract cached_tokens from OpenAI-compatible usage.prompt_tokens_details.

    OpenAI's API exposes only cache *reads* (prompt_tokens_details.cached_tokens).
    There is no equivalent of Anthropic's cache_creation_input_tokens — caching is
    automatic and the API does not report a separate creation cost. Therefore
    cache_creation_input_tokens is always 0 for all OpenAI-compatible clients.
    """
    details = getattr(usage, "prompt_tokens_details", None) if usage else None
    raw = getattr(details, "cached_tokens", 0) if details else 0
    if not isinstance(raw, int):
        return 0
    if raw < 0:
        raise ValueError(f"API returned negative cached_tokens: {raw}")
    return raw


def _build_openai_token_usage(usage: Any, provider_tag: str) -> TokenUsage:
    """Build TokenUsage from an OpenAI-compatible usage object.

    Logs a warning when the API response omits the usage block entirely —
    token counts will default to 0, which may mask quota or billing issues.
    """
    if usage is None:
        Log.nova.warning("[%s] API response missing usage information — token counts defaulting to 0", provider_tag)
    cached = _extract_cached_tokens(usage)
    return TokenUsage(
        input_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
        output_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
        cache_read_input_tokens=cached,
    )


def _openai_complete_with_tools(
    sdk_client: Any,
    model: str,
    provider_label: str,
    max_attempts: int,
    *,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    tool_handlers: "dict[str, ToolHandler]",
    max_iterations: "int | None",
    max_tokens: int,
    temperature: float,
    system: "str | list[dict[str, Any]] | None",
    output_config: "dict[str, Any] | None" = None,
    agent_name: "str | None" = None,
    metrics: "Any | None" = None,
    tool_choice_first_turn: str = "auto",
) -> "CompletionResult":
    """Shared tool-loop driver for all OpenAI-compatible clients.

    REVUE-241 P1: wraps openai_tool_loop in ``_with_retry`` so callers inherit
    the same rate-limit contract as ``complete()``. DRY across OpenAI / Azure /
    OpenRouter / Custom — without this helper, each client owned a near-identical
    copy of the same boilerplate.

    REVUE-241: ``output_config`` (Anthropic-style) is translated here into
    OpenAI's ``response_format`` shape so callers can pass one provider-
    agnostic kwarg regardless of which client they hold.

    REVUE-241 P2 (OpenAI path): ``agent_name`` and ``metrics`` are forwarded
    to the inner loop so the OpenAI-compatible path records the same per-agent
    telemetry as the Anthropic path. Without this forwarding, every tool-use
    review on OpenAI / Azure / OpenRouter / Custom is invisible in
    .revue/metrics.jsonl even though the underlying loop now supports it.
    """
    from . import tool_loop as _tl
    iterations = max_iterations if max_iterations is not None else _tl.DEFAULT_MAX_TOOL_ITERATIONS

    # Translate Anthropic's output_config (which carries {format: {type,
    # schema}}) into OpenAI's response_format ({type, json_schema: {name,
    # strict, schema}}). Caller doesn't need to know which provider they're
    # speaking to; the conversion happens here at the boundary.
    response_format: "dict[str, Any] | None" = None
    if output_config is not None:
        fmt = output_config.get("format") or {}
        if fmt.get("type") == "json_schema" and "schema" in fmt:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": fmt.get("name", "findings_response"),
                    "strict": True,
                    "schema": fmt["schema"],
                },
            }

    def _call() -> "CompletionResult":
        return _tl.openai_tool_loop(
            sdk_client,
            model=model,
            messages=messages,
            tools=tools,
            tool_handlers=tool_handlers,
            max_iterations=iterations,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            provider_label=provider_label,
            response_format=response_format,
            agent_name=agent_name,
            metrics=metrics,
            tool_choice_first_turn=tool_choice_first_turn,
        )

    return _with_retry(_call, max_attempts=max_attempts)


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

    def __init__(
        self,
        config: AIConfig,
        metrics: MetricsCollector | None = None,
    ) -> None:
        self._model = config.model
        self._max_attempts = 3 if config.retry_on_rate_limit else 1
        self._client = openai.OpenAI(
            api_key=config.resolve_api_key(),
            base_url=config.base_url or None,
            timeout=_TIMEOUT,
        )
        self._metrics = metrics or NullMetricsCollector()
        # REVUE-263: snapshot registry knobs at construction time. One lookup
        # per client instance, not per call.
        self._model_cfg = _resolve_model_config(self._model)

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
            token_usage = _build_openai_token_usage(resp.usage, "openai")
            Log.nova.debug(
                "[openai] cached=%s prompt=%s completion=%s",
                token_usage.cache_read_input_tokens,
                token_usage.input_tokens,
                token_usage.output_tokens,
            )
            return CompletionResult(
                text=resp.choices[0].message.content or "",
                usage=token_usage,
            )

        return _with_retry(_call, max_attempts=self._max_attempts)

    def complete_with_tools(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]],
        tool_handlers: "dict[str, ToolHandler]",
        max_iterations: "int | None" = None,
        max_tokens: int | None = None,
        temperature: float = 0.3,
        system: "str | list[dict[str, Any]] | None" = None,
        agent_name: "str | None" = None,
        output_config: "dict[str, Any] | None" = None,
    ) -> CompletionResult:
        resolved_max_tokens, tool_choice_first_turn = _apply_model_knobs(
            self._model_cfg, max_tokens
        )
        return _openai_complete_with_tools(
            self._client, self._model, "openai", self._max_attempts,
            messages=messages, tools=tools, tool_handlers=tool_handlers,
            max_iterations=max_iterations, max_tokens=resolved_max_tokens,
            temperature=temperature, system=system,
            output_config=output_config,
            agent_name=agent_name, metrics=self._metrics,
            tool_choice_first_turn=tool_choice_first_turn,
        )


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
            Log.nova.debug(
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

    def complete_with_tools(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]],
        tool_handlers: "dict[str, ToolHandler]",
        max_iterations: "int | None" = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        system: "str | list[dict[str, Any]] | None" = None,
        agent_name: "str | None" = None,
        output_config: "dict[str, Any] | None" = None,
    ) -> CompletionResult:
        # REVUE-241 P1: the tool loop must inherit the same retry contract as
        # complete() — without _with_retry a single transient 429 collapses a
        # reviewer that previously got 3 attempts.
        from . import tool_loop as _tl
        iterations = max_iterations if max_iterations is not None else _tl.DEFAULT_MAX_TOOL_ITERATIONS

        def _call() -> CompletionResult:
            return _tl.anthropic_tool_loop(
                self._client,
                model=self._model,
                messages=messages,
                tools=tools,
                tool_handlers=tool_handlers,
                max_iterations=iterations,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                agent_name=agent_name,
                metrics=self._metrics,
                output_config=output_config,
            )

        return _with_retry(_call, max_attempts=self._max_attempts)


class AzureOpenAIClient:
    """Azure OpenAI Service client."""

    def __init__(
        self,
        config: AIConfig,
        metrics: MetricsCollector | None = None,
    ) -> None:
        self._model = config.azure_deployment or config.model
        self._max_attempts = 3 if config.retry_on_rate_limit else 1
        self._client = openai.AzureOpenAI(
            api_key=config.resolve_api_key(),
            azure_endpoint=config.azure_endpoint,
            api_version=config.azure_api_version,
            timeout=_TIMEOUT,
        )
        self._metrics = metrics or NullMetricsCollector()
        # REVUE-263: registry lookup uses ``config.model`` (the model id),
        # NOT ``azure_deployment`` (the deployment alias) — the registry is
        # keyed by canonical model identifiers, not provider-side aliases.
        self._model_cfg = _resolve_model_config(config.model)

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
            token_usage = _build_openai_token_usage(resp.usage, "azure")
            Log.nova.debug(
                "[azure] cached=%s prompt=%s completion=%s",
                token_usage.cache_read_input_tokens,
                token_usage.input_tokens,
                token_usage.output_tokens,
            )
            return CompletionResult(
                text=resp.choices[0].message.content or "",
                usage=token_usage,
            )

        return _with_retry(_call, max_attempts=self._max_attempts)

    def complete_with_tools(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]],
        tool_handlers: "dict[str, ToolHandler]",
        max_iterations: "int | None" = None,
        max_tokens: int | None = None,
        temperature: float = 0.3,
        system: "str | list[dict[str, Any]] | None" = None,
        agent_name: "str | None" = None,
        output_config: "dict[str, Any] | None" = None,
    ) -> CompletionResult:
        # REVUE-241 P1: wrap the tool loop in _with_retry so reviewers keep
        # their 3-attempt retry budget on the new default path.
        # REVUE-263: apply per-model registry knobs (tool_choice_first_turn,
        # max_tokens_default) — caller-supplied max_tokens still wins.
        resolved_max_tokens, tool_choice_first_turn = _apply_model_knobs(
            self._model_cfg, max_tokens
        )
        return _openai_complete_with_tools(
            self._client, self._model, "azure", self._max_attempts,
            messages=messages, tools=tools, tool_handlers=tool_handlers,
            max_iterations=max_iterations, max_tokens=resolved_max_tokens,
            temperature=temperature, system=system,
            output_config=output_config,
            agent_name=agent_name, metrics=self._metrics,
            tool_choice_first_turn=tool_choice_first_turn,
        )


class OpenRouterClient:
    """OpenRouter (openrouter.ai) client — OpenAI-compatible with extra headers."""

    def __init__(
        self,
        config: AIConfig,
        metrics: MetricsCollector | None = None,
    ) -> None:
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
        self._metrics = metrics or NullMetricsCollector()
        self._model_cfg = _resolve_model_config(self._model)

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
            token_usage = _build_openai_token_usage(resp.usage, "openrouter")
            Log.nova.debug(
                "[openrouter] cached=%s prompt=%s completion=%s",
                token_usage.cache_read_input_tokens,
                token_usage.input_tokens,
                token_usage.output_tokens,
            )
            return CompletionResult(
                text=resp.choices[0].message.content or "",
                usage=token_usage,
            )

        return _with_retry(_call, max_attempts=self._max_attempts)

    def complete_with_tools(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]],
        tool_handlers: "dict[str, ToolHandler]",
        max_iterations: "int | None" = None,
        max_tokens: int | None = None,
        temperature: float = 0.3,
        system: "str | list[dict[str, Any]] | None" = None,
        agent_name: "str | None" = None,
        output_config: "dict[str, Any] | None" = None,
    ) -> CompletionResult:
        # REVUE-241 P1: wrap the tool loop in _with_retry so reviewers keep
        # their 3-attempt retry budget on the new default path.
        # REVUE-263: apply per-model registry knobs.
        resolved_max_tokens, tool_choice_first_turn = _apply_model_knobs(
            self._model_cfg, max_tokens
        )
        return _openai_complete_with_tools(
            self._client, self._model, "openrouter", self._max_attempts,
            messages=messages, tools=tools, tool_handlers=tool_handlers,
            max_iterations=max_iterations, max_tokens=resolved_max_tokens,
            temperature=temperature, system=system,
            output_config=output_config,
            agent_name=agent_name, metrics=self._metrics,
            tool_choice_first_turn=tool_choice_first_turn,
        )


class CustomGatewayClient:
    """Generic OpenAI-compatible gateway at a user-supplied base_url."""

    def __init__(
        self,
        config: AIConfig,
        metrics: MetricsCollector | None = None,
    ) -> None:
        self._model = config.model
        self._max_attempts = 3 if config.retry_on_rate_limit else 1
        self._client = openai.OpenAI(
            api_key=config.resolve_api_key(),
            base_url=config.base_url,
            timeout=_TIMEOUT,
        )
        self._metrics = metrics or NullMetricsCollector()
        self._model_cfg = _resolve_model_config(self._model)

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
            token_usage = _build_openai_token_usage(resp.usage, "custom")
            Log.nova.debug(
                "[custom] cached=%s prompt=%s completion=%s",
                token_usage.cache_read_input_tokens,
                token_usage.input_tokens,
                token_usage.output_tokens,
            )
            return CompletionResult(
                text=resp.choices[0].message.content or "",
                usage=token_usage,
            )

        return _with_retry(_call, max_attempts=self._max_attempts)

    def complete_with_tools(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]],
        tool_handlers: "dict[str, ToolHandler]",
        max_iterations: "int | None" = None,
        max_tokens: int | None = None,
        temperature: float = 0.3,
        system: "str | list[dict[str, Any]] | None" = None,
        agent_name: "str | None" = None,
        output_config: "dict[str, Any] | None" = None,
    ) -> CompletionResult:
        # REVUE-241 P1: wrap the tool loop in _with_retry so reviewers keep
        # their 3-attempt retry budget on the new default path.
        # REVUE-263: apply per-model registry knobs.
        resolved_max_tokens, tool_choice_first_turn = _apply_model_knobs(
            self._model_cfg, max_tokens
        )
        return _openai_complete_with_tools(
            self._client, self._model, "custom", self._max_attempts,
            messages=messages, tools=tools, tool_handlers=tool_handlers,
            max_iterations=max_iterations, max_tokens=resolved_max_tokens,
            temperature=temperature, system=system,
            output_config=output_config,
            agent_name=agent_name, metrics=self._metrics,
            tool_choice_first_turn=tool_choice_first_turn,
        )


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

# Providers whose chat-completions API natively supports the OpenAI-style
# response_format={"type": "json_object"} mode. Anthropic is intentionally
# omitted here — it uses its own ``output_config.format`` grammar (wired via
# ``output_config`` on messages.create, see finding_schema.py for the schema
# and tool_loop.anthropic_tool_loop for the wiring). Adding a new provider
# to _PROVIDER_REGISTRY only requires editing this set if it speaks OpenAI's
# response_format dialect; structured outputs for Anthropic-compatible
# providers belong on the output_config path instead.
_JSON_FORMAT_PROVIDERS: frozenset[str] = frozenset({
    "openai", "azure", "openrouter", "custom", "google", "groq",
})

# Providers whose constructors accept a `metrics` keyword argument.
# REVUE-241 P2: OpenAI-compatible clients now also persist MetricsEvent on
# the tool-use path (via openai_tool_loop). Listing them here lets
# ``create_ai_client`` forward the collector so reviewer-agent token usage
# surfaces in .revue/metrics.jsonl regardless of provider.
_METRICS_AWARE_PROVIDERS: frozenset[str] = frozenset({
    "anthropic", "openai", "azure", "openrouter", "custom",
})


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
