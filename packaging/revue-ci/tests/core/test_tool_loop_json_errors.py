"""Regression: openai_tool_loop must surface JSON parse errors to the model.

Previously, a malformed ``function.arguments`` blob was silently parsed as
``{}`` and forwarded to the handler. The model never saw the parse error,
burned tool-iterations on retries with the same broken args, and the
handler ran with empty input — producing confusing downstream errors
instead of a self-correcting loop.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from revue_core.core.tool_loop import openai_tool_loop, ToolResult


class _FakeChoice:
    def __init__(self, *, content: str | None = None, tool_calls=None) -> None:
        self.message = SimpleNamespace(content=content, tool_calls=tool_calls)


class _FakeResponse:
    def __init__(self, *, content: str | None = None, tool_calls=None) -> None:
        self.choices = [_FakeChoice(content=content, tool_calls=tool_calls)]
        self.usage = SimpleNamespace(
            prompt_tokens=0, completion_tokens=0, total_tokens=0,
        )


class _ScriptedSDK:
    """Drives the loop with a pre-scripted sequence of responses."""

    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)
        self.received_messages_per_call: list[list[dict]] = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create),
        )

    def _create(self, **kwargs: Any) -> _FakeResponse:
        self.received_messages_per_call.append(list(kwargs["messages"]))
        return self._responses.pop(0)


def _tool_call(call_id: str, name: str, arguments: str):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def test_malformed_json_arguments_surface_as_tool_error_to_model():
    """Round 1 has invalid JSON args → model gets an error in round 2's messages."""
    calls: list[dict] = []

    def echo_handler(**kwargs):
        calls.append(kwargs)
        return ToolResult(content="ran", is_error=False)

    scripted_sdk = _ScriptedSDK(
        responses=[
            # Round 1 — model emits a tool call with broken JSON.
            _FakeResponse(tool_calls=[_tool_call("call_1", "echo", "{not valid json")]),
            # Round 2 — model emits final answer; loop exits.
            _FakeResponse(content="ok"),
        ]
    )

    result = openai_tool_loop(
        scripted_sdk,
        model="m",
        messages=[{"role": "user", "content": "go"}],
        tools=[{"name": "echo", "description": "x", "input_schema": {"type": "object", "properties": {}}}],
        tool_handlers={"echo": echo_handler},
        max_iterations=3,
        max_tokens=64,
        temperature=0.0,
        system=None,
        provider_label="openai",
    )

    # The handler must NOT have been called with empty args — instead the loop
    # should have skipped the dispatch entirely and surfaced the parse error.
    assert calls == [], (
        f"Handler should not run when JSON args are malformed; "
        f"silent default-to-empty hides the parse error from the model. "
        f"Got calls={calls}"
    )

    # Round 2's messages must include a tool role entry naming the parse error.
    round_2_messages = scripted_sdk.received_messages_per_call[1]
    tool_messages = [m for m in round_2_messages if m.get("role") == "tool"]
    assert any(
        "not valid json" in str(m.get("content", "")).lower()
        or "json" in str(m.get("content", "")).lower()
        for m in tool_messages
    ), (
        f"Round 2 messages must include a tool-role error so the model can "
        f"self-correct. Got tool messages: {tool_messages}"
    )
    assert result.text == "ok"


def test_valid_json_arguments_still_dispatch_normally():
    """Guards against the fix accidentally suppressing valid tool calls."""
    calls: list[dict] = []

    def echo_handler(**kwargs):
        calls.append(kwargs)
        return ToolResult(content=f"saw {kwargs}", is_error=False)

    scripted_sdk = _ScriptedSDK(
        responses=[
            _FakeResponse(tool_calls=[_tool_call("call_1", "echo", '{"x": 42}')]),
            _FakeResponse(content="done"),
        ]
    )

    openai_tool_loop(
        scripted_sdk,
        model="m",
        messages=[{"role": "user", "content": "go"}],
        tools=[{"name": "echo", "description": "x", "input_schema": {"type": "object", "properties": {"x": {"type": "integer"}}}}],
        tool_handlers={"echo": echo_handler},
        max_iterations=3,
        max_tokens=64,
        temperature=0.0,
        system=None,
        provider_label="openai",
    )

    assert calls == [{"x": 42}], f"Handler should receive parsed args; got {calls}"
