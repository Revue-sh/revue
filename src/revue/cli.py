#!/usr/bin/env python3
"""Revue CLI — local diff review, config init, and validation.

Entry point registered as ``revue`` in pyproject.toml.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Callable, Optional

# Configure Python logging.
# Root handler: WARNING level for third-party noise suppression.
# revue.* hierarchy: INFO so pipeline progress reaches the terminal.
# Format: plain %(message)s — all [revue] messages embed their own prefix.
# Override the revue.* level via REVUE_LOG_LEVEL env var if needed.
logging.basicConfig(
    level=logging.NOTSET,
    format="%(message)s",
    stream=sys.stdout,
)
logging.getLogger("revue").setLevel(
    os.environ.get("REVUE_LOG_LEVEL", "INFO").upper()
)
# Suppress third-party HTTP library noise.
logging.getLogger("anthropic._base_client").setLevel("WARNING")
logging.getLogger("httpcore").setLevel("WARNING")
logging.getLogger("httpx").setLevel("WARNING")

from revue.core.display import SEVERITY_EMOJI_ALT  # noqa: E402
from revue.core.logging_channels import Log  # noqa: F401, E402
from revue.core.log import RevueLogger  # noqa: E402

# REVUE-241: route channel messages (Log.nova, Log.pipeline, ...) to stdout.
# Channel level filters still apply (per-channel default INFO, overridable
# via REVUE_LOG_<CHANNEL> env var) — without this hook, Log.nova INFO lines
# such as ``[vex-verdict] reject_finding ...`` only land in the dated file
# logger and never reach a dogfood / pipeline run's terminal output.
RevueLogger.shared().setup(on_log=lambda message: print(message, flush=True))
from revue.core.config_loader import (
    DEFAULT_REVUE_YML,
    load_config,
    validate_config,
)
from revue.core.models_registry import (
    ModelConfig,
    load_builtin_registry,
    merge_user_overrides,
)
from revue.core.diff_parser import filter_changes, parse_diff_file
from revue.core.ai_client import create_ai_client
from revue.core.pipeline import ReviewPipeline
from revue.core.models import PRContext
from revue.core.agent_loader import filter_code_replacement
from revue.comments.body_builder import BodyBuilder
from revue.comments.models import Attribution, ConsolidatedFinding
from revue.comments.summary_builder import (
    SEVERITY_EMOJI,
    SEVERITY_ORDER,
    _CATEGORY_MAP,
    _CATEGORY_CLEAN_LABELS,
    _AGENT_DISPLAY_NAMES,
    _AGENT_EMOJIS,
    _star_rating,
    build_enhanced_summary as _build_enhanced_summary,
)
from revue.core.logging_utils import _Lazy


# ---------------------------------------------------------------------------
# Metrics helpers
# ---------------------------------------------------------------------------

def _print_metrics_summary(pipeline: ReviewPipeline) -> None:
    """Print cache metrics summary using in-memory totals from the collector."""
    metrics_collector = getattr(pipeline, "_metrics", None)
    if not metrics_collector:
        return
    totals = metrics_collector.verbose_summary()
    if totals is None:
        return
    write_tokens = totals.get("cache_creation_tokens", 0)
    read_tokens = totals.get("cache_read_tokens", 0)
    total_cached = write_tokens + read_tokens
    hit_rate = (read_tokens / total_cached * 100) if total_cached > 0 else 0
    print(
        f"[revue] cache  write: {write_tokens:,} tokens  read: {read_tokens:,} tokens  "
        f"({hit_rate:.0f}% cache hit rate this run)",
        flush=True,
    )


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="revue",
        description="Revue — AI-powered code review",
    )
    sub = parser.add_subparsers(dest="command")

    # -- review --
    review = sub.add_parser("review", help="Review a local diff file")
    review.add_argument("--diff", required=True, help="Path to .diff file")
    review.add_argument("--config", default=".revue.yml", help="Path to config file")
    review.add_argument(
        "--provider",
        choices=["anthropic", "openai", "azure", "openrouter", "custom"],
        help="Override AI provider",
    )
    review.add_argument("--model", help="Override model string")
    review.add_argument(
        "--output",
        choices=["markdown", "json", "text"],
        default=None,
        help="Output format (default: markdown)",
    )
    review.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse diff and validate config without calling AI",
    )
    review.add_argument(
        "--format",
        choices=["markdown", "json", "text"],
        default=None,
        dest="format",
        help="Alias for --output (used by CI pipes)",
    )

    # Bitbucket-specific flags (used by the Bitbucket Pipe)
    review.add_argument(
        "--platform",
        choices=["github", "gitlab", "bitbucket"],
        default=None,
        help="VCS platform — enables posting comments back to the PR/MR",
    )
    review.add_argument("--pr-id", type=int, default=None, help="PR/MR ID (required for --platform)")
    review.add_argument("--workspace", default=None, help="Bitbucket workspace slug")
    review.add_argument("--repo-slug", default=None, help="Bitbucket repository slug")
    review.add_argument("--bb-username", default=None, help="Bitbucket username for API auth")
    review.add_argument("--bb-token", default=None, help="Bitbucket API token")
    review.add_argument(
        "--comment-style",
        choices=["summary", "per-issue"],
        default=None,
        help="How to post review findings: 'summary' = one comment per file, 'per-issue' = one inline comment per finding. Overrides .revue.yml output.comment_style.",
    )
    review.add_argument(
        "--auto-detect-pr",
        action="store_true",
        default=False,
        help=(
            "Auto-detect PR/MR ID and platform from CI environment variables "
            "(BITBUCKET_PR_ID, BITBUCKET_WORKSPACE, GITHUB_PR_NUMBER, CI_MERGE_REQUEST_IID). "
            "Fetches and injects PR description context into each agent for smarter reviews."
        ),
    )
    review.add_argument(
        "--pr-description-file",
        default=None,
        help=(
            "Path to a plain-text or markdown file containing the PR/MR description. "
            "Parsed into sections and injected as context into each agent. "
            "Takes precedence over --auto-detect-pr when both are provided. "
            "Preferred in CI: let the pipeline fetch the description "
            "(curl / gh / gitlab API) and write it to a file; the CLI stays platform-agnostic. "
            "Example: --pr-description-file /tmp/pr_description.txt"
        ),
    )
    review.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Print cache metrics summary after review completes (requires REVUE_METRICS_ENABLED)",
    )

    review.set_defaults(func=cmd_review)

    # -- init --
    init = sub.add_parser("init", help="Scaffold a .revue.yml in current directory")
    init.add_argument(
        "--force", action="store_true", help="Overwrite existing .revue.yml"
    )
    init.set_defaults(func=cmd_init)

    # -- validate --
    val = sub.add_parser("validate", help="Validate a config file")
    val.add_argument("--config", default=".revue.yml", help="Path to config file")
    val.set_defaults(func=cmd_validate)

    # -- list-models --
    list_models = sub.add_parser(
        "list-models",
        help="List supported and user-overridden models with their per-model knobs",
    )
    fmt = list_models.add_mutually_exclusive_group()
    fmt.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit JSON array (one object per model) instead of the default table",
    )
    fmt.add_argument(
        "--markdown",
        action="store_true",
        dest="as_markdown",
        help="Emit a Markdown table (used to regenerate the README section)",
    )
    list_models.set_defaults(func=cmd_list_models)

    return parser


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_review(
    args: argparse.Namespace,
    pipeline_factory: Callable | None = None,
) -> int:
    """Run a code review.  Accepts an optional *pipeline_factory* for DIP injection."""
    # 1. Verify diff file exists
    diff_path = Path(args.diff)
    if not diff_path.exists():
        print(f"Error: diff file not found: {args.diff}", file=sys.stderr)
        return 1

    # 2. Build overrides from CLI flags
    # --format is an alias for --output used by CI pipes
    effective_output = getattr(args, "output", None) or getattr(args, "format", None)
    overrides: dict[str, object] = {}
    if args.provider:
        overrides["provider"] = args.provider
    if args.model:
        overrides["model"] = args.model
    if effective_output:
        overrides["output_format"] = effective_output

    # 3. Load config
    try:
        config = load_config(config_path=args.config, overrides=overrides)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    # 4. Validate config
    errors = validate_config(config)
    if errors:
        for err in errors:
            print(f"Config error: {err}", file=sys.stderr)
        return 1

    # 5. Dry-run: parse, filter, list files and exit (no AI call)
    if args.dry_run:
        try:
            changes = parse_diff_file(str(diff_path))
        except Exception as exc:
            print(f"Error parsing diff: {exc}", file=sys.stderr)
            return 1

        included, excluded = filter_changes(
            changes, config.ignore_patterns, config.max_diff_lines
        )
        total = len(changes)
        print(f"Found {total} files ({len(excluded)} excluded by filters)")
        for fc in included:
            print(f"  [review] {fc.file_path} (+{fc.additions}/-{fc.deletions})")
        for fc in excluded:
            print(f"  [skip]   {fc.file_path}")
        return 0

    # 6. Resolve API key — fail fast
    try:
        config.resolve_api_key()
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    # 7. Build pipeline (DIP: injected factory or default)
    try:
        if pipeline_factory is not None:
            pipeline = pipeline_factory(config)
        else:
            pipeline = ReviewPipeline(config)
    except Exception as exc:
        print(f"Error creating AI client: {exc}", file=sys.stderr)
        return 1

    # 8. Fetch PR description for smart context filtering (REVUE-84/86)
    pr_description = None
    pr_description_file = getattr(args, "pr_description_file", None)
    auto_detect = getattr(args, "auto_detect_pr", False)
    explicit_pr_id = getattr(args, "pr_id", None)
    explicit_platform = getattr(args, "platform", None)
    # Resolve PR ID unconditionally — must not be gated inside elif below,
    # since --pr-description-file and --pr-id can be passed together (CI does this).
    resolved_pr_id: Optional[str] = explicit_pr_id or _resolve_pr_id_from_env()

    if pr_description_file:
        # Platform-agnostic path (REVUE-86): CI fetches description, writes file, passes path.
        # The CLI just reads and parses — no network I/O, no platform detection.
        from revue.core.pr_description_adapter import PRDescription
        desc_path = Path(pr_description_file)
        if desc_path.exists():
            try:
                raw = desc_path.read_text(encoding="utf-8")
            except Exception as exc:
                print(f"[revue] PR description file unreadable ({exc}) — continuing.", flush=True)
                raw = ""
            if not raw.strip():
                print(f"[revue] PR description file is empty — continuing.", flush=True)
            else:
                try:
                    pr_description = PRDescription.parse(title="", body=raw)
                    print(f"[revue] PR context loaded from file ({desc_path.name})", flush=True)
                except Exception as exc:
                    print(f"[revue] PR description parse failed ({exc}) — continuing.", flush=True)
        else:
            print(f"[revue] PR description file not found: {pr_description_file} — continuing.", flush=True)

    elif auto_detect or explicit_pr_id:
        from revue.core.pr_description_adapter import (
            get_pr_description_from_env,
            get_bitbucket_pr_description,
        )

        if resolved_pr_id:
            try:
                if auto_detect and not explicit_platform:
                    # Let the adapter auto-detect from CI env vars
                    pr_description = get_pr_description_from_env(resolved_pr_id)
                elif explicit_platform == "bitbucket" or os.getenv("BITBUCKET_WORKSPACE"):
                    workspace = getattr(args, "workspace", None) or os.getenv("BITBUCKET_WORKSPACE", "")
                    repo_slug = getattr(args, "repo_slug", None) or os.getenv("BITBUCKET_REPO_SLUG", "")
                    bb_user = getattr(args, "bb_username", None) or os.getenv("BITBUCKET_USERNAME", "")
                    bb_token = getattr(args, "bb_token", None) or os.getenv("BITBUCKET_API_TOKEN", "")
                    if all([workspace, repo_slug, bb_user, bb_token]):
                        pr_description = get_bitbucket_pr_description(
                            workspace, repo_slug, resolved_pr_id, bb_user, bb_token
                        )
                if pr_description:
                    print(f"[revue] PR context loaded: '{pr_description.title}'", flush=True)
                else:
                    print("[revue] PR context unavailable — continuing without it.", flush=True)
            except Exception as exc:
                print(f"[revue] PR context fetch failed ({exc}) — continuing.", flush=True)

    # 9. Run pipeline
    from revue.core.pipeline import AllAgentsFailedError
    print(f"[revue] Provider: {config.provider}  Model: {config.model}", flush=True)
    print(f"[revue] Validating license...")
    fallback_mode = "normal"
    try:
        _platform = getattr(args, "platform", None)
        _pr_id = int(resolved_pr_id) if resolved_pr_id is not None else None
        # REPOSITORY is the canonical "owner/repo" env var — set once in
        # the customer's repo CI variables; the CLI needs no platform detection.
        # Explicit --workspace / --repo-slug CLI args take precedence.
        # Fall back to legacy Bitbucket-specific env vars for backward compat.
        _cli_owner = getattr(args, "workspace", None)
        _cli_name = getattr(args, "repo_slug", None)
        _revue_repo = os.getenv("REPOSITORY", "")
        _env_parts = (_revue_repo.split("/", 1) + [""])[:2]
        _repo_owner = _cli_owner or _env_parts[0] or os.getenv("BITBUCKET_WORKSPACE")
        _repo_name = _cli_name or _env_parts[1] or os.getenv("BITBUCKET_REPO_SLUG")
        _pr_context = (
            PRContext(
                platform=_platform,
                pr_number=_pr_id,
                repo_owner=_repo_owner or "",
                repo_name=_repo_name or "",
                repo_path=os.getcwd(),
            )
            if _platform and _pr_id
            else None
        )
        review_results, excluded, files_reviewed, failed_agents = pipeline.run(
            str(diff_path),
            pr_description=pr_description,
            pr_context=_pr_context,
        )
        fallback_mode = getattr(pipeline, "last_fallback_mode", "normal")

        # Print cache metrics summary if --verbose enabled
        verbose = getattr(args, "verbose", False)
        if verbose:
            _print_metrics_summary(pipeline)
    except AllAgentsFailedError:
        print(
            "\n[revue] ❌ All agents failed — review aborted.\n"
            "  All findings are missing from this review.\n"
            "  Check the errors above for details (rate limits, timeouts, credentials).",
            flush=True,
        )
        return 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    total = files_reviewed + len(excluded)
    print(f"[revue] Found {total} file(s) in diff ({len(excluded)} excluded by filters)")

    # 9. Output
    results: list[dict[str, str]] = []
    for rr in review_results:
        if rr.error:
            print(f"Error reviewing {rr.file_path}: {rr.error}", file=sys.stderr)
            results.append({"file": rr.file_path, "review": f"ERROR: {rr.error}"})
        else:
            results.append({"file": rr.file_path, "review": rr.response})

    # 9b. Post comments back to Bitbucket if --platform bitbucket
    # Priority: CLI flag > .revue.yml > hardcoded default (per-issue)
    # CLI default is None so we can distinguish "not set" from "explicitly set"
    if args.comment_style is None:
        config_style = getattr(config, "comment_style", None)
        args.comment_style = config_style if config_style in ("per-issue", "summary") else "per-issue"
    platform = getattr(args, "platform", None)
    posting_result: tuple[int, int] | None = None
    if platform == "bitbucket":
        posting_result = _post_to_bitbucket(args, review_results, config, fallback_mode=fallback_mode)
    elif platform == "github":
        posting_result = _post_to_github(args, review_results, config, fallback_mode=fallback_mode)
    elif platform == "gitlab":
        posting_result = _post_to_gitlab(args, review_results, config, fallback_mode=fallback_mode)

    # Fail the pipeline when any agent failed — review is incomplete.
    # We post findings from successful agents first (above) so developers
    # still see partial results, but exit non-zero signals the incomplete state.
    if failed_agents:
        details = getattr(pipeline, "last_failed_agent_details", []) or []
        # REVUE-241: list per-agent reason inline so operators don't have to
        # scroll back through the log to find which client method raised.
        by_name = {d.get("name", ""): d for d in details}
        lines = [
            f"\n[revue] ❌ Review incomplete — {len(failed_agents)} agent(s) failed: "
            f"{', '.join(failed_agents)}",
            "  Findings from failed agents are missing from this review.",
        ]
        for name in failed_agents:
            d = by_name.get(name)
            if d:
                lines.append(f"    • {name}: {d.get('reason', 'unknown')}")
            else:
                lines.append(f"    • {name}: (no detail captured)")
        print("\n".join(lines), flush=True)
        return 1

    fmt = config.output_format
    if fmt == "json":
        print(json.dumps(results, indent=2))
    elif fmt == "text":
        for r in results:
            print(f"--- {r['file']} ---")
            print(r["review"])
            print()
    else:
        # markdown (default)
        for r in results:
            print(f"## {r['file']}")
            print(r["review"])

    if posting_result is not None:
        _posted, _failed = posting_result
        if _posted == 0 and _failed > 0:
            return 1

    print("[revue] ✅ Review cycle complete.", flush=True)
    return 0


def _format_synthesis_attribution(contributors: list) -> str:
    """Format synthesis attribution as ``Agents: <Name> <emoji> **<Category>** | ... → Nova <emoji> (synthesised)``.

    Emojis and display names are looked up from ``core.display.AGENT_EMOJIS``
    / ``AGENT_DISPLAY_NAMES``; this docstring no longer embeds specific
    glyphs so re-skinning an agent doesn't make the example stale.

    contributors: list of (agent_name, category) pairs — tuples or 2-element lists.
    """
    def agent_label(name: str, category: str) -> str:
        display = _AGENT_DISPLAY_NAMES.get(name, name.title())
        emoji = _AGENT_EMOJIS.get(name, "")
        label = f"{display} {emoji}" if emoji else display
        return f"{label} **{category.replace('-', ' ').title()}**"

    unique = list(dict.fromkeys((c[0], c[1]) for c in contributors))
    agents_str = " | ".join(agent_label(name, cat) for name, cat in unique)
    nova_display = _AGENT_DISPLAY_NAMES.get("nova", "Nova")
    nova_emoji = _AGENT_EMOJIS.get("nova", "")
    nova_label = f"{nova_display} {nova_emoji}" if nova_emoji else nova_display
    return f"Agents: {agents_str} → {nova_label} (synthesised)"


def _parse_findings(response: str) -> tuple[list, str]:
    """Parse findings list from a JSON review response. Returns (findings, summary).

    Handles variations in AI response structure:
    - {"findings": [...], "summary": "..."}
    - {"review": {"findings": [...], "summary": "..."}}
    - fields may use "message" instead of "summary"
    """
    clean = response.strip()
    if clean.startswith("```"):
        clean = "\n".join(clean.split("\n")[1:])
    if clean.endswith("```"):
        clean = "\n".join(clean.split("\n")[:-1])
    data = json.loads(clean.strip())
    # Unwrap nested "review" key if present (some models wrap the response)
    if "review" in data and isinstance(data["review"], dict):
        data = data["review"]
    findings = data.get("findings", [])
    summary = data.get("summary", "") or data.get("message", "")
    return findings, summary


def _extract_finding_fields(f: dict) -> tuple[str, str, str, str, str, int]:
    """Extract and normalise fields from a finding dict.

    Returns (sev, issue, details, rec, cat, line).
    Handles field name variations across different AI models.
    """
    sev = (f.get("severity") or "info").lower()
    issue = (f.get("issue") or f.get("message") or f.get("title") or "").strip()
    details = (f.get("details") or f.get("description") or f.get("detail") or "").strip()
    rec = (f.get("recommendation") or f.get("suggestion") or f.get("fix") or "").strip()
    cat = (f.get("category") or f.get("type") or "").strip()
    _raw_line = f.get("line") or f.get("lines") or f.get("line_number") or 1
    try:
        line = int(_raw_line)
    except (ValueError, TypeError):
        line = 1  # AI returned non-numeric value (e.g. code text) — fall back to line 1
    return sev, issue, details, rec, cat, line


def _format_finding(f: dict) -> str:
    """Format a single finding as a readable markdown block."""
    sev, issue, details, rec, cat, _ = _extract_finding_fields(f)
    emoji = SEVERITY_EMOJI.get(sev, "⚪")

    lines = [f"#### {emoji} {issue}"]
    if cat:
        display_cat = cat.replace('-', ' ').title()
        display_agent = _AGENT_DISPLAY_NAMES.get(f.get("agent_name", ""), "")
        label = f"{display_agent} · {display_cat}" if display_agent else display_cat
        lines.append(f"*{label}*  ")
    if details:
        lines.append(f"\n{details}")
    if rec:
        lines.append(f"\n> 💡 {rec}")
    return "\n".join(lines)


def _format_file_review(file_path: str, response: str) -> str:
    """Format a raw JSON review response into readable markdown with visual hierarchy."""


    try:
        findings, summary = _parse_findings(response)
    except (json.JSONDecodeError, TypeError, KeyError):
        return f"### `{file_path}`\n\n{response}\n"

    if not findings:
        return f"### `{file_path}`\n\n✅ *No issues found.*\n"

    # Count by severity for the header badge line
    counts = {}
    for f in findings:
        sev = f.get("severity", "info").lower()
        counts[sev] = counts.get(sev, 0) + 1

    badge_parts = []
    for sev in SEVERITY_ORDER:
        if sev in counts:
            badge_parts.append(f"{SEVERITY_EMOJI[sev]} {counts[sev]} {sev}")
    badge_line = " · ".join(badge_parts)

    lines = [f"### `{file_path}`"]
    lines.append(f"> {badge_line}\n")

    if summary:
        lines.append(f"{summary}\n")

    # Group findings: high/medium inline, low collapsed
    high_med = [f for f in findings if f.get("severity", "").lower() in ("high", "medium")]
    low_info = [f for f in findings if f.get("severity", "").lower() in ("low", "info")]

    for f in high_med:
        lines.append(_format_finding(f))
        lines.append("")

    if low_info:
        low_labels = " · ".join(
            f"{SEVERITY_EMOJI.get(f.get('severity','info').lower(),'⚪')} {f.get('issue','')}"
            for f in low_info
        )
        lines.append(f"<details><summary>Minor issues: {low_labels}</summary>\n")
        for f in low_info:
            lines.append(_format_finding(f))
            lines.append("")
        lines.append("</details>")

    return "\n".join(lines)


# Compiled once at module level — used by fingerprint scanning helpers below.
_FP_SENTINEL_RE = re.compile(r'\[//\]: # \(revue:fp:([a-f0-9]+)\)')
# Severity emoji alternation derived from core.display so renaming a badge
# propagates here automatically.
_FINDING_HEADER_RE = re.compile(rf'^\*\*(?:{SEVERITY_EMOJI_ALT})\s*\[(?:HIGH|MEDIUM|LOW|INFO)\]')
# Extracts the normalised severity token from an existing Revue comment body.
# Used so open-prior counting uses the ORIGINAL severity (as posted) not the
# current-run re-analysis, keeping the Quality Breakdown consistent with what
# users see in the UI.
_FINDING_SEV_EXTRACT_RE = re.compile(rf'\*\*(?:{SEVERITY_EMOJI_ALT})\s*\[(HIGH|MEDIUM|LOW|INFO)\]')


def _apply_sentinel_strategy(
    body: str, comment_id_str: str, result: dict, resolved: bool = False
) -> None:
    """Strategy 1: extract a sentinel-embedded fingerprint from a comment body.

    Each Revue finding comment written by REVUE-119+ code contains
    ``[//]: # (revue:fp:{hash})`` — extract and record it so fresh-CI
    runs can skip re-posting the same finding.

    ``resolved`` is the discussion-level resolution state injected by
    GitLabAdapter (``_discussion_resolved`` field).  Stored so that
    ``_run_per_issue_dedup`` can exclude resolved-thread findings from
    the summary 'requires attention' count.
    """
    m = _FP_SENTINEL_RE.search(body)
    if m:
        sev_m = _FINDING_SEV_EXTRACT_RE.search(body)
        result[m.group(1)] = {
            "platform_comment_id": comment_id_str,
            "file_path": "",
            "resolved": resolved,
            "severity": sev_m.group(1).lower() if sev_m else "",
        }


def _apply_location_strategy(c: dict, body: str, comment_id_str: str, result: dict, gen_fp) -> None:
    """Strategy 2: derive a location-based fingerprint from inline comment metadata.

    Uses ``file_path + line`` only (no diff context) so it matches findings
    computed by ``gen_fingerprint(file, line, "")`` — covers older comments
    that pre-date the sentinel scheme.

    Supports all three platforms:
    - Bitbucket: ``c["inline"]["path"]`` / ``c["inline"]["to"]``
    - GitLab:    ``c["position"]["new_path"]`` / ``c["position"]["new_line"]``  (dict)
    - GitHub:    top-level ``c["path"]`` / ``c["line"]``
                 (GitHub ``c["position"]`` is an integer diff-position, NOT a dict)

    ``_discussion_resolved`` on the comment dict is propagated so that
    resolved won't-fix threads don't inflate the summary count.
    """
    if not _FINDING_HEADER_RE.match(body):
        return
    # Each platform stores location differently:
    #   Bitbucket: c["inline"]["path"] / c["inline"]["to"]
    #   GitLab:    c["position"]["new_path"] / c["position"]["new_line"]  (dict)
    #   GitHub:    c["path"] / c["line"]  (top-level; c["position"] is an int, not a dict)
    inline = c.get("inline") or {}
    pos_raw = c.get("position")
    position = pos_raw if isinstance(pos_raw, dict) else {}
    file_path = inline.get("path") or position.get("new_path") or c.get("path", "")
    line = inline.get("to") or position.get("new_line") or c.get("line") or 0
    if file_path and line:
        sev_m = _FINDING_SEV_EXTRACT_RE.search(body)
        result[gen_fp(file_path, int(line), "")] = {
            "platform_comment_id": comment_id_str,
            "file_path": file_path,
            "resolved": bool(c.get("_discussion_resolved", False)),
            "severity": sev_m.group(1).lower() if sev_m else "",
        }


def _build_api_fingerprint_map(adapter, pr_num: int) -> dict[str, dict]:
    """Scan live PR comments for embedded fingerprint sentinels and location-based fingerprints.

    Runs both discovery strategies in a single pass over PR comments.
    Collecting these on startup makes deduplication work on fresh CI checkouts
    where the local ``.revue/`` store is empty.

    Returns ``{fingerprint: {"platform_comment_id": str, "file_path": str}}``
    so the result merges cleanly with ``PerPRCommentStore.get_unresolved_fingerprints``.
    """
    from revue.comments.fingerprint import fingerprint as gen_fingerprint
    result: dict[str, dict] = {}
    try:
        comments = adapter.get_existing_comments(pr_id=pr_num)
        for c in comments:
            body = c.get("content", {}).get("raw", "") or c.get("body", "") or ""
            # For GitLab: use discussion ID (injected as _discussion_id) as the
            # platform_comment_id so AC5's resolve_inline_comment call uses the
            # correct endpoint. Bitbucket/GitHub have no _discussion_id, so fall
            # back to the note/comment ID as before.
            effective_id = str(c.get("_discussion_id", "") or c.get("id", ""))
            resolved = bool(c.get("_discussion_resolved", False))
            _apply_sentinel_strategy(body, effective_id, result, resolved=resolved)
            _apply_location_strategy(c, body, effective_id, result, gen_fingerprint)
    except Exception:
        pass
    return result


def _get_highest_severity(severities: list[str]) -> str:
    """Return the highest severity from a list, using SEVERITY_ORDER."""
    for sev in SEVERITY_ORDER:
        if sev in severities:
            return sev
    return "info"



def _github_suggestion_block(lines: list[str], replacement_line_count: int = 1) -> str:
    """Return a GitHub native suggestion block for inline PR comments.

    GitHub renders ```suggestion fences as a one-click "Commit suggestion" button.
    lines should be the exact replacement lines (already string-safe).
    Multi-line range is handled via start_line/line in the API payload, not the fence.
    replacement_line_count is accepted for interface symmetry but has no effect here.
    """
    return "\n```suggestion\n" + "\n".join(lines) + "\n```\n"


def _gitlab_suggestion_block(lines: list[str], replacement_line_count: int = 1) -> str:
    """Return a GitLab native suggestion block for inline MR comments.

    ``suggestion:-0+N`` deletes N+1 original lines total (anchor line + N below it).
    For replacement_line_count=1 (single line): ``suggestion:-0+0`` (delete anchor only).
    For replacement_line_count=3 (three lines): ``suggestion:-0+2`` (anchor + 2 more).
    GitLab renders an "Apply suggestion" button.
    """
    lines_to_delete = max(0, replacement_line_count - 1)
    return f"\n```suggestion:-0+{lines_to_delete}\n" + "\n".join(lines) + "\n```\n"


_SUGGESTION_BLOCK_FORMATTERS: dict[str, Callable[[list[str], int], str]] = {
    "github": _github_suggestion_block,
    "gitlab": _gitlab_suggestion_block,
}


def _format_recommendation(
    rec: str,
    code_replacement: "list[str] | None",
    platform_str: str,
    replacement_line_count: int = 1,
) -> str:
    """Format the recommendation section of an inline comment body.

    When code_replacement is non-empty and the platform supports native suggestion
    blocks, returns the appropriate fenced suggestion block.  Otherwise falls back
    to the plain blockquote used before REVUE-187.

    Bitbucket is not in _SUGGESTION_BLOCK_FORMATTERS so it always uses the blockquote.
    """
    formatter = _SUGGESTION_BLOCK_FORMATTERS.get(platform_str)
    if formatter and code_replacement:
        prose = f"\n> 💡 **Recommendation:** {rec}" if rec.strip() else ""
        return f"{prose}{formatter(code_replacement, replacement_line_count=replacement_line_count)}"
    if "```" in rec:
        fence_idx = rec.index("```")
        prose = rec[:fence_idx].rstrip()
        code = rec[fence_idx:]
        prefix = f"\n> 💡 **Recommendation:** {prose}" if prose else "\n> 💡 **Recommendation:**"
        return f"{prefix}\n\n{code}"
    return f"\n> 💡 **Recommendation:** {rec}"


def _post_or_evict_and_retry(
    adapter,
    pr_num: int,
    position,
    body: str,
    eviction_state: list[bool],
    replacement_line_count: int = 1,
) -> str | None:
    """Post a review comment, evicting resolved threads once if the 200-comment limit is hit.

    ``eviction_state`` is a single-element list used as a mutable flag so the
    caller's loop can share state across iterations without a nonlocal.  Set
    ``eviction_state[0] = False`` before the loop; the function flips it to
    True on the first eviction attempt so subsequent calls skip the expensive
    API round-trip.

    Returns the posted comment ID on success, or None on failure.
    """
    comment_id = adapter.post_review_comment(
        pr_id=pr_num, position=position, body=body,
        replacement_line_count=replacement_line_count,
    )
    if comment_id is not None:
        return comment_id

    if not getattr(adapter, "comment_limit_reached", False) or eviction_state[0]:
        return None

    eviction_state[0] = True
    evicted = adapter.evict_resolved_revue_comments(pr_num)
    if evicted == 0:
        return None

    print(f"[revue] 🗑️ Evicted {evicted} resolved Revue comment(s) to free up space")
    adapter.comment_limit_reached = False
    return adapter.post_review_comment(
        pr_id=pr_num, position=position, body=body,
        replacement_line_count=replacement_line_count,
    )


def _run_per_issue_dedup(
    adapter,
    pr_num: int,
    platform_str: str,
    review_results: list,
    diff_by_file: dict,
    dedup_store,
) -> tuple[int, int, dict[str, int], int, int, list]:
    """Core per-issue dedup loop shared across all platform posting functions.

    Nova's consolidated list is the authoritative set of findings — every item
    in it is posted inline (WYSIWYG).  Cross-cycle dedup (AC1/AC2) prevents
    re-posting a finding that already has an open comment from a prior review.

    On fresh CI (empty local store), dedup falls back to scanning live API
    comments via ``_build_api_fingerprint_map`` — sentinel and location-based
    fingerprints derived from inline comment metadata prevent re-posting without
    any stored state.

    Returns ``(posted, skipped, total_findings, previously_tracked, failed, summary_sink)`` where:
    - ``total_findings``     — severity breakdown for findings requiring attention
                               (new postings + open-prior skips; excludes resolved-prior)
    - ``previously_tracked`` — count of findings skipped because they matched a
                               RESOLVED prior thread (won't-fix decisions). These are
                               excluded from total_findings so the summary does not
                               claim they 'require attention'
    - ``failed``             — count of findings where the API call was attempted but
                               returned an error (e.g. 403 Forbidden). Non-zero means
                               the review is incomplete and the user should check credentials.
    - ``summary_sink``       — unanchored findings (position=0 on GitHub/Bitbucket) that
                               cannot be posted inline; passed to _build_enhanced_summary
                               for rendering via BodyBuilder.build_summary() (AC6).

    Order guarantee: total_findings is incremented AFTER the resolved-prior check
    so that resolved won't-fix findings are never counted toward the summary total.
    If this order is ever changed, test_resolved_prior_excluded_from_summary_count
    and test_open_prior_still_counted_in_summary will catch the regression.
    """
    from revue.comments.fingerprint import fingerprint as gen_fingerprint
    from revue.comments.models import CommentState
    from revue.core.diff_position_resolver import DiffPositionResolver
    from revue.core.vcs_adapter import DiffPosition, compute_gitlab_line_code

    prior_unresolved = dedup_store.get_unresolved_fingerprints(platform_str, pr_num)
    # Seed from live API — fills the gap when local store is empty (fresh CI).
    # Local store entries take precedence (richer metadata); API covers the rest.
    api_fps = _build_api_fingerprint_map(adapter, pr_num)
    merged_prior = {**api_fps, **prior_unresolved}
    posted = 0
    skipped = 0
    failed = 0
    previously_tracked = 0
    total_findings: dict[str, int] = {"high": 0, "medium": 0, "low": 0, "info": 0}
    summary_sink: list[ConsolidatedFinding] = []
    _eviction_state: list[bool] = [False]  # shared mutable flag for _post_or_evict_and_retry
    # Hunk fps seen this cycle — used by AC5 to detect fixed findings.
    seen_hunk_fps: set[str] = set()

    # Phase 1: Collect findings, compute fingerprints, add to seen_hunk_fps, group by line.
    # grouping tuple: (file_path, line, sev, issue, rec, details, cat, finding_dict)
    findings_to_process: list[tuple[str, int, str, str, str, str, str, dict]] = []

    for rr in review_results:
        if rr.error or not rr.response:
            continue
        try:
            findings, _ = _parse_findings(rr.response)
        except Exception:
            continue

        diff_content = diff_by_file.get(rr.file_path, "")

        for f in findings:
            sev, issue, details, rec, cat, line = _extract_finding_fields(f)

            if not issue and not details:
                continue

            fp = gen_fingerprint(rr.file_path, line, diff_content)
            # Always add to seen_hunk_fps — even if deduped later, needed for AC5 auto-resolve.
            seen_hunk_fps.add(fp)

            findings_to_process.append((rr.file_path, line, sev, issue, rec, details, cat, f))

    # Phase 2: Group findings by (file_path, line) — REVUE-172 AC1.
    groups: dict[tuple[str, int], list[tuple[str, str, str, str, str, dict]]] = {}
    for file_path, line, sev, issue, rec, details, cat, f in findings_to_process:
        key = (file_path, line)
        if key not in groups:
            groups[key] = []
        groups[key].append((sev, issue, rec, details, cat, f))

    # Phase 3: For each group, check merged_prior once (per Jira note: grouping before dedup).
    # Then post merged comment if new, or skip if deduped.
    _builder = BodyBuilder()
    for (file_path, line), group_items in groups.items():
        diff_content = diff_by_file.get(file_path, "")
        fp = gen_fingerprint(file_path, line, diff_content)

        # AC1/AC2: Check if line was already posted in a previous review cycle.
        # Check diff-based fp first (most precise); fall back to location-based.
        matched_entry = merged_prior.get(fp) or merged_prior.get(
            gen_fingerprint(file_path, line, "")
        )
        if matched_entry is not None:
            if matched_entry.get("resolved", False):
                # Thread is resolved (won't-fix decision). Do NOT count findings — issue is handled.
                previously_tracked += len(group_items)
            else:
                # Open prior thread — still requires attention. Count each finding.
                # Use the severity from the EXISTING comment (as visible in the UI).
                prior_sev = matched_entry.get("severity", "")
                if not prior_sev:
                    cb = matched_entry.get("comment_body", "")
                    sev_m = _FINDING_SEV_EXTRACT_RE.search(cb)
                    prior_sev = sev_m.group(1).lower() if sev_m else ""
                # Count each finding in the group using prior severity.
                for sev, issue, rec, details, cat, f in group_items:
                    count_sev = prior_sev if prior_sev in total_findings else sev
                    if count_sev in total_findings:
                        total_findings[count_sev] += 1
            skipped += len(group_items)
            continue

        # New group — count all findings toward summary total.
        for sev, issue, rec, details, cat, f in group_items:
            if sev in total_findings:
                total_findings[sev] += 1

        replacement_line_count = 1  # default; overridden below for single findings
        if len(group_items) == 1:
            sev, issue, rec, details, cat, f = group_items[0]
            agent_name = f.get("agent_name") or "unknown"
            code_replacement = filter_code_replacement(f.get("code_replacement"))
            replacement_line_count = f.get("replacement_line_count", 1)
            synthesised_from = f.get("synthesised_from")
            if synthesised_from:
                attribution = [Attribution(agent_name=a[0], category=a[1]) for a in synthesised_from]
                logging.debug(
                    "[revue:body] %s:%s synthesised_from → %d attribution(s): %s",
                    file_path, line, len(attribution),
                    _Lazy(lambda: ", ".join(f"{a.agent_name}/{a.category}" for a in attribution)),
                )
            else:
                attribution = [Attribution(agent_name=agent_name, category=cat or "general")]
            logging.debug(
                "[revue:body] %s:%s → singleton  platform=%s  agent=%s  sev=%s  has_code=%s",
                file_path, line, platform_str, attribution[0].agent_name, sev,
                code_replacement is not None,
            )
            consolidated = ConsolidatedFinding(
                file_path=file_path,
                line_number=line,
                severity=sev,  # type: ignore[arg-type]
                issue=issue or "Issue found",
                suggestion=rec or "",
                confidence=float(f.get("confidence", 0.8)),
                category=cat or "general",
                attribution=attribution,
                code_replacement=code_replacement,
                replacement_line_count=replacement_line_count,
                snippet="",
                group_type="singleton",
            )
            body = _builder.build(consolidated, fp=fp, platform=platform_str)
        else:
            # Multiple findings on same line — use build_grouped() for proper per-item rendering.
            grouped_items: list[ConsolidatedFinding] = []
            for sev, iss, rec, details, cat, f in group_items:
                agent_name = f.get("agent_name") or "unknown"
                item_code_replacement = filter_code_replacement(f.get("code_replacement"))
                grouped_items.append(ConsolidatedFinding(
                    file_path=file_path,
                    line_number=line,
                    severity=sev,  # type: ignore[arg-type]
                    issue=iss or "Issue found",
                    suggestion=rec or "",
                    confidence=float(f.get("confidence", 0.8)),
                    category=cat or "general",
                    attribution=[Attribution(agent_name=agent_name, category=cat or "general")],
                    code_replacement=item_code_replacement,
                    replacement_line_count=f.get("replacement_line_count", 1),
                    snippet="",
                    group_type="same_line",
                ))
            logging.debug(
                "[revue:body] %s:%s → grouped(%d)  platform=%s  agents=%s",
                file_path, line, len(grouped_items), platform_str,
                ", ".join(gi.attribution[0].agent_name for gi in grouped_items),
            )
            body = _builder.build_grouped(grouped_items, fp=fp, platform=platform_str)
            replacement_line_count = 1

        # Snap agent-reported line to a valid diff position before resolving (REVUE-201).
        # Tier 3 (file-read fallback) is intentionally disabled — repo_path is not available
        # in local diff review mode; snap/line_in_diff use Tier 1/2 only.
        snapped_line = DiffPositionResolver.snap(line, diff_content)
        # F2: snap relocation invalidates the span — reset to single-line anchor.
        if snapped_line != line:
            replacement_line_count = 1
        # F3: end-line must exist in the diff; fall back to single-line if it overshoots.
        elif replacement_line_count > 1:
            end_line = snapped_line + replacement_line_count - 1
            if not DiffPositionResolver.line_in_diff(end_line, diff_content):
                replacement_line_count = 1

        # Resolve position and post — same logic as before.
        if platform_str == "gitlab":
            lc, resolved_line, old_ln = compute_gitlab_line_code(
                file_path, diff_content, snapped_line
            )
            position = DiffPosition(
                file_path=file_path,
                line_number=resolved_line,
                line_code=lc,
                new_line=resolved_line,
                old_line=old_ln if old_ln > 0 else None,
                side="RIGHT",
            )
        else:
            position = adapter.resolve_position(file_path, snapped_line, diff_content)

        # position=0 is the sentinel for "line outside diff hunks" on both
        # GitHub (500) and Bitbucket (403). Collect into summary_sink for
        # BodyBuilder.build_summary() rather than silently discarding (AC6).
        if platform_str in ("github", "bitbucket") and position.position == 0:
            if len(group_items) == 1:
                summary_sink.append(consolidated)
            else:
                summary_sink.extend(grouped_items)
            skipped += len(group_items)
            continue

        comment_id = _post_or_evict_and_retry(
            adapter, pr_num, position, body, _eviction_state, replacement_line_count
        )

        if comment_id is not None:
            posted += 1
            dedup_store.save_finding(
                platform=platform_str,
                pr_number=pr_num,
                file_path=file_path,
                fingerprint=fp,
                platform_comment_id=comment_id,
                line_number=line,
                comment_body=body,
            )
        else:
            failed += 1

    # AC5: auto-resolve findings absent from new review.
    # Use merged_prior so API-seeded entries (fresh CI) are also considered.
    resolved_fps = set(merged_prior.keys()) - seen_hunk_fps

    for fp in resolved_fps:
        entry = merged_prior[fp]
        old_comment_id = entry.get("platform_comment_id")
        if old_comment_id:
            ok = adapter.resolve_inline_comment(
                pr_id=pr_num,
                comment_id=old_comment_id,
                reply_body="✅ Issue appears to be resolved in latest commit.",
            )
            if ok:
                dedup_store.mark_resolved(
                    platform=platform_str,
                    pr_number=pr_num,
                    file_path=entry.get("file_path", ""),
                    fingerprint=fp,
                    state=CommentState.AUTO_RESOLVED,
                    reason="auto-resolved",
                )

    return posted, skipped, total_findings, previously_tracked, failed, summary_sink


def _post_to_platform(
    adapter,
    pr_id,
    platform_str: str,
    platform_enum,
    repo_owner: str,
    repo_name: str,
    review_results: list,
    diff_by_file: dict,
    comment_style: str,
    pr_label: str = "PR",
    fallback_mode: str = "normal",
    show_reviewed_files: bool = True,
    rating_cfg: dict | None = None,
) -> tuple[int, int]:
    """Shared posting logic for Bitbucket, GitHub, and GitLab (Winston #2).

    All three platforms share identical dedup, summary tracking, and comment
    formatting logic.  The three public functions are thin credential wrappers
    that resolve credentials + build an adapter, then delegate here.

    Args:
        adapter:       Pre-built platform adapter (BitbucketAdapter etc.).
        pr_id:         PR/MR number (str or int).
        platform_str:  Lowercase platform name ("bitbucket", "github", "gitlab").
        platform_enum: Platform enum value for CommentFileStore.
        repo_owner:    Repository owner/namespace (for summary tracking).
        repo_name:     Repository name (for summary tracking).
        review_results: List of ReviewResult objects from the pipeline.
        diff_by_file:  Parsed diff keyed by file_path (for fingerprinting).
        comment_style: "per-issue" or "summary".
        pr_label:      Display label — "PR" for Bitbucket/GitHub, "MR" for GitLab.
    """
    from datetime import datetime, timezone

    from revue.comments.file_store import CommentFileStore
    from revue.comments.json_store import PerPRCommentStore
    from revue.comments.models import SummaryComment

    _repo_path = Path(os.getcwd())
    dedup_store = PerPRCommentStore(_repo_path)
    pr_num = int(pr_id)

    _summary_store = CommentFileStore(_repo_path)
    _existing_summary = _summary_store.get_summary_for_pr(
        platform=platform_enum,
        repo_owner=repo_owner,
        repo_name=repo_name,
        pr_number=pr_num,
    )
    _revision = (_existing_summary.revision + 1) if _existing_summary else 1
    _last_updated = "just now"

    _REVUE_SUMMARY_MARKER = "## 🤖 Revue.io — Code Review"

    _REVISION_RE = re.compile(r"Review #(\d+)")

    def _scan_for_existing_summary() -> Optional[tuple[str, int]]:
        """Scan live platform comments for a Revue summary.

        Returns (comment_id, revision) when found, None otherwise.
        Checks issue-level comments first (GitHub summary lives there), then
        falls back to all PR comments (Bitbucket/GitLab use a unified endpoint).
        """
        try:
            # GitHub posts the summary as an issue comment (/issues/{id}/comments),
            # not as a review comment (/pulls/{id}/comments).  Use get_issue_comments
            # when available so we find the existing summary and update in-place.
            get_issue_fn = getattr(adapter, "get_issue_comments", None)
            if callable(get_issue_fn):
                for c in get_issue_fn(pr_id=pr_num):
                    body = c.get("content", {}).get("raw", "") or c.get("body", "") or ""
                    if _REVUE_SUMMARY_MARKER in body:
                        m = _REVISION_RE.search(body)
                        return (str(c.get("id", "")), int(m.group(1)) if m else 1)
            # Fallback: covers Bitbucket/GitLab where summary is in general comments
            for c in adapter.get_existing_comments(pr_id=pr_num):
                body = c.get("content", {}).get("raw", "") or c.get("body", "") or ""
                if _REVUE_SUMMARY_MARKER in body:
                    m = _REVISION_RE.search(body)
                    return (str(c.get("id", "")), int(m.group(1)) if m else 1)
        except Exception:
            pass
        return None

    # Eagerly scan for a live summary when the local state file is absent.
    # This ensures _revision is set correctly on ephemeral CI where .revue/ is
    # not persisted between pipeline runs.
    _scanned: Optional[tuple[str, int]] = None
    if _existing_summary is None:
        _scanned = _scan_for_existing_summary()
        if _scanned is not None:
            _revision = _scanned[1] + 1

    def _post_or_update_summary(body: str) -> None:
        nonlocal _existing_summary, _revision
        now = datetime.now(timezone.utc)

        # Resolve the comment ID to update: prefer state file, fall back to
        # the eagerly scanned result so re-reviews never post a duplicate summary
        # even when the local state file is stale or missing (e.g. ephemeral CI).
        existing_comment_id = (
            _existing_summary.platform_comment_id if _existing_summary
            else (_scanned[0] if _scanned else None)
        )

        if existing_comment_id:
            ok = adapter.update_comment(
                pr_id=pr_num,
                comment_id=existing_comment_id,
                body=body,
            )
            if ok:
                created_at = _existing_summary.created_at if _existing_summary else now
                updated = SummaryComment(
                    id=None,
                    platform=platform_enum,
                    platform_comment_id=existing_comment_id,
                    pr_number=pr_num,
                    repo_owner=repo_owner,
                    repo_name=repo_name,
                    total_issues=sum(total_findings.values()),
                    fixed_count=0,
                    discussed_count=0,
                    remaining_count=sum(total_findings.values()),
                    last_updated_at=now,
                    created_at=created_at,
                    revision=_revision,
                )
                _summary_store.create_or_update_summary(updated)
                print(f"[revue] Summary comment updated in-place (Review #{_revision})")
                return
            else:
                print(
                    f"[revue] ⚠ Failed to update existing summary comment {existing_comment_id} "
                    f"— API may have rate-limited or revoked access. Posting new summary comment.",
                    file=sys.stderr,
                )
                _revision = _revision + 1
        comment_id = adapter.post_summary_comment(pr_id=pr_num, body=body)
        if comment_id:
            summary = SummaryComment(
                id=None,
                platform=platform_enum,
                platform_comment_id=comment_id,
                pr_number=pr_num,
                repo_owner=repo_owner,
                repo_name=repo_name,
                total_issues=sum(total_findings.values()),
                fixed_count=0,
                discussed_count=0,
                remaining_count=sum(total_findings.values()),
                last_updated_at=now,
                created_at=now,
                revision=_revision,
            )
            _summary_store.create_or_update_summary(summary)
        else:
            print(f"Warning: Failed to post review summary to {pr_label}", file=sys.stderr)

    if comment_style == "per-issue":
        # Pre-count all findings so _post_or_update_summary closure has a valid
        # total_findings to reference regardless of posting order.
        total_findings: dict[str, int] = {"high": 0, "medium": 0, "low": 0, "info": 0}
        for rr in review_results:
            if rr.error or not rr.response:
                continue
            try:
                findings, _ = _parse_findings(rr.response)
                for f in findings:
                    sev = f.get("severity", "low").lower()
                    if sev in total_findings:
                        total_findings[sev] += 1
            except Exception as exc:
                print(f"[revue] Warning: failed to count findings for {rr.file_path}: {exc}", file=sys.stderr)

        # GitLab and Bitbucket show comments newest-first: post inline first,
        # summary last so it lands at the top of the thread.
        # GitHub shows oldest-first: post summary first so it stays pinned.
        gitlab_order = platform_str in ("gitlab", "bitbucket")

        if not gitlab_order:
            # GitHub: post preliminary summary (pre-dedup counts) so it stays
            # pinned at the top.  May over-count resolved won't-fix findings on
            # re-runs, but ordering constraint prevents a post-dedup rebuild here.
            summary_body = _build_enhanced_summary(
                review_results, total_findings, _revision, _last_updated,
                fallback_mode=fallback_mode,
                show_reviewed_files=show_reviewed_files,
                rating_cfg=rating_cfg,
            )
            _post_or_update_summary(summary_body)

        posted, skipped, total_findings, previously_tracked, failed, summary_sink = _run_per_issue_dedup(
            adapter, pr_num, platform_str, review_results, diff_by_file, dedup_store
        )
        # total_findings is now reassigned to the post-dedup accurate counts.
        # The closure in _post_or_update_summary sees the updated binding.

        if gitlab_order:
            # Rebuild summary with accurate post-dedup counts: total_findings
            # excludes resolved-prior findings; previously_tracked notes how many
            # won't-fix decisions were skipped.
            summary_body = _build_enhanced_summary(
                review_results, total_findings, _revision, _last_updated,
                fallback_mode=fallback_mode,
                show_reviewed_files=show_reviewed_files,
                rating_cfg=rating_cfg,
                previously_tracked=previously_tracked,
                summary_sink=summary_sink,
            )
            _post_or_update_summary(summary_body)

        if failed:
            if getattr(adapter, "comment_limit_reached", False):
                print(f"[revue] ❌ {pr_label} #{pr_id} has reached Bitbucket's 200-comment limit — resolve or delete old Revue comments to make room for new ones")
            else:
                print(f"[revue] ❌ {failed} comment(s) could not be posted to {pr_label} #{pr_id} — API error (check token permissions)")
        if skipped > 0:
            print(f"[revue] Review posted to {pr_label} #{pr_id} — {posted} new, {skipped} preserved inline comment(s)")
        else:
            print(f"[revue] Review posted to {pr_label} #{pr_id} — {posted} inline comment(s)")
    else:
        posted = 0
        failed = 0
        total_findings: dict[str, int] = {"high": 0, "medium": 0, "low": 0, "info": 0}
        file_sections = []
        for rr in review_results:
            if rr.error or not rr.response:
                continue
            try:
                findings, _ = _parse_findings(rr.response)
                for f in findings:
                    sev = f.get("severity", "low").lower()
                    if sev in total_findings:
                        total_findings[sev] += 1
            except Exception:
                pass
            file_sections.append(_format_file_review(rr.file_path, rr.response))
            posted += 1
        summary_body = _build_enhanced_summary(
            review_results, total_findings, _revision, _last_updated,
            fallback_mode=fallback_mode,
            show_reviewed_files=show_reviewed_files,
            rating_cfg=rating_cfg,
        )
        if file_sections:
            summary_body += "\n\n---\n\n" + "\n\n".join(file_sections)
        _post_or_update_summary(summary_body)
        print(f"[revue] Review posted to {pr_label} #{pr_id} — {posted} file(s) in summary comment")

    return posted, failed


def _build_hunk_tracker(adapter, dedup_store, config):
    """Return a HunkTracker (or NullHunkTracker when Nova is unavailable)."""
    from revue.comments.hunk_tracker import HunkTracker, NullHunkTracker, NovaSingleShotResolutionStrategy
    nova_client = create_ai_client(config) if config else None
    if not nova_client:
        return NullHunkTracker(adapter, dedup_store)
    return HunkTracker(
        adapter=adapter,
        dedup_store=dedup_store,
        resolution_strategy=NovaSingleShotResolutionStrategy(nova_client),
    )


def _post_to_bitbucket(args: argparse.Namespace, review_results: list, config=None, fallback_mode: str = "normal") -> tuple[int, int] | None:
    """Resolve Bitbucket credentials and delegate to Poster."""
    from revue.comments.platform_adapter import BitbucketAdapter
    from revue.comments.poster import Poster
    from revue.comments.json_store import PerPRCommentStore
    from revue.comments.file_store import CommentFileStore
    from revue.core.diff_parser import parse_diff_file
    from revue.comments.models import Platform

    pr_id = getattr(args, "pr_id", None)
    workspace = getattr(args, "workspace", None)
    repo_slug = getattr(args, "repo_slug", None)
    bb_username = getattr(args, "bb_username", None)
    bb_token = getattr(args, "bb_token", None)
    comment_style = getattr(args, "comment_style", "per-issue")

    missing = [n for n, v in [
        ("--pr-id", pr_id), ("--workspace", workspace),
        ("--repo-slug", repo_slug), ("--bb-username", bb_username),
        ("--bb-token", bb_token),
    ] if not v]
    if missing:
        print(f"Warning: Bitbucket posting skipped — missing: {', '.join(missing)}", file=sys.stderr)
        return

    show_reviewed_files = getattr(config, "show_reviewed_files", True) if config else True
    rating_cfg = getattr(config, "rating_weights", None) if config else None
    adapter = BitbucketAdapter(
        username=bb_username,
        app_password=bb_token,
        workspace=workspace,
        repo_slug=repo_slug,
    )
    diff_by_file = _parse_diff_by_file(getattr(args, "diff", None), parse_diff_file)
    _repo_path = Path(os.getcwd())
    dedup_store = PerPRCommentStore(_repo_path)
    poster = Poster(
        adapter=adapter,
        platform_str="bitbucket",
        platform_enum=Platform.BITBUCKET,
        dedup_store=dedup_store,
        summary_store=CommentFileStore(_repo_path),
        diff_by_file=diff_by_file,
        hunk_tracker=_build_hunk_tracker(adapter, dedup_store, config),
    )
    return poster.post(
        pr_id=pr_id,
        review_results=review_results,
        comment_style=comment_style,
        repo_owner=workspace,
        repo_name=repo_slug,
        pr_label="PR",
        fallback_mode=fallback_mode,
        show_reviewed_files=show_reviewed_files,
        rating_cfg=rating_cfg,
    )


def _post_to_github(args: argparse.Namespace, review_results: list, config=None, fallback_mode: str = "normal") -> tuple[int, int] | None:
    """Resolve GitHub credentials and delegate to Poster."""
    from revue.core.github_adapter import GitHubAdapter
    from revue.comments.poster import Poster
    from revue.comments.json_store import PerPRCommentStore
    from revue.comments.file_store import CommentFileStore
    from revue.core.diff_parser import parse_diff_file
    from revue.comments.models import Platform

    pr_id = getattr(args, "pr_id", None)
    comment_style = getattr(args, "comment_style", "per-issue")

    if not pr_id:
        print("Warning: GitHub posting skipped — missing --pr-id", file=sys.stderr)
        return

    token = os.getenv("GITHUB_TOKEN", "")
    if not token:
        print("Warning: GitHub posting skipped — GITHUB_TOKEN not set", file=sys.stderr)
        return

    repo = os.getenv("GITHUB_REPOSITORY", "")
    if not repo:
        workspace = getattr(args, "workspace", None) or ""
        repo_slug = getattr(args, "repo_slug", None) or ""
        if workspace and repo_slug:
            repo = f"{workspace}/{repo_slug}"
    if not repo:
        print("Warning: GitHub posting skipped — cannot determine repo (set GITHUB_REPOSITORY or --workspace/--repo-slug)", file=sys.stderr)
        return

    repo_owner, repo_name = (repo.split("/", 1) + [""])[:2]
    show_reviewed_files = getattr(config, "show_reviewed_files", True) if config else True
    rating_cfg = getattr(config, "rating_weights", None) if config else None
    adapter = GitHubAdapter(token=token, repo=repo)
    diff_by_file = _parse_diff_by_file(getattr(args, "diff", None), parse_diff_file)
    _repo_path = Path(os.getcwd())
    dedup_store = PerPRCommentStore(_repo_path)
    poster = Poster(
        adapter=adapter,
        platform_str="github",
        platform_enum=Platform.GITHUB,
        dedup_store=dedup_store,
        summary_store=CommentFileStore(_repo_path),
        diff_by_file=diff_by_file,
        hunk_tracker=_build_hunk_tracker(adapter, dedup_store, config),
    )
    return poster.post(
        pr_id=pr_id,
        review_results=review_results,
        comment_style=comment_style,
        repo_owner=repo_owner,
        repo_name=repo_name,
        pr_label="GitHub PR",
        fallback_mode=fallback_mode,
        show_reviewed_files=show_reviewed_files,
        rating_cfg=rating_cfg,
    )


def _post_to_gitlab(args: argparse.Namespace, review_results: list, config=None, fallback_mode: str = "normal") -> tuple[int, int] | None:
    """Resolve GitLab credentials and delegate to Poster."""
    from revue.core.gitlab_adapter import GitLabAdapter
    from revue.comments.poster import Poster
    from revue.comments.json_store import PerPRCommentStore
    from revue.comments.file_store import CommentFileStore
    from revue.core.diff_parser import parse_diff_file
    from revue.comments.models import Platform

    pr_id = getattr(args, "pr_id", None)
    comment_style = getattr(args, "comment_style", "per-issue")

    if not pr_id:
        print("Warning: GitLab posting skipped — missing --pr-id", file=sys.stderr)
        return

    token = os.getenv("GITLAB_TOKEN", "")
    if not token:
        print("Warning: GitLab posting skipped — GITLAB_TOKEN not set", file=sys.stderr)
        return

    project_id: str = os.getenv("CI_PROJECT_PATH", "")
    if not project_id:
        workspace = getattr(args, "workspace", None) or ""
        repo_slug = getattr(args, "repo_slug", None) or ""
        if workspace and repo_slug:
            project_id = f"{workspace}/{repo_slug}"
    if not project_id:
        print("Warning: GitLab posting skipped — cannot determine project (set CI_PROJECT_PATH or --workspace/--repo-slug)", file=sys.stderr)
        return

    repo_owner, repo_name = (project_id.split("/", 1) + [""])[:2]
    show_reviewed_files = getattr(config, "show_reviewed_files", True) if config else True
    rating_cfg = getattr(config, "rating_weights", None) if config else None
    adapter = GitLabAdapter(token=token, project_id=project_id)
    diff_by_file = _parse_diff_by_file(getattr(args, "diff", None), parse_diff_file)
    _repo_path = Path(os.getcwd())
    dedup_store = PerPRCommentStore(_repo_path)
    poster = Poster(
        adapter=adapter,
        platform_str="gitlab",
        platform_enum=Platform.GITLAB,
        dedup_store=dedup_store,
        summary_store=CommentFileStore(_repo_path),
        diff_by_file=diff_by_file,
        hunk_tracker=_build_hunk_tracker(adapter, dedup_store, config),
    )
    return poster.post(
        pr_id=pr_id,
        review_results=review_results,
        comment_style=comment_style,
        repo_owner=repo_owner,
        repo_name=repo_name,
        pr_label="GitLab MR",
        fallback_mode=fallback_mode,
        show_reviewed_files=show_reviewed_files,
        rating_cfg=rating_cfg,
    )


def _parse_diff_by_file(diff_path, parse_diff_file_fn) -> dict[str, str]:
    """Parse diff file into {file_path: diff_content} lookup. Fail-safe."""
    if not diff_path:
        return {}
    try:
        return {fc.file_path: fc.diff for fc in parse_diff_file_fn(str(diff_path))}
    except Exception:
        return {}  # fingerprint falls back to line_number


def _resolve_pr_id_from_env() -> Optional[int]:
    """Resolve PR/MR ID from common CI environment variables.

    Checks in order:
    - Bitbucket: BITBUCKET_PR_ID
    - GitHub: GITHUB_PR_NUMBER (set by actions/checkout or workflow context)
    - GitLab: CI_MERGE_REQUEST_IID

    Returns None if no PR ID found or value is not numeric.
    """
    for var in ("BITBUCKET_PR_ID", "GITHUB_PR_NUMBER", "CI_MERGE_REQUEST_IID"):
        val = os.getenv(var, "").strip()
        if val and val.isdigit():
            return int(val)
    return None


def cmd_init(args: argparse.Namespace) -> int:
    target = Path(".revue.yml")
    if target.exists() and not args.force:
        print(
            "Error: .revue.yml already exists. Use --force to overwrite.",
            file=sys.stderr,
        )
        return 1

    target.write_text(DEFAULT_REVUE_YML)
    print("Created .revue.yml")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        config = load_config(config_path=args.config)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    errors = validate_config(config)
    if errors:
        for err in errors:
            print(f"Config error: {err}", file=sys.stderr)
        return 1

    print("Config valid")
    return 0


# ---------------------------------------------------------------------------
# list-models (REVUE-264)
# ---------------------------------------------------------------------------

# Columns emitted by ``revue list-models`` (default + markdown modes).
# Keep this list as the single source of truth — both renderers iterate it.
_LIST_MODELS_COLUMNS: tuple[tuple[str, str], ...] = (
    ("Model", "model_id"),
    ("Provider", "provider"),
    ("Tier", "tier"),
    ("schema_strict", "schema_strict"),
    ("tool_choice_first_turn", "tool_choice_first_turn"),
    ("max_tokens_default", "max_tokens_default"),
)

# Marker appended to a cell when the user has overridden it via .revue.yml.
_OVERRIDE_MARKER = "*"


def _load_user_models_block(cwd: Path) -> dict[str, dict[str, object]] | None:
    """Read ``.revue.yml`` in *cwd* and return its ``models:`` mapping.

    Returns ``None`` when the file is absent, empty, or has no ``models:``
    block. Any YAML parse error is surfaced as ``ValueError`` so the caller
    can decide whether to fail or fall back to the built-in registry.
    """
    import yaml  # local import: avoid yaml at module-import time

    revue_yml = cwd / ".revue.yml"
    if not revue_yml.exists():
        return None
    try:
        raw = yaml.safe_load(revue_yml.read_text()) or {}
    except (yaml.YAMLError, OSError) as exc:
        raise ValueError(f"failed to read {revue_yml}: {exc}") from exc
    models = raw.get("models") if isinstance(raw, dict) else None
    if not isinstance(models, dict):
        return None
    return models


def _resolve_list_models_registry() -> tuple[dict[str, ModelConfig], set[tuple[str, str]]]:
    """Return ``(registry, overridden_cells)``.

    ``overridden_cells`` is the set of ``(model_id, knob_name)`` pairs that
    differ from the built-in entry — used by the renderers to annotate cells.
    A customer-added model (not in the built-in) marks every cell as
    overridden.
    """
    builtin = load_builtin_registry()
    user_models = _load_user_models_block(Path.cwd())
    if not user_models:
        return builtin, set()
    merged = merge_user_overrides(builtin, user_models)

    overridden: set[tuple[str, str]] = set()
    knob_fields = ("provider", "schema_mode", "schema_strict",
                   "tool_choice_first_turn", "max_tokens_default", "tier")
    for model_id, entry in merged.items():
        base = builtin.get(model_id)
        if base is None:
            # Customer-added entry: flag the row itself as overridden.
            overridden.add((model_id, "model_id"))
            continue
        for knob in knob_fields:
            if getattr(entry, knob) != getattr(base, knob):
                overridden.add((model_id, knob))
    return merged, overridden


def _config_as_dict(
    cfg: ModelConfig,
    overridden_cells: set[tuple[str, str]] | None = None,
) -> dict[str, object]:
    """Render a ModelConfig as a plain dict (drops the read-only extras wrapper).

    When *overridden_cells* is supplied, an ``_overridden_fields`` key lists
    the knob names that came from ``.revue.yml`` for this model. A customer-
    added row (flagged via ``(model_id, "model_id")``) is reported as
    ``_customer_added: true`` instead, since every knob technically deviates
    from "no built-in".
    """
    payload: dict[str, object] = {
        "model_id": cfg.model_id,
        "provider": cfg.provider,
        "schema_mode": cfg.schema_mode,
        "schema_strict": cfg.schema_strict,
        "tool_choice_first_turn": cfg.tool_choice_first_turn,
        "max_tokens_default": cfg.max_tokens_default,
        "tier": cfg.tier,
        "extras": dict(cfg.extras),
    }
    if overridden_cells is not None:
        is_customer_added = (cfg.model_id, "model_id") in overridden_cells
        payload["_customer_added"] = is_customer_added
        if is_customer_added:
            payload["_overridden_fields"] = []
        else:
            payload["_overridden_fields"] = sorted(
                knob for (mid, knob) in overridden_cells if mid == cfg.model_id
            )
    return payload


def _cell_value(cfg: ModelConfig, field: str, overridden: bool) -> str:
    """Stringify a knob value and append the override marker when applicable."""
    if field == "model_id":
        raw = cfg.model_id
    else:
        raw = getattr(cfg, field)
    text = "true" if raw is True else "false" if raw is False else str(raw)
    return f"{text}{_OVERRIDE_MARKER}" if overridden else text


def _render_human_table(
    registry: dict[str, ModelConfig],
    overridden_cells: set[tuple[str, str]],
) -> str:
    """Render the registry as a fixed-width, terminal-friendly table.

    Uses stdlib f-string padding only — no ``tabulate`` / ``rich``.
    """
    headers = [label for label, _ in _LIST_MODELS_COLUMNS]

    rows: list[list[str]] = []
    for model_id in sorted(registry):
        cfg = registry[model_id]
        row: list[str] = []
        for label, field in _LIST_MODELS_COLUMNS:
            is_override = (model_id, field) in overridden_cells
            # A customer-added row marks every cell as overridden so the user
            # sees the row stands out even on its row-only flag.
            if (model_id, "model_id") in overridden_cells:
                is_override = True
            row.append(_cell_value(cfg, field, is_override))
        rows.append(row)

    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt_row(cells: list[str]) -> str:
        return "  ".join(c.ljust(widths[i]) for i, c in enumerate(cells))

    lines = [fmt_row(headers), fmt_row(["-" * w for w in widths])]
    lines.extend(fmt_row(r) for r in rows)
    lines.append("")
    lines.append(
        f"({_OVERRIDE_MARKER}) value overridden by .revue.yml in current directory."
    )
    return "\n".join(lines)


def _render_markdown_table(
    registry: dict[str, ModelConfig],
    overridden_cells: set[tuple[str, str]],
) -> str:
    """Render the registry as a GitHub-flavoured Markdown table."""
    headers = [label for label, _ in _LIST_MODELS_COLUMNS]
    header_line = "| " + " | ".join(headers) + " |"
    sep_line = "| " + " | ".join("---" for _ in headers) + " |"

    body_lines: list[str] = []
    for model_id in sorted(registry):
        cfg = registry[model_id]
        cells: list[str] = []
        for label, field in _LIST_MODELS_COLUMNS:
            is_override = (model_id, field) in overridden_cells
            if (model_id, "model_id") in overridden_cells:
                is_override = True
            cells.append(_cell_value(cfg, field, is_override))
        body_lines.append("| " + " | ".join(cells) + " |")

    footer = (
        f"\n_({_OVERRIDE_MARKER}) value overridden by `.revue.yml` "
        "in current directory._"
    )
    return "\n".join([header_line, sep_line, *body_lines]) + footer


def cmd_list_models(args: argparse.Namespace) -> int:
    """``revue list-models`` handler.

    Default output is a human-readable table. ``--json`` emits a JSON array;
    ``--markdown`` emits a GitHub-flavoured Markdown table (used to regenerate
    the README's Supported Models section).
    """
    try:
        registry, overridden_cells = _resolve_list_models_registry()
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if getattr(args, "as_json", False):
        # Deterministic order: sorted by model_id so the JSON output is
        # diff-friendly and CI assertions are stable.
        payload = [
            _config_as_dict(registry[mid], overridden_cells)
            for mid in sorted(registry)
        ]
        print(json.dumps(payload, indent=2))
        return 0

    if getattr(args, "as_markdown", False):
        print(_render_markdown_table(registry, overridden_cells))
        return 0

    print(_render_human_table(registry, overridden_cells))
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(0)
    sys.exit(args.func(args) or 0)


if __name__ == "__main__":
    main()
