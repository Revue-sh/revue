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

from positioning.calculator import calculate
from positioning.adapters import ADAPTERS

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
        if result.status == "anchored":
            return False, (
                f"  expected: null (not anchored)\n"
                f"  got:      anchored start_line={result.start_line} "
                f"api_params={api_params}"
            )
        return True, f"  status={result.status}  reason={result.reason}"

    if result.status != "anchored":
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

    icon = "✅" if result.status == "anchored" else "⚠️ "
    print(f"{icon} {file_path}:{line}  [{platform}]  status={result.status}")
    print(f"   reason: {result.reason}")
    if result.status == "anchored":
        print(f"   start_line={result.start_line}  end_line={result.end_line}")
        print(f"   api_params: {json.dumps(api_params, indent=2)}")
    return 0 if result.status == "anchored" else 1


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
    user_prompt = (
        "Carefully review the code diff for bugs, security issues, performance "
        f"problems, and code quality concerns. {_REVIEW_INSTRUCTIONS}"
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
    from positioning.calculator import _HUNK_RE

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
        raw = output_file.read_text().strip()
        clean = raw
        if clean.startswith("```"):
            clean = "\n".join(clean.split("\n")[1:])
        if clean.endswith("```"):
            clean = "\n".join(clean.split("\n")[:-1])
        clean = clean.strip()
        try:
            data = json.loads(clean)
        except json.JSONDecodeError as exc:
            Log.pipeline.warning(
                "[revue] consolidate  agent=%s  invalid JSON: %s", agent_name, exc
            )
            continue
        if not isinstance(data, list):
            data = data.get("findings", []) if isinstance(data, dict) else []

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
        anchor = "📍" if pos.status == "anchored" else "⚠️ "
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
        if pos.status == "anchored":
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

    if args.cmd == "run":
        return cmd_run(args)

    parser.error(f"unknown command: {args.cmd}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
