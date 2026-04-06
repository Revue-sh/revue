#!/usr/bin/env python3
"""Revue CLI — local diff review, config init, and validation.

Entry point registered as ``revue`` in pyproject.toml.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Callable, Optional

from revue.core.config_loader import (
    DEFAULT_REVUE_YML,
    load_config,
    validate_config,
)
from revue.core.diff_parser import filter_changes, parse_diff_file
from revue.core.ai_client import create_ai_client
from revue.core.pipeline import ReviewPipeline


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

        resolved_pr_id = explicit_pr_id or _resolve_pr_id_from_env()
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
    print(f"[revue] Validating license...")
    try:
        review_results, excluded, files_reviewed = pipeline.run(str(diff_path), pr_description=pr_description)
    except AllAgentsFailedError as exc:
        # All reviewer agents failed (e.g. API credit exhausted, auth failure).
        # Log the generic message plus the first agent error to stderr for diagnostics.
        # first_error is kept on stderr only — not propagated further.
        print(f"[revue] ✗ {exc}", file=sys.stderr)
        print(f"[revue]   First agent error: {exc.first_error}", file=sys.stderr)
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
    if platform == "bitbucket":
        _post_to_bitbucket(args, review_results, config)
    elif platform == "github":
        _post_to_github(args, review_results, config)
    elif platform == "gitlab":
        _post_to_gitlab(args, review_results, config)

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

    return 0


SEVERITY_EMOJI = {"high": "🔴", "medium": "🟡", "low": "🔵", "info": "ℹ️"}
SEVERITY_ORDER = ["high", "medium", "low", "info"]

# AC2: category JSON field → display label (always show all 4)
_CATEGORY_MAP = {
    "architecture": "Architecture",
    "security": "Security",
    "performance": "Performance",
    "code-quality": "Code Quality",
}
_CATEGORY_CLEAN_LABELS = {
    "Architecture": "SOLID compliant, no structural issues",
    "Security": "No vulnerabilities detected",
    "Performance": "No blocking issues",
    "Code Quality": "All patterns followed",
}


def _star_rating(total: int, high: int, medium: int) -> str:
    """Return a star rating string (1–5) based on finding severity counts."""
    if total == 0:
        return "⭐⭐⭐⭐⭐ 5.0/5.0"
    score = 5.0 - (high * 1.5 + medium * 0.5)
    score = max(1.0, min(5.0, score))
    full = int(score)
    half = 1 if (score - full) >= 0.5 else 0
    empty = 5 - full - half
    stars = "⭐" * full + ("✨" if half else "") + "☆" * empty
    return f"{stars} {score:.1f}/5.0"


def _build_enhanced_summary(
    review_results: list,
    total_findings: dict[str, int],
    revision: int,
    last_updated_at: str,
) -> str:
    """Build the rich REVUE-97 summary comment body (AC1–AC7).

    Args:
        review_results:  List of ReviewResult objects from the pipeline.
        total_findings:  Dict of {severity: count} aggregated across all files.
        revision:        Current review revision number (1 = first post).
        last_updated_at: Human-readable relative timestamp string.
    """
    total = sum(total_findings.values())
    high = total_findings.get("high", 0)
    medium = total_findings.get("medium", 0)

    # AC1: verdict + star rating
    if total == 0:
        verdict_icon = "✅"
        verdict_text = "Approved"
    elif high > 0:
        verdict_icon = "❌"
        verdict_text = f"{total} issue{'s' if total != 1 else ''} found"
    else:
        verdict_icon = "⚠️"
        verdict_text = f"{total} issue{'s' if total != 1 else ''} found"

    stars = _star_rating(total, high, medium)

    lines = [
        f"## 🤖 Revue.io — Code Review (Review #{revision})",
        "",
        f"**Overall:** {stars} · {verdict_icon} {verdict_text}  ",
        f"**Last updated:** {last_updated_at}",
        "",
    ]

    # AC2: category breakdown — always show all 4
    category_counts: dict[str, list] = {label: [] for label in _CATEGORY_MAP.values()}
    for rr in review_results:
        if rr.error or not rr.response:
            continue
        try:
            findings, _ = _parse_findings(rr.response)
        except Exception:
            continue
        for f in findings:
            raw_cat = f.get("category", "").lower().strip()
            display = _CATEGORY_MAP.get(raw_cat)
            if display:
                category_counts[display].append(f)

    lines.append("### Quality Breakdown")
    for display_label in _CATEGORY_MAP.values():
        cat_findings = category_counts[display_label]
        if not cat_findings:
            lines.append(f"- ✅ **{display_label}:** {_CATEGORY_CLEAN_LABELS[display_label]}")
        else:
            by_sev: dict[str, int] = {}
            for f in cat_findings:
                s = f.get("severity", "low").lower()
                by_sev[s] = by_sev.get(s, 0) + 1
            sev_parts = " ".join(
                f"{SEVERITY_EMOJI.get(s, '⚪')} {by_sev[s]} {s}"
                for s in SEVERITY_ORDER
                if by_sev.get(s, 0) > 0
            )
            lines.append(f"- ⚠️ **{display_label}:** {sev_parts}")
    lines.append("")

    # AC3: files reviewed
    reviewed_files = [rr for rr in review_results if not rr.error and rr.response]
    lines.append(f"### Files Reviewed ({len(reviewed_files)})")
    for rr in reviewed_files:
        lines.append(f"- `{rr.file_path}`")
    lines.append("")

    # AC1 / AC4: findings summary
    if total == 0:
        lines.append("### Findings: 0 issues")
        lines.append("")
        lines.append(
            "**Verdict:** Clean implementation following project standards. "
            "No issues detected across all reviewed files."
        )
    else:
        counts_str = " · ".join(
            f"{SEVERITY_EMOJI.get(s, '⚪')} {total_findings[s]} {s}"
            for s in SEVERITY_ORDER
            if total_findings.get(s, 0) > 0
        )
        lines.append(f"### Findings: {total} issue{'s' if total != 1 else ''}")
        lines.append(f"{counts_str}")
        lines.append("")
        lines.append(
            f"**Verdict:** {verdict_icon} {total} issue{'s' if total != 1 else ''} require "
            f"attention. See inline comments for details."
        )

    return "\n".join(lines)


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
        lines.append(f"*{cat.replace('-', ' ').title()}*  ")
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


def _run_per_issue_dedup(
    adapter,
    pr_num: int,
    platform_str: str,
    review_results: list,
    diff_by_file: dict,
    dedup_store,
) -> tuple[int, int, dict[str, int]]:
    """Core per-issue dedup loop shared across all platform posting functions.

    Nova's consolidated list is the authoritative set of findings — every item
    in it is posted inline (WYSIWYG).  Cross-cycle dedup (AC1/AC2) prevents
    re-posting a finding that already has an open comment from a prior review.

    Returns ``(posted, skipped, total_findings)`` where ``total_findings`` is
    the severity breakdown of Nova's full list — the number the summary shows.
    """
    from revue.comments.fingerprint import fingerprint as gen_fingerprint
    from revue.comments.models import CommentState
    from revue.core.vcs_adapter import DiffPosition

    prior_unresolved = dedup_store.get_unresolved_fingerprints(platform_str, pr_num)
    posted = 0
    skipped = 0
    total_findings: dict[str, int] = {"high": 0, "medium": 0, "low": 0, "info": 0}
    # Hunk fps seen this cycle — used by AC5 to detect fixed findings.
    seen_hunk_fps: set[str] = set()

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
            emoji = SEVERITY_EMOJI.get(sev, "⚪")

            if not issue and not details:
                continue

            # Count every finding in Nova's list (WYSIWYG — summary reflects this).
            if sev in total_findings:
                total_findings[sev] += 1

            fp = gen_fingerprint(rr.file_path, line, diff_content)
            seen_hunk_fps.add(fp)

            # AC1/AC2: skip if already posted in a previous review cycle.
            if dedup_store.has_fingerprint(platform_str, pr_num, rr.file_path, fp):
                skipped += 1
                continue

            body_parts = [f"**{emoji} [{sev.upper()}] {issue}**"]
            if cat:
                body_parts.append(f"*{cat.replace('-', ' ').title()}*")
            if details:
                body_parts.append(f"\n{details}")
            if rec:
                body_parts.append(f"\n> 💡 **Recommendation:** {rec}")
            body = "\n".join(body_parts)

            position = DiffPosition(file_path=rr.file_path, line_number=line, side="RIGHT")
            comment_id = adapter.post_review_comment(pr_id=pr_num, position=position, body=body)

            if comment_id is not None:
                posted += 1
                dedup_store.save_finding(
                    platform=platform_str,
                    pr_number=pr_num,
                    file_path=rr.file_path,
                    fingerprint=fp,
                    platform_comment_id=comment_id,
                    line_number=line,
                    comment_body=body,
                )

    # AC5: auto-resolve findings absent from new review
    resolved_fps = set(prior_unresolved.keys()) - seen_hunk_fps

    for fp in resolved_fps:
        entry = prior_unresolved[fp]
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

    return posted, skipped, total_findings


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
) -> None:
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

    def _scan_for_existing_summary() -> Optional[str]:
        """Scan live platform comments for a Revue summary, return its comment ID."""
        try:
            comments = adapter.get_existing_comments(pr_id=pr_num)
            for c in comments:
                body = c.get("content", {}).get("raw", "") or c.get("body", "") or ""
                if _REVUE_SUMMARY_MARKER in body:
                    return str(c.get("id", ""))
        except Exception:
            pass
        return None

    def _post_or_update_summary(body: str) -> None:
        nonlocal _existing_summary, _revision
        now = datetime.now(timezone.utc)

        # Resolve the comment ID to update: prefer state file, fall back to
        # scanning live comments so re-reviews never post a duplicate summary
        # even when the local state file is stale or missing (e.g. ephemeral CI).
        existing_comment_id = (
            _existing_summary.platform_comment_id if _existing_summary
            else _scan_for_existing_summary()
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
                print("[revue] Existing summary comment not found — posting fresh comment")
                _revision = 1
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
        # Pre-count from Nova's consolidated list so summary is always accurate
        # whether it's posted before or after inline comments.
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

        summary_body = _build_enhanced_summary(
            review_results, total_findings, _revision, _last_updated
        )

        # GitLab shows comments newest-first: post inline first, summary last
        # (summary lands at the top).  Bitbucket/GitHub show oldest-first: post
        # summary first so it stays pinned at the top of the thread.
        gitlab_order = (platform_str == "gitlab")
        if not gitlab_order:
            _post_or_update_summary(summary_body)

        posted, skipped, _ = _run_per_issue_dedup(
            adapter, pr_num, platform_str, review_results, diff_by_file, dedup_store
        )

        if gitlab_order:
            _post_or_update_summary(summary_body)

        if skipped > 0:
            print(f"[revue] Review posted to {pr_label} #{pr_id} — {posted} new, {skipped} preserved inline comment(s)")
        else:
            print(f"[revue] Review posted to {pr_label} #{pr_id} — {posted} inline comment(s)")
    else:
        posted = 0
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
            review_results, total_findings, _revision, _last_updated
        )
        if file_sections:
            summary_body += "\n\n---\n\n" + "\n\n".join(file_sections)
        _post_or_update_summary(summary_body)
        print(f"[revue] Review posted to {pr_label} #{pr_id} — {posted} file(s) in summary comment")


def _post_to_bitbucket(args: argparse.Namespace, review_results: list, config=None) -> None:
    """Resolve Bitbucket credentials and delegate to _post_to_platform."""
    from revue.core.bitbucket_adapter import BitbucketAdapter
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

    adapter = BitbucketAdapter(
        api_token=bb_token, username=bb_username,
        workspace=workspace, repo_slug=repo_slug,
    )
    diff_by_file = _parse_diff_by_file(getattr(args, "diff", None), parse_diff_file)
    _post_to_platform(
        adapter=adapter, pr_id=pr_id,
        platform_str="bitbucket", platform_enum=Platform.BITBUCKET,
        repo_owner=workspace, repo_name=repo_slug,
        review_results=review_results, diff_by_file=diff_by_file,
        comment_style=comment_style, pr_label="PR",
    )


def _post_to_github(args: argparse.Namespace, review_results: list, config=None) -> None:
    """Resolve GitHub credentials and delegate to _post_to_platform."""
    from revue.core.github_adapter import GitHubAdapter
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
    adapter = GitHubAdapter(token=token, repo=repo)
    diff_by_file = _parse_diff_by_file(getattr(args, "diff", None), parse_diff_file)
    _post_to_platform(
        adapter=adapter, pr_id=pr_id,
        platform_str="github", platform_enum=Platform.GITHUB,
        repo_owner=repo_owner, repo_name=repo_name,
        review_results=review_results, diff_by_file=diff_by_file,
        comment_style=comment_style, pr_label="GitHub PR",
    )


def _post_to_gitlab(args: argparse.Namespace, review_results: list, config=None) -> None:
    """Resolve GitLab credentials and delegate to _post_to_platform."""
    from revue.core.gitlab_adapter import GitLabAdapter
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
    adapter = GitLabAdapter(token=token, project_id=project_id)
    diff_by_file = _parse_diff_by_file(getattr(args, "diff", None), parse_diff_file)
    _post_to_platform(
        adapter=adapter, pr_id=pr_id,
        platform_str="gitlab", platform_enum=Platform.GITLAB,
        repo_owner=repo_owner, repo_name=repo_name,
        review_results=review_results, diff_by_file=diff_by_file,
        comment_style=comment_style, pr_label="GitLab MR",
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
