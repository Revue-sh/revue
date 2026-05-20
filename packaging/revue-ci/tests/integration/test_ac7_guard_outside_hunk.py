"""REVUE-241 AC7 regression: tool-use must suppress the "guard outside hunk"
false positive that Maya kept filing on PR #25.

What this test proves (hermetic):
  The wiring is correct. When a reviewer is loaded with a ``ReadFileTool``,
  ``LoadedAgent._invoke_client`` dispatches to ``complete_with_tools`` instead
  of ``complete``, the underlying ``anthropic_tool_loop`` calls the tool
  handler with the path the model requested, and the loop's final response is
  the one returned to the caller. Concretely:

    * Control  — ``read_file_tool=None`` → ``client.complete`` is called →
      the model (here, our stub) files a "missing None guard" finding.
    * Treatment — ``read_file_tool=<spy>`` → ``client.complete_with_tools``
      is called → the model first issues a ``read_file`` tool_use, the spy
      records the path argument, the model sees the file (which contains the
      None guard above the hunk) and returns zero findings.

  The behavioural claim — "a real Maya, looking at a real file, will suppress
  the false positive" — is what a separate ``--live`` exercise validates.
  This test is the *wiring* regression: if a future refactor removes the
  ``read_file_tool`` parameter, drops the dispatch in ``_invoke_client``, or
  short-circuits the tool loop, this test fails.

AC7 → fixture mapping:
  PR #25 surfaced 7/17 prose-only findings that were factually wrong; the
  canonical class is "guard exists in the file but outside the diff hunk".
  This fixture encodes that pattern: a Python function with a ``None`` guard
  at the top of the body, then a hunk further down that *uses* the value
  without the guard visible. Without full-file context the diff alone reads
  like a null-deref bug. AC7 calls for ≤2/17 such false positives once
  tool-use is enabled; this test pins the wiring that makes that achievable.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import revue_core
from revue_core.core.agent_loader import LoadedAgent, load_agent_definition
from revue_core.core.ai_client import CompletionResult, TokenUsage
from revue_core.core.models import FileChange
from revue_core.core.tools.read_file import ReadFileTool, ToolResult


# ---------------------------------------------------------------------------
# Fixture: the file + diff that triggers the false-positive class
# ---------------------------------------------------------------------------

# The full file contains a ``None`` guard at line 3 that returns early.
# The diff hunk (below) only touches lines further down, so a reviewer that
# sees the diff alone has no way to know the guard exists.
FIXTURE_FILE_PATH = "ac7_target.py"
FIXTURE_FILE_BODY = (
    "def process(value):\n"
    "    # AC7 fixture: the guard below sits OUTSIDE the diff hunk.\n"
    "    if value is None:\n"
    "        return None\n"
    "    # ... 10 lines of unrelated logic ...\n"
    "    intermediate = value + 1\n"
    "    intermediate = intermediate * 2\n"
    "    intermediate = intermediate - 3\n"
    "    return _finish(intermediate)\n"
    "\n"
    "\n"
    "def _finish(intermediate):\n"
    "    return intermediate\n"
)

# The diff hunk only shows the bottom of process() — the guard at line 3 is
# invisible. A diff-only reviewer is liable to file a null-deref finding.
FIXTURE_DIFF = (
    "@@ -7,3 +7,4 @@ def process(value):\n"
    "     intermediate = value + 1\n"
    "     intermediate = intermediate * 2\n"
    "+    intermediate = intermediate - 3\n"
    "     return _finish(intermediate)\n"
)


@pytest.fixture
def fixture_repo(tmp_path: Path) -> Path:
    """Write the fixture file to a temporary repo root so ReadFileTool can
    resolve it via its normal sandbox path."""
    (tmp_path / FIXTURE_FILE_PATH).write_text(FIXTURE_FILE_BODY, encoding="utf-8")
    return tmp_path


@pytest.fixture
def fixture_change() -> FileChange:
    return FileChange(
        file_path=FIXTURE_FILE_PATH,
        change_type="modified",
        additions=1,
        deletions=0,
        diff=FIXTURE_DIFF,
    )


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

# Control response: a plausible false-positive finding the diff-only reviewer
# would file. Phrased like Maya's actual style so the regression is honest.
# REVUE-246: wrapped in the three-state envelope; the legacy bare-array shape
# is now classified as an error (AC8 atomic migration).
_CONTROL_FINDING_JSON = (
    '{"status": "findings", "findings": ['
    '{"file_path": "ac7_target.py", "line_number": 10, "severity": "major", '
    '"issue": "value may be None — call to _finish will dereference None", '
    '"suggestion": "Guard with `if value is None: return None` before use.", '
    '"confidence": 0.85}'
    ']}'
)

# Treatment response (post tool-use): a clean verdict, because the model has now
# seen the file and observed the guard at line 3.
_TREATMENT_FINAL_JSON = (
    '{"status": "clean", "summary": "guard at line 3 covers the dereference", '
    '"confidence": 0.9}'
)


def _anth_text_response(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason="end_turn",
        usage=SimpleNamespace(
            input_tokens=10, output_tokens=5,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        ),
    )


def _anth_tool_use_response(tool_id: str, path: str) -> SimpleNamespace:
    return SimpleNamespace(
        content=[
            SimpleNamespace(
                type="tool_use",
                id=tool_id,
                name="read_file",
                input={"path": path},
            )
        ],
        stop_reason="tool_use",
        usage=SimpleNamespace(
            input_tokens=10, output_tokens=5,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        ),
    )


class _ScriptedClient:
    """Minimal AIClient stand-in.

    * ``complete``           — control path; returns the scripted finding.
    * ``complete_with_tools`` — treatment path; drives the real
      ``anthropic_tool_loop`` with a fake SDK that scripts a tool_use →
      final-text exchange. The real loop dispatches the real tool handler.
    """

    def __init__(self) -> None:
        self.complete_calls: list[dict] = []
        self.complete_with_tools_calls: list[dict] = []

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
        self.complete_calls.append({"messages": messages, "agent_name": agent_name})
        return CompletionResult(text=_CONTROL_FINDING_JSON, usage=TokenUsage())

    def complete_with_tools(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]],
        tool_handlers: "dict[str, Any]",
        max_iterations: "int | None" = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        system: "str | list[dict[str, Any]] | None" = None,
        agent_name: "str | None" = None,
        output_config: "dict[str, Any] | None" = None,
    ) -> CompletionResult:
        self.complete_with_tools_calls.append({
            "messages": messages,
            "agent_name": agent_name,
            "tools": tools,
            "handlers": list(tool_handlers.keys()),
        })

        # Drive the *real* tool loop with a scripted SDK so the handler
        # dispatch is exercised end-to-end.
        from revue_core.core import tool_loop as _tl
        from unittest.mock import MagicMock

        fake_sdk = MagicMock()
        fake_sdk.messages.create.side_effect = [
            _anth_tool_use_response("tu_1", FIXTURE_FILE_PATH),
            _anth_text_response(_TREATMENT_FINAL_JSON),
        ]
        return _tl.anthropic_tool_loop(
            fake_sdk,
            model="claude-test",
            messages=messages,
            tools=tools,
            tool_handlers=tool_handlers,
            max_iterations=max_iterations or 5,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            agent_name=agent_name,
            output_config=output_config,
        )


class _SpyReadFileTool:
    """Wraps a real ReadFileTool so the test can assert which paths the
    agent asked for. The advisor flagged that the load-bearing assertion is
    ``path == FIXTURE_FILE_PATH`` — without it the test would pass even if
    the loop never actually invoked our handler."""

    def __init__(self, inner: ReadFileTool) -> None:
        self._inner = inner
        self.execute_calls: list[str] = []

    def tool_definition(self) -> dict:
        return self._inner.tool_definition()

    def execute(self, *, path: str) -> ToolResult:
        self.execute_calls.append(path)
        return self._inner.execute(path=path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _maya_definition() -> Any:
    """Load Maya from her real definition file — no agent-prompt drift."""
    maya_md = Path(revue_core.__file__).parent / "agents" / "maya.md"
    return load_agent_definition(maya_md)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_control_path_files_the_false_positive_finding(
    fixture_repo: Path, fixture_change: FileChange,
) -> None:
    """Without tool-use, Maya (here, our scripted stand-in) files a
    "missing None guard" finding because the guard is outside the hunk.
    This pins the failure mode the AC7 fix targets."""
    client = _ScriptedClient()
    maya = LoadedAgent(
        _maya_definition(), client, max_tokens=1024, read_file_tool=None,
    )

    findings = maya.analyse([fixture_change])

    assert len(client.complete_calls) == 1, (
        "control path must route through complete(), not complete_with_tools()"
    )
    assert len(client.complete_with_tools_calls) == 0
    assert len(findings) == 1
    assert findings[0].file_path == FIXTURE_FILE_PATH
    assert "none" in findings[0].issue.lower(), (
        "control finding should be the canonical None-guard false positive"
    )


def test_treatment_path_routes_through_tool_use_and_invokes_handler(
    fixture_repo: Path, fixture_change: FileChange,
) -> None:
    """With tool-use, ``_invoke_client`` must dispatch to
    ``complete_with_tools``, the loop must call the ``read_file`` handler
    with the fixture's path, and the loop's final-text response is what
    the caller receives."""
    client = _ScriptedClient()
    inner_tool = ReadFileTool(
        repo_root=fixture_repo, allowed_paths={FIXTURE_FILE_PATH},
    )
    spy = _SpyReadFileTool(inner_tool)
    maya = LoadedAgent(
        _maya_definition(), client, max_tokens=1024, read_file_tool=spy,
    )

    findings = maya.analyse([fixture_change])

    assert len(client.complete_with_tools_calls) == 1, (
        "treatment path must route through complete_with_tools()"
    )
    assert len(client.complete_calls) == 0, (
        "treatment path must NOT call complete() — that bypasses the tool loop"
    )
    assert spy.execute_calls == [FIXTURE_FILE_PATH], (
        f"read_file handler must be invoked with {FIXTURE_FILE_PATH}; "
        f"saw {spy.execute_calls!r}"
    )
    assert findings.is_clean, (
        "treatment path returns the loop's final response — scripted as clean"
    )
    assert len(findings) == 0, "clean verdict has no findings"


def test_tool_use_causally_changes_the_outcome(
    fixture_repo: Path, fixture_change: FileChange,
) -> None:
    """The whole point of AC7: the *same* agent + diff produces a finding
    on the control path and zero findings on the treatment path. If a
    future refactor accidentally short-circuits ``read_file_tool``, this
    delta collapses and the test fires.

    This assertion is intentionally paired with the wiring assertions
    above — by itself it could pass for the wrong reason (two different
    scripts), but together they prove the right code path was reached
    *and* the outcome differs as AC7 requires."""
    maya_def = _maya_definition()

    control_client = _ScriptedClient()
    control_findings = LoadedAgent(
        maya_def, control_client, max_tokens=1024, read_file_tool=None,
    ).analyse([fixture_change])

    treatment_client = _ScriptedClient()
    inner_tool = ReadFileTool(
        repo_root=fixture_repo, allowed_paths={FIXTURE_FILE_PATH},
    )
    spy = _SpyReadFileTool(inner_tool)
    treatment_findings = LoadedAgent(
        maya_def, treatment_client, max_tokens=1024, read_file_tool=spy,
    ).analyse([fixture_change])

    assert len(control_findings) > len(treatment_findings), (
        f"tool-use must suppress findings: control={len(control_findings)} "
        f"treatment={len(treatment_findings)}"
    )
    assert spy.execute_calls, "treatment path must actually have called read_file"
