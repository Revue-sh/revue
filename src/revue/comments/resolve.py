#!/usr/bin/env python3
"""resolve.py — CLI entry point for auto-resolving PR comments.

Usage (typically called from scripts/run-comparison.sh):
    PYTHONPATH=src python3 src/revue/comments/resolve.py \
        --repo-path /path/to/repo --ticket REVUE-98

Exit 0 always — resolution failure must not break CI.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional

from revue.comments.models import Platform
from revue.comments.service import CommentResolutionService


def _detect_pr_context() -> tuple[
    Optional[Platform], Optional[str], Optional[str], Optional[int]
]:
    """Detect platform, repo_owner, repo_name, pr_number from CI env vars."""
    # Bitbucket
    pr_id = os.environ.get("BITBUCKET_PR_ID")
    if pr_id:
        return (
            Platform.BITBUCKET,
            os.environ.get("BITBUCKET_REPO_OWNER", ""),
            os.environ.get("BITBUCKET_REPO_SLUG", ""),
            int(pr_id),
        )

    # GitHub
    pr_number = os.environ.get("GITHUB_PR_NUMBER")
    if pr_number:
        repo = os.environ.get("GITHUB_REPOSITORY", "/")
        owner, name = repo.split("/", 1)
        return (Platform.GITHUB, owner, name, int(pr_number))

    # GitLab
    mr_iid = os.environ.get("GITLAB_MR_IID")
    if mr_iid:
        project = os.environ.get("GITLAB_PROJECT_PATH", "/")
        owner, name = project.split("/", 1)
        return (Platform.GITLAB, owner, name, int(mr_iid))

    return (None, None, None, None)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Auto-resolve PR comments for fixed findings"
    )
    parser.add_argument(
        "--repo-path",
        required=True,
        help="Path to the repository root",
    )
    parser.add_argument(
        "--ticket",
        required=True,
        help="Ticket identifier (e.g. REVUE-98)",
    )
    parser.add_argument(
        "--new-findings-json",
        default=None,
        help="Path to JSON file with new findings list for fingerprint comparison",
    )
    parser.add_argument(
        "--commit-sha",
        default="",
        help="Commit SHA for auto-resolve messages",
    )
    parser.add_argument(
        "--commit-author",
        default="",
        help="Commit author for auto-resolve messages",
    )
    args = parser.parse_args(argv)

    platform, repo_owner, repo_name, pr_number = _detect_pr_context()
    if not platform or not repo_owner or not repo_name or not pr_number:
        print("No PR context detected, skipping comment resolution.")
        return

    try:
        service = CommentResolutionService(args.repo_path)

        if args.new_findings_json:
            with open(args.new_findings_json) as fh:
                new_findings = json.load(fh)
            summary = service.process_new_review(
                platform, repo_owner, repo_name, pr_number,
                new_findings,
                commit_sha=args.commit_sha,
                commit_author=args.commit_author,
            )
        else:
            summary = service.process_pr_scan(
                platform, repo_owner, repo_name, pr_number
            )

        resolved = summary.fixed_count
        dismissed = summary.discussed_count
        remaining = summary.remaining_count
        print(
            f"\u2705 {resolved} resolved, {dismissed} dismissed, "
            f"{remaining} remaining"
        )
    except Exception as exc:
        print(f"Warning: comment resolution failed: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
