"""Tool-use loops for Anthropic and OpenAI-compatible providers (REVUE-239).

This module owns the iteration logic that turns a stateless ``messages.create``
into an agentic conversation with tool calls. Two flavours:

* :func:`anthropic_tool_loop` — Anthropic's native ``tool_use`` content blocks
  and ``tool_result`` content blocks (Messages API).
* :func:`openai_tool_loop` — OpenAI's ``tool_calls`` array on
  ``message`` plus ``role: "tool"`` follow-up messages. Shared by all four
  OpenAI-compatible clients (OpenAI, Azure, OpenRouter, custom gateways).

Both expose the same public API: callers pass Anthropic-style tool definitions
(``name`` / ``description`` / ``input_schema``) and a ``{name: handler}`` dict.
The OpenAI loop translates internally to the function-tool envelope.

Iteration cap (``max_iterations``) prevents runaway loops. When the cap is hit
without the model producing a final answer, the loop returns the last text
content (often empty) so the caller can fall back to deterministic behaviour
rather than crashing.

Sits adjacent to ``ai_client.py`` so the client file stays focused on
provider construction and the single-shot ``complete()`` API.
"""
from __future__ import annotations

import json as _json
import logging
from types import SimpleNamespace
from typing import Any, Callable, Final

from .tools import ToolResult


ToolHandler = Callable[..., ToolResult]
DEFAULT_MAX_TOOL_ITERATIONS: Final[int] = 5

# REVUE-241 Gap 2: when the loop hits max_iterations while the model is still
# calling tools, this prompt is appended to the message history and one more
# call is made without tools. Phrased positively (synthesise / emit) because
# LLMs follow constructive directives more reliably than prohibitions like
# "stop calling tools" — the dogfood failure mode this addresses showed the
# model wanted to keep reading; we redirect it toward finishing instead.
FINALIZE_PROMPT: Final[str] = (
    "You have enough context now — synthesize the findings you've identified "
    "so far from the files you've already read and emit your final JSON response."
)

_log = logging.getLogger(__name__)


def _summarise_tool_input(tool_input: dict[str, Any]) -> str:
    """Compact, single-line representation of tool args for log lines.

    Long values are truncated so a single read_file with a giant `path` doesn't
    flood the log; the goal is "which file did this agent read", not a full
    audit dump.
    """
    parts: list[str] = []
    for key, value in tool_input.items():
        text = repr(value)
        if len(text) > 80:
            text = text[:77] + "..."
        parts.append(f"{key}={text}")
    return ", ".join(parts)


def _log_tool_invocation(
    *,
    agent_name: "str | None",
    tool_name: str,
    tool_input: dict[str, Any],
    result: ToolResult,
) -> None:
    """Emit one INFO line per successful tool call, WARNING on error result.

    This is the only externally visible evidence that a reviewer agent actually
    invoked ``read_file`` (rather than relying on the diff alone). A 0-findings
    review with no [tool-call] lines means the tool was wired but never used —
    the AC7 calibration story changes accordingly.
    """
    agent = agent_name or "?"
    summary = _summarise_tool_input(tool_input)
    if result.is_error:
        _log.warning(
            "[tool-call-error] agent=%s tool=%s input={%s} error=%s",
            agent, tool_name, summary, result.content,
        )
    else:
        _log.info(
            "[tool-call] agent=%s tool=%s input={%s}",
            agent, tool_name, summary,
        )


def _anthropic_tools_to_openai(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate Anthropic tool defs to OpenAI's function-tool envelope."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object"}),
            },
        }
        for t in tools
    ]


def _dispatch_tool(
    handlers: dict[str, ToolHandler],
    name: str,
    tool_input: dict[str, Any],
) -> ToolResult:
    """Invoke handler matching *name*; return an error ToolResult when missing.

    Any exception from the handler (schema mismatch, missing key, attribute
    access on a None, etc.) is caught and reported back to the model as a
    tool_result rather than crashing the loop — the model can self-correct
    on retry. Narrowing this to TypeError previously let KeyError /
    AttributeError / ValueError escape and abort the entire review.
    """
    handler = handlers.get(name)
    if handler is None:
        return ToolResult(
            content=(
                f"Error: tool '{name}' is not available. "
                f"Available tools: {sorted(handlers.keys())}"
            ),
            is_error=True,
        )
    try:
        return handler(**tool_input)
    except Exception as exc:
        return ToolResult(
            content=f"Error invoking '{name}': {type(exc).__name__}: {exc}",
            is_error=True,
        )


