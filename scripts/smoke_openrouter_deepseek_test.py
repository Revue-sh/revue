#!/usr/bin/env python3
"""DeepSeek-V4-Pro evaluation harness for OpenRouter (REVUE-265).

A reusable smoke-test harness that exercises the supported-models quality
bar against any OpenRouter-hosted model. Built for the REVUE-265 spike on
``deepseek/deepseek-v4-pro`` but model-agnostic by design.

Compared to the existing two smoke scripts, this harness adds:

  * **Per-scenario repetition** — each scenario runs ``--trials`` times so
    determinism / variance can be observed across runs (no seed, parity
    with production ``temperature=0.3``).
  * **Tool-choice matrix** — the tool-calling scenario runs with both
    ``tool_choice_first_turn=auto`` and ``tool_choice_first_turn=required``
    so the report can state empirically which value the model honours.
  * **Raw output capture** — every final JSON payload (schema scenarios)
    and final tool-loop response is written to ``--out-dir`` for direct
    inclusion in the eval doc. Note: files are written, not truncated —
    re-running with the same ``--out-dir`` overwrites matching trial
    files but does NOT remove unrelated leftovers from prior runs.
  * **Aggregate summary** — pass-rates, mean latency and mean token usage
    per scenario.

Schema source of truth: ``openai_response_format_for_three_state()`` from
``src/revue/core/finding_schema.py`` — identical to production reviewer
clients (REVUE-241 wiring).

Usage:
    direnv exec . python3 scripts/smoke_openrouter_deepseek_test.py
    direnv exec . python3 scripts/smoke_openrouter_deepseek_test.py \\
        --model qwen/qwen3-coder-next --trials 1 --out-dir /tmp/qwen-baseline

Exit codes:
    0 — every trial passed schema AND landed on the expected branch
    1 — at least one trial schema-OK but branch mismatch (model-judgment)
    2 — at least one trial failed schema or raised a transport error

Requires:
    OPENROUTER_API_KEY in env (loaded via .envrc / direnv).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# Allow imports from src/ without installing the package
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from revue.core.finding_schema import (  # noqa: E402
    openai_response_format_for_three_state,
)


# ---------------------------------------------------------------------------
# Schema scenarios (mirror smoke_openrouter_schema.py)
# ---------------------------------------------------------------------------

_BUGGY_DIFF = """\
--- a/calculator.py
+++ b/calculator.py
@@ -1,3 +1,6 @@
 def divide(a, b):
-    return a / b
+    return a / b  # bug: no guard against b == 0
+
+if __name__ == "__main__":
+    print(divide(10, 0))
"""

_CLEAN_DIFF = """\
--- a/safe_divide.py
+++ b/safe_divide.py
@@ -1,5 +1,6 @@
 def safe_divide(a, b):
+    \"\"\"Return a / b, or None if b is zero.\"\"\"
     if b == 0:
         return None
     return a / b
"""


_SYSTEM_REVIEW = (
    "You are a code reviewer. Review the unified diff and return a single "
    "JSON object matching the schema. Pick exactly one of the three "
    "branches: 'findings' (one or more real issues), 'clean' (no issues), "
    "or 'error' (cannot review). Be honest — if the diff has no issues, "
    "use the clean branch. Do not wrap the JSON in markdown."
)

_SYSTEM_ERROR = (
    "You are a code reviewer. A required file-reading tool is unavailable, "
    "so you cannot inspect any code. Return exactly one JSON object matching "
    "the schema. You MUST use the 'error' branch. The object MUST contain "
    "both top-level keys: 'status' (set to 'error') and 'error' (with 'code' "
    "set to 'tool_unavailable' and a short 'message'). Return nothing else."
)


@dataclass
class SchemaScenario:
    name: str
    system: str
    user: str
    expected_branch: str


SCHEMA_SCENARIOS: list[SchemaScenario] = [
    SchemaScenario(
        name="findings",
        system=_SYSTEM_REVIEW,
        user=f"Review this diff:\n\n```diff\n{_BUGGY_DIFF}\n```\n\nReturn the JSON now.",
        expected_branch="findings",
    ),
    SchemaScenario(
        name="clean",
        system=_SYSTEM_REVIEW,
        user=f"Review this diff:\n\n```diff\n{_CLEAN_DIFF}\n```\n\nReturn the JSON now.",
        expected_branch="clean",
    ),
    SchemaScenario(
        name="error",
        system=_SYSTEM_ERROR,
        user="You need to review a diff but cannot access the repository. Return the JSON now.",
        expected_branch="error",
    ),
]


# ---------------------------------------------------------------------------
# Tool-calling scenario (mirror smoke_openrouter_tools.py)
# ---------------------------------------------------------------------------

_TOOL_DIFF = """\
--- a/src/handler.py
+++ b/src/handler.py
@@ -1,4 +1,5 @@
 from .helper import helper
