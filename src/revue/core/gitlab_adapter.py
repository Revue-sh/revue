#!/usr/bin/env python3
"""GitLab VCS adapter — implements VCSAdapter for the GitLab REST API.

Uses only ``urllib.request`` (stdlib) for HTTP calls.  Supports both
OAuth2 (``Authorization: Bearer``) and Personal Access Token
(``PRIVATE-TOKEN``) authentication, selected via ``token_type``.
"""

from __future__ import annotations

import hmac
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

_LOG = logging.getLogger(__name__)

from revue.core.models import FileChange, CodeFix
from revue.core.vcs_adapter import (
    DiffPosition,
    extract_gitlab_version_shas,
    translate_gitlab_line_code,
)

_HUNK_RE = re.compile(r"^@@ -(\d+),?(\d*) \+(\d+),?(\d*) @@")


class GitLabAdapter:
    """Implements VCSAdapter for GitLab using the GitLab REST API."""

    def __init__(
        self,
        token: str,
        project_id: int | str,
        base_url: str = "https://gitlab.com",
        token_type: str = "oauth",
        webhook_secret: str = "",
    ) -> None:
        self._token = token
        self._project_id = project_id
        self._base_url = base_url.rstrip("/")
        self._token_type = token_type
        self._webhook_secret = webhook_secret

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        """Build auth headers depending on token type."""
        if self._token_type == "pat":
            return {"PRIVATE-TOKEN": self._token}
        return {"Authorization": f"Bearer {self._token}"}

    def _api_base(self) -> str:
        """Return the project-scoped API prefix."""
        encoded = urllib.parse.quote(str(self._project_id), safe="")
        return f"{self._base_url}/api/v4/projects/{encoded}"

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> Any:
        """Issue an HTTP request and return parsed JSON."""
        url = f"{self._api_base()}{path}"
        data = json.dumps(body).encode() if body is not None else None
        headers = self._headers()
        headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise ValueError(
                    f"GitLab auth error {exc.code}: {exc.reason}"
                ) from exc
            if exc.code == 404:
                raise RuntimeError(
                    f"GitLab resource not found: {url}"
                ) from exc
            if exc.code == 400:
                try:
                    body = exc.read().decode("utf-8", errors="replace")
                except Exception:
                    body = "(unreadable)"
                raise RuntimeError(
                    f"GitLab 400 Bad Request — {url}\n  Response: {body}"
                ) from exc
            if exc.code >= 500:
                raise RuntimeError(
                    f"GitLab server error {exc.code}: {exc.reason}"
                ) from exc
            raise

    # ------------------------------------------------------------------
    # VCSAdapter interface
    # ------------------------------------------------------------------

    def get_diff(self, pr_id: int) -> list[FileChange]:
        """Fetch MR diff from GitLab API (MR IID). Parse into FileChange objects.

        GET /projects/{project_id}/merge_requests/{iid}/changes
        Returns {changes: [{old_path, new_path, diff, new_file, deleted_file, renamed_file, ...}]}
        Each changes[].diff is a unified diff hunk fragment; additions/deletions are
        counted directly since the fragment is not a full git-diff header.
        """
        try:
            resp = self._request("GET", f"/merge_requests/{pr_id}/changes")
        except Exception as exc:
            _LOG.warning("get_diff failed for MR %s: %s", pr_id, exc)
            return []

        raw_changes: list[dict[str, Any]] = resp.get("changes", [])
        changes: list[FileChange] = []
        for c in raw_changes:
            diff_text = c.get("diff", "")
            additions, deletions = self._count_diff_lines(diff_text)

            if c.get("new_file"):
                change_type = "added"
            elif c.get("deleted_file"):
                change_type = "deleted"
            else:
                change_type = "modified"

            changes.append(
                FileChange(
                    file_path=c.get("new_path", c.get("old_path", "")),
                    change_type=change_type,
                    additions=additions,
                    deletions=deletions,
                    diff=diff_text,
                )
            )
        return changes

    def _get_mr_version_shas(self, pr_id: int) -> tuple[str, str, str]:
        """Return (base_commit_sha, start_commit_sha, head_commit_sha) from the latest MR version.

        Delegates extraction logic to the shared utility in vcs_adapter so both
        GitLabAdapter implementations stay in sync.  Raises ValueError / RuntimeError
        on failure — callers decide whether the failure is fatal.
        """
        versions = self._request("GET", f"/merge_requests/{pr_id}/versions")
        return extract_gitlab_version_shas(versions)

    def post_review_comment(
        self, pr_id: int, position: DiffPosition, body: str
    ) -> str | None:
        """Post an inline discussion note on the MR.

        POST /projects/{project_id}/merge_requests/{iid}/discussions

        Returns:
            The GitLab discussion ID as a string on success (used for thread
            resolution in resolve_inline_comment), None on failure.
        """
        try:
            base_sha, start_sha, head_sha = self._get_mr_version_shas(pr_id)
        except (ValueError, RuntimeError) as exc:
            _LOG.warning("post_review_comment: failed to get MR version SHAs for MR %s: %s", pr_id, exc)
            return None

        pos_obj: dict[str, Any] = {
            "base_sha": base_sha,
            "start_sha": start_sha,
            "head_sha": head_sha,
            "position_type": "text",
            "old_path": position.file_path,
            "new_path": position.file_path,
            "new_line": position.new_line or position.line_number,
        }
        if position.line_code:
            pos_obj["line_code"] = position.line_code
        if position.old_line is not None and position.old_line > 0:
            pos_obj["old_line"] = position.old_line
        try:
            resp = self._request(
                "POST",
                f"/merge_requests/{pr_id}/discussions",
                {"body": body, "position": pos_obj},
            )
            # GitLab returns the discussion object with an "id" field
            discussion_id = resp.get("id")
            return str(discussion_id) if discussion_id is not None else None
        except Exception as exc:
            _LOG.warning(
                "post_review_comment failed for MR %s file=%s line=%s — %s\n  position=%s",
                pr_id,
                position.file_path,
                position.new_line or position.line_number,
                exc,
                pos_obj,
            )
            return None

    # Backward-compat alias — remove in v2.0
    post_inline_comment = post_review_comment

    def post_summary_comment(self, pr_id: int, body: str) -> str | None:
        """Post a top-level MR note (not a discussion).

        POST /projects/{project_id}/merge_requests/{iid}/notes
        Returns the note ID as a string on success, None on error.
        """
        try:
            resp = self._request(
                "POST",
                f"/merge_requests/{pr_id}/notes",
                {"body": body},
            )
            note_id = resp.get("id")
            return str(note_id) if note_id is not None else None
        except Exception as exc:
            _LOG.warning("post_summary_comment failed for MR %s: %s", pr_id, exc)
            return None

    def update_comment(self, pr_id: int, comment_id: str, body: str) -> bool:
        """Update an existing MR note in-place.

        PUT /projects/{project_id}/merge_requests/{iid}/notes/{note_id}
        Returns True on success, False on error.
        """
        try:
            self._request(
                "PUT",
                f"/merge_requests/{pr_id}/notes/{comment_id}",
                {"body": body},
            )
            return True
        except Exception as exc:
            _LOG.warning("update_comment failed for MR %s note %s: %s", pr_id, comment_id, exc)
            return False

    def get_existing_comments(self, pr_id: int) -> list[dict]:
        """Fetch all discussion notes on the MR, flattened from discussions.

        GET /projects/{project_id}/merge_requests/{iid}/discussions
        Each discussion has a notes[] array; all notes are collected into a
        single flat list.  Returns [] on error.
        """
        try:
            discussions: list[dict[str, Any]] = self._request(
                "GET", f"/merge_requests/{pr_id}/discussions"
            )
            notes: list[dict] = []
            for discussion in discussions:
                notes.extend(discussion.get("notes", []))
            return notes
        except Exception as exc:
            _LOG.warning("get_existing_comments failed for MR %s: %s", pr_id, exc)
            return []

    def resolve_position(
        self, file_path: str, line_number: int, diff: str
    ) -> DiffPosition:
        """Use translate_gitlab_line_code() from vcs_adapter.py."""
        line_code = translate_gitlab_line_code(
            base_commit_sha="",
            head_commit_sha="",
            file_path=file_path,
            line_number=line_number,
        )
        return DiffPosition(
            file_path=file_path,
            line_number=line_number,
            line_code=line_code,
            new_line=line_number,
        )

    def set_review_status(self, pr_id: int, status: str) -> bool:
        """Set the MR approval status for blocking mode.

        Args:
            pr_id:  MR IID.
            status: ``"approved"`` to approve or ``"unapproved"`` to revoke.
                    Pass ``"unapproved"`` when Revue finds blocking-severity issues.

        Returns:
            True on success, False on error (non-fatal — review still posted).

        GitLab API:
            POST /projects/{id}/merge_requests/{iid}/approve
            POST /projects/{id}/merge_requests/{iid}/unapprove
        """
        if status not in ("approved", "unapproved"):
            _LOG.warning("set_review_status: unknown status %r — skipping", status)
            return False
        endpoint = f"/merge_requests/{pr_id}/{status}"
        try:
            self._request("POST", endpoint)
            return True
        except Exception as exc:
            _LOG.warning("set_review_status(%r) failed for MR %s: %s", status, pr_id, exc)
            return False

    def post_apply_suggestion(
        self, pr_id: int, position: DiffPosition, code_fix: CodeFix
    ) -> bool:
        """Post a Sage-generated fix as a GitLab Apply Suggestion.

        GitLab's suggestion syntax uses backtick-wrapped blocks in discussion
        notes:

            ```suggestion:-0+0
            fixed line 1
            fixed line 2
            ```

        The ``:-X+Y`` suffix indicates lines to remove/add relative to the
        comment position. For multi-line fixes, we calculate the range based
        on code_fix.start_line and code_fix.end_line.

        Args:
            pr_id: Merge request IID
            position: DiffPosition for the comment
            code_fix: CodeFix with original_lines, fixed_lines, explanation

        Returns:
            True on success, False on error
        """
        # Calculate suggestion range
        # GitLab syntax: :-<lines_to_remove>+<lines_to_add>
        lines_to_remove = len(code_fix.original_lines)
        lines_to_add = len(code_fix.fixed_lines)

        suggestion_lines = "\n".join(code_fix.fixed_lines)
        body = f"""{code_fix.explanation}

```suggestion:-{lines_to_remove}+{lines_to_add}
{suggestion_lines}
```

*🤖 Sage suggestion (confidence: {code_fix.confidence:.0f}%)*
"""

        # Build position object for GitLab Discussions API
        pos_obj: dict[str, Any] = {
            "base_sha": "",  # Required but can be empty for simple cases
            "head_sha": "",  # Required but can be empty
            "start_sha": "",  # Required but can be empty
            "position_type": "text",
            "new_path": position.file_path,
            "new_line": position.line_number,
        }

        if position.line_code:
            pos_obj["line_code"] = position.line_code

        try:
            self._request(
                "POST",
                f"/merge_requests/{pr_id}/discussions",
                {"body": body, "position": pos_obj},
            )
            return True
        except Exception as exc:
            _LOG.warning(
                "post_apply_suggestion failed for MR %s: %s", pr_id, exc
            )
            return False

    # ------------------------------------------------------------------
    # Webhook helpers (static)
    # ------------------------------------------------------------------

    @staticmethod
    def verify_webhook_token(token_header: str, expected_token: str) -> bool:
        """Verify ``X-Gitlab-Token`` header for webhook security.

        Uses ``hmac.compare_digest`` for timing-safe comparison.

        .. deprecated::
            Use ``verify_webhook_signature`` to satisfy the VCSAdapter protocol.
            This static helper is kept for backward compatibility.
        """
        return hmac.compare_digest(token_header, expected_token)

    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        """Verify the ``X-Gitlab-Token`` header (VCSAdapter protocol compliance).

        GitLab does not use HMAC-SHA256 for webhook verification — it passes
        the configured secret token verbatim in ``X-Gitlab-Token``.
        ``payload`` is accepted for protocol compatibility but is not used.

        Args:
            payload:   Raw request body bytes (unused for GitLab).
            signature: Value of the ``X-Gitlab-Token`` header.

        Returns:
            True if ``signature`` matches the configured ``webhook_secret``.
        """
        return hmac.compare_digest(signature, self._webhook_secret)

    def resolve_inline_comment(
        self, pr_id: int, comment_id: str, reply_body: str
    ) -> bool:
        """Resolve a GitLab discussion thread.

        GitLab uses discussion-based threading. To resolve:
        PUT /projects/{id}/merge_requests/{iid}/discussions/{discussion_id}
        with { "resolved": true }

        ``comment_id`` is expected to be the discussion ID (not the note ID).

        Optionally posts a reply before resolving (for context).

        Args:
            pr_id:       Merge request IID.
            comment_id:  The GitLab discussion ID.
            reply_body:  Optional reply message before resolving.

        Returns:
            True on success, False on error.
        """
        # Post reply if provided
        if reply_body:
            try:
                self._request(
                    "POST",
                    f"/merge_requests/{pr_id}/discussions/{comment_id}/notes",
                    {"body": reply_body},
                )
            except Exception as exc:
                _LOG.warning("resolve_inline_comment: reply failed for MR %d discussion %s: %s", pr_id, comment_id, exc)
                # Continue to resolve even if reply fails

        # Resolve the discussion
        try:
            self._request(
                "PUT",
                f"/merge_requests/{pr_id}/discussions/{comment_id}",
                {"resolved": True},
            )
            _LOG.info("Resolved discussion %s on MR %d", comment_id, pr_id)
            return True
        except Exception as exc:
            _LOG.warning("resolve_inline_comment failed for MR %d discussion %s: %s", pr_id, comment_id, exc)
            return False

    @staticmethod
    def parse_webhook_event(
        headers: dict[str, str], payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Parse a GitLab webhook event.

        Returns dict with ``{event_type, pr_id, project_id, action}``
        for Merge Request Hook events with ``object_attributes.action``
        in (open, update, reopen).  Returns ``None`` for everything else.
        """
        event_type = headers.get("X-Gitlab-Event", "")
        if event_type != "Merge Request Hook":
            return None

        obj = payload.get("object_attributes")
        if not obj:
            return None

        action = obj.get("action", "")
        if action not in ("open", "update", "reopen"):
            return None

        return {
            "event_type": "merge_request",
            "pr_id": obj.get("iid"),
            "project_id": payload.get("project", {}).get("id"),
            "action": action,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _count_diff_lines(diff_text: str) -> tuple[int, int]:
        """Count additions and deletions in a unified diff fragment."""
        additions = 0
        deletions = 0
        for line in diff_text.split("\n"):
            if line.startswith("+") and not line.startswith("+++"):
                additions += 1
            elif line.startswith("-") and not line.startswith("---"):
                deletions += 1
        return additions, deletions
