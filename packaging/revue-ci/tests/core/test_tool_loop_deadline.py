"""REVUE-339: cooperative deadline + finalize budget reservation.

When an agent's iteration loop runs long, the global wall-clock timeout
(``agent_timeout_seconds``) can fire mid-finalize and discard findings the
model already synthesised. This story reserves a slice of wall-clock budget
(``finalize_reserve``) for the forced-finalize call: once the loop crosses
``deadline - finalize_reserve`` it stops iterating and goes straight to a
tool-free finalize call whose HTTP ``timeout=`` is sized to the remaining
wall-clock so the SDK can never outlast it.

The deadline is a single global value computed once in
``run_agents_parallel`` and shared by every concurrent agent (NOT per-agent —
see REVUE-320 for per-agent overrides). These tests exercise the
``openai_tool_loop`` consumer of that deadline.
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from revue_core.core.tool_loop import openai_tool_loop, DEFAULT_FINALIZE_RESERVE_SECONDS
from revue_core.core.tools import ToolResult


# ---------------------------------------------------------------------------
# Helpers (mirror the forced-finalize test fixtures)
# ---------------------------------------------------------------------------

def _openai_msg(content: "str | None", tool_calls: "list | None" = None) -> SimpleNamespace:
    return SimpleNamespace(content=content, tool_calls=tool_calls or [])


def _openai_choice(message: SimpleNamespace, finish_reason: str = "stop") -> SimpleNamespace:
    return SimpleNamespace(message=message, finish_reason=finish_reason)


def _openai_resp(choices: list, usage: "Any | None" = None) -> SimpleNamespace:
    return SimpleNamespace(
        choices=choices,
        usage=usage or SimpleNamespace(
            prompt_tokens=10, completion_tokens=5, total_tokens=15,
        ),
    )


def _openai_tool_call(tc_id: str, name: str, args_json: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=tc_id,
        function=SimpleNamespace(name=name, arguments=args_json),
    )


def _read_file_handler(path: str) -> ToolResult:
    return ToolResult(content=f"contents of {path}", is_error=False)


def _tool_use_resp(i: int) -> SimpleNamespace:
    return _openai_resp([_openai_choice(_openai_msg(
        None, tool_calls=[_openai_tool_call(f"tc_{i}", "read_file", f'{{"path":"f{i}.py"}}')]
    ), finish_reason="tool_calls")])


# ---------------------------------------------------------------------------
# AC5 — default reserve
# ---------------------------------------------------------------------------

def test_default_finalize_reserve_is_thirty_seconds() -> None:
    """AC5: finalize_reserve defaults to 30s (empirical from reasoning models)."""
    assert DEFAULT_FINALIZE_RESERVE_SECONDS == 30.0


# ---------------------------------------------------------------------------
# AC8 case 1 — deadline already in the past: skip iterations, go to finalize
# ---------------------------------------------------------------------------

def test_deadline_in_past_skips_iterations_and_finalizes() -> None:
    """A deadline already past (minus reserve) must short-circuit the loop into
    the tool-free finalize call without issuing any tool-use iterations."""
    sdk = MagicMock()
    sdk.chat.completions.create.side_effect = [
        # Only the finalize call should fire — no tool-use iterations.
        _openai_resp([_openai_choice(_openai_msg('{"findings": []}'))]),
    ]

    # deadline is already in the past, so deadline - reserve is also past.
    past_deadline = time.monotonic() - 100.0

    result = openai_tool_loop(
        sdk, model="gpt-4o-mini",
        messages=[{"role": "user", "content": "review"}],
        tools=[{"name": "read_file", "input_schema": {"type": "object"}}],
        tool_handlers={"read_file": _read_file_handler},
        max_iterations=5, max_tokens=1024, temperature=0.3,
        system=None, provider_label="openai",
        deadline=past_deadline,
        finalize_reserve=DEFAULT_FINALIZE_RESERVE_SECONDS,
    )

    # Exactly one call: the forced finalize. No tool-use iterations ran.
    assert sdk.chat.completions.create.call_count == 1
    final_kwargs = sdk.chat.completions.create.call_args_list[-1][1]
    assert final_kwargs.get("tools") in (None, [])
    assert result.text == '{"findings": []}'


# ---------------------------------------------------------------------------
# AC8 case 2 — deadline crossed mid-loop: break and finalize
# ---------------------------------------------------------------------------

def test_deadline_crossed_mid_loop_breaks_to_finalize(monkeypatch) -> None:
    """If the deadline minus reserve is crossed between iterations, the loop
    must break and issue the tool-free finalize even though max_iterations is
    not yet exhausted.

    ``time.monotonic`` is stubbed for determinism: the first iteration sees the
    deadline in the future (runs a tool call); the second iteration sees it
    crossed (breaks to finalize). This isolates the boundary logic from real
    wall-clock jitter.
    """
    sdk = MagicMock()
    sdk.chat.completions.create.side_effect = [
        _tool_use_resp(0),  # iteration 0 runs (deadline not yet crossed)
        # finalize call
        _openai_resp([_openai_choice(_openai_msg('{"findings": [{"x": 1}]}'))]),
    ]

    deadline = 1000.0
    reserve = 0.0  # check is simply ``now > deadline``

    # Sequence of monotonic() reads inside the loop:
    #   iter 0 deadline-check → 999.0 (before deadline, runs)
    #   iter 1 deadline-check → 1001.0 (past deadline, breaks)
    #   finalize timeout calc → 1001.0
    clock = iter([999.0, 1001.0, 1001.0])
    monkeypatch.setattr(
        "revue_core.core.tool_loop.time.monotonic",
        lambda: next(clock, 1001.0),
    )

    result = openai_tool_loop(
        sdk, model="gpt-4o-mini",
        messages=[{"role": "user", "content": "review"}],
        tools=[{"name": "read_file", "input_schema": {"type": "object"}}],
        tool_handlers={"read_file": _read_file_handler},
        max_iterations=10, max_tokens=1024, temperature=0.3,
        system=None, provider_label="openai",
        deadline=deadline,
        finalize_reserve=reserve,
    )

    # iteration 0's tool_use call + finalize = 2 calls; the loop broke to
    # finalize on iteration 1 instead of running all 10 iterations.
    assert sdk.chat.completions.create.call_count == 2
    final_kwargs = sdk.chat.completions.create.call_args_list[-1][1]
    assert final_kwargs.get("tools") in (None, [])
    assert result.text == '{"findings": [{"x": 1}]}'


# ---------------------------------------------------------------------------
# AC4 — finalize HTTP call passes a timeout= kwarg bounded by the deadline
# ---------------------------------------------------------------------------

def test_finalize_passes_http_timeout_bounded_by_deadline() -> None:
    """AC4: the finalize SDK call must pass timeout=max(1, deadline - now) so a
    slow finalize cannot outlast the wall clock."""
    sdk = MagicMock()
    sdk.chat.completions.create.side_effect = [
        _openai_resp([_openai_choice(_openai_msg('{"findings": []}'))]),
    ]

    now = time.monotonic()
    deadline = now + 12.0  # ~12s of wall-clock remaining when finalize fires

    openai_tool_loop(
        sdk, model="gpt-4o-mini",
        messages=[{"role": "user", "content": "review"}],
        tools=[{"name": "read_file", "input_schema": {"type": "object"}}],
        tool_handlers={"read_file": _read_file_handler},
        max_iterations=5, max_tokens=1024, temperature=0.3,
        system=None, provider_label="openai",
        deadline=now - 1.0,  # already past → straight to finalize
        finalize_reserve=0.0,
    )
    # ^ that run uses an already-past deadline so finalize fires; but we want to
    # assert the timeout reflects remaining budget. Re-run with a future deadline.

    sdk2 = MagicMock()
    sdk2.chat.completions.create.side_effect = [
        _openai_resp([_openai_choice(_openai_msg('{"findings": []}'))]),
    ]
    openai_tool_loop(
        sdk2, model="gpt-4o-mini",
        messages=[{"role": "user", "content": "review"}],
        tools=[{"name": "read_file", "input_schema": {"type": "object"}}],
        tool_handlers={"read_file": _read_file_handler},
        max_iterations=5, max_tokens=1024, temperature=0.3,
        system=None, provider_label="openai",
        deadline=now - 1.0,  # past → finalize immediately
        finalize_reserve=0.0,
    )
    finalize_kwargs = sdk2.chat.completions.create.call_args_list[-1][1]
    # When the deadline is already past, remaining budget floors at 1s.
    assert "timeout" in finalize_kwargs
    assert finalize_kwargs["timeout"] == pytest.approx(1.0, abs=0.01)


def test_finalize_timeout_reflects_remaining_wall_clock() -> None:
    """When wall-clock remains, the finalize timeout must be roughly that
    remaining budget (deadline - now), not a fixed constant."""
    sdk = MagicMock()
    sdk.chat.completions.create.side_effect = [
        _openai_resp([_openai_choice(_openai_msg('{"findings": []}'))]),
    ]

    now = time.monotonic()
    # deadline - reserve must already be past so we finalize on entry, but the
    # raw deadline still has budget for the finalize HTTP timeout.
    deadline = now + 8.0
    reserve = 20.0  # deadline - reserve = now - 12s → past → finalize on entry

    openai_tool_loop(
        sdk, model="gpt-4o-mini",
        messages=[{"role": "user", "content": "review"}],
        tools=[{"name": "read_file", "input_schema": {"type": "object"}}],
        tool_handlers={"read_file": _read_file_handler},
        max_iterations=5, max_tokens=1024, temperature=0.3,
        system=None, provider_label="openai",
        deadline=deadline,
        finalize_reserve=reserve,
    )

    finalize_kwargs = sdk.chat.completions.create.call_args_list[-1][1]
    assert "timeout" in finalize_kwargs
    # ~8s of raw wall-clock remained for finalize.
    assert finalize_kwargs["timeout"] == pytest.approx(8.0, abs=1.0)


# ---------------------------------------------------------------------------
# AC8 case 3 — finalize HTTP timeout fires: graceful, no crash
# ---------------------------------------------------------------------------

def test_finalize_http_timeout_does_not_crash_loop() -> None:
    """AC8: when the finalize HTTP call raises a timeout, the exception must
    propagate as a normal exception (handled one level up by agent_runner) —
    the loop must not swallow it into a silent empty result, but it must also
    not raise an unexpected error type."""
    import httpx

    sdk = MagicMock()
    sdk.chat.completions.create.side_effect = httpx.TimeoutException("finalize timed out")

    now = time.monotonic()

    with pytest.raises(httpx.TimeoutException):
        openai_tool_loop(
            sdk, model="gpt-4o-mini",
            messages=[{"role": "user", "content": "review"}],
            tools=[{"name": "read_file", "input_schema": {"type": "object"}}],
            tool_handlers={"read_file": _read_file_handler},
            max_iterations=5, max_tokens=1024, temperature=0.3,
            system=None, provider_label="openai",
            deadline=now - 1.0,  # past → finalize fires immediately and times out
            finalize_reserve=0.0,
        )


# ---------------------------------------------------------------------------
# Backward compatibility — deadline is optional; omitting it preserves
# the existing forced-finalize behaviour exactly.
# ---------------------------------------------------------------------------

def test_no_deadline_preserves_existing_behaviour() -> None:
    """AC8: normal flow unchanged when no deadline is supplied — the loop runs
    to max_iterations and the existing forced-finalize fires only on cap-hit."""
    sdk = MagicMock()
    sdk.chat.completions.create.side_effect = [
        _tool_use_resp(i) for i in range(3)
    ] + [
        _openai_resp([_openai_choice(_openai_msg('{"findings": []}'))]),
    ]

    result = openai_tool_loop(
        sdk, model="gpt-4o-mini",
        messages=[{"role": "user", "content": "review"}],
        tools=[{"name": "read_file", "input_schema": {"type": "object"}}],
        tool_handlers={"read_file": _read_file_handler},
        max_iterations=3, max_tokens=1024, temperature=0.3,
        system=None, provider_label="openai",
        # deadline omitted → no early break; cap-hit finalize as before.
    )

    assert sdk.chat.completions.create.call_count == 4
    assert result.text == '{"findings": []}'


# ---------------------------------------------------------------------------
# AC4 (refinement) — finalize timeout uses captured deadline budget
# ---------------------------------------------------------------------------

def test_finalize_timeout_uses_captured_deadline_budget(monkeypatch) -> None:
    """The finalize timeout must use the deadline budget captured at the loop
    break (when deadline was crossed), not a fresh time.monotonic() read in the
    finalize block. This ensures the logged ``remain`` value and the actual
    timeout passed to the HTTP call are consistent — no divergence, no redundant
    monotonic() syscall.
    """
    sdk = MagicMock()
    sdk.chat.completions.create.side_effect = [
        _tool_use_resp(0),  # iteration 0 runs (deadline not yet crossed)
        _tool_use_resp(1),  # iteration 1 runs (deadline still not crossed)
        # iteration 2 deadline-check breaks to finalize
        _openai_resp([_openai_choice(_openai_msg('{"findings": [{"x": 1}]}'))]),
    ]

    deadline = 1000.0
    reserve = 0.0

    # Sequence of monotonic() reads:
    #   iter 0 deadline-check → 990.0 (remain = 10.0, tool_use runs)
    #   iter 1 deadline-check → 995.0 (remain = 5.0, tool_use runs)
    #   iter 2 deadline-check → 1002.0 (past deadline, breaks to finalize)
    #   finalize timeout calc should use captured remain=5.0, NOT recompute to (1000 - 1002) = -2 / max(1.0, -2) = 1.0
    clock = iter([990.0, 995.0, 1002.0])  # The 1002.0 is "new" clock time; should use captured 5.0 instead
    monkeypatch.setattr(
        "revue_core.core.tool_loop.time.monotonic",
        lambda: next(clock, 1002.0),
    )

    result = openai_tool_loop(
        sdk, model="gpt-4o-mini",
        messages=[{"role": "user", "content": "review"}],
        tools=[{"name": "read_file", "input_schema": {"type": "object"}}],
        tool_handlers={"read_file": _read_file_handler},
        max_iterations=10, max_tokens=1024, temperature=0.3,
        system=None, provider_label="openai",
        deadline=deadline,
        finalize_reserve=reserve,
    )

    # The finalize timeout should be max(1.0, 5.0) = 5.0 (captured remain),
    # not max(1.0, -2.0) = 1.0 (fresh recompute after wall-clock advanced).
    finalize_kwargs = sdk.chat.completions.create.call_args_list[-1][1]
    assert "timeout" in finalize_kwargs
    assert finalize_kwargs["timeout"] == pytest.approx(5.0, abs=0.01)
