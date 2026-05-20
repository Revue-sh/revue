"""REVUE-241 P2 (OpenAI path): openai_tool_loop records MetricsEvent after the loop completes.

Mirror of the Anthropic-path contract enforced in
``test_anthropic_complete_with_tools_records_metrics_event`` and inside
``anthropic_tool_loop`` itself. Without this, every OpenAI-compatible
reviewer call (OpenAI / Azure / OpenRouter / Custom) leaves no trace in
``.revue/metrics.jsonl`` — tokens spent on a tool-use review vanish from
audit, even though the per-call ``complete()`` path has been recording
them since REVUE-154.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from revue_core.core.metrics import CapturingMetricsCollector
from revue_core.core.tool_loop import openai_tool_loop
from revue_core.core.tools import ToolResult


def _msg(content: "str | None", tool_calls: "list | None" = None) -> SimpleNamespace:
    return SimpleNamespace(content=content, tool_calls=tool_calls or [])


def _choice(message: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(message=message)


def _usage(prompt: int, completion: int, cached: int = 0) -> SimpleNamespace:
    details = SimpleNamespace(cached_tokens=cached) if cached else None
    return SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
        prompt_tokens_details=details,
    )


def _resp(choices: list, usage: "Any | None" = None) -> SimpleNamespace:
    return SimpleNamespace(choices=choices, usage=usage or _usage(10, 5))


def test_openai_tool_loop_records_metrics_event_when_collector_supplied() -> None:
    """When ``metrics`` is passed, a single MetricsEvent is recorded after the
    loop terminates carrying provider, model, agent_name, and token counts —
    same shape the Anthropic loop produces."""
    sdk = MagicMock()
    sdk.chat.completions.create.side_effect = [
        _resp([_choice(_msg('{"findings": []}'))], usage=_usage(prompt=42, completion=17, cached=8)),
    ]
    collector = CapturingMetricsCollector()

    openai_tool_loop(
        sdk, model="gpt-4o-mini",
        messages=[{"role": "user", "content": "review"}],
        tools=[],
        tool_handlers={},
        max_iterations=5, max_tokens=1024, temperature=0.3,
        system=None, provider_label="openrouter",
        agent_name="maya",
        metrics=collector,
    )

    assert len(collector.events) == 1, "expected exactly one MetricsEvent for the loop"
    event = collector.events[0]
    assert event.event_type == "agent_call"
    assert event.agent_name == "maya"
    assert event.provider == "openrouter", (
        "provider must reflect provider_label so OpenAI / Azure / OpenRouter / "
        "Custom remain distinguishable in metrics.jsonl"
    )
    assert event.model == "gpt-4o-mini"
    assert event.input_tokens == 42
    assert event.output_tokens == 17
    assert event.cache_read_tokens == 8


def test_openai_tool_loop_records_metrics_after_multi_iteration_tool_loop() -> None:
    """Across a multi-turn tool-use loop, only one MetricsEvent is recorded
    (mirrors Anthropic loop) and the token counts reflect the FINAL turn's
    usage — that's where the API reports the cumulative completion cost."""
    sdk = MagicMock()
    tool_call = SimpleNamespace(
        id="tc_1",
        function=SimpleNamespace(name="read_file", arguments='{"path":"a.py"}'),
    )
    sdk.chat.completions.create.side_effect = [
        _resp([_choice(_msg(None, tool_calls=[tool_call]))], usage=_usage(20, 4)),
        _resp([_choice(_msg('{"findings": []}'))], usage=_usage(60, 12)),
    ]

    def handler(path: str) -> ToolResult:
        return ToolResult(content=f"contents of {path}", is_error=False)

    collector = CapturingMetricsCollector()

    openai_tool_loop(
        sdk, model="gpt-4o-mini",
        messages=[{"role": "user", "content": "review"}],
        tools=[{"name": "read_file", "input_schema": {"type": "object"}}],
        tool_handlers={"read_file": handler},
        max_iterations=5, max_tokens=1024, temperature=0.3,
        system=None, provider_label="openai",
        agent_name="leo",
        metrics=collector,
    )

    assert len(collector.events) == 1
    event = collector.events[0]
    assert event.input_tokens == 60
    assert event.output_tokens == 12


def test_openai_tool_loop_skips_metrics_when_no_collector() -> None:
    """When ``metrics`` is omitted the loop must still run — callers from
    test paths and one-off scripts must not be required to construct a
    collector."""
    sdk = MagicMock()
    sdk.chat.completions.create.side_effect = [
        _resp([_choice(_msg("done"))]),
    ]

    # Must not raise. No metrics assertions — there's no collector to inspect.
    openai_tool_loop(
        sdk, model="gpt-4o-mini",
        messages=[{"role": "user", "content": "review"}],
        tools=[],
        tool_handlers={},
        max_iterations=5, max_tokens=1024, temperature=0.3,
        system=None, provider_label="openai",
    )
