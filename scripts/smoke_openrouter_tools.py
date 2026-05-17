#!/usr/bin/env python3
"""Tool-calling + strict json_schema smoke test for OpenRouter-hosted models.

Mirrors the production code path in ``src/revue/core/tool_loop.py``:
``response_format`` (strict three-state schema) is passed on EVERY turn
alongside the OpenAI function-tool list. The model is expected to either
emit ``tool_calls`` (loop continues) or final schema-conformant content
(loop exits).

Scenario: review a small diff whose verdict depends on a file the model
must fetch via ``read_file``. Validates:

  1. The model emits a tool_call (not just inline content).
  2. Tool arguments parse as valid JSON conforming to the tool schema.
  3. After receiving the tool result, the model produces schema-valid
     final output via the three-state response_format.
  4. The loop terminates within MAX_TURNS.

Default target is ``qwen/qwen3-coder-next``. Pass ``--model`` to test others.

Usage:
    direnv exec . python3 scripts/smoke_openrouter_tools.py
    direnv exec . python3 scripts/smoke_openrouter_tools.py --model qwen/qwen3-coder

Requires:
    OPENROUTER_API_KEY in env (loaded via .envrc / direnv).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


# Allow imports from src/ without installing the package
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from revue.core.finding_schema import (  # noqa: E402
    openai_response_format_for_three_state,
)


# ---------------------------------------------------------------------------
# Scenario fixtures
# ---------------------------------------------------------------------------

_DIFF = """\
--- a/src/handler.py
+++ b/src/handler.py
@@ -1,4 +1,5 @@
 from .helper import helper
+from .multiplier import MULTIPLIER

 def process():
