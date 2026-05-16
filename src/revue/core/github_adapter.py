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

from revue.core.logging_channels import Log, log_comment_posted

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
        self, pr_id: int, position: DiffPosition, body: str, replacement_line_count: int = 1
    ) -> str | None:
        """Post an inline review comment via the GitHub Review API.

        Uses ``POST /repos/{owner}/{repo}/pulls/{pr_id}/reviews`` with
        ``event=COMMENT`` so the comment is published immediately without
        requiring a pending review.

        For multi-line suggestions (replacement_line_count > 1), uses start_line/line/side
        instead of position so the comment spans the full replacement range.

        Returns:
            The review comment ID as a string on success, None on failure.
            GitHub returns the ID in the first comment of the ``comments`` array.
        """
        if replacement_line_count > 1:
            comment: dict[str, Any] = {
                "path": position.file_path,
                "start_line": position.line_number,
                "start_side": "RIGHT",
                "line": position.line_number + replacement_line_count - 1,
                "side": "RIGHT",
                "body": body,
            }
        else:
            comment = {
                "path": position.file_path,
                "line": position.line_number,
                "side": "RIGHT",
                "body": body,
            }
        payload: dict[str, Any] = {
            "event": "COMMENT",
            "comments": [comment],
        }
        try:
            resp = self._request(
                "POST",
                f"/repos/{self._repo}/pulls/{pr_id}/reviews",
                payload,
            )
            # GitHub returns the review object; comments are nested
            comments = resp.get("comments", []) if isinstance(resp, dict) else []
            comment_id = comments[0].get("id") if comments else resp.get("id")
            return str(comment_id) if comment_id is not None else None
        except Exception as exc:
            Log.cli.error("post_review_comment failed for PR %d: %s", pr_id, exc)
            return None

    def post_review_comment_with_params(
        self, pr_id: int, api_params: dict, body: str, replacement_line_count: int = 1
    ) -> str | None:
        """Post an inline review comment using pre-built params from GitHubPositionAdapter.to_api_params()."""
        comment = {**api_params, "body": body}
        payload: dict[str, Any] = {"event": "COMMENT", "comments": [comment]}
        try:
            resp = self._request(
                "POST",
                f"/repos/{self._repo}/pulls/{pr_id}/reviews",
                payload,
            )
            comments = resp.get("comments", []) if isinstance(resp, dict) else []
            comment_id = comments[0].get("id") if comments else resp.get("id")
            log_comment_posted(
                platform="github", pr_id=pr_id, comment_id=str(comment_id) if comment_id is not None else None,
                api_params=api_params,
            )
            return str(comment_id) if comment_id is not None else None
        except Exception as exc:
            Log.cli.error("post_review_comment_with_params failed for PR %d: %s", pr_id, exc)
            return None

    # Backward-compat alias — remove in v2.0
    post_inline_comment = post_review_comment

    def post_summary_comment(self, pr_id: int, body: str) -> str | None:
        """Post a top-level PR comment (not a review comment).

        Uses ``POST /repos/{owner}/{repo}/issues/{pr_id}/comments``.

        Returns the comment ID as a string on success, None on error.
        """
        try:
            resp = self._request(
                "POST",
                f"/repos/{self._repo}/issues/{pr_id}/comments",
                {"body": body},
            )
            comment_id = resp.get("id")
            return str(comment_id) if comment_id is not None else None
        except Exception as exc:
            Log.cli.error("post_summary_comment failed for PR %d: %s", pr_id, exc)
            return None

    def update_comment(self, pr_id: int, comment_id: str, body: str) -> bool:
        """Update an existing issue comment in-place.

        PATCH /repos/{owner}/{repo}/issues/comments/{comment_id}

        Returns True on success, False on error.
        """
        try:
            self._request(
                "PATCH",
                f"/repos/{self._repo}/issues/comments/{comment_id}",
                {"body": body},
            )
            return True
        except Exception as exc:
            Log.cli.error("update_comment failed for PR %d comment %s: %s", pr_id, comment_id, exc)
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
            Log.cli.error("get_existing_comments failed for PR %d: %s", pr_id, exc)
            return []

    def get_thread_replies(self, pr_id: int, comment_id: str) -> list[dict]:
        """Fetch replies to a review comment thread.

        GET /repos/{owner}/{repo}/pulls/{pr_id}/comments/{comment_id}/replies

        Returns a list of dicts with keys: id, body, created_at.
        Returns [] on any error (never raises).
        """
        try:
            replies = self._request(
                "GET",
                f"/repos/{self._repo}/pulls/{pr_id}/comments/{comment_id}/replies",
            )
            return [
                {
                    "id": str(c["id"]),
                    "body": c.get("body", ""),
                    "created_at": c.get("created_at", ""),
                }
                for c in replies
            ]
        except Exception as exc:
            Log.cli.error(
                "get_thread_replies failed for PR %d comment %s: %s",
                pr_id, comment_id, exc,
            )
            return []

    def reply_to_comment(self, pr_id: int, comment_id: str, body: str) -> str | None:
        """Post a reply to a review comment thread.

        POST /repos/{owner}/{repo}/pulls/{pr_id}/comments/{comment_id}/replies

        Returns the reply ID on success, None on failure.
        """
        try:
            result = self._request(
                "POST",
                f"/repos/{self._repo}/pulls/{pr_id}/comments/{comment_id}/replies",
                {"body": body},
            )
            return str(result.get("id", ""))
        except Exception as exc:
            Log.cli.error(
                "reply_to_comment failed for PR %d comment %s: %s",
                pr_id, comment_id, exc,
            )
            return None

    def get_issue_comments(self, pr_id: int) -> list[dict]:
        """Return issue-level PR comments (e.g. the Revue summary comment).

        These live at /issues/{pr_id}/comments, separate from inline review
        comments returned by get_existing_comments().
        Returns empty list on any error.
        """
        try:
            return self._request(
                "GET", f"/repos/{self._repo}/issues/{pr_id}/comments"
            )
        except Exception as exc:
            Log.cli.error("get_issue_comments failed for PR %d: %s", pr_id, exc)
            return []

    def _graphql(self, query: str, variables: dict) -> dict:
        """Execute a GraphQL query/mutation against the GitHub API."""
        data = json.dumps({"query": query, "variables": variables}).encode()
        headers = {**self._headers(), "Content-Type": "application/json"}
        req = urllib.request.Request(
            "https://api.github.com/graphql",
            data=data,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req) as resp:
                result = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode()
            raise RuntimeError(f"GraphQL HTTP error {exc.code}: {exc.reason} — {body}") from exc
        if "errors" in result:
            raise RuntimeError(f"GraphQL error: {result['errors']}")
        return result

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
            Log.cli.error(
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

    def resolve_inline_comment(
        self, pr_id: int, comment_id: str, reply_body: str
    ) -> bool:
        """Resolve a GitHub review comment thread.

        Posts an optional reply then resolves the thread via the GraphQL
        resolveReviewThread mutation (the REST API has no resolution endpoint).

        Args:
            pr_id:       Pull request number.
            comment_id:  The GitHub review comment ID (numeric string).
            reply_body:  Optional reply message to post before resolving.

        Returns:
            True on success, False on error.
        """
        # Post reply if provided
        if reply_body:
            try:
                self._request(
                    "POST",
                    f"/repos/{self._repo}/pulls/{pr_id}/comments/{comment_id}/replies",
                    {"body": reply_body},
                )
            except Exception as exc:
                Log.cli.warning(
                    "resolve_inline_comment: reply failed for PR %d comment %s: %s",
                    pr_id, comment_id, exc,
                )
                return False  # sentinel not written — caller retries on next run

        # Resolve the thread via GraphQL (REST PATCH /resolved is not a GitHub API)
        try:
            owner, name = self._repo.split("/", 1)
            threads_query = """
            query($owner: String!, $name: String!, $pr: Int!) {
              repository(owner: $owner, name: $name) {
                pullRequest(number: $pr) {
                  reviewThreads(first: 100) {
                    nodes {
                      id
                      comments(first: 1) {
                        nodes { databaseId }
                      }
                    }
                  }
                }
              }
            }
            """
            data = self._graphql(threads_query, {"owner": owner, "name": name, "pr": pr_id})
            review_threads = (
                data.get("data", {}).get("repository", {})
                .get("pullRequest", {}).get("reviewThreads", {}).get("nodes", [])
            )
            thread_id = None
            for t in review_threads:
                first_comment = t.get("comments", {}).get("nodes", [{}])[0]
                db_id = first_comment.get("databaseId")
                if db_id and str(db_id) == str(comment_id):
                    thread_id = t["id"]
                    break

            if not thread_id:
                Log.cli.warning(
                    "resolve_inline_comment: no thread found for comment %s on PR %d",
                    comment_id, pr_id,
                )
                return False

            resolve_mutation = """
            mutation($threadId: ID!) {
              resolveReviewThread(input: {threadId: $threadId}) {
                thread { isResolved }
              }
            }
            """
            result = self._graphql(resolve_mutation, {"threadId": thread_id})
            resolved = (
                result.get("data", {}).get("resolveReviewThread", {})
                .get("thread", {}).get("isResolved", False)
            )
            if resolved:
                Log.cli.info("Resolved comment thread %s on PR %d", comment_id, pr_id)
            return resolved
        except Exception as exc:
            Log.cli.warning(
                "resolve_inline_comment failed for PR %d comment %s: %s",
                pr_id, comment_id, exc,
            )
            return False

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