# Anthropic changed the ``ephemeral`` default TTL from 1h to 5m on
# 2026-03-06; the bare form now buys only 5 minutes. The codebase
# convention (see ai_client.py:_CACHE_CONTROL_1H) is explicit 1h so the
# per-file cache prefix outlives a single review cycle — without this
# the cache_write cost is paid every 5 minutes on a developer's local
# loop. Final prevents reassignment.
_TOOL_RESULT_CACHE_CONTROL: Final = {"type": "ephemeral", "ttl": "1h"}


def _cumulative_tool_result_bytes(messages: list[dict[str, Any]]) -> int:
    """Sum UTF-8 byte length of every tool_result block across the conversation.

    Used by the REVUE-243 AC3 cumulative cap to decide when to short-circuit
    the loop into the no-tools finalize path. Counts only content payload,
    not the wrapping JSON envelope — the envelope overhead is ~70 bytes per
    block, dwarfed by even a single read_lines window."""
    total = 0
    for msg in messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            payload = block.get("content")
            if isinstance(payload, str):
                total += len(payload.encode("utf-8"))
            elif isinstance(payload, list):
                # Mixed-content tool_results (rare); count nested text blocks.
                for inner in payload:
                    if isinstance(inner, dict) and inner.get("type") == "text":
                        total += len(str(inner.get("text", "")).encode("utf-8"))
    return total


DEFAULT_TOOL_RESULT_BYTES_CAP: Final[int] = 80_000


