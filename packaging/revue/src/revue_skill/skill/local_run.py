#!/usr/bin/env python3
"""Local Revue pipeline runner — no platform APIs, no Anthropic/OpenAI SDK calls.

Subcommands
-----------
position --all [--platform P]
    Run all position fixtures; show pass/fail summary.

position <fixture-path>
    Run one fixture JSON; show pass/fail.

position --diff <diff-file> --file <file-path> --line <N> --platform <P>
    Run the position calculator on a real diff file.

run [--base <branch>] [--platform <P>]
    Run the full Revue review pipeline on git diff vs <base> (default: main).
    Agents and Nova run via 'claude --print --bare' — no Anthropic SDK calls.
    Findings are displayed with resolved positions; nothing is posted anywhere.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT))  # for _revue package

from revue_skill.vendored.position_adapter import calculate, PositionStatus
from revue_skill.vendored.terminal_state import TerminalState, classify_terminal_state
from revue_skill.vendored.positioning_adapters import ADAPTERS

# REVUE-261 — concurrency cap default for Slice 3 Vex forks. Mirrors the
# argparse default on ``classify-and-build-vex-jobs --max-vex-forks``.
DEFAULT_MAX_VEX_FORKS: int = 20


def _build_tool_scope_constraint(diff_files: list[str] | tuple[str, ...]) -> str:
    """Build the reviewer-tools scope constraint block for Phase 1 prompts.

    Tells the Agent fork which paths are in-scope for reading and warns that
    reading outside the list invalidates the findings. The constraint is
    observability-only (Phase 3 emits a soft audit warning, never rejects).

    The constraint sentence is verbatim from the approved plan; do not
    paraphrase. See Slice 2 of
    /Users/langostin/.claude/plans/cryptic-percolating-perlis.md.
    """
    header = (
        "Available tools: Read, Grep, Bash. Restrict reads to the file paths "
        "listed below — these are the files modified in this PR. Reading "
        "paths outside this list invalidates your findings."
    )
    if not diff_files:
        return header + "\n\nFiles modified in this PR: (none)"
    bullets = "\n".join(f"  - {p}" for p in diff_files)
    return f"{header}\n\nFiles modified in this PR:\n{bullets}"


def _audit_finding_paths(
    *,
    agent_name: str,
    findings: list[dict],
    diff_files: set[str] | frozenset[str] | list[str] | tuple[str, ...],
) -> list[str]:
    """Cross-check finding ``file_path`` values against the diff file set.

    Returns the list of out-of-diff paths (preserving first-occurrence order).
    If any are found, emits a single warning line to stderr naming the agent
    and listing every offending path. Findings are never mutated — this
    helper is purely observability per Slice 2's threat-model framing
    (the dev tool doesn't need hard enforcement).

    Findings missing a ``file_path`` field are silently skipped (malformed
    findings are surfaced elsewhere via three-state validation; the audit
    only opines on the path-scope dimension).
    """
    diff_set = set(diff_files)
    out_of_diff: list[str] = []
    seen: set[str] = set()
    for item in findings:
        path = item.get("file_path") if isinstance(item, dict) else None
        if not path or path in seen:
            continue
        if path not in diff_set:
            out_of_diff.append(path)
            seen.add(path)

    if out_of_diff:
        joined = ", ".join(out_of_diff)
        sys.stderr.write(
            f"[revue-local][tool-audit] agent={agent_name} referenced "
            f"out-of-diff path(s): {joined}\n"
        )
        sys.stderr.flush()
    return out_of_diff


def _classify_agent_output(raw_text: str) -> TerminalState:
    """Classify a /revue-local Agent-fork output via REVUE-246's contract.

    Calls ``classify_terminal_state`` with dev-tool defaults (one-shot
    Agent fork, no tool loop): ``stop_reason='end_turn'``, ``iterations_used=1``,
    ``max_iterations=1``, ``hit_iteration_cap=False``. The returned
    :class:`TerminalState` carries the validated payload — callers route
    on ``state`` ('findings' / 'clean' / 'error'). Legacy shapes (raw
    findings arrays, ``{"findings": []}``) collapse to
    ``invalid_response_schema`` — the AC10 silent-bail-out disambiguation
    REVUE-246 was built to surface.
    """
    return classify_terminal_state(
        raw_text=raw_text,
        stop_reason="end_turn",
        iterations_used=1,
        max_iterations=1,
        hit_iteration_cap=False,
    )

LOG_DIR = Path("/tmp/revue_local")

# ---------------------------------------------------------------------------
# RevueLogger → stdout wiring
# ---------------------------------------------------------------------------

def _setup_local_logging() -> None:
    """Point RevueLogger's proxy hook at stdout so all Log.* calls appear in
    the terminal.  Call once at the top of each cmd_* function.

    _capture_revue_logs() overrides the hook temporarily during agent/Nova
    blocks to redirect to the log file, then restores the stdout hook.
    """
    from revue.core.logging_channels import Log  # noqa: F401 — ensures channels are registered
    from revue.core.log import RevueLogger

    def _to_stdout(message: str) -> None:
        sys.stdout.write(message + "\n")
        sys.stdout.flush()

    RevueLogger.shared().setup(on_log=_to_stdout)


@contextmanager
def _capture_revue_logs(log_fh):
    """Redirect Log.* + revue.* stdlib messages to log_fh during a block.

    On exit, restores the RevueLogger proxy hook to stdout (set by
    _setup_local_logging) so subsequent Log.* calls still appear in the
    terminal.
    """
    from revue.core.log import RevueLogger

    # Python stdlib logging handler
    revue_logger = logging.getLogger("revue")
    handler = logging.StreamHandler(log_fh)
    handler.setFormatter(logging.Formatter("%(message)s"))
    revue_logger.addHandler(handler)
    saved_propagate = revue_logger.propagate
    saved_level = revue_logger.level
    revue_logger.propagate = False
    revue_logger.setLevel(logging.INFO)

    # Override RevueLogger proxy hook → log file only (suppress terminal noise)
    shared = RevueLogger.shared()
    saved_hook = shared._proxy_hook

    def _log_to_fh(message: str) -> None:
        log_fh.write(message + "\n")
        log_fh.flush()

    shared.setup(on_log=_log_to_fh)

    try:
        yield
    finally:
        revue_logger.removeHandler(handler)
        revue_logger.propagate = saved_propagate
        revue_logger.setLevel(saved_level)
        # Restore stdout hook (saved before we redirected to the file)
        shared._proxy_hook = saved_hook


FIXTURES_DIR = REPO_ROOT / "src/revue/tests/fixtures/positioning"
PLATFORMS = ("github", "gitlab", "bitbucket")
_REVIEW_AGENTS = frozenset({"maya", "zara", "kai", "leo"})  # excludes nova, cleo


# ---------------------------------------------------------------------------
# Helpers — fixture mode
# ---------------------------------------------------------------------------

def _load_fixture(path: Path) -> dict:
    return json.loads(path.read_text())


def _check_fixture(path: Path) -> tuple[bool, str]:
    """Run calculator + adapter against one fixture. Returns (passed, detail_line)."""
    f = _load_fixture(path)
    platform = f["platform"]

    result = calculate(
        diff_snippet=f.get("diff_snippet", ""),
        reported_line=f["reported_line"],
        file_path=f["file_path"],
        replacement_line_count=f.get("replacement_line_count", 1),
    )
    adapter = ADAPTERS.get(platform)
    api_params = adapter.build_params(result, f) if adapter else None

    exp_pos = f.get("expected_position")
    exp_params = f.get("expected_api_params")

    if exp_pos is None:
        if result.status == PositionStatus.ANCHORED:
            return False, (
                f"  expected: null (not anchored)\n"
                f"  got:      anchored start_line={result.start_line} "
                f"api_params={api_params}"
            )
        return True, f"  status={result.status}  reason={result.reason}"

    if result.status != PositionStatus.ANCHORED:
        return False, (
            f"  expected: anchored start_line={exp_pos['start_line']}\n"
            f"  got:      status={result.status}  reason={result.reason}"
        )

    mismatches = []
    if result.start_line != exp_pos["start_line"]:
        mismatches.append(
            f"start_line: got {result.start_line}, expected {exp_pos['start_line']}"
        )
    if result.end_line != exp_pos["end_line"]:
        mismatches.append(
            f"end_line: got {result.end_line}, expected {exp_pos['end_line']}"
        )
    if api_params != exp_params:
        mismatches.append(
            f"api_params:\n"
            f"    got:      {json.dumps(api_params)}\n"
            f"    expected: {json.dumps(exp_params)}"
        )

    if mismatches:
        return False, "  " + "\n  ".join(mismatches)
    return True, f"  start_line={result.start_line}  end_line={result.end_line}"


def _label(path: Path) -> str:
    parts = path.parts
    return f"{parts[-2]}/{parts[-1]}"


# ---------------------------------------------------------------------------
# Helpers — real diff mode (position subcommand)
# ---------------------------------------------------------------------------

def _extract_file_diff(full_diff: str, file_path: str) -> str:
    """Extract the per-file diff section for file_path from a full unified diff."""
    lines = full_diff.splitlines(keepends=True)
    capture = False
    result: list[str] = []
    for line in lines:
        if line.startswith("diff --git"):
            if capture:
                break
            capture = file_path in line
            if capture:
                result = [line]
        elif capture:
            result.append(line)
    return "".join(result)


def cmd_position_diff(diff_path: Path, file_path: str, line: int, platform: str) -> int:
    if not diff_path.exists():
        print(f"error: diff file not found: {diff_path}", file=sys.stderr)
        return 2

    full_diff = diff_path.read_text()
    file_diff = _extract_file_diff(full_diff, file_path)

    if not file_diff:
        files = [
            (p[2:] if p.startswith("b/") else p)
            for l in full_diff.splitlines()
            if l.startswith("diff --git")
            for p in [l.split()[-1]]
        ]
        print(f"error: '{file_path}' not found in diff.", file=sys.stderr)
        if files:
            print("Files in diff:", file=sys.stderr)
            for f in files:
                print(f"  {f}", file=sys.stderr)
        return 2

    result = calculate(diff_snippet=file_diff, reported_line=line, file_path=file_path)
    adapter = ADAPTERS.get(platform)
    api_params = adapter.build_params(result, {}) if adapter else None

    icon = "✅" if result.status == PositionStatus.ANCHORED else "⚠️ "
    print(f"{icon} {file_path}:{line}  [{platform}]  status={result.status}")
    print(f"   reason: {result.reason}")
    if result.status == PositionStatus.ANCHORED:
        print(f"   start_line={result.start_line}  end_line={result.end_line}")
        print(f"   api_params: {json.dumps(api_params, indent=2)}")
    return 0 if result.status == PositionStatus.ANCHORED else 1


# ---------------------------------------------------------------------------
# Helpers — run mode (full pipeline)
# ---------------------------------------------------------------------------

def _build_diff_by_file(full_diff: str) -> dict[str, str]:
    """Split a full unified diff into a {file_path: per_file_diff} mapping."""
    result: dict[str, str] = {}
    current_file: str | None = None
    current_lines: list[str] = []

    for line in full_diff.splitlines(keepends=True):
        if line.startswith("diff --git"):
            if current_file and current_lines:
                result[current_file] = "".join(current_lines)
            current_lines = [line]
            # Extract path from "diff --git a/foo b/foo" → "foo"
            parts = line.split()
            raw = parts[-1] if len(parts) >= 4 else None
            current_file = raw[2:] if raw and raw.startswith("b/") else raw
        elif current_file is not None:
            current_lines.append(line)

    if current_file and current_lines:
        result[current_file] = "".join(current_lines)

    return result


# ---------------------------------------------------------------------------
# Subcommand: position
# ---------------------------------------------------------------------------

def cmd_position(args: argparse.Namespace) -> int:
    if args.diff:
        return cmd_position_diff(
            diff_path=Path(args.diff),
            file_path=args.file,
            line=args.line,
            platform=args.platform or "github",
        )

    if args.all:
        platforms = [args.platform] if args.platform else list(PLATFORMS)
        fixtures: list[Path] = []
        for p in platforms:
            d = FIXTURES_DIR / p
            if d.exists():
                fixtures.extend(sorted(d.glob("fixture_*.json")))

        passed = 0
        for path in fixtures:
            ok, detail = _check_fixture(path)
            icon = "✅" if ok else "❌"
            print(f"{icon} {_label(path)}")
            if not ok:
                print(detail)
            else:
                passed += 1

        total = len(fixtures)
        print(f"\n{passed}/{total} passing")
        return 0 if passed == total else 1

    path = Path(args.fixture)
    if not path.exists():
        alt = FIXTURES_DIR / args.fixture
        if alt.exists():
            path = alt
        else:
            print(f"error: fixture not found: {args.fixture}", file=sys.stderr)
            return 2

    ok, detail = _check_fixture(path)
    icon = "✅" if ok else "❌"
    print(f"{icon} {_label(path)}")
    print(detail)
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# Subcommand: run (full pipeline)
# ---------------------------------------------------------------------------

def cmd_prepare(args: argparse.Namespace) -> int:
    """Prepare agent job files for in-session Task execution.

    Reads the diff, loads agent definitions, and writes one job JSON per
    agent to --jobs-dir.  No AI calls are made.  The skill reads the
    manifest and runs each agent as a Task in the current Claude Code session.

    Output files:
        <jobs-dir>/manifest.json     — list of {agent, job_file, output_file}
        <jobs-dir>/<agent>.json      — {system_prompt, diff_text, user_prompt}
        <jobs-dir>/diff_by_file.json — raw per-file diff strings
    """
    import fnmatch
    from revue.core.logging_channels import Log

    _setup_local_logging()

    base = args.base or "main"
    platform = args.platform or "github"
    file_filters: list[str] = args.files or []
    jobs_dir = Path(args.jobs_dir)
    jobs_dir.mkdir(parents=True, exist_ok=True)

    from revue.core.diff_parser import parse_diff_file, filter_changes
    from revue.core.agent_loader import (
        load_agents_from_dir, _build_diff_text, _REVIEW_INSTRUCTIONS,
    )

    # Step 1: diff
    Log.pipeline.info("[revue] prepare  diffing against %s ...", base)
    diff_proc = subprocess.run(
        ["git", "diff", base],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    raw_diff = diff_proc.stdout
    if not raw_diff.strip():
        Log.pipeline.info("[revue] prepare  no changes relative to %s", base)
        return 0

    diff_by_file = _build_diff_by_file(raw_diff)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".diff", delete=False) as tmp:
        tmp.write(raw_diff)
        diff_path = tmp.name

    # Step 2: parse + filter
    all_changes = parse_diff_file(diff_path)
    changes, excluded = filter_changes(all_changes, ignore_patterns=[])

    if file_filters:
        changes = [
            fc for fc in changes
            if any(fnmatch.fnmatch(fc.file_path, pat) for pat in file_filters)
        ]
        Log.pipeline.info(
            "[revue] prepare  %d file(s) matched filters  (%d skipped)",
            len(changes), len(all_changes) - len(changes),
        )
    else:
        Log.pipeline.info(
            "[revue] prepare  %d file(s) to review  (%d excluded)",
            len(changes), len(excluded),
        )

    if not changes:
        Log.pipeline.info("[revue] prepare  nothing to review")
        return 0

    # Step 3: build agent job files (stub client — no AI calls)
    class _StubClient:
        def complete(self, *a, **kw):
            raise RuntimeError("stub — must not be called in prepare mode")

    all_agents = load_agents_from_dir(
        str(REPO_ROOT / "_revue/agents"), _StubClient(), max_tokens=4096
    )
    review_agents = [a for a in all_agents if a.name in _REVIEW_AGENTS]

    diff_text = _build_diff_text(changes)
    tool_scope_block = _build_tool_scope_constraint(sorted(diff_by_file.keys()))
    user_prompt = (
        "Carefully review the code diff for bugs, security issues, performance "
        f"problems, and code quality concerns. {_REVIEW_INSTRUCTIONS}"
        "\n\nIMPORTANT: Your ONLY task is to review the diff above and output the "
        "three-state JSON envelope (findings | clean | error) per the contract above. "
        "Do NOT emit a bare findings array — that is the legacy shape and will be "
        "rejected as invalid_response_schema. Do NOT use the Agent tool. Do NOT spawn "
        "other agents. Do NOT make any HTTP requests to api.anthropic.com or any other "
        "external API. Produce your output using only the diff text provided above, "
        "then write your JSON envelope to the output file using the Write tool."
        f"\n\n{tool_scope_block}"
    )

    manifest = []
    for agent in review_agents:
        system_prompt = (
            f"The code diff above is what you must review. {agent.definition.system_prompt}"
        )
        job = {
            "agent_name": agent.name,
            "system_prompt": system_prompt,
            "diff_text": diff_text,
            "user_prompt": user_prompt,
        }
        job_file = jobs_dir / f"{agent.name}.json"
        job_file.write_text(json.dumps(job, indent=2))
        output_file = jobs_dir / f"{agent.name}_output.json"
        manifest.append({
            "agent": agent.name,
            "job_file": str(job_file),
            "output_file": str(output_file),
        })
        Log.pipeline.info(
            "[revue] prepare  agent=%s  job=%s  output=%s",
            agent.name, job_file.name, output_file.name,
        )

    # Step 4: write diff_by_file and manifest
    (jobs_dir / "diff_by_file.json").write_text(json.dumps(diff_by_file, indent=2))
    manifest_data = {
        "base": base,
        "platform": platform,
        "files_reviewed": len(changes),
        "agents": manifest,
    }
    (jobs_dir / "manifest.json").write_text(json.dumps(manifest_data, indent=2))

    Log.pipeline.info(
        "[revue] prepare  done  jobs_dir=%s  agents=%d  files=%d",
        jobs_dir, len(manifest), len(changes),
    )
    return 0


def cmd_consolidate(args: argparse.Namespace) -> int:
    """Consolidate agent outputs written by the in-session Tasks.

    Reads <jobs-dir>/<agent>_output.json (raw JSON text from each Task),
    parses findings, groups, synthesises with Nova (whose response is also
    written by the skill as a Task), and displays results.

    If --nova-output is provided the file contains Nova's raw JSON response.
    If omitted, groups are passed through without synthesis.
    """
    from revue.core.logging_channels import Log

    _setup_local_logging()

    jobs_dir = Path(args.jobs_dir)
    platform = args.platform or "github"

    from revue.core.pipeline import _air_to_agent_finding
    from revue.core.agent_loader import load_agents_from_dir, _parse_finding_item
    from revue.core.models import AIReview
    from revue.comments.consolidator import (
        ProximityAndCountGroupingStrategy,
        NovaSingleShotStrategy,
    )
    _HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

    manifest_path = jobs_dir / "manifest.json"
    if not manifest_path.exists():
        Log.cli.error("[revue] consolidate  manifest not found: %s", manifest_path)
        return 1

    manifest = json.loads(manifest_path.read_text())
    diff_by_file: dict[str, str] = json.loads(
        (jobs_dir / "diff_by_file.json").read_text()
    )

    # Step 1: load agent definitions (stub client — only need severity_default)
    stub_agents: dict[str, object] = {}
    try:
        class _StubClient:
            def complete(self, *a, **kw):
                raise RuntimeError("stub")
        loaded = load_agents_from_dir(
            str(REPO_ROOT / "_revue/agents"), _StubClient(), max_tokens=4096
        )
        stub_agents = {a.name: a for a in loaded}
    except Exception:
        pass

    # Step 2: parse all agent outputs → AIReview list
    all_reviews: list[AIReview] = []
    for entry in manifest["agents"]:
        agent_name = entry["agent"]
        output_file = Path(entry["output_file"])
        if not output_file.exists():
            Log.pipeline.warning(
                "[revue] consolidate  no output for agent=%s — skipping", agent_name
            )
            continue
        raw = output_file.read_text()
        ts = _classify_agent_output(raw)
        if ts.state == "error":
            err = ts.payload["error"]
            Log.pipeline.warning(
                "[revue] consolidate  agent=%s  error code=%s message=%s",
                agent_name, err["code"], err["message"],
            )
            continue
        if ts.state == "clean":
            Log.pipeline.info(
                "[revue] consolidate  agent=%s  clean — %s (conf=%.2f)",
                agent_name,
                ts.payload.get("summary", ""),
                float(ts.payload.get("confidence", 0.0)),
            )
            continue
        # state == "findings" — payload schema guarantees the list is present and non-empty.
        data = ts.payload["findings"]

        # Slice 2: soft post-hoc audit — observability only, never rejects findings.
        _audit_finding_paths(
            agent_name=agent_name,
            findings=data,
            diff_files=set(diff_by_file.keys()),
        )

        severity_default = (
            stub_agents[agent_name].definition.severity_default
            if agent_name in stub_agents else "minor"
        )
        count = 0
        for item in data:
            parsed = _parse_finding_item(item, agent_name, severity_default)
            if parsed is not None:
                all_reviews.append(parsed)
                count += 1
        Log.pipeline.info(
            "[revue] consolidate  agent=%s  %d finding(s) parsed", agent_name, count
        )

    if not all_reviews:
        Log.pipeline.info("[revue] consolidate  no findings from any agent")
        return 0

    # Step 3: group
    agent_findings = [_air_to_agent_finding(r) for r in all_reviews]
    Log.pipeline.info(
        "[revue] consolidate  %d raw finding(s) → grouping...", len(agent_findings)
    )
    grouping = ProximityAndCountGroupingStrategy()
    groups = grouping.group(agent_findings)
    Log.pipeline.info("[revue] consolidate  %d group(s)", len(groups))

    # Step 4: Nova synthesis
    nova_output_path = Path(args.nova_output) if getattr(args, "nova_output", None) else None
    if nova_output_path and nova_output_path.exists():
        nova_raw = nova_output_path.read_text().strip()
        Log.pipeline.info("[revue] consolidate  nova synthesis from Task output")
        class _NovaClient:
            def complete(self, *a, **kw):
                from revue.core.models import CompletionResult, TokenUsage
                return CompletionResult(text=nova_raw, usage=TokenUsage())
        nova_client = _NovaClient()
    else:
        Log.pipeline.info("[revue] consolidate  no Nova output — passthrough synthesis")
        class _PassthroughClient:
            def complete(self, *a, **kw):
                from revue.core.models import CompletionResult, TokenUsage
                return CompletionResult(text="[]", usage=TokenUsage())
        nova_client = _PassthroughClient()

    nova = NovaSingleShotStrategy(ai_client=nova_client, diff_by_file=diff_by_file)
    consolidated = []
    for group in groups:
        try:
            consolidated.append(nova.synthesise(group))
        except Exception as exc:
            Log.pipeline.warning(
                "[revue] consolidate  synthesis failed for %s: %s", group.file_path, exc
            )

    # Step 5: display findings
    adapter = ADAPTERS.get(platform)
    Log.cli.info("\n%s", "=" * 64)
    Log.cli.info("  FINDINGS — %d total  [platform=%s]", len(consolidated), platform)
    Log.cli.info("%s", "=" * 64)

    for i, finding in enumerate(consolidated, 1):
        file_diff = diff_by_file.get(finding.file_path, "")
        pos = calculate(
            diff_snippet=file_diff,
            reported_line=finding.line_number,
            file_path=finding.file_path,
            replacement_line_count=finding.replacement_line_count,
        )
        api_params = adapter.build_params(pos, {}) if adapter else None
        sev = finding.severity.upper()
        anchor = "📍" if pos.status == PositionStatus.ANCHORED else "⚠️ "
        agents_str = ", ".join(a.agent_name for a in finding.attribution)

        Log.cli.info(
            "\n[%d] %s — %s:%d  (%s)",
            i, sev, finding.file_path, finding.line_number, agents_str,
        )
        Log.cli.info("     Issue:      %s", finding.issue)
        Log.cli.info("     Suggestion: %s", finding.suggestion)

        if finding.code_replacement:
            rlc = finding.replacement_line_count
            Log.cli.info(
                "     Code (%d line%s replaced):", rlc, "s" if rlc != 1 else ""
            )
            for ln in finding.code_replacement:
                Log.cli.info("       %s", ln)

        Log.cli.info("     Position:   %s %s  — %s", anchor, pos.status, pos.reason)
        if pos.status == PositionStatus.ANCHORED:
            Log.cli.info(
                "       start_line=%d  end_line=%d", pos.start_line, pos.end_line
            )
            Log.cli.info("       api_params: %s", json.dumps(api_params))

        if file_diff:
            hunk_lines = file_diff.splitlines()
            context_lines = []
            cur_new = 0
            for dl in hunk_lines:
                m = _HUNK_RE.match(dl)
                if m:
                    cur_new = int(m.group(3)) if m.group(3) else 0
                    context_lines.append(("hdr", 0, dl))
                    continue
                if dl.startswith(("+++", "---", "diff --git", "index ", "\\ ")):
                    continue
                line_no = cur_new
                if dl.startswith("+"):
                    cur_new += 1
                elif not dl.startswith("-"):
                    cur_new += 1
                if abs(line_no - finding.line_number) <= 4:
                    context_lines.append(("diff", line_no, dl))

            if context_lines:
                Log.cli.info(
                    "     Hunk context (±4 lines around :%d):", finding.line_number
                )
                for kind, lno, dl in context_lines:
                    if kind == "hdr":
                        Log.cli.info("       %s", dl)
                    else:
                        marker = ">>>" if lno == finding.line_number else "   "
                        sigil = dl[0] if dl else " "
                        Log.cli.info("     %s %4d %s %s", marker, lno, sigil, dl[1:60])
            else:
                Log.cli.info("     Hunk context: (no diff found for this file)")
        else:
            Log.cli.info("     Hunk context: (file not in diff)")

    Log.cli.info("")
    return 0


# ---------------------------------------------------------------------------
# Slice 3 — Vex in-loop helpers
#
# Phase 3 is split into three subcommands so the LLM step is externalised to
# orchestrator Agent forks (Phase 3b, skill-side) while prompt construction
# (3a) and verdict application + OrphanLineGuard sweep (3c) stay in pure
# subprocess Python. Helpers are unit-tested directly in
# ``src/revue/tests/scripts/test_local_run_vex_loop.py``.
# ---------------------------------------------------------------------------

def _build_vex_prompts(*, file_content: str, finding) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for the Vex job on *finding*.

    Reuses the production builder ``VexVerifier._build_prompt`` and the
    instance attribute ``_system_prompt`` exposed by
    ``VexVerifier(ai_client=None)`` — never duplicates _DEFAULT_SYSTEM_PROMPT.
    Byte-equivalence with the production path is enforced by
    ``test_local_run_vex_prompt_matches_production``.
    """
    from revue.comments._verifier import VexVerifier
    verifier = VexVerifier(ai_client=None)
    system_prompt = verifier._system_prompt
    user_prompt = VexVerifier._build_prompt(
        file_content=file_content, finding=finding
    )
    return system_prompt, user_prompt


def _read_repo_file_safely(repo_root: Path, file_path: str) -> "str | None":
    """Read a file under *repo_root*; return None on any IO error.

    Mirrors :class:`ReadFileTool`'s fail-open semantics: if the file is
    missing or unreadable, Vex's blast radius must not exceed the bug it
    was added to catch.
    """
    full = repo_root / file_path
    try:
        return full.read_text()
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return None


def _build_vex_job_manifest(
    *,
    findings: list,
    repo_root: Path,
    jobs_dir: Path,
    max_vex_forks: int = DEFAULT_MAX_VEX_FORKS,
) -> dict:
    """Phase 3a — emit one Vex job file per code_replacement finding.

    Returns the manifest as a dict (caller persists it):
        {
            "jobs": [
                {"finding_index": int, "job_file": str, "output_file": str},
                ...
            ],
            "skipped_indices": [int, ...],   # findings beyond max_vex_forks
        }

    Findings with ``code_replacement=None`` are skipped silently — Vex has
    nothing to verify on prose-only findings (mirrors
    ``VexVerifyPostProcessor.process`` early-return at line 586).

    When the number of findings-needing-Vex exceeds ``max_vex_forks``,
    process the first N and warn on the rest. Skipped findings pass
    through unmodified in 3c (no verdict, no orphan-guard sweep — both
    depend on Vex's decision).

    P10: ``diff_by_file`` is intentionally NOT a parameter — diff-scope
    enforcement happens in Phase 1's reviewer-tools constraint block (Slice 2)
    and Phase 3's path audit. Threading a redundant copy here would only
    invite drift.
    """
    vex_dir = jobs_dir / "vex_jobs"
    vex_dir.mkdir(parents=True, exist_ok=True)

    eligible: list[int] = [
        i for i, f in enumerate(findings) if f.code_replacement is not None
    ]
    processed = eligible[:max_vex_forks]
    skipped = eligible[max_vex_forks:]

    if skipped:
        # Stderr warning is part of the cap contract — see TC7.
        # P4: include explicit "N finding(s)" token so cap-warning tests can
        # assert the actual count without ambiguity against other digits.
        print(
            f"warning: max_vex_forks={max_vex_forks} cap reached; "
            f"{len(skipped)} finding(s) will pass through without Vex verification.",
            file=sys.stderr,
        )

    jobs: list[dict] = []
    for finding_index in processed:
        finding = findings[finding_index]
        file_content = _read_repo_file_safely(repo_root, finding.file_path)
        if file_content is None:
            # Match VexVerifyPostProcessor.process() read_error fail-open:
            # skip Vex for this finding entirely. The original code_replacement
            # passes through unmodified in 3c (no verdict, no orphan-guard).
            print(
                f"warning: vex skip {finding.file_path}: file unreadable under "
                f"repo_root={repo_root}; finding will pass through unmodified.",
                file=sys.stderr,
            )
            continue

        system_prompt, user_prompt = _build_vex_prompts(
            file_content=file_content, finding=finding
        )
        job_file = vex_dir / f"vex_job_{finding_index}.json"
        output_file = vex_dir / f"vex_verdict_{finding_index}.json"
        job_file.write_text(json.dumps({
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "finding_index": finding_index,
            "output_file_path": str(output_file),
        }, indent=2))
        jobs.append({
            "finding_index": finding_index,
            "job_file": str(job_file),
            "output_file": str(output_file),
        })

    return {"jobs": jobs, "skipped_indices": skipped}


def _coerce_verdict(verdict_payload: dict):
    """Coerce a verdict JSON payload into a ``VexVerdict`` dataclass.

    Tolerant of missing/None corrected_anchor (mirrors
    ``_parse_corrected_anchor``).

    Raises:
        ValueError: if the payload is missing a ``verdict`` field, the field
            is ``None``, or its value is not one of the three contract
            verdicts (``apply``, ``drop_cr_keep_prose``, ``reject_finding``).
            3c catches this and fails-open rather than mutate findings on
            garbage input.
    """
    from revue.comments._verifier import VexVerdict, CorrectedAnchor, _VALID_VERDICTS

    verdict_value = verdict_payload.get("verdict")
    if verdict_value is None:
        raise ValueError("verdict payload missing required field 'verdict'")
    if verdict_value not in _VALID_VERDICTS:
        raise ValueError(f"unknown verdict {verdict_value!r}")

    anchor_raw = verdict_payload.get("corrected_anchor")
    anchor = None
    if isinstance(anchor_raw, dict):
        try:
            raw_line = int(anchor_raw["line"])
            raw_rlc = int(anchor_raw["replacement_line_count"])
            # F4 / P13 — clamp rlc to ≥ 1 BEFORE CorrectedAnchor construction
            # so a malformed LLM output (rlc=0 or negative) doesn't get
            # silently swallowed by the dataclass invariant. Without the
            # pre-clamp, the verdict would still be applied (with anchor
            # discarded), but the clamp would never be exercised — the test
            # for it would be a near-tautology.
            clamped_rlc = max(1, raw_rlc)
            anchor = CorrectedAnchor(
                line=raw_line,
                replacement_line_count=clamped_rlc,
            )
        except (KeyError, TypeError, ValueError):
            anchor = None

    return VexVerdict(
        verdict=verdict_value,
        reason=str(verdict_payload.get("reason", "")),
        corrected_anchor=anchor,
    )


def _apply_vex_verdict_to_finding(finding, verdict_payload: dict):
    """Apply one verdict to one finding via the production applicator.

    Delegates to ``VexVerifyPostProcessor._apply_verdict`` (the staticmethod
    that maps verdicts to finding mutations) — no local reimplementation.

    P13: applies the production ``_sanitize_correction`` Gate 1 (clamp)
    locally. When a ``corrected_anchor`` is present, the absolute delta from
    the agent's reported line must not exceed ``VEX_CORRECTION_MAX_DELTA``;
    out-of-window corrections are stripped (anchor → None) so the verdict
    falls back to the agent's reported line. ``replacement_line_count < 1``
    cannot reach this point — ``CorrectedAnchor.__post_init__`` raises on
    construction inside ``_coerce_verdict``, where it's caught and the
    anchor becomes None.
    """
    from dataclasses import replace
    from revue.comments._verifier import (
        VexVerifyPostProcessor,
        VEX_CORRECTION_MAX_DELTA,
    )

    verdict = _coerce_verdict(verdict_payload)

    # P13 — Gate 1 clamp. Gate 2 (composition re-validation against the diff)
    # is intentionally NOT applied here: the dev-tool diff_by_file isn't
    # threaded through this helper, and the orchestrator's own Vex prompt
    # asks for valid anchors. Production runs Gate 2 because it sees real
    # LLM output under load; the dev tool's blast radius is bounded by the
    # K-window alone.
    anchor = verdict.corrected_anchor
    if anchor is not None:
        delta = abs(anchor.line - finding.line_number)
        if delta > VEX_CORRECTION_MAX_DELTA:
            verdict = replace(verdict, corrected_anchor=None)

    return VexVerifyPostProcessor._apply_verdict(finding, verdict)


class _VexAlreadyAppliedSentinel:
    """Marker stage occupying the Vex slot in the local post-processor chain.

    Verdicts are applied BEFORE the chain runs (so the verdict-index mapping
    built from the Phase-3a snapshot stays stable). Iterating the canonical
    chain — rather than hard-coding the sequence locally — means a future
    stage added to ``build_consolidation_postprocessors`` surfaces here as a
    NotImplementedError on the unknown type, forcing an explicit
    include/skip decision (F6).
    """

    def process(self, finding):
        # Identity: verdicts were applied pre-chain; nothing to do here.
        return finding


def _apply_verdicts_and_finalise(
    *,
    findings: list,
    verdicts_by_index: dict,
    diff_by_file: dict,
    repo_root: Path,
) -> list:
    """Phase 3c — apply Vex verdicts then run the canonical post-processor chain.

    Canonical production chain (single source of truth:
    ``revue.core.pipeline.build_consolidation_postprocessors``):
      NoOpSuggestionDropper → Vex → OrphanLineGuard → UnanchoredFindingExtractor.

    The local path iterates the chain returned by the helper rather than
    hard-coding the sequence — F6: a future-added stage will surface as a
    ``NotImplementedError`` on the unknown type, forcing an explicit
    include/skip decision rather than silently drifting. Vex itself is
    REPLACED with a ``_VexAlreadyAppliedSentinel`` (verdicts were applied
    from disk before the chain runs); ``UnanchoredFindingExtractor`` is
    included with a no-op summary sink since it's a cheap pass-through.

    Order rationale: Phase 3a built the Vex job manifest from the
    snapshotted (un-NoOp'd) finding list. Running NoOp BEFORE verdict
    application here would shift the verdict-index mapping, so verdicts
    are applied first; NoOp still filters trivial no-ops, just from the
    post-verdict survivors.
    """
    from revue.comments.consolidator import (
        NoOpSuggestionDropper,
        UnanchoredFindingExtractor,
    )
    from revue.comments._orphan_line_guard import OrphanLineGuardPostProcessor
    from revue.core.pipeline import build_consolidation_postprocessors

    # Step 1 — apply verdicts. Findings without a verdict pass through
    # unmodified (cap overflow, read_error, no code_replacement).
    after_vex: list = []
    for idx, finding in enumerate(findings):
        verdict_payload = verdicts_by_index.get(idx)
        if verdict_payload is None:
            after_vex.append(finding)
            continue
        try:
            result = _apply_vex_verdict_to_finding(finding, verdict_payload)
        except Exception:
            # Match VexVerifyPostProcessor.process fail-open semantics: a
            # broken verdict keeps the finding unmodified rather than
            # dropping a real bug.
            after_vex.append(finding)
            continue
        if result is not None:  # reject_finding → None
            after_vex.append(result)

    # Step 2 — iterate the canonical post-processor chain.
    vex_sentinel = _VexAlreadyAppliedSentinel()
    orphan_guard = OrphanLineGuardPostProcessor(
        repo_root=repo_root, diff_by_file=diff_by_file
    )
    summary_sink: list = []  # cheap no-op sink — extractor is pass-through
    chain = build_consolidation_postprocessors(
        vex_post_processor=vex_sentinel,
        orphan_guard_post_processor=orphan_guard,
        summary_sink=summary_sink,
    )
    current = list(after_vex)
    for stage in chain:
        # Recognised types: NoOp, the Vex sentinel (skip — verdicts
        # pre-applied), OrphanLineGuard, UnanchoredFindingExtractor.
        # An unknown type means the canonical chain grew a new stage and
        # the local path must decide what to do with it.
        if isinstance(stage, _VexAlreadyAppliedSentinel):
            # Vex verdicts already applied before the chain — skip the slot
            # explicitly so the orchestrator never invokes the real Vex
            # post-processor (which would burn an LLM call).
            continue
        if not isinstance(stage, (
            NoOpSuggestionDropper,
            OrphanLineGuardPostProcessor,
            UnanchoredFindingExtractor,
        )):
            raise NotImplementedError(
                f"unknown post-processor in canonical chain: {type(stage).__name__}; "
                "decide whether the local Phase 3c should run it and update "
                "_apply_verdicts_and_finalise."
            )
        next_findings: list = []
        for finding in current:
            result = stage.process(finding)
            if result is not None:
                next_findings.append(result)
        current = next_findings
    return current


# ---------------------------------------------------------------------------
# Phase-3 shared rendering — used by both ``consolidate`` and
# ``apply-verdicts-and-finalize`` so output formatting stays identical.
# ---------------------------------------------------------------------------

def _render_findings(findings: list, diff_by_file: dict, platform: str) -> None:
    """Print findings to ``Log.cli`` with positions and hunk context.

    Body extracted verbatim from the legacy ``cmd_consolidate`` rendering
    loop so both Phase-3 exits produce identical output.
    """
    from revue.core.logging_channels import Log
    _HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

    adapter = ADAPTERS.get(platform)
    Log.cli.info("\n%s", "=" * 64)
    Log.cli.info("  FINDINGS — %d total  [platform=%s]", len(findings), platform)
    Log.cli.info("%s", "=" * 64)

    for i, finding in enumerate(findings, 1):
        file_diff = diff_by_file.get(finding.file_path, "")
        pos = calculate(
            diff_snippet=file_diff,
            reported_line=finding.line_number,
            file_path=finding.file_path,
            replacement_line_count=finding.replacement_line_count,
        )
        api_params = adapter.build_params(pos, {}) if adapter else None
        sev = finding.severity.upper()
        anchor = "📍" if pos.status == PositionStatus.ANCHORED else "⚠️ "
        agents_str = ", ".join(a.agent_name for a in finding.attribution)

        Log.cli.info(
            "\n[%d] %s — %s:%d  (%s)",
            i, sev, finding.file_path, finding.line_number, agents_str,
        )
        Log.cli.info("     Issue:      %s", finding.issue)
        Log.cli.info("     Suggestion: %s", finding.suggestion)

        if finding.code_replacement:
            rlc = finding.replacement_line_count
            Log.cli.info(
                "     Code (%d line%s replaced):", rlc, "s" if rlc != 1 else ""
            )
            for ln in finding.code_replacement:
                Log.cli.info("       %s", ln)

        Log.cli.info("     Position:   %s %s  — %s", anchor, pos.status, pos.reason)
        if pos.status == PositionStatus.ANCHORED:
            Log.cli.info(
                "       start_line=%d  end_line=%d", pos.start_line, pos.end_line
            )
            Log.cli.info("       api_params: %s", json.dumps(api_params))

        if file_diff:
            hunk_lines = file_diff.splitlines()
            context_lines = []
            cur_new = 0
            for dl in hunk_lines:
                m = _HUNK_RE.match(dl)
                if m:
                    cur_new = int(m.group(3)) if m.group(3) else 0
                    context_lines.append(("hdr", 0, dl))
                    continue
                if dl.startswith(("+++", "---", "diff --git", "index ", "\\ ")):
                    continue
                line_no = cur_new
                if dl.startswith("+"):
                    cur_new += 1
                elif not dl.startswith("-"):
                    cur_new += 1
                if abs(line_no - finding.line_number) <= 4:
                    context_lines.append(("diff", line_no, dl))

            if context_lines:
                Log.cli.info(
                    "     Hunk context (±4 lines around :%d):", finding.line_number
                )
                for kind, lno, dl in context_lines:
                    if kind == "hdr":
                        Log.cli.info("       %s", dl)
                    else:
                        marker = ">>>" if lno == finding.line_number else "   "
                        sigil = dl[0] if dl else " "
                        Log.cli.info("     %s %4d %s %s", marker, lno, sigil, dl[1:60])
            else:
                Log.cli.info("     Hunk context: (no diff found for this file)")
        else:
            Log.cli.info("     Hunk context: (file not in diff)")

    Log.cli.info("")


# ---------------------------------------------------------------------------
# Shared consolidation core — used by ``consolidate`` and
# ``classify-and-build-vex-jobs`` so they produce the same ConsolidatedFinding
# list before diverging on Vex.
# ---------------------------------------------------------------------------

def _consolidate_from_manifest(jobs_dir: Path, nova_output: "str | None"):
    """Run Steps 1-4 of ``cmd_consolidate`` and return
    ``(consolidated, diff_by_file)``.

    No rendering, no Vex — purely produces the
    ``list[ConsolidatedFinding]`` that downstream phases operate on.
    """
    from revue.core.logging_channels import Log
    from revue.core.pipeline import _air_to_agent_finding
    from revue.core.agent_loader import load_agents_from_dir, _parse_finding_item
    from revue.core.models import AIReview
    from revue.comments.consolidator import (
        ProximityAndCountGroupingStrategy,
        NovaSingleShotStrategy,
    )

    manifest_path = jobs_dir / "manifest.json"
    if not manifest_path.exists():
        Log.cli.error("[revue] consolidate  manifest not found: %s", manifest_path)
        return [], {}

    manifest = json.loads(manifest_path.read_text())
    diff_by_file: dict[str, str] = json.loads(
        (jobs_dir / "diff_by_file.json").read_text()
    )

    stub_agents: dict[str, object] = {}
    try:
        class _StubClient:
            def complete(self, *a, **kw):
                raise RuntimeError("stub")
        loaded = load_agents_from_dir(
            str(REPO_ROOT / "_revue/agents"), _StubClient(), max_tokens=4096
        )
        stub_agents = {a.name: a for a in loaded}
    except Exception:
        pass

    all_reviews: list[AIReview] = []
    for entry in manifest["agents"]:
        agent_name = entry["agent"]
        output_file = Path(entry["output_file"])
        if not output_file.exists():
            Log.pipeline.warning(
                "[revue] consolidate  no output for agent=%s — skipping", agent_name
            )
            continue
        raw = output_file.read_text()
        ts = _classify_agent_output(raw)
        if ts.state == "error":
            err = ts.payload["error"]
            Log.pipeline.warning(
                "[revue] consolidate  agent=%s  error code=%s message=%s",
                agent_name, err["code"], err["message"],
            )
            continue
        if ts.state == "clean":
            Log.pipeline.info(
                "[revue] consolidate  agent=%s  clean — %s (conf=%.2f)",
                agent_name,
                ts.payload.get("summary", ""),
                float(ts.payload.get("confidence", 0.0)),
            )
            continue
        data = ts.payload["findings"]
        severity_default = (
            stub_agents[agent_name].definition.severity_default
            if agent_name in stub_agents else "minor"
        )
        count = 0
        for item in data:
            parsed = _parse_finding_item(item, agent_name, severity_default)
            if parsed is not None:
                all_reviews.append(parsed)
                count += 1
        Log.pipeline.info(
            "[revue] consolidate  agent=%s  %d finding(s) parsed", agent_name, count
        )

    if not all_reviews:
        Log.pipeline.info("[revue] consolidate  no findings from any agent")
        return [], diff_by_file

    agent_findings = [_air_to_agent_finding(r) for r in all_reviews]
    Log.pipeline.info(
        "[revue] consolidate  %d raw finding(s) → grouping...", len(agent_findings)
    )
    groups = ProximityAndCountGroupingStrategy().group(agent_findings)
    Log.pipeline.info("[revue] consolidate  %d group(s)", len(groups))

    nova_output_path = Path(nova_output) if nova_output else None
    if nova_output_path and nova_output_path.exists():
        nova_raw = nova_output_path.read_text().strip()
        Log.pipeline.info("[revue] consolidate  nova synthesis from Task output")
        class _NovaClient:
            def complete(self, *a, **kw):
                from revue.core.models import CompletionResult, TokenUsage
                return CompletionResult(text=nova_raw, usage=TokenUsage())
        nova_client = _NovaClient()
    else:
        Log.pipeline.info("[revue] consolidate  no Nova output — passthrough synthesis")
        class _PassthroughClient:
            def complete(self, *a, **kw):
                from revue.core.models import CompletionResult, TokenUsage
                return CompletionResult(text="[]", usage=TokenUsage())
        nova_client = _PassthroughClient()

    nova = NovaSingleShotStrategy(ai_client=nova_client, diff_by_file=diff_by_file)
    consolidated = []
    for group in groups:
        try:
            consolidated.append(nova.synthesise(group))
        except Exception as exc:
            Log.pipeline.warning(
                "[revue] consolidate  synthesis failed for %s: %s", group.file_path, exc
            )
    return consolidated, diff_by_file


def _serialise_finding(finding) -> dict:
    """Serialise a ConsolidatedFinding to a JSON-safe dict (snapshot format).

    Only the fields 3c needs to reconstruct the dataclass — keep the surface
    narrow so a future ConsolidatedFinding field doesn't silently break the
    snapshot round-trip.
    """
    return {
        "file_path": finding.file_path,
        "line_number": finding.line_number,
        "severity": finding.severity,
        "issue": finding.issue,
        "suggestion": finding.suggestion,
        "confidence": finding.confidence,
        "category": finding.category,
        "attribution": [
            {"agent_name": a.agent_name, "category": a.category}
            for a in finding.attribution
        ],
        "code_replacement": list(finding.code_replacement) if finding.code_replacement else None,
        "replacement_line_count": finding.replacement_line_count,
        "snippet": finding.snippet,
        "group_type": finding.group_type,
    }


def _deserialise_finding(data: dict):
    """Inverse of ``_serialise_finding`` — rebuild a ConsolidatedFinding."""
    from revue.comments.models import Attribution, ConsolidatedFinding
    return ConsolidatedFinding(
        file_path=data["file_path"],
        line_number=data["line_number"],
        severity=data["severity"],
        issue=data["issue"],
        suggestion=data["suggestion"],
        confidence=data["confidence"],
        category=data["category"],
        attribution=[Attribution(**a) for a in data["attribution"]],
        code_replacement=data["code_replacement"],
        replacement_line_count=data["replacement_line_count"],
        snippet=data.get("snippet", ""),
        group_type=data.get("group_type", "singleton"),
    )


# ---------------------------------------------------------------------------
# Subcommand: classify-and-build-vex-jobs (Phase 3a)
# ---------------------------------------------------------------------------

def cmd_classify_and_build_vex_jobs(args: argparse.Namespace) -> int:
    """Phase 3a — three-state classify + consolidate + emit Vex jobs.

    Reads the agent outputs (manifest.json + <agent>_output.json), runs the
    same classification + grouping + Nova-passthrough logic as
    ``cmd_consolidate``, then writes:
      - $JOBS_DIR/vex_jobs/manifest.json (Vex job manifest)
      - $JOBS_DIR/vex_jobs/vex_job_<i>.json (per-finding job file)
      - $JOBS_DIR/consolidated_findings_snapshot.json (3c reuses this)

    The orchestrator (Phase 3b, skill markdown) then spawns one Agent fork
    per job entry, writing verdict JSON to each entry's output_file before
    invoking ``apply-verdicts-and-finalize``.
    """
    from revue.core.logging_channels import Log

    _setup_local_logging()

    jobs_dir = Path(args.jobs_dir)
    max_vex_forks = int(args.max_vex_forks)

    # P2 — reject non-positive caps. ``eligible[:0]`` and ``eligible[:-N]``
    # silently change semantics (empty / drop last); surface as a hard error
    # rather than ship a slice that bypasses Vex without warning.
    if max_vex_forks <= 0:
        print(
            f"error: --max-vex-forks must be ≥ 1 (got {max_vex_forks})",
            file=sys.stderr,
        )
        return 2

    # P3 — both 3a and 3c read diff_by_file.json. Surface a clear error rather
    # than a FileNotFoundError stack trace when Phase 1 was skipped.
    diff_path = jobs_dir / "diff_by_file.json"
    if not diff_path.exists():
        print(
            f"error: missing prepare artifacts at {diff_path} — run Phase 1 "
            "(prepare) first.",
            file=sys.stderr,
        )
        return 2

    consolidated, diff_by_file = _consolidate_from_manifest(
        jobs_dir, getattr(args, "nova_output", None)
    )

    snapshot_path = jobs_dir / "consolidated_findings_snapshot.json"
    snapshot_path.write_text(json.dumps(
        [_serialise_finding(f) for f in consolidated], indent=2
    ))

    vex_manifest = _build_vex_job_manifest(
        findings=consolidated,
        repo_root=REPO_ROOT,
        jobs_dir=jobs_dir,
        max_vex_forks=max_vex_forks,
    )
    vex_manifest_path = jobs_dir / "vex_jobs" / "manifest.json"
    vex_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    vex_manifest_path.write_text(json.dumps(vex_manifest, indent=2))

    Log.pipeline.info(
        "[revue] classify-and-build-vex-jobs  findings=%d  vex_jobs=%d  skipped=%d",
        len(consolidated), len(vex_manifest["jobs"]),
        len(vex_manifest["skipped_indices"]),
    )
    return 0


# ---------------------------------------------------------------------------
# Subcommand: apply-verdicts-and-finalize (Phase 3c)
# ---------------------------------------------------------------------------

def cmd_apply_verdicts_and_finalize(args: argparse.Namespace) -> int:
    """Phase 3c — read verdict JSONs, apply per finding, run OrphanLineGuard.

    Reads:
      - $JOBS_DIR/consolidated_findings_snapshot.json  (from 3a)
      - $JOBS_DIR/vex_jobs/manifest.json               (from 3a)
      - $JOBS_DIR/vex_jobs/vex_verdict_<i>.json        (one per fork, written by 3b)
      - $JOBS_DIR/diff_by_file.json                    (from prepare)

    Renders the final findings to stdout via ``_render_findings``.
    """
    from revue.core.logging_channels import Log

    _setup_local_logging()

    jobs_dir = Path(args.jobs_dir)
    platform = args.platform or "github"

    snapshot_path = jobs_dir / "consolidated_findings_snapshot.json"
    if not snapshot_path.exists():
        Log.cli.error(
            "[revue] apply-verdicts  snapshot not found: %s "
            "(did you run classify-and-build-vex-jobs first?)",
            snapshot_path,
        )
        return 1

    findings = [_deserialise_finding(d) for d in json.loads(snapshot_path.read_text())]

    # P3 — guard against the prepare artifact being absent rather than crashing
    # with FileNotFoundError partway through 3c.
    diff_path = jobs_dir / "diff_by_file.json"
    if not diff_path.exists():
        Log.cli.error(
            "[revue] apply-verdicts  missing prepare artifacts at %s — "
            "run Phase 1 (prepare) first.",
            diff_path,
        )
        return 1
    diff_by_file = json.loads(diff_path.read_text())

    vex_manifest_path = jobs_dir / "vex_jobs" / "manifest.json"
    vex_manifest = (
        json.loads(vex_manifest_path.read_text()) if vex_manifest_path.exists()
        else {"jobs": [], "skipped_indices": []}
    )

    # P11 — surface cap-overflow at finalisation time so users reading only the
    # final rendered output know the Vex cap fired during Phase 3a.
    skipped_count = len(vex_manifest.get("skipped_indices") or [])
    if skipped_count:
        Log.cli.warning(
            "[revue] apply-verdicts  %d finding(s) bypassed Vex due to "
            "--max-vex-forks cap",
            skipped_count,
        )

    # Read each verdict JSON (forks wrote them in Phase 3b). Missing files
    # fail open — the finding passes through unmodified, mirroring
    # ``VexVerifyPostProcessor`` semantics.
    verdicts_by_index: dict[int, dict] = {}
    for entry in vex_manifest["jobs"]:
        verdict_path = Path(entry["output_file"])
        if not verdict_path.exists():
            Log.pipeline.warning(
                "[revue] apply-verdicts  missing verdict for finding %d at %s — passthrough",
                entry["finding_index"], verdict_path,
            )
            continue
        try:
            verdicts_by_index[entry["finding_index"]] = json.loads(verdict_path.read_text())
        except json.JSONDecodeError as exc:
            Log.pipeline.warning(
                "[revue] apply-verdicts  malformed verdict for finding %d: %s — passthrough",
                entry["finding_index"], exc,
            )

    finalised = _apply_verdicts_and_finalise(
        findings=findings,
        verdicts_by_index=verdicts_by_index,
        diff_by_file=diff_by_file,
        repo_root=REPO_ROOT,
    )

    _render_findings(finalised, diff_by_file, platform)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Legacy run mode — kept for direct CLI use only.

    For /revue-local skill use: run 'prepare' then 'consolidate' instead,
    with the skill executing agents as Tasks in the current Claude Code session.
    """
    print(
        "⚠  cmd_run is deprecated for /revue-local use.\n"
        "   The skill now uses 'prepare' + Task execution + 'consolidate'.\n"
        "   Running prepare → consolidate without AI agent calls (passthrough)...\n",
        file=sys.stderr,
    )
    import tempfile as _tf
    jobs_dir = Path(_tf.mkdtemp(prefix="revue_jobs_"))
    prepare_args = argparse.Namespace(
        base=getattr(args, "base", "main"),
        platform=getattr(args, "platform", "github"),
        files=getattr(args, "files", []),
        jobs_dir=str(jobs_dir),
    )
    rc = cmd_prepare(prepare_args)
    if rc != 0:
        return rc
    consolidate_args = argparse.Namespace(
        jobs_dir=str(jobs_dir),
        platform=getattr(args, "platform", "github"),
        nova_output=None,
    )
    return cmd_consolidate(consolidate_args)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="local_run.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # position subcommand
    pos = sub.add_parser("position", help="Test position calculator")
    pos.add_argument("fixture", nargs="?", help="Path to a single fixture JSON file")
    pos.add_argument("--all", action="store_true", help="Run all fixtures")
    pos.add_argument(
        "--platform", choices=PLATFORMS,
        help="Platform filter (--all) or target platform (--diff)",
    )
    pos.add_argument("--diff", metavar="DIFF_FILE", help="Path to a unified diff file")
    pos.add_argument("--file", metavar="FILE_PATH", help="File path within the diff to test")
    pos.add_argument("--line", type=int, metavar="N", help="Line number to resolve")

    # prepare subcommand
    prep = sub.add_parser(
        "prepare",
        help="Build agent job files for in-session Task execution (no AI calls)",
    )
    prep.add_argument(
        "--base", default="main", metavar="BRANCH",
        help="Base branch to diff against (default: main)",
    )
    prep.add_argument(
        "--platform", choices=PLATFORMS, default="github",
        help="Target platform (default: github)",
    )
    prep.add_argument(
        "--files", nargs="+", metavar="PATTERN",
        help="Glob patterns to limit which files are reviewed",
    )
    prep.add_argument(
        "--jobs-dir", required=True, metavar="DIR",
        help="Directory to write job JSON files and manifest",
    )

    # consolidate subcommand
    cons = sub.add_parser(
        "consolidate",
        help="Parse agent Task outputs, group, synthesise, and display findings",
    )
    cons.add_argument(
        "--jobs-dir", required=True, metavar="DIR",
        help="Directory containing manifest.json and <agent>_output.json files",
    )
    cons.add_argument(
        "--platform", choices=PLATFORMS, default="github",
        help="Target platform for position params (default: github)",
    )
    cons.add_argument(
        "--nova-output", metavar="FILE",
        help="Path to Nova Task output JSON (optional; passthrough if omitted)",
    )

    # classify-and-build-vex-jobs subcommand (Phase 3a, Slice 3)
    vex_a = sub.add_parser(
        "classify-and-build-vex-jobs",
        help=(
            "Phase 3a: classify agent outputs + consolidate + emit Vex job files "
            "(no AI calls; orchestrator runs Vex in Phase 3b)"
        ),
    )
    vex_a.add_argument(
        "--jobs-dir", required=True, metavar="DIR",
        help="Directory containing manifest.json and <agent>_output.json files",
    )
    vex_a.add_argument(
        "--nova-output", metavar="FILE",
        help="Path to Nova Task output JSON (optional; passthrough if omitted)",
    )
    vex_a.add_argument(
        "--max-vex-forks", type=int, default=DEFAULT_MAX_VEX_FORKS,
        metavar="N",
        help=(
            f"Cap on Vex Agent forks per run (default: {DEFAULT_MAX_VEX_FORKS}). "
            "Findings beyond the cap pass through unmodified with a stderr warning."
        ),
    )

    # apply-verdicts-and-finalize subcommand (Phase 3c, Slice 3)
    vex_c = sub.add_parser(
        "apply-verdicts-and-finalize",
        help=(
            "Phase 3c: read Vex verdicts written by Phase-3b forks, apply via "
            "VexVerifyPostProcessor._apply_verdict, run OrphanLineGuard, render output"
        ),
    )
    vex_c.add_argument(
        "--jobs-dir", required=True, metavar="DIR",
        help="Directory containing the Phase-3a snapshot and Vex job verdicts",
    )
    vex_c.add_argument(
        "--platform", choices=PLATFORMS, default="github",
        help="Target platform for position params (default: github)",
    )

    # run subcommand (legacy — prefer prepare + consolidate)
    run = sub.add_parser("run", help="Legacy: prepare + passthrough consolidate (no agents run)")
    run.add_argument(
        "--base", default="main", metavar="BRANCH",
        help="Base branch to diff against (default: main)",
    )
    run.add_argument(
        "--platform", choices=PLATFORMS, default="github",
        help="Target platform for position params (default: github)",
    )
    run.add_argument(
        "--model", default="haiku", metavar="MODEL",
        help="Unused (kept for backwards compat)",
    )
    run.add_argument(
        "--files", nargs="+", metavar="PATTERN",
        help="Glob patterns to limit which files are reviewed",
    )

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.cmd == "position":
        if args.diff:
            if not args.file or not args.line:
                parser.error("--diff requires --file and --line")
        elif not args.all and not args.fixture:
            parser.error("position requires --all, a fixture path, or --diff/--file/--line")
        return cmd_position(args)

    if args.cmd == "prepare":
        return cmd_prepare(args)

    if args.cmd == "consolidate":
        return cmd_consolidate(args)

    if args.cmd == "classify-and-build-vex-jobs":
        return cmd_classify_and_build_vex_jobs(args)

    if args.cmd == "apply-verdicts-and-finalize":
        return cmd_apply_verdicts_and_finalize(args)

    if args.cmd == "run":
        return cmd_run(args)

    parser.error(f"unknown command: {args.cmd}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
