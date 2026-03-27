#!/usr/bin/env python3
"""GitHub VCS adapter — implements VCSAdapter for the GitHub REST API.

Uses only ``urllib.request`` (stdlib) for HTTP calls.  Supports GitHub App
token authentication and webhook signature verification via HMAC-SHA256.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

from AIReviewer.core.models import FileChange
from AIReviewer.core.vcs_adapter import (
    DiffPosition,
    translate_github_position,
)

_GITHUB_API_VERSION = "2022-11-28"


class GitHubAdapter:
    """Implements VCSAdapter for GitHub using the GitHub REST API."""

    def __init__(
        self,
        token: str,
        repo: str,
        base_url: str = "https://api.github.com",
    ) -> None:
        self._token = token
        self._repo = repo
        self._base_url = base_url.rstrip("/")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        """Standard headers for every GitHub API request."""
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": _GITHUB_API_VERSION,
        }

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> Any:
        """Issue an HTTP request and return parsed JSON."""
        url = f"{self._base_url}{path}"
        data = json.dumps(body).encode() if body is not None else None
        headers = self._headers()
        if data is not None:
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise ValueError(
                    f"GitHub auth error {exc.code}: {exc.reason}"
                ) from exc
            if exc.code == 404:
                raise RuntimeError(
                    f"GitHub resource not found: {url}"
                ) from exc
            if exc.code >= 500:
                raise RuntimeError(
                    f"GitHub server error {exc.code}: {exc.reason}"
                ) from exc
            raise

    # ------------------------------------------------------------------
    # VCSAdapter interface
    # ------------------------------------------------------------------

    def get_diff(self, pr_id: int) -> list[FileChange]:
        """Fetch PR diff from GitHub Files API. Parse into FileChange objects.

        Binary files (no ``patch`` field) are skipped.
        """
        files: list[dict[str, Any]] = self._request(
            "GET", f"/repos/{self._repo}/pulls/{pr_id}/files"
        )
        changes: list[FileChange] = []
        for f in files:
            if "patch" not in f:
                # Binary or otherwise unpatchable file — skip
                continue
            status = f.get("status", "modified")
            change_type = {
                "added": "added",
                "removed": "deleted",
                "renamed": "modified",
            }.get(status, "modified")
            changes.append(
                FileChange(
                    file_path=f["filename"],
                    change_type=change_type,
                    additions=f.get("additions", 0),
                    deletions=f.get("deletions", 0),
                    diff=f["patch"],
                )
            )
        return changes

    def post_inline_comment(
        self, pr_id: int, position: DiffPosition, body: str
    ) -> bool:
        """Post an inline review comment via the GitHub Review API.

        Uses ``POST /repos/{owner}/{repo}/pulls/{pr_id}/reviews`` with
        ``event=COMMENT`` so the comment is published immediately without
        requiring a pending review.

        Returns True on success, False on any error (logs the exception).
        """
        payload: dict[str, Any] = {
            "event": "COMMENT",
            "comments": [
                {
                    "path": position.file_path,
                    "position": position.position,
                    "body": body,
                }
            ],
        }
        try:
            self._request(
                "POST",
                f"/repos/{self._repo}/pulls/{pr_id}/reviews",
                payload,
            )
            return True
        except Exception as exc:
            logger.error("post_inline_comment failed for PR %d: %s", pr_id, exc)
            return False

    def post_summary_comment(self, pr_id: int, body: str) -> bool:
        """Post a top-level PR comment (not a review comment).

        Uses ``POST /repos/{owner}/{repo}/issues/{pr_id}/comments``.

        Returns True on success, False on any error (logs the exception).
        """
        try:
            self._request(
                "POST",
                f"/repos/{self._repo}/issues/{pr_id}/comments",
                {"body": body},
            )
            return True
        except Exception as exc:
            logger.error("post_summary_comment failed for PR %d: %s", pr_id, exc)
            return False

    def get_existing_comments(self, pr_id: int) -> list[dict]:
        """Return existing review comments on the PR.

        Returns empty list on any error (logs the exception).
        """
        try:
            return self._request(
                "GET", f"/repos/{self._repo}/pulls/{pr_id}/comments"
            )
        except Exception as exc:
            logger.error("get_existing_comments failed for PR %d: %s", pr_id, exc)
            return []

    def resolve_position(
        self, file_path: str, line_number: int, diff: str
    ) -> DiffPosition:
        """Use translate_github_position() from vcs_adapter.py."""
        return translate_github_position(file_path, line_number, diff)

    # ------------------------------------------------------------------
    # Webhook helpers (static)
    # ------------------------------------------------------------------

    @staticmethod
    def verify_webhook_signature(
        payload: bytes, signature: str, secret: str
    ) -> bool:
        """Verify HMAC-SHA256 webhook signature from GitHub.

        ``signature`` is expected in the form ``sha256=<hex>``.
        Uses ``hmac.compare_digest`` for timing-safe comparison.
        """
        if not signature.startswith("sha256="):
            return False
        expected = hmac.new(
            secret.encode(), payload, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(f"sha256={expected}", signature)

    @staticmethod
    def parse_webhook_event(
        headers: dict[str, str], payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Parse a GitHub webhook event.

        Returns dict with ``{event_type, pr_id, repo, action, installation_id}``
        for pull_request events with action in (opened, synchronize, reopened).
        Returns ``None`` for all other events.
        """
        event_type = headers.get("X-GitHub-Event", "")
        if event_type != "pull_request":
            return None

        action = payload.get("action", "")
        if action not in ("opened", "synchronize", "reopened"):
            return None

        pr = payload.get("pull_request")
        if not pr:
            return None

        repo_data = payload.get("repository", {})
        return {
            "event_type": "pull_request",
            "pr_id": pr.get("number"),
            "repo": repo_data.get("full_name", ""),
            "action": action,
            "installation_id": payload.get("installation", {}).get("id"),
        }