+from .multiplier import MULTIPLIER

 def process():
-    return helper()
+    return helper() * MULTIPLIER
"""

_TOOL_FILE_CONTENTS: dict[str, str] = {
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

_TOOL_SYSTEM = (
    "You are a code reviewer. Review the unified diff and return ONE JSON "
    "object matching the three-state schema. When the diff references "
    "symbols imported from other files, you SHOULD use the read_file tool "
    "to inspect those files before forming a verdict — a verdict made "
    "without reading the referenced definitions is unreliable. Your final "
    "JSON object MUST include a top-level 'status' field set to 'findings', "
    "'clean', or 'error'. Do not wrap the JSON in markdown."
)

_TOOL_USER = (
    f"Review this diff:\n\n```diff\n{_TOOL_DIFF}\n```\n\n"
    "Return the JSON after gathering any context you need."
)

_TOOL_MAX_TURNS = 5
_MAX_TOKENS = 2048


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_three_state(payload: Any) -> tuple[bool, str]:
    """Manual shape check against THREE_STATE_SCHEMA. Returns (ok, reason)."""
    if not isinstance(payload, dict):
        return False, "payload is not a JSON object"
    status = payload.get("status")
    if status == "findings":
        findings = payload.get("findings")
        if not isinstance(findings, list):
            return False, "findings: missing or not a list"
        for i, f in enumerate(findings):
            if not isinstance(f, dict):
                return False, f"findings[{i}]: not an object"
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
# Schema scenario runner
# ---------------------------------------------------------------------------


@dataclass
class TrialResult:
    scenario: str
    trial: int
    ok_schema: bool
    branch_match: bool
    reason: str
    branch_observed: str | None
    branch_expected: str
    elapsed_s: float
    prompt_tokens: int | None
    completion_tokens: int | None
    raw: str
    finish_reason: str | None = None
    error: str | None = None
    # Tool-loop-only:
    tool_calls_total: int | None = None
    turns_used: int | None = None
    tool_choice_first_turn: str | None = None


def _create_client(api_key: str, title: str) -> Any:
    import openai  # type: ignore[import-not-found]
    return openai.OpenAI(
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        timeout=90.0,
        default_headers={
            "HTTP-Referer": "https://revue.io",
            "X-Title": f"Revue AI Code Review ({title})",
        },
    )


def _run_schema_trial(
    client: Any,
    model: str,
    scenario: SchemaScenario,
    trial: int,
    response_format: dict[str, Any],
) -> TrialResult:
    t0 = time.monotonic()
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": scenario.system},
                {"role": "user", "content": scenario.user},
            ],
            response_format=response_format,
            max_tokens=_MAX_TOKENS,
            temperature=0.3,
        )
    except Exception as exc:
        return TrialResult(
            scenario=scenario.name,
            trial=trial,
            ok_schema=False,
            branch_match=False,
            reason="request raised",
            branch_observed=None,
            branch_expected=scenario.expected_branch,
            elapsed_s=time.monotonic() - t0,
            prompt_tokens=None,
            completion_tokens=None,
            raw="",
            error=f"{type(exc).__name__}: {exc}",
        )

    elapsed = time.monotonic() - t0
    raw = resp.choices[0].message.content or ""
    usage = getattr(resp, "usage", None)
    prompt_tokens = getattr(usage, "prompt_tokens", None) if usage else None
    completion_tokens = getattr(usage, "completion_tokens", None) if usage else None
    finish_reason = resp.choices[0].finish_reason

    if not raw.strip():
        return TrialResult(
            scenario=scenario.name, trial=trial, ok_schema=False,
            branch_match=False, reason="empty response body",
            branch_observed=None, branch_expected=scenario.expected_branch,
            elapsed_s=elapsed,
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            raw=raw, finish_reason=finish_reason,
        )

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return TrialResult(
            scenario=scenario.name, trial=trial, ok_schema=False,
            branch_match=False, reason=f"invalid JSON: {exc}",
            branch_observed=None, branch_expected=scenario.expected_branch,
            elapsed_s=elapsed,
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            raw=raw, finish_reason=finish_reason,
        )

    ok, reason = _validate_three_state(payload)
    observed = payload.get("status") if isinstance(payload, dict) else None
    return TrialResult(
        scenario=scenario.name,
        trial=trial,
        ok_schema=ok,
        branch_match=(observed == scenario.expected_branch),
        reason=reason,
        branch_observed=observed,
        branch_expected=scenario.expected_branch,
        elapsed_s=elapsed,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        raw=raw,
        finish_reason=finish_reason,
    )


# ---------------------------------------------------------------------------
# Tool-loop trial
# ---------------------------------------------------------------------------


def _run_tool_trial(
    client: Any,
    model: str,
    trial: int,
    tool_choice_first_turn: str,
    response_format: dict[str, Any],
) -> TrialResult:
    """Run one trial of the tool-calling scenario.

    The model's tool_choice on turn 1 is parameterized: 'auto' (model
    decides) or 'required' (must emit a tool_call). Turns 2..N revert
    to 'auto' so the model can finalize.
    """
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _TOOL_SYSTEM},
        {"role": "user", "content": _TOOL_USER},
    ]

    total_prompt = 0
    total_completion = 0
    total_tool_calls = 0
    final_text = ""
    final_finish: str | None = None
    t_start = time.monotonic()
    turns_used = 0

    for turn in range(1, _TOOL_MAX_TURNS + 1):
        turns_used = turn
        tc_mode = tool_choice_first_turn if turn == 1 else "auto"
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=_TOOLS,
                tool_choice=tc_mode,
                response_format=response_format,
                max_tokens=_MAX_TOKENS,
                temperature=0.3,
            )
        except Exception as exc:
            return TrialResult(
                scenario="tool_loop",
                trial=trial,
                ok_schema=False,
                branch_match=False,
                reason="request raised",
                branch_observed=None,
                branch_expected="any",
                elapsed_s=time.monotonic() - t_start,
                prompt_tokens=total_prompt,
                completion_tokens=total_completion,
                raw="",
                error=f"turn {turn}: {type(exc).__name__}: {exc}",
                tool_calls_total=total_tool_calls,
                turns_used=turns_used,
                tool_choice_first_turn=tool_choice_first_turn,
            )

        choice = resp.choices[0]
        msg = choice.message
        usage = getattr(resp, "usage", None)
        total_prompt += getattr(usage, "prompt_tokens", 0) if usage else 0
        total_completion += getattr(usage, "completion_tokens", 0) if usage else 0
        final_finish = choice.finish_reason
        tool_calls = getattr(msg, "tool_calls", None) or []

        if tool_calls:
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
                except json.JSONDecodeError as exc:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": f"Error: tool arguments were not valid JSON: {exc.msg}",
                    })
                    continue
                if fn_name == "read_file":
                    path = args.get("path", "")
                    content = _TOOL_FILE_CONTENTS.get(
                        path, f"<error: file not found in synthetic repo: {path!r}>"
                    )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": content,
                    })
                else:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": f"Error: unknown tool {fn_name!r}",
                    })
            continue

        final_text = getattr(msg, "content", None) or ""
        break
    else:
        # Exhausted turns without final content
        return TrialResult(
            scenario="tool_loop",
            trial=trial,
            ok_schema=False,
            branch_match=False,
            reason=f"exceeded max_turns ({_TOOL_MAX_TURNS}) without final content",
            branch_observed=None,
            branch_expected="any",
            elapsed_s=time.monotonic() - t_start,
            prompt_tokens=total_prompt,
            completion_tokens=total_completion,
            raw="",
            finish_reason=final_finish,
            tool_calls_total=total_tool_calls,
            turns_used=turns_used,
            tool_choice_first_turn=tool_choice_first_turn,
        )

    elapsed = time.monotonic() - t_start

    # Verdict: tool was called AND final JSON conforms to schema.
    if total_tool_calls == 0:
        return TrialResult(
            scenario="tool_loop", trial=trial, ok_schema=False,
            branch_match=False,
            reason="no tool_calls emitted across whole loop",
            branch_observed=None, branch_expected="any",
            elapsed_s=elapsed, prompt_tokens=total_prompt,
            completion_tokens=total_completion, raw=final_text,
            finish_reason=final_finish, tool_calls_total=0,
            turns_used=turns_used,
            tool_choice_first_turn=tool_choice_first_turn,
        )

    if not final_text.strip():
        return TrialResult(
            scenario="tool_loop", trial=trial, ok_schema=False,
            branch_match=False, reason="final response empty",
            branch_observed=None, branch_expected="any",
            elapsed_s=elapsed, prompt_tokens=total_prompt,
            completion_tokens=total_completion, raw=final_text,
            finish_reason=final_finish, tool_calls_total=total_tool_calls,
            turns_used=turns_used,
            tool_choice_first_turn=tool_choice_first_turn,
        )

    try:
        payload = json.loads(final_text)
    except json.JSONDecodeError as exc:
        return TrialResult(
            scenario="tool_loop", trial=trial, ok_schema=False,
            branch_match=False, reason=f"final invalid JSON: {exc}",
            branch_observed=None, branch_expected="any",
            elapsed_s=elapsed, prompt_tokens=total_prompt,
            completion_tokens=total_completion, raw=final_text,
            finish_reason=final_finish, tool_calls_total=total_tool_calls,
            turns_used=turns_used,
            tool_choice_first_turn=tool_choice_first_turn,
        )

    ok, reason = _validate_three_state(payload)
    observed = payload.get("status") if isinstance(payload, dict) else None
    return TrialResult(
        scenario="tool_loop", trial=trial, ok_schema=ok,
        # For the tool loop, "branch_match" means: produced ANY valid branch
        # AND fetched context first. (Findings is the most likely correct
        # judgement given multiplier.py = 0, but we don't hard-fail clean.)
        branch_match=ok,
        reason=reason,
        branch_observed=observed, branch_expected="any",
        elapsed_s=elapsed, prompt_tokens=total_prompt,
        completion_tokens=total_completion, raw=final_text,
        finish_reason=final_finish, tool_calls_total=total_tool_calls,
        turns_used=turns_used,
        tool_choice_first_turn=tool_choice_first_turn,
    )


# ---------------------------------------------------------------------------
# Summary helpers
# ---------------------------------------------------------------------------


def _safe_mean(xs: list[float | int | None]) -> float | None:
    vals = [x for x in xs if x is not None]
    if not vals:
        return None
    return statistics.fmean(vals)


def _print_aggregate(results: list[TrialResult], scenario: str) -> None:
    subset = [r for r in results if r.scenario == scenario]
    if not subset:
        return
    n_ok = sum(1 for r in subset if r.ok_schema)
    n_branch = sum(1 for r in subset if r.ok_schema and r.branch_match)
    mean_latency = _safe_mean([r.elapsed_s for r in subset])
    mean_prompt = _safe_mean([r.prompt_tokens for r in subset])
    mean_completion = _safe_mean([r.completion_tokens for r in subset])
    branches = sorted({r.branch_observed for r in subset if r.branch_observed})

    def _fmt(value: float | None, spec: str) -> str:
        return f"{value:{spec}}" if value is not None else "n/a"

    print(f"  [{scenario:<10}] schema={n_ok}/{len(subset)}, "
          f"branch_match={n_branch}/{len(subset)}, "
          f"latency_mean={_fmt(mean_latency, '.1f')}s, "
          f"tokens_mean=p{_fmt(mean_prompt, '.0f')}/c{_fmt(mean_completion, '.0f')}, "
          f"branches_observed={branches}")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_eval(
    model: str,
    trials: int,
    out_dir: Path | None,
    skip_tools: bool,
) -> int:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("FAIL — OPENROUTER_API_KEY not set in environment.", file=sys.stderr)
        return 2

    try:
        import openai  # noqa: F401  # type: ignore[import-not-found]
    except ImportError:
        print("FAIL — openai package not installed.", file=sys.stderr)
        return 2

    client = _create_client(api_key, "REVUE-265 eval")
    response_format = openai_response_format_for_three_state()

    print(f"→ model:   {model}")
    print(f"→ trials:  {trials} per scenario")
    print(f"→ schema:  three-state (strict={response_format['json_schema']['strict']})")
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        print(f"→ out_dir: {out_dir}")
    print()

    all_results: list[TrialResult] = []

    # --- Schema scenarios ---
    for scenario in SCHEMA_SCENARIOS:
        print(f"=== scenario: {scenario.name} (expect: {scenario.expected_branch}) ===")
        for trial in range(1, trials + 1):
            result = _run_schema_trial(
                client, model, scenario, trial, response_format,
            )
            all_results.append(result)

            if result.error:
                print(f"  trial {trial}: ERROR after {result.elapsed_s:.1f}s — {result.error}")
            else:
                marker = "PASS" if result.ok_schema and result.branch_match else (
                    "SCHEMA-OK" if result.ok_schema else "FAIL"
                )
                print(
                    f"  trial {trial}: [{marker}] {result.elapsed_s:.1f}s, "
                    f"tokens p{result.prompt_tokens}/c{result.completion_tokens}, "
                    f"branch={result.branch_observed!r}, verdict={result.reason}"
                )

            if out_dir:
                _dump_trial(out_dir, model, result)
        print()

    # --- Tool-call matrix ---
    if not skip_tools:
        for tc_first in ("auto", "required"):
            print(f"=== scenario: tool_loop (tool_choice_first_turn={tc_first}) ===")
            for trial in range(1, trials + 1):
                result = _run_tool_trial(
                    client, model, trial, tc_first, response_format,
                )
                all_results.append(result)

                if result.error:
                    print(f"  trial {trial}: ERROR after {result.elapsed_s:.1f}s — {result.error}")
                else:
                    marker = "PASS" if result.ok_schema else "FAIL"
                    print(
                        f"  trial {trial}: [{marker}] {result.elapsed_s:.1f}s, "
                        f"turns={result.turns_used}, tool_calls={result.tool_calls_total}, "
                        f"tokens p{result.prompt_tokens}/c{result.completion_tokens}, "
                        f"final_branch={result.branch_observed!r}, verdict={result.reason}"
                    )

                if out_dir:
                    _dump_trial(out_dir, model, result)
            print()

    # --- Aggregate summary ---
    print("=== aggregate summary ===")
    for scenario in SCHEMA_SCENARIOS:
        _print_aggregate(all_results, scenario.name)
    if not skip_tools:
        # Group tool_loop by tool_choice_first_turn
        for tc_first in ("auto", "required"):
            subset = [r for r in all_results
                      if r.scenario == "tool_loop" and r.tool_choice_first_turn == tc_first]
            if not subset:
                continue
            n_ok = sum(1 for r in subset if r.ok_schema)
            n_emitted_tool = sum(1 for r in subset if (r.tool_calls_total or 0) > 0)
            mean_latency = _safe_mean([r.elapsed_s for r in subset])
            mean_calls = _safe_mean([r.tool_calls_total for r in subset])
            lat_s = f"{mean_latency:.1f}" if mean_latency is not None else "n/a"
            calls_s = f"{mean_calls:.1f}" if mean_calls is not None else "n/a"
            print(f"  [tool_loop:{tc_first:<8}] schema_ok={n_ok}/{len(subset)}, "
                  f"emitted_tool_call={n_emitted_tool}/{len(subset)}, "
                  f"latency_mean={lat_s}s, mean_tool_calls={calls_s}")

    # Exit code aggregates
    total = len(all_results)
    n_schema_ok = sum(1 for r in all_results if r.ok_schema)
    n_pass = sum(1 for r in all_results
                 if r.ok_schema and (r.branch_match or r.scenario == "tool_loop"))
    print()
    print(f"  total trials:     {total}")
    print(f"  schema valid:     {n_schema_ok}/{total}")
    print(f"  full pass:        {n_pass}/{total}")

    if n_pass == total:
        return 0
    if n_schema_ok == total:
        return 1
    return 2


def _dump_trial(out_dir: Path, model: str, result: TrialResult) -> None:
    """Write a single trial's raw output and metadata to ``out_dir``."""
    safe_model = model.replace("/", "__")
    suffix = ""
    if result.scenario == "tool_loop" and result.tool_choice_first_turn:
        suffix = f"_{result.tool_choice_first_turn}"
    filename = f"{safe_model}__{result.scenario}{suffix}__trial{result.trial}.json"
    meta = {
        "model": model,
        "scenario": result.scenario,
        "trial": result.trial,
        "ok_schema": result.ok_schema,
        "branch_match": result.branch_match,
        "branch_observed": result.branch_observed,
        "branch_expected": result.branch_expected,
        "reason": result.reason,
        "elapsed_s": result.elapsed_s,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "finish_reason": result.finish_reason,
        "error": result.error,
        "tool_calls_total": result.tool_calls_total,
        "turns_used": result.turns_used,
        "tool_choice_first_turn": result.tool_choice_first_turn,
        "raw": result.raw,
    }
    (out_dir / filename).write_text(json.dumps(meta, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--model",
        default="deepseek/deepseek-v4-pro",
        help="OpenRouter model slug (default: deepseek/deepseek-v4-pro)",
    )
    parser.add_argument(
        "--trials",
        type=int,
        default=3,
        help="Trials per scenario (default: 3 for determinism check)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="If set, dump every trial's raw response + metadata as JSON files here.",
    )
    parser.add_argument(
        "--skip-tools",
        action="store_true",
        help="Skip the tool-calling matrix (schema scenarios only).",
    )
    args = parser.parse_args()
    return run_eval(
        model=args.model,
        trials=args.trials,
        out_dir=args.out_dir,
        skip_tools=args.skip_tools,
    )


if __name__ == "__main__":
    sys.exit(main())
