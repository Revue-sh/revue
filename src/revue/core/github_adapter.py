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

from revue.core.models import FileChange, CodeFix
from revue.core.vcs_adapter import (
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
        webhook_secret: str = "",
    ) -> None:
        self._token = token
        self._repo = repo
        self._base_url = base_url.rstrip("/")
        self._webhook_secret = webhook_secret

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
        """Fetch PR diff from GitHub Files API with pagination.

        GitHub paginates /pulls/{pr_id}/files at 30 files per page by default
        (max 100 per page). This method fetches all pages.
        Binary files (no ``patch`` field) are skipped.
        """
        changes: list[FileChange] = []
        page = 1
        per_page = 100
        while True:
            files: list[dict[str, Any]] = self._request(
                "GET",
                f"/repos/{self._repo}/pulls/{pr_id}/files"
                f"?per_page={per_page}&page={page}",
            )
            if not files:
                break
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
            if len(files) < per_page:
                # Last page reached
                break
            page += 1
        return changes

    def post_review_comment(
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
            logger.error("post_review_comment failed for PR %d: %s", pr_id, exc)
            return False

    # Backward-compat alias — remove in v2.0
    post_inline_comment = post_review_comment

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

    def post_suggested_change(
        self, pr_id: int, position: DiffPosition, code_fix: CodeFix
    ) -> bool:
        """Post a Sage-generated fix as a GitHub Suggested Change.

        GitHub's Suggested Change format uses special markdown syntax in review
        comments:

            ```suggestion
            fixed line 1
            fixed line 2
            ```

        For multi-line suggestions, GitHub requires the position to point to the
        first line of the range, and the suggestion block contains all fixed lines.

        Args:
            pr_id: Pull request ID
            position: DiffPosition for the first line of the fix
            code_fix: CodeFix with original_lines, fixed_lines, explanation

        Returns:
            True on success, False on error
        """
        # Build suggestion markdown
        suggestion_lines = "\n".join(code_fix.fixed_lines)
        body = f"""{code_fix.explanation}

```suggestion
{suggestion_lines}
```

*🤖 Sage suggestion (confidence: {code_fix.confidence:.0f}%)*
"""

        # Use Review API with suggestion comment
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
            logger.error(
                "post_suggested_change failed for PR %d: %s", pr_id, exc
            )
            return False

    # ------------------------------------------------------------------
    # Webhook helpers (static)
    # ------------------------------------------------------------------

    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        """Verify HMAC-SHA256 webhook signature (VCSAdapter protocol compliance).

        ``signature`` is expected in the form ``sha256=<hex>``
        (``X-Hub-Signature-256`` header from GitHub).
        Uses ``hmac.compare_digest`` for timing-safe comparison.

        Args:
            payload:   Raw request body bytes.
            signature: Value of the ``X-Hub-Signature-256`` header.

        Returns:
            True if the signature is valid.
        """
        return self._verify_webhook_signature_with_secret(payload, signature, self._webhook_secret)

    @staticmethod
    def _verify_webhook_signature_with_secret(
        payload: bytes, signature: str, secret: str
    ) -> bool:
        """Low-level HMAC verification — accepts explicit secret for testing."""
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
