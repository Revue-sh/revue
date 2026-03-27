#!/usr/bin/env python3
"""GitHub VCS adapter — implements VCSAdapter for the GitHub REST API.

Uses only ``urllib.request`` (stdlib) for HTTP calls.  Supports GitHub App
token authentication and webhook signature verification via HMAC-SHA256.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import urllib.error
import urllib.request
from typing import Any

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
        """Fetch PR diff from GitHub API. Parse into FileChange objects."""
        files: list[dict[str, Any]] = self._request(
            "GET", f"/repos/{self._repo}/pulls/{pr_id}/files"
        )
        changes: list[FileChange] = []
        for f in files:
            patch = f.get("patch", "")
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
                    diff=patch,
                )
            )
        return changes

    def post_inline_comment(
        self, pr_id: int, position: DiffPosition, body: str
    ) -> bool:
        """Post a review comment at the given diff position."""
        payload: dict[str, Any] = {
            "body": body,
            "path": position.file_path,
            "position": position.position,
        }
        if position.commit_id:
            payload["commit_id"] = position.commit_id
        self._request(
            "POST",
            f"/repos/{self._repo}/pulls/{pr_id}/comments",
            payload,
        )
        return True

    def post_summary_comment(self, pr_id: int, body: str) -> bool:
        """Post a top-level PR comment with the full review summary."""
        self._request(
            "POST",
            f"/repos/{self._repo}/issues/{pr_id}/comments",
            {"body": body},
        )
        return True

    def get_existing_comments(self, pr_id: int) -> list[dict]:
        """Return existing review comments on the PR."""
        return self._request(
            "GET", f"/repos/{self._repo}/pulls/{pr_id}/comments"
        )

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
