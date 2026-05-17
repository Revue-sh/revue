#!/usr/bin/env python3
"""Strict json_schema smoke test for OpenRouter-hosted models.

Runs three scenarios against the production three-state response_format
(strict: true) and verifies each response:

  1. arrives without HTTP error,
  2. parses as JSON,
  3. conforms to the expected branch of THREE_STATE_SCHEMA.

Scenarios:
  * findings — diff with an obvious bug → status: findings
  * clean    — already-safe diff (comment-only change) → status: clean
  * error    — explicit instruction to return error envelope → status: error

Default target is ``qwen/qwen3-coder-next`` — chosen as the first candidate
under the "supported-models hard gate" policy. Pass ``--model`` to test
others (e.g. ``nvidia/nemotron-3-super-120b-a12b``).

Usage:
    direnv exec . python3 scripts/smoke_openrouter_schema.py
    direnv exec . python3 scripts/smoke_openrouter_schema.py --model qwen/qwen3-coder

Requires:
    OPENROUTER_API_KEY in env (loaded via .envrc / direnv).
"""
from __future__ import annotations

import argparse
import json
import os
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
# Scenarios
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
class Scenario:
    name: str
    system: str
    user: str
    expected_branch: str


SCENARIOS: list[Scenario] = [
    Scenario(
        name="findings",
        system=_SYSTEM_REVIEW,
        user=f"Review this diff:\n\n```diff\n{_BUGGY_DIFF}\n```\n\nReturn the JSON now.",
        expected_branch="findings",
    ),
    Scenario(
        name="clean",
        system=_SYSTEM_REVIEW,
        user=f"Review this diff:\n\n```diff\n{_CLEAN_DIFF}\n```\n\nReturn the JSON now.",
        expected_branch="clean",
    ),
    Scenario(
        name="error",
        system=_SYSTEM_ERROR,
        user="You need to review a diff but cannot access the repository. Return the JSON now.",
        expected_branch="error",
    ),
]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_three_state(payload: dict[str, Any]) -> tuple[bool, str]:
    """Manual shape check against THREE_STATE_SCHEMA. Returns (ok, reason)."""
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
# Runner
# ---------------------------------------------------------------------------


@dataclass
class ScenarioResult:
    name: str
    ok: bool
    reason: str
    branch_observed: str | None
    branch_expected: str
    branch_match: bool
    elapsed_s: float
    prompt_tokens: int | None
    completion_tokens: int | None
    raw: str
    finish_reason: str | None = None
    error: str | None = None


def _run_scenario(
    client: Any,
    model: str,
    scenario: Scenario,
    response_format: dict[str, Any],
) -> ScenarioResult:
    t0 = time.monotonic()
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": scenario.system},
                {"role": "user", "content": scenario.user},
            ],
            response_format=response_format,
            max_tokens=2048,
            temperature=0.3,
        )
    except Exception as exc:
        return ScenarioResult(
            name=scenario.name,
            ok=False,
            reason="request raised",
            branch_observed=None,
            branch_expected=scenario.expected_branch,
            branch_match=False,
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
        return ScenarioResult(
            name=scenario.name, ok=False, reason="empty response body",
            branch_observed=None, branch_expected=scenario.expected_branch,
            branch_match=False, elapsed_s=elapsed,
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            raw=raw, finish_reason=finish_reason,
        )

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return ScenarioResult(
            name=scenario.name, ok=False, reason=f"invalid JSON: {exc}",
            branch_observed=None, branch_expected=scenario.expected_branch,
            branch_match=False, elapsed_s=elapsed,
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            raw=raw, finish_reason=finish_reason,
        )

    ok, reason = _validate_three_state(payload)
    observed = payload.get("status") if isinstance(payload, dict) else None
    return ScenarioResult(
        name=scenario.name,
        ok=ok,
        reason=reason,
        branch_observed=observed,
        branch_expected=scenario.expected_branch,
        branch_match=(observed == scenario.expected_branch),
        elapsed_s=elapsed,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        raw=raw,
        finish_reason=finish_reason,
    )


def run_smoke_test(model: str) -> int:
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
            "X-Title": "Revue AI Code Review (smoke test)",
        },
    )

    response_format = openai_response_format_for_three_state()

    print(f"→ model: {model}")
    print(f"→ schema: three-state (strict={response_format['json_schema']['strict']})")
    print(f"→ scenarios: {', '.join(s.name for s in SCENARIOS)}")
    print()

    results: list[ScenarioResult] = []
    for scenario in SCENARIOS:
        print(f"=== scenario: {scenario.name} (expect: {scenario.expected_branch}) ===")
        result = _run_scenario(client, model, scenario, response_format)
        results.append(result)

        if result.error:
            print(f"  ERROR after {result.elapsed_s:.1f}s — {result.error}")
        else:
            print(
                f"  {result.elapsed_s:.1f}s, tokens: "
                f"prompt={result.prompt_tokens} completion={result.completion_tokens}, "
                f"finish_reason={getattr(result, 'finish_reason', '?')}"
            )
            print(f"  observed branch: {result.branch_observed!r}")
            print(f"  schema verdict: {result.reason}")
            preview = result.raw[:300].replace("\n", " ")
            print(f"  raw preview: {preview}")
        print()

    # Final summary
    print("=== summary ===")
    n_pass_schema = sum(1 for r in results if r.ok)
    n_pass_branch = sum(1 for r in results if r.ok and r.branch_match)
    print(f"  schema valid:    {n_pass_schema}/{len(results)}")
    print(f"  expected branch: {n_pass_branch}/{len(results)}")
    for r in results:
        marker = "PASS" if r.ok and r.branch_match else ("SCHEMA-OK" if r.ok else "FAIL")
        print(
            f"  [{marker:>9}] {r.name:<10} "
            f"(expected={r.branch_expected}, observed={r.branch_observed})"
        )

    if n_pass_branch == len(results):
        return 0
    if n_pass_schema == len(results):
        return 1  # schema OK but branch mismatch — model judgment issue
    return 2  # at least one schema failure


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--model",
        default="qwen/qwen3-coder-next",
        help="OpenRouter model slug (default: qwen/qwen3-coder-next)",
    )
    args = parser.parse_args()
    return run_smoke_test(args.model)


if __name__ == "__main__":
    sys.exit(main())