def anthropic_tool_loop(
    sdk_client: Any,
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    tool_handlers: dict[str, ToolHandler],
    max_iterations: int,
    max_tokens: int,
    temperature: float,
    system: "str | list[dict[str, Any]] | None",
    agent_name: "str | None" = None,
    metrics: "Any | None" = None,
    output_config: "dict[str, Any] | None" = None,
    tool_result_bytes_cap: int = DEFAULT_TOOL_RESULT_BYTES_CAP,
) -> "CompletionResult":  # type: ignore[name-defined]
    """Run the Anthropic native tool-use loop until stop_reason != 'tool_use'.

    Returns the concatenated text from the final assistant message. When the
    loop hits *max_iterations* without the model issuing a final answer, the
    returned text is empty (no exception) so the caller can fall back to
    deterministic behaviour.

    REVUE-241 D2: successful tool_result blocks carry ``cache_control: ephemeral``
    so the file contents read during this turn anchor a cache breakpoint —
    subsequent tool_use iterations and re-runs against the same diff hit the
    cache prefix instead of paying full input cost on every read.

    REVUE-241 P2: when ``metrics`` is provided, a ``MetricsEvent`` is recorded
    after the loop completes so per-agent telemetry survives the tool-use path
    (the single-shot ``complete()`` records metrics directly; the tool loop
    must do the same or reviewers vanish from metrics.jsonl).
    """
    # Local imports avoid a circular dependency with ai_client at module load.
    from datetime import datetime, timezone
    from .ai_client import CompletionResult, TokenUsage
    from .metrics import MetricsEvent

    current_messages: list[dict[str, Any]] = list(messages)
    last_text = ""
    last_usage: Any = None
    last_stop_reason: "str | None" = None
    iterations_used = 0

    for iter_idx in range(max_iterations):
        iterations_used = iter_idx + 1
        _log.debug(
            "[tool-loop-iter] agent=%s iter=%d/%d msgs=%d",
            agent_name or "?", iterations_used, max_iterations, len(current_messages),
        )
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": current_messages,
            "tools": tools,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system is not None:
            kwargs["system"] = (
                [{"type": "text", "text": system}] if isinstance(system, str) else list(system)
            )
        # REVUE-241: grammar-constrain the final text response so multi-turn
        # tool use can't drift into prose. Per Anthropic's docs the grammar
        # applies only to direct text output — tool_use blocks and
        # tool_result content are unconstrained — so the loop's iteration
        # logic stays correct; the kwarg just rides along on every call.
        if output_config is not None:
            kwargs["output_config"] = output_config

        resp = sdk_client.messages.create(**kwargs)
        last_usage = getattr(resp, "usage", None)
        last_stop_reason = getattr(resp, "stop_reason", None)

        if last_stop_reason != "tool_use":
            last_text = "".join(
                getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text"
            )
            # REVUE-241 diagnostic: when the final response has empty text,
            # dump every content block so we can see whether the API returned
            # nothing, a non-text block, or text that was filtered. Without
            # this hook, "text_len=0 stop_reason=end_turn" is a black box —
            # it could be a structured-outputs grammar issue, a provider
            # routing oddity, or an SDK shape mismatch.
            if not last_text:
                block_summaries = []
                for b in resp.content:
                    btype = getattr(b, "type", "?")
                    if btype == "text":
                        block_summaries.append(f"text({len(getattr(b, 'text', ''))})")
                    elif btype == "tool_use":
                        block_summaries.append(
                            f"tool_use(name={getattr(b, 'name', '?')!r}, "
                            f"input={getattr(b, 'input', None)!r})"
                        )
                    else:
                        block_summaries.append(f"{btype}({type(b).__name__})")
                _log.warning(
                    "[tool-loop-empty-text] agent=%s stop_reason=%s blocks=[%s] "
                    "n_blocks=%d — final response carried no text content",
                    agent_name or "?", last_stop_reason,
                    ", ".join(block_summaries) or "(none)",
                    len(resp.content),
                )
            break

        # Echo the assistant turn into history as serialisable blocks.
        assistant_blocks: list[dict[str, Any]] = []
        for b in resp.content:
            btype = getattr(b, "type", None)
            if btype == "text":
                assistant_blocks.append({"type": "text", "text": b.text})
            elif btype == "tool_use":
                assistant_blocks.append({
                    "type": "tool_use",
                    "id": b.id,
                    "name": b.name,
                    "input": b.input,
                })
        current_messages.append({"role": "assistant", "content": assistant_blocks})

        # REVUE-241 P3: Anthropic permits at most 4 cache_control breakpoints
        # per request. Strip cache_control from every prior tool_result so the
        # only surviving breakpoint is the one we attach below. Without this,
        # a 5-iteration loop with one tool_use per turn would pin 5 breakpoints
        # and the request would either be rejected or silently miss the cache.
        for prior_msg in current_messages:
            if prior_msg.get("role") != "user":
                continue
            prior_content = prior_msg.get("content")
            if not isinstance(prior_content, list):
                continue
            for prior_block in prior_content:
                if isinstance(prior_block, dict) and prior_block.get("type") == "tool_result":
                    prior_block.pop("cache_control", None)

        # Build tool_result blocks for every tool_use block in this response.
        # Errors are never cached — they're transient and shouldn't pin a
        # cache prefix (REVUE-241 D2).
        tool_results: list[dict[str, Any]] = []
        for b in resp.content:
            if getattr(b, "type", None) != "tool_use":
                continue
            result = _dispatch_tool(tool_handlers, b.name, b.input)
            _log_tool_invocation(
                agent_name=agent_name,
                tool_name=b.name,
                tool_input=b.input,
                result=result,
            )
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": b.id,
                "content": result.content,
                "is_error": result.is_error,
            })

        # Mark ONLY the last successful tool_result with cache_control. The
        # cache_control breakpoint defines the *end* of the cacheable prefix —
        # earlier successful results are implicitly within it, so they don't
        # need their own marker. This bounds breakpoint count to 1 per turn
        # (combined with prior-turn stripping above: ≤ 1 across the loop).
        for block in reversed(tool_results):
            if not block.get("is_error"):
                block["cache_control"] = _TOOL_RESULT_CACHE_CONTROL
                break

        current_messages.append({"role": "user", "content": tool_results})

        # REVUE-243 AC3: cumulative tool-result cap. Once total tool_result
        # content bytes across the conversation cross the threshold, break out
        # so the forced-finalize block below fires and emits whatever the
        # agent has synthesised so far. Without this, a reviewer that keeps
        # asking for more files will eventually push the request past 200K
        # tokens and crash the whole review with `prompt is too long`.
        cumulative_bytes = _cumulative_tool_result_bytes(current_messages)
        if cumulative_bytes >= tool_result_bytes_cap:
            _log.warning(
                "[tool-loop-cap-fired] agent=%s cumulative_bytes=%d cap=%d "
                "iteration=%d/%d — forcing finalize",
                agent_name or "?", cumulative_bytes, tool_result_bytes_cap,
                iterations_used, max_iterations,
            )
            break

    # REVUE-241 Gap 2: forced-finalize guardrail. If the cap was hit while the
    # model was still calling tools, the loop above would return text_len=0 and
    # the parser would treat the agent as "0 findings". Instead, issue one more
    # API call without tools and with a positive nudge to synthesise findings
    # from the work done so far — degrades catastrophic failure (no findings)
    # to graceful partial-information output.
    if last_stop_reason == "tool_use":
        _log.info(
            "[tool-loop-finalize] agent=%s iterations=%d max=%d — issuing "
            "tool-free finalize call to surface findings synthesised so far",
            agent_name or "?", iterations_used, max_iterations,
        )

        # Anthropic requires alternating user/assistant turns; current_messages[-1]
        # is the user message holding tool_result blocks from the final loop
        # iteration. Append the finalize text to that message's content list so
        # we don't introduce two adjacent user messages.
        if (
            current_messages
            and current_messages[-1].get("role") == "user"
            and isinstance(current_messages[-1].get("content"), list)
        ):
            current_messages[-1]["content"].append({
                "type": "text",
                "text": FINALIZE_PROMPT,
            })
        else:
            current_messages.append({"role": "user", "content": FINALIZE_PROMPT})

        finalize_kwargs: dict[str, Any] = {
            "model": model,
            "messages": current_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system is not None:
            finalize_kwargs["system"] = (
                [{"type": "text", "text": system}] if isinstance(system, str) else list(system)
            )
        if output_config is not None:
            finalize_kwargs["output_config"] = output_config
        # Deliberately omit `tools` — forces text-only response so the model
        # cannot keep deferring with more tool calls.

        finalize_resp = sdk_client.messages.create(**finalize_kwargs)
        finalize_usage = getattr(finalize_resp, "usage", None)
        last_stop_reason = getattr(finalize_resp, "stop_reason", last_stop_reason)
        last_text = "".join(
            getattr(b, "text", "") for b in finalize_resp.content
            if getattr(b, "type", None) == "text"
        )
        iterations_used += 1  # the finalize call counts toward the agent's work

        # Observability: a dedicated outcome line so operators can tell
        # "finalize recovered" apart from "finalize ran but stayed empty".
        # Without this, the only post-finalize signal is the generic
        # [tool-loop-done] line — indistinguishable from the success path
        # of a single-shot agent.
        finalize_block_summaries: list[str] = []
        for b in finalize_resp.content:
            btype = getattr(b, "type", "?")
            if btype == "text":
                finalize_block_summaries.append(f"text({len(getattr(b, 'text', ''))})")
            elif btype == "tool_use":
                finalize_block_summaries.append(
                    f"tool_use(name={getattr(b, 'name', '?')!r})"
                )
            else:
                finalize_block_summaries.append(f"{btype}({type(b).__name__})")
        finalize_blocks_str = ", ".join(finalize_block_summaries) or "(none)"

        if not last_text:
            # The safety net itself failed — escalate so this isn't
            # mistaken for the "agent had nothing to say" success path.
            _log.warning(
                "[tool-loop-finalize-outcome] agent=%s stop_reason=%s "
                "text_len=0 blocks=[%s] — finalize call produced no text; "
                "the no-tools safety net did not recover findings",
                agent_name or "?", last_stop_reason or "?", finalize_blocks_str,
            )
        else:
            _log.info(
                "[tool-loop-finalize-outcome] agent=%s stop_reason=%s "
                "text_len=%d blocks=[%s]",
                agent_name or "?", last_stop_reason or "?",
                len(last_text), finalize_blocks_str,
            )

        # Sum finalize tokens into last_usage so the MetricsEvent below reflects
        # the additional spend — otherwise the agent looks free at the moment it
        # actually paid the most.
        if finalize_usage is not None:
            if last_usage is not None:
                last_usage = SimpleNamespace(
                    input_tokens=getattr(last_usage, "input_tokens", 0)
                        + getattr(finalize_usage, "input_tokens", 0),
                    output_tokens=getattr(last_usage, "output_tokens", 0)
                        + getattr(finalize_usage, "output_tokens", 0),
                    cache_creation_input_tokens=getattr(last_usage, "cache_creation_input_tokens", 0)
                        + getattr(finalize_usage, "cache_creation_input_tokens", 0),
                    cache_read_input_tokens=getattr(last_usage, "cache_read_input_tokens", 0)
                        + getattr(finalize_usage, "cache_read_input_tokens", 0),
                )
            else:
                last_usage = finalize_usage

    # Emit a termination signal so operators can distinguish "loop ended
    # cleanly with a final response" from "finalize call had to recover".
    if last_stop_reason == "tool_use":
        # Should not happen after finalize (no tools), but keep the warning
        # as a safety net.
        _log.warning(
            "[tool-loop-max-iterations] agent=%s iterations=%d max=%d "
            "stop_reason=tool_use text_len=%d — loop terminated mid-tool-use; "
            "model never produced a final answer",
            agent_name or "?", iterations_used, max_iterations, len(last_text),
        )
    else:
        _log.info(
            "[tool-loop-done] agent=%s iterations=%d stop_reason=%s text_len=%d",
            agent_name or "?", iterations_used, last_stop_reason or "?", len(last_text),
        )

    token_usage = TokenUsage(
        input_tokens=getattr(last_usage, "input_tokens", 0) if last_usage else 0,
        output_tokens=getattr(last_usage, "output_tokens", 0) if last_usage else 0,
        cache_creation_input_tokens=getattr(last_usage, "cache_creation_input_tokens", 0) if last_usage else 0,
        cache_read_input_tokens=getattr(last_usage, "cache_read_input_tokens", 0) if last_usage else 0,
    )

    # REVUE-241 P2: record per-agent token usage so the tool-use path matches
    # the telemetry coverage of single-shot complete(). Skip when no collector
    # was passed — keeps the function callable from tests that don't care.
    if metrics is not None:
        metrics.record(
            MetricsEvent(
                event_type="agent_call",
                timestamp=datetime.now(timezone.utc).isoformat(),
                agent_name=agent_name,
                provider="anthropic",
                model=model,
                cache_creation_tokens=token_usage.cache_creation_input_tokens,
                cache_read_tokens=token_usage.cache_read_input_tokens,
                input_tokens=token_usage.input_tokens,
                output_tokens=token_usage.output_tokens,
            )
        )

    return CompletionResult(text=last_text, usage=token_usage)


def openai_tool_loop(
    sdk_client: Any,
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    tool_handlers: dict[str, ToolHandler],
    max_iterations: int,
    max_tokens: int,
    temperature: float,
    system: "str | list[dict[str, Any]] | None",
    provider_label: str,
    response_format: "dict[str, Any] | None" = None,
    agent_name: "str | None" = None,
    metrics: "Any | None" = None,
) -> "CompletionResult":  # type: ignore[name-defined]
    """Run the OpenAI-compatible tool-use loop.

    Shared by OpenAIClient, AzureOpenAIClient, OpenRouterClient, and
    CustomGatewayClient — they all speak the OpenAI function-tool format.

    REVUE-241: optional ``response_format`` grammar-constrains the model's
    final text response after multi-turn tool use. Symmetric to the
    Anthropic loop's ``output_config`` parameter. On OpenRouter, support
    is best-effort and depends on the backend the request is routed to.

    REVUE-241 P2 (OpenAI path): when ``metrics`` is provided, a
    ``MetricsEvent`` is recorded after the loop terminates so OpenAI /
    Azure / OpenRouter / Custom reviewer calls surface in
    ``.revue/metrics.jsonl`` — closing the asymmetry where only the
    Anthropic loop persisted per-agent token usage on the tool-use path.
    """
    from datetime import datetime, timezone
    from .ai_client import CompletionResult, TokenUsage, _build_openai_token_usage, _openai_messages
    from .metrics import MetricsEvent

    current_messages = _openai_messages(messages, system)
    openai_tools = _anthropic_tools_to_openai(tools)
    last_text = ""
    last_usage: Any = None
    hit_cap_in_tool_use = False

    for iter_idx in range(max_iterations):
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": current_messages,
            "tools": openai_tools,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if response_format is not None:
            kwargs["response_format"] = response_format
        resp = sdk_client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        message = choice.message
        last_usage = getattr(resp, "usage", None)

        tool_calls = getattr(message, "tool_calls", None) or []
        if not tool_calls:
            last_text = getattr(message, "content", None) or ""
            break

        # Track when the final iteration still wants to call tools — triggers
        # the forced-finalize call below (REVUE-241 Gap 2).
        if iter_idx == max_iterations - 1:
            hit_cap_in_tool_use = True

        # Echo the assistant turn (with tool_calls) into history.
        current_messages.append({
            "role": "assistant",
            "content": getattr(message, "content", None),
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in tool_calls
            ],
        })

        for tc in tool_calls:
            raw_args = tc.function.arguments or ""
            try:
                tool_input = _json.loads(raw_args) if raw_args else {}
            except _json.JSONDecodeError as exc:
                # Surface the parse error so the model can self-correct on the
                # next iteration. Previously we defaulted to {} silently, which
                # let the handler run with empty args and burned iterations
                # without the model ever seeing what went wrong.
                current_messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": (
                        f"Error: tool arguments were not valid JSON ({exc.msg}). "
                        f"Please retry with a syntactically valid JSON object."
                    ),
                })
                continue
            result = _dispatch_tool(tool_handlers, tc.function.name, tool_input)
            current_messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result.content,
            })

    # REVUE-241 Gap 2: forced-finalize guardrail mirroring the Anthropic path.
    # When the loop hits max_iterations while still calling tools, issue one
    # more API call without tools and with a positive nudge so the model
    # synthesises the findings it has identified instead of leaving last_text
    # empty — the dogfood failure mode that surfaces as "agent returned 0
    # findings" when the agent actually did work but never got to emit it.
    if hit_cap_in_tool_use:
        _log.info(
            "[tool-loop-finalize] agent=%s provider=%s iterations=%d max=%d — "
            "issuing tool-free finalize call to surface findings synthesised so far",
            agent_name or "?", provider_label, max_iterations, max_iterations,
        )
        # OpenAI tool results are role="tool", so a fresh role="user" message
        # for the finalize nudge is fine — no adjacency conflict.
        current_messages.append({"role": "user", "content": FINALIZE_PROMPT})

        finalize_kwargs: dict[str, Any] = {
            "model": model,
            "messages": current_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if response_format is not None:
            finalize_kwargs["response_format"] = response_format
        # Deliberately omit `tools` — forces text-only response.

        finalize_resp = sdk_client.chat.completions.create(**finalize_kwargs)
        finalize_choice = finalize_resp.choices[0]
        last_text = getattr(finalize_choice.message, "content", None) or ""
        finalize_usage = getattr(finalize_resp, "usage", None)

        # Observability mirror of the Anthropic path: dedicated outcome line
        # distinguishes "finalize recovered" from "finalize stayed empty".
        finalize_finish_reason = getattr(finalize_choice, "finish_reason", "?")
        finalize_tool_calls = getattr(finalize_choice.message, "tool_calls", None) or []
        finalize_blocks = []
        if last_text:
            finalize_blocks.append(f"text({len(last_text)})")
        for tc in finalize_tool_calls:
            finalize_blocks.append(
                f"tool_call(name={getattr(getattr(tc, 'function', None), 'name', '?')!r})"
            )
        finalize_blocks_str = ", ".join(finalize_blocks) or "(none)"

        if not last_text:
            _log.warning(
                "[tool-loop-finalize-outcome] agent=%s provider=%s "
                "finish_reason=%s text_len=0 blocks=[%s] — finalize call "
                "produced no text; the no-tools safety net did not recover findings",
                agent_name or "?", provider_label,
                finalize_finish_reason, finalize_blocks_str,
            )
        else:
            _log.info(
                "[tool-loop-finalize-outcome] agent=%s provider=%s "
                "finish_reason=%s text_len=%d blocks=[%s]",
                agent_name or "?", provider_label,
                finalize_finish_reason, len(last_text), finalize_blocks_str,
            )

        if finalize_usage is not None:
            if last_usage is not None:
                last_usage = SimpleNamespace(
                    prompt_tokens=getattr(last_usage, "prompt_tokens", 0)
                        + getattr(finalize_usage, "prompt_tokens", 0),
                    completion_tokens=getattr(last_usage, "completion_tokens", 0)
                        + getattr(finalize_usage, "completion_tokens", 0),
                    total_tokens=getattr(last_usage, "total_tokens", 0)
                        + getattr(finalize_usage, "total_tokens", 0),
                )
            else:
                last_usage = finalize_usage

    token_usage = _build_openai_token_usage(last_usage, provider_label) if last_usage else TokenUsage()

    # REVUE-241 P2 (OpenAI path): mirror anthropic_tool_loop's MetricsEvent
    # write. Skip when no collector was passed — keeps the function callable
    # from tests and scripts that don't care about telemetry. ``provider``
    # is set to ``provider_label`` ("openai" / "azure" / "openrouter" /
    # "custom") so OpenAI-compatible variants remain distinguishable in
    # metrics.jsonl rather than collapsing to a generic "openai" tag.
    if metrics is not None:
        metrics.record(
            MetricsEvent(
                event_type="agent_call",
                timestamp=datetime.now(timezone.utc).isoformat(),
                agent_name=agent_name,
                provider=provider_label,
                model=model,
                cache_creation_tokens=token_usage.cache_creation_input_tokens,
                cache_read_tokens=token_usage.cache_read_input_tokens,
                input_tokens=token_usage.input_tokens,
                output_tokens=token_usage.output_tokens,
            )
        )

    return CompletionResult(text=last_text, usage=token_usage)
