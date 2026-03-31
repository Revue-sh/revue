#!/usr/bin/env python3
"""Revue CLI — local diff review, config init, and validation.

Entry point registered as ``revue`` in pyproject.toml.
"""

from __future__ import annotations

import argparse
import json
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

    # 8. Fetch PR description for smart context filtering (REVUE-84)
    pr_description = None
    auto_detect = getattr(args, "auto_detect_pr", False)
    explicit_pr_id = getattr(args, "pr_id", None)
    explicit_platform = getattr(args, "platform", None)

    if auto_detect or explicit_pr_id:
        from revue.core.pr_description_adapter import (
            get_pr_description_from_env,
            get_bitbucket_pr_description,
        )
        import os

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
    print(f"[revue] Validating license...")
    try:
        review_results, excluded = pipeline.run(str(diff_path), pr_description=pr_description)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    total = len(review_results) + len(excluded)
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
        _post_to_bitbucket(args, review_results)

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
            print(f"## {r['file']}\n")
            print(r["review"])
            print()

    return 0


SEVERITY_EMOJI = {"high": "🔴", "medium": "🟡", "low": "🔵", "info": "ℹ️"}
SEVERITY_ORDER = ["high", "medium", "low", "info"]


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
    line = int(f.get("line") or f.get("lines") or 1) if f.get("line") or f.get("lines") else 1
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


def _post_to_bitbucket(args: argparse.Namespace, review_results: list) -> None:
    """Post review findings to a Bitbucket PR.

    Supports two comment styles:
    - 'per-issue' (default): one inline comment per finding, anchored to the file.
      Allows developers to reply, create tasks, and apply fixes per issue.
    - 'summary': one comment per file with all findings grouped together.
    """

    from revue.core.bitbucket_adapter import BitbucketAdapter
    from revue.core.vcs_adapter import DiffPosition

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
        api_token=bb_token,
        username=bb_username,
        workspace=workspace,
        repo_slug=repo_slug,
    )

    # Aggregate totals for the summary header
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
        except Exception:
            pass

    total = sum(total_findings.values())
    verdict = "✅ Looks good!" if total == 0 else f"⚠️ {total} issue{'s' if total != 1 else ''} found"
    counts_str = " · ".join(
        f"{SEVERITY_EMOJI.get(s, '⚪')} {n} {s}" for s, n in total_findings.items() if n > 0
    )

    if comment_style == "per-issue":
        # Post one inline comment per finding, anchored to line 1 of the file
        # (line numbers from AI findings would improve this in a future story)
        posted = 0
        for rr in review_results:
            if rr.error or not rr.response:
                continue
            try:
                findings, file_summary = _parse_findings(rr.response)
            except Exception:
                continue

            for f in findings:
                sev, issue, details, rec, cat, line = _extract_finding_fields(f)
                emoji = SEVERITY_EMOJI.get(sev, "⚪")

                # Skip findings with no meaningful content
                if not issue and not details:
                    continue

                body_parts = [f"**{emoji} [{sev.upper()}] {issue}**"]
                if cat:
                    body_parts.append(f"*{cat.replace('-', ' ').title()}*")
                if details:
                    body_parts.append(f"\n{details}")
                if rec:
                    body_parts.append(f"\n> 💡 **Recommendation:** {rec}")
                body = "\n".join(body_parts)

                position = DiffPosition(
                    file_path=rr.file_path,
                    line_number=line,
                    side="RIGHT",
                )
                ok = adapter.post_review_comment(pr_id=int(pr_id), position=position, body=body)
                if ok:
                    posted += 1

        # Post a single summary comment at the top with the overall verdict
        header = f"## 🤖 Revue.io — Code Review\n\n> **{verdict}**"
        if counts_str:
            header += f" · {counts_str}"
        header += f" · {len(review_results)} file{'s' if len(review_results) != 1 else ''} reviewed"
        adapter.post_summary_comment(pr_id=int(pr_id), body=header)
        print(f"[revue] Review posted to PR #{pr_id} — {posted} inline comment(s)")

    else:
        # summary mode: one formatted comment per file
        header = f"## 🤖 Revue.io — Code Review\n\n> **{verdict}**"
        if counts_str:
            header += f" · {counts_str}"
        header += f" · {len(review_results)} file{'s' if len(review_results) != 1 else ''} reviewed\n\n---\n"
        summary_lines = [header]

        posted = 0
        for rr in review_results:
            if rr.error or not rr.response:
                continue
            summary_lines.append(_format_file_review(rr.file_path, rr.response))
            posted += 1

        if posted == 0:
            summary_lines.append("*No findings — looks good! ✅*")

        ok = adapter.post_summary_comment(pr_id=int(pr_id), body="\n".join(summary_lines))
        if ok:
            print(f"[revue] Review posted to PR #{pr_id} — {posted} file(s) in summary comment")
        else:
            print("Warning: Failed to post review summary to Bitbucket", file=sys.stderr)


def _resolve_pr_id_from_env() -> Optional[int]:
    """Resolve PR/MR ID from common CI environment variables.

    Checks in order:
    - Bitbucket: BITBUCKET_PR_ID
    - GitHub: GITHUB_PR_NUMBER (set by actions/checkout or workflow context)
    - GitLab: CI_MERGE_REQUEST_IID

    Returns None if no PR ID found or value is not numeric.
    """
    import os
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
