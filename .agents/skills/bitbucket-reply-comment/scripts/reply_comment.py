#!/usr/bin/env python3
"""Post a reply to a Bitbucket PR comment thread.

Usage:
    echo "Reply text" | python reply_comment.py <workspace/repo> <pr_number> <parent_id>
    python reply_comment.py <workspace/repo> <pr_number> <parent_id> --body "Reply text"
    python reply_comment.py <workspace/repo> <pr_number> <parent_id> --body-file /tmp/reply.txt

    # Multiple replies from a JSON file:
    python reply_comment.py <workspace/repo> <pr_number> --batch /tmp/replies.json
    JSON format: [{"parent_id": 123, "body": "text"}, ...]

Environment:
    BITBUCKET_API_TOKEN   Personal API token (Basic auth with email below)

Examples:
    echo "Acknowledged." | python reply_comment.py cbscd/revue 43 780891903
    python reply_comment.py cbscd/revue 43 780891903 --body "10s is intentional."
    python reply_comment.py cbscd/revue 43 --batch /tmp/replies.json
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

EMAIL = "dsanchezcisneros@gmail.com"
BASE_URL = "https://api.bitbucket.org/2.0"


def _creds() -> str:
    token = os.environ.get("BITBUCKET_API_TOKEN", "")
    if not token:
        sys.exit("BITBUCKET_API_TOKEN not set — run: source ~/.zshenv")
    return base64.b64encode(f"{EMAIL}:{token}".encode()).decode()


def post_reply(workspace: str, repo: str, pr_id: int, parent_id: int, body: str) -> int:
    """Post a reply and return the new comment ID."""
    url = f"{BASE_URL}/repositories/{workspace}/{repo}/pullrequests/{pr_id}/comments"
    payload = json.dumps({"content": {"raw": body}, "parent": {"id": parent_id}}).encode()
    creds = _creds()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Authorization": f"Basic {creds}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        result = json.load(resp)
        return result["id"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Reply to a Bitbucket PR comment thread")
    parser.add_argument("repo", help="workspace/repo  e.g. cbscd/revue")
    parser.add_argument("pr", type=int, help="Pull request number")
    parser.add_argument(
        "parent_id",
        type=int,
        nargs="?",
        help="Parent comment ID (root of the thread to reply to)",
    )
    body_group = parser.add_mutually_exclusive_group()
    body_group.add_argument("--body", help="Reply body as a string argument")
    body_group.add_argument("--body-file", help="Path to a file containing the reply body")
    body_group.add_argument(
        "--batch",
        help="Path to a JSON file: [{parent_id, body}, ...]  for posting multiple replies",
    )
    args = parser.parse_args()

    workspace, repo = args.repo.split("/", 1)

    if args.batch:
        # Batch mode — post multiple replies from a JSON file
        with open(args.batch) as f:
            items = json.load(f)
        ok = fail = 0
        for item in items:
            pid = item["parent_id"]
            body = item["body"]
            try:
                new_id = post_reply(workspace, repo, args.pr, pid, body)
                print(f"OK  thread {pid} → reply {new_id}")
                ok += 1
            except urllib.error.HTTPError as e:
                print(f"ERR thread {pid}: {e}")
                fail += 1
            time.sleep(0.3)
        print(f"\n{ok} posted, {fail} failed")
        return

    # Single reply mode
    if args.parent_id is None:
        sys.exit("parent_id is required for single-reply mode (or use --batch)")

    if args.body:
        body = args.body
    elif args.body_file:
        with open(args.body_file) as f:
            body = f.read()
    else:
        # Read from stdin
        body = sys.stdin.read()

    body = body.strip()
    if not body:
        sys.exit("Reply body is empty — nothing posted")

    try:
        new_id = post_reply(workspace, repo, args.pr, args.parent_id, body)
        print(f"Posted reply {new_id} to thread {args.parent_id}")
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        sys.exit(f"Failed to post reply: {e} — {error_body}")


if __name__ == "__main__":
    main()