-    return helper()
+    return helper() * MULTIPLIER
"""

# Synthetic repo content the read_file tool will return on request.
# Note: helper.py defines what helper() returns; multiplier.py defines
# MULTIPLIER. A serious reviewer should fetch both before judging the change.
_FILE_CONTENTS: dict[str, str] = {
    "src/helper.py": "def helper():\n    return 42\n",
    "src/multiplier.py": "MULTIPLIER = 0  # WARNING: zero, will always produce 0\n",
    "src/handler.py": (
        "from .helper import helper\n"
        "from .multiplier import MULTIPLIER\n"
        "\n"
        "def process():\n"
        "    return helper() * MULTIPLIER\n"
    ),
}


_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read the full contents of a file from the repository by path. "
                "Use this to fetch context for symbols referenced in the diff."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to repo root, e.g. 'src/helper.py'.",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    },
]


_SYSTEM = (
    "You are a code reviewer. Review the unified diff and return ONE JSON "
    "object matching the three-state schema. When the diff references "
    "symbols imported from other files, you SHOULD use the read_file tool "
    "to inspect those files before forming a verdict — a verdict made "
    "without reading the referenced definitions is unreliable. Your final "
    "JSON object MUST include a top-level 'status' field set to 'findings', "
    "'clean', or 'error'. Do not wrap the JSON in markdown."
)

_USER = f"Review this diff:\n\n```diff\n{_DIFF}\n```\n\nReturn the JSON after gathering any context you need."


_MAX_TURNS = 5
_MAX_TOKENS = 2048


# ---------------------------------------------------------------------------
# Validation (mirrors smoke_openrouter_schema.py)
# ---------------------------------------------------------------------------


def _validate_three_state(payload: dict[str, Any]) -> tuple[bool, str]:
    status = payload.get("status")
    if status == "findings":
        findings = payload.get("findings")
        if not isinstance(findings, list):
            return False, "findings: missing or not a list"
        for i, f in enumerate(findings):
            required = {"file_path", "line_number", "severity", "issue",
                        "suggestion", "confidence", "category"}
            missing = required - set(f.keys())
            if missing:
                return False, f"findings[{i}]: missing keys {sorted(missing)}"
        return True, f"findings branch ({len(findings)} item(s))"
    if status == "clean":
        if "summary" not in payload or "confidence" not in payload:
            return False, "clean: missing summary or confidence"
        return True, "clean branch"
    if status == "error":
        err = payload.get("error")
        if not isinstance(err, dict):
            return False, "error: missing error object"
        if "code" not in err or "message" not in err:
            return False, "error: missing code or message"
        return True, f"error branch (code={err['code']!r})"
    return False, f"unknown status: {status!r}"


# ---------------------------------------------------------------------------
# Loop
# ---------------------------------------------------------------------------


def run_tool_loop(model: str) -> int:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("FAIL — OPENROUTER_API_KEY not set in environment.", file=sys.stderr)
        return 2

    try:
        import openai  # type: ignore[import-not-found]
    except ImportError:
        print("FAIL — openai package not installed.", file=sys.stderr)
        return 2

    client = openai.OpenAI(
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        timeout=60.0,
        default_headers={
            "HTTP-Referer": "https://revue.io",
            "X-Title": "Revue AI Code Review (tool-call smoke test)",
        },
    )

    response_format = openai_response_format_for_three_state()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": _USER},
    ]

    print(f"→ model: {model}")
    print(f"→ tools: {[t['function']['name'] for t in _TOOLS]}")
    print(f"→ schema: three-state (strict={response_format['json_schema']['strict']})")
    print(f"→ max_turns: {_MAX_TURNS}, max_tokens_per_turn: {_MAX_TOKENS}")
    print()

    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_tool_calls = 0
    final_text = ""
    final_finish_reason: str | None = None
    t_start = time.monotonic()

    for turn in range(1, _MAX_TURNS + 1):
        print(f"=== turn {turn} ===")
        t0 = time.monotonic()
        # Diagnostic: force the model to emit a tool_call on turn 1 so we can
        # distinguish "tool mechanism broken" from "model declined to use tool".
        # Subsequent turns revert to auto so the model can finalize.
        tool_choice = "required" if turn == 1 else "auto"
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=_TOOLS,
                tool_choice=tool_choice,
                response_format=response_format,
                max_tokens=_MAX_TOKENS,
                temperature=0.3,
            )
        except Exception as exc:
            print(f"  ERROR after {time.monotonic() - t0:.1f}s — {type(exc).__name__}: {exc}")
            return 1
        elapsed = time.monotonic() - t0

        choice = resp.choices[0]
        msg = choice.message
        usage = getattr(resp, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
        completion_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
        total_prompt_tokens += prompt_tokens
        total_completion_tokens += completion_tokens
        finish_reason = choice.finish_reason
        tool_calls = getattr(msg, "tool_calls", None) or []

        print(f"  {elapsed:.1f}s, tool_choice={tool_choice}, tokens: prompt={prompt_tokens} completion={completion_tokens}, "
              f"finish_reason={finish_reason}, tool_calls={len(tool_calls)}")

        if tool_calls:
            # Echo assistant message into history with tool_calls
            messages.append({
                "role": "assistant",
                "content": getattr(msg, "content", None),
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
                total_tool_calls += 1
                fn_name = tc.function.name
                raw_args = tc.function.arguments or "{}"
                try:
                    args = json.loads(raw_args)
                    arg_str = json.dumps(args)
                except json.JSONDecodeError as exc:
                    print(f"  → {fn_name}(<invalid JSON: {exc}>) — surfacing error to model")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": f"Error: tool arguments were not valid JSON: {exc.msg}",
                    })
                    continue

                if fn_name == "read_file":
                    path = args.get("path", "")
                    content = _FILE_CONTENTS.get(
                        path, f"<error: file not found in synthetic repo: {path!r}>"
                    )
                    print(f"  → read_file({arg_str}) → {len(content)} chars")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": content,
                    })
                else:
                    print(f"  → unknown tool: {fn_name!r}")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": f"Error: unknown tool {fn_name!r}",
                    })
            print()
            continue

        # No tool calls — final response.
        final_text = getattr(msg, "content", None) or ""
        final_finish_reason = finish_reason
        print(f"  → final content: {len(final_text)} chars")
        print()
        break
    else:
        # Hit MAX_TURNS without exit
        print(f"FAIL — exceeded max_turns ({_MAX_TURNS}) without final content.")
        return 1

    total_elapsed = time.monotonic() - t_start
    print("=== final response ===")
    print(final_text[:1500])
    if len(final_text) > 1500:
        print(f"... [truncated, total {len(final_text)} chars]")
    print()

    print("=== summary ===")
    print(f"  total time:       {total_elapsed:.1f}s")
    print(f"  total tool_calls: {total_tool_calls}")
    print(f"  total tokens:     prompt={total_prompt_tokens} completion={total_completion_tokens}")
    print(f"  final finish:     {final_finish_reason}")

    # Verdict gates
    if total_tool_calls == 0:
        print("FAIL — model never called the read_file tool (didn't exercise the tool loop).")
        return 1

    if not final_text.strip():
        print("FAIL — final response was empty.")
        return 1

    try:
        payload = json.loads(final_text)
    except json.JSONDecodeError as exc:
        print(f"FAIL — final response is not valid JSON: {exc}")
        return 1

    ok, reason = _validate_three_state(payload)
    if ok:
        print(f"PASS — tool loop + strict json_schema honored: {reason}")
        return 0
    print(f"FAIL — final schema verdict: {reason}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--model",
        default="qwen/qwen3-coder-next",
        help="OpenRouter model slug (default: qwen/qwen3-coder-next)",
    )
    args = parser.parse_args()
    return run_tool_loop(args.model)


if __name__ == "__main__":
    sys.exit(main())
