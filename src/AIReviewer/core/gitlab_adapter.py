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

from AIReviewer.core.models import FileChange
from AIReviewer.core.vcs_adapter import (
    DiffPosition,
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
    ) -> None:
        self._token = token
        self._project_id = project_id
        self._base_url = base_url.rstrip("/")
        self._token_type = token_type

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

    def post_inline_comment(
        self, pr_id: int, position: DiffPosition, body: str
    ) -> bool:
        """Post an inline discussion note on the MR.

        POST /projects/{project_id}/merge_requests/{iid}/discussions
        Returns True on success, False on error.
        """
        pos_obj: dict[str, Any] = {
            "base_sha": position.commit_id,
            "head_sha": position.commit_id,
            "position_type": "text",
            "old_path": position.file_path,
            "new_path": position.file_path,
            "new_line": position.new_line or position.line_number,
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
            _LOG.warning("post_inline_comment failed for MR %s: %s", pr_id, exc)
            return False

    def post_summary_comment(self, pr_id: int, body: str) -> bool:
        """Post a top-level MR note (not a discussion).

        POST /projects/{project_id}/merge_requests/{iid}/notes
        Returns True on success, False on error.
        """
        try:
            self._request(
                "POST",
                f"/merge_requests/{pr_id}/notes",
                {"body": body},
            )
            return True
        except Exception as exc:
            _LOG.warning("post_summary_comment failed for MR %s: %s", pr_id, exc)
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

    # ------------------------------------------------------------------
    # Webhook helpers (static)
    # ------------------------------------------------------------------

    @staticmethod
    def verify_webhook_token(token_header: str, expected_token: str) -> bool:
        """Verify ``X-Gitlab-Token`` header for webhook security.

        Uses ``hmac.compare_digest`` for timing-safe comparison.
        """
        return hmac.compare_digest(token_header, expected_token)

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
