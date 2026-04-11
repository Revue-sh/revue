#!/usr/bin/env python3
"""Delete comments from a Bitbucket PR.

Usage:
    python delete_comments.py <workspace/repo> <pr_number> [options]

Options:
    --mode blank          Delete blank/already-deleted comments (root + replies)
    --mode no-replies     Delete Revue finding roots that have no developer replies
    --mode all-safe       Both of the above (default)
    --ids 123,456,...     Delete specific comment IDs regardless of mode
    --dry-run             Show what would be deleted without deleting anything

Environment:
    BITBUCKET_API_TOKEN   Personal API token (Basic auth with email below)

Examples:
    python delete_comments.py cbscd/revue 43
    python delete_comments.py cbscd/revue 43 --mode blank --dry-run
    python delete_comments.py cbscd/revue 43 --ids 780830216,780830247
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

EMAIL = "dsanchezcisneros@gmail.com"
BASE_URL = "https://api.bitbucket.org/2.0"
FINDING_RE = re.compile(r"^\*\*(?:🔴|🟡|🔵|ℹ️)\s*\[(?:HIGH|MEDIUM|LOW|INFO)\]")


def _creds() -> str:
    token = os.environ.get("BITBUCKET_API_TOKEN", "")
    if not token:
        sys.exit("BITBUCKET_API_TOKEN not set — run: source ~/.zshenv")
    return base64.b64encode(f"{EMAIL}:{token}".encode()).decode()


def _fetch_all_comments(workspace: str, repo: str, pr_id: int) -> list[dict]:
    creds = _creds()
    comments: list[dict] = []
    url: str | None = (
        f"{BASE_URL}/repositories/{workspace}/{repo}/pullrequests/{pr_id}/comments?pagelen=100"
    )
    while url:
        req = urllib.request.Request(url, headers={"Authorization": f"Basic {creds}"})
        with urllib.request.urlopen(req) as resp:
            data = json.load(resp)
        comments.extend(data.get("values", []))
        url = data.get("next")
    return comments


def _build_delete_set(
    comments: list[dict],
    mode: str,
    extra_ids: set[int],
) -> set[int]:
    to_delete: set[int] = set(extra_ids)

    if mode in ("blank", "all-safe"):
        for c in comments:
            blank = c.get("deleted") or not (c.get("content", {}).get("raw", "") or "").strip()
            if blank:
                to_delete.add(c["id"])

    if mode in ("no-replies", "all-safe"):
        reply_parent_ids = {c["parent"]["id"] for c in comments if "parent" in c}
        for c in comments:
            if "parent" in c:
                continue
            body = c.get("content", {}).get("raw", "") or ""
            if FINDING_RE.match(body) and c["id"] not in reply_parent_ids:
                to_delete.add(c["id"])

    return to_delete


def _delete(workspace: str, repo: str, pr_id: int, comment_id: int, creds: str) -> bool:
    url = f"{BASE_URL}/repositories/{workspace}/{repo}/pullrequests/{pr_id}/comments/{comment_id}"
    req = urllib.request.Request(url, method="DELETE", headers={"Authorization": f"Basic {creds}"})
    try:
        with urllib.request.urlopen(req):
            return True
    except urllib.error.HTTPError:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete Bitbucket PR comments")
    parser.add_argument("repo", help="workspace/repo  e.g. cbscd/revue")
    parser.add_argument("pr", type=int, help="Pull request number")
    parser.add_argument(
        "--mode",
        choices=["blank", "no-replies", "all-safe"],
        default="all-safe",
        help="Deletion mode (default: all-safe)",
    )
    parser.add_argument(
        "--ids",
        default="",
        help="Comma-separated comment IDs to delete unconditionally",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview without deleting")
    args = parser.parse_args()

    workspace, repo = args.repo.split("/", 1)
    extra_ids = {int(i.strip()) for i in args.ids.split(",") if i.strip()}

    print(f"Fetching comments for {workspace}/{repo} PR #{args.pr}…")
    comments = _fetch_all_comments(workspace, repo, args.pr)
    print(f"  Total comments fetched: {len(comments)}")

    to_delete = _build_delete_set(comments, args.mode, extra_ids)
    print(f"  Comments to delete: {len(to_delete)}")
    print(f"  Estimated count after: ~{len(comments) - len(to_delete)}")

    if args.dry_run:
        print("\nDry run — nothing deleted. IDs that would be removed:")
        for cid in sorted(to_delete):
            print(f"  {cid}")
        return

    creds = _creds()
    ok = fail = 0
    for cid in to_delete:
        if _delete(workspace, repo, args.pr, cid, creds):
            ok += 1
        else:
            fail += 1
        time.sleep(0.05)

    print(f"\nDeleted {ok} ok, {fail} failed")
    remaining = len(comments) - ok
    print(f"PR comment count: {len(comments)} → ~{remaining}")
    if remaining > 190:
        print(f"WARNING: still at {remaining} — approaching 200-comment limit. Run again or use --ids.")


if __name__ == "__main__":
    main()
