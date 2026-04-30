"""Platform-specific API adapters for comment resolution."""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import urllib.parse
from abc import ABC, abstractmethod
from typing import Any, Optional

import httpx

from revue.core.diff_parser import parse_diff
from revue.core.models import FileChange
from revue.core.vcs_adapter import DiffPosition
from .models import Platform

_log = logging.getLogger(__name__)

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _line_in_diff(line_number: int, file_path: str, diff: str) -> bool:
    """Return True if line_number falls within any new-file hunk range for file_path.

    Handles both full multi-file diffs (with 'diff --git' headers) and
    per-file hunk-only diffs (FileChange.diff from diff_by_file, which
    strips the header in _parse_single_file_diff).

    For per-file diffs (no git header), ``file_path`` is not validated —
    callers must supply the correct per-file diff via ``diff_by_file.get(file_path)``.
    """
    if not diff:
        return False
    in_file: bool | None = None  # None = not yet determined
    for line in diff.splitlines():
        if line.startswith("diff --git"):
            if in_file is None:
                in_file = False  # full diff — start locked out until we find our file
            in_file = f" b/{file_path}" in line
            continue
        if in_file is None:
            in_file = True  # first line is a hunk header — per-file diff, treat as in file
        if not in_file:
            continue
        m = _HUNK_RE.match(line)
        if m:
            new_start = int(m.group(1))
            new_count = int(m.group(2)) if m.group(2) is not None else 1
            if new_start <= line_number < new_start + new_count:
                return True
    return False


class PlatformAdapter(ABC):
    """Abstract base for platform-specific comment operations."""
    
    @abstractmethod
    def post_comment(
        self, 
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        file_path: str,
        line_number: int,
        body: str,
        commit_sha: str
    ) -> tuple[str, Optional[str]]:
        """
        Post a comment to a PR.
        
        Returns:
            (comment_id, thread_id): Platform-specific IDs
        """
        pass
    
    @abstractmethod
    def resolve_comment(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int = 0,
        comment_id: str = "",
        thread_id: Optional[str] = None
    ) -> bool:
        """
        Resolve a comment via API.

        Returns:
            True if successful, False if API doesn't support resolution
        """
        pass

    @abstractmethod
    def post_reply(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int = 0,
        comment_id: str = "",
        thread_id: Optional[str] = None,
        body: str = ""
    ) -> str:
        """
        Post a reply to an existing comment.

        Returns:
            reply_id: Platform-specific reply ID
        """
        pass

    @abstractmethod
    def get_comment_replies(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int = 0,
        comment_id: str = ""
    ) -> list[dict]:
        """
        Fetch all replies to a comment.

        Returns:
            List of reply dicts with 'body', 'author', 'created_at'
        """
        pass
    
    @abstractmethod
    def get_all_pr_comments(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
    ) -> list[dict]:
        """Fetch every comment on a PR in a single call (paginated).

        Returns a flat list of comment dicts in the platform's native shape.
        Used by won't-fix reply tracking to discover Revue findings and replies
        without relying on the local store (works on fresh CI checkouts).
        """
        pass

    @abstractmethod
    def is_comment_resolved(
        self,
        repo_owner: str,
        repo_name: str,
        comment_id: str
    ) -> bool:
        """Check if comment is resolved on platform."""
        pass

    def get_pr_template(self, repo_owner: str, repo_name: str) -> Optional[str]:
        """Fetch the repo's PR/MR description template. Returns None if not found."""
        return None

    def ensure_lessons_pr(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        branch: str,
        revue_yml_content: str,
        commit_message: str,
        pr_title: str,
        pr_description: str,
    ) -> str:
        """Create or update a lessons PR/MR for the given branch. Returns PR/MR URL.

        Must be overridden by platform-specific adapters. Raises NotImplementedError
        if the platform does not yet support automatic lessons PR creation.
        """
        raise NotImplementedError(
            f"Lessons PR creation is not supported on {type(self).__name__}"
        )

    @abstractmethod
    def get_diff(self, pr_id: int) -> list[FileChange]:
        """Fetch PR diff and return as FileChange objects.

        Returns [] on error. Bitbucket-specific; GitHub/GitLab return [].
        """
        pass

    @abstractmethod
    def set_pr_status(self, commit_sha: str, state: str, description: str = "") -> bool:
        """Post build/review status against a commit.

        Bitbucket-specific; GitHub uses check runs, GitLab uses pipeline stages.
        Returns True on success, False on error.
        """
        pass

    @abstractmethod
    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        """Verify webhook signature using HMAC.

        Returns True if signature is valid, False otherwise.
        """
        pass

    @staticmethod
    @abstractmethod
    def parse_webhook_event(
        headers: dict[str, str], payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Parse webhook event from headers and payload.

        Returns dict with event details or None if not a relevant event.
        """
        pass

    @abstractmethod
    def post_summary_comment(self, pr_id: int, body: str) -> "str | None":
        """Post a top-level (non-inline) PR comment. Returns comment ID or None on failure."""
        pass

    @abstractmethod
    def update_comment(self, pr_id: int, comment_id: str, body: str) -> bool:
        """Update an existing PR comment in-place. Returns True on success."""
        pass

    @abstractmethod
    def get_existing_comments(self, pr_id: int) -> list[dict]:
        """Fetch all comments on a PR. Returns [] on error."""
        pass

    def resolve_conversation(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        comment_id: str,
    ) -> None:
        """Resolve a PR-level conversation (non-inline thread).

        No-op by default; Bitbucket overrides with the PUT /resolve API call.
        """


class BitbucketAdapter(PlatformAdapter):
    """Bitbucket Cloud API adapter.

    Supports both comment threading and pipeline operations (diff, status, webhooks).
    Auth: HTTP Basic Auth or Bearer token depending on token type.
    """

    # Bitbucket Repository/Workspace Access Tokens (OAuth) start with this prefix.
    # App Passwords do not — they use HTTP Basic Auth instead.
    _BEARER_PREFIX = "ATCTT3"

    def __init__(
        self,
        username: str,
        app_password: str,
        workspace: str = "",
        repo_slug: str = "",
        webhook_secret: str = "",
    ):
        """Initialize BitbucketAdapter.

        Args:
            username: Bitbucket account username or email.
            app_password: Bitbucket API token or app password.
            workspace: Bitbucket workspace slug (e.g. "cbscd") — required for pipeline operations.
            repo_slug: Repository slug (e.g. "revue") — required for pipeline operations.
            webhook_secret: Secret for webhook signature verification.
        """
        self.username = username
        self.app_password = app_password
        self.base_url = "https://api.bitbucket.org/2.0"
        self.workspace = workspace
        self.repo_slug = repo_slug
        self.webhook_secret = webhook_secret
        # Repository/Workspace Access Tokens must be sent as Bearer, not Basic Auth.
        self._is_bearer = app_password.startswith(self._BEARER_PREFIX)
        # Set to True when Bitbucket's 200-comment-per-PR limit is hit.
        self.comment_limit_reached = False

    def _auth_kwargs(self) -> dict:
        """Return the httpx auth keyword args for this token type."""
        if self._is_bearer:
            return {"headers": {"Authorization": f"Bearer {self.app_password}"}}
        return {"auth": (self.username, self.app_password)}
    
    def post_comment(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        file_path: str,
        line_number: int,
        body: str,
        commit_sha: str
    ) -> tuple[str, Optional[str]]:
        """Post inline comment to Bitbucket PR."""
        url = f"{self.base_url}/repositories/{repo_owner}/{repo_name}/pullrequests/{pr_number}/comments"
        
        payload = {
            "content": {"raw": body},
            "inline": {
                "path": file_path,
                "to": line_number
            }
        }
        
        response = httpx.post(url, json=payload, **self._auth_kwargs())
        response.raise_for_status()

        data = response.json()
        return (str(data['id']), None)  # Bitbucket doesn't have thread IDs
    
    def resolve_comment(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        comment_id: str,
        thread_id: Optional[str] = None
    ) -> bool:
        """Resolve comment via Bitbucket API."""
        url = f"{self.base_url}/repositories/{repo_owner}/{repo_name}/pullrequests/{pr_number}/comments/{comment_id}/resolve"
        try:
            response = httpx.post(url, timeout=10.0, **self._auth_kwargs())
        except httpx.HTTPError as exc:
            _log.warning("Failed to resolve comment %s: %s", comment_id, exc)
            return False
        if response.status_code == 400:
            _log.warning(
                "Cannot resolve comment %s — Bitbucket only resolves inline comments",
                comment_id,
            )
            return False
        return response.status_code == 200

    def post_reply(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        comment_id: str,
        thread_id: Optional[str],
        body: str
    ) -> str:
        """Post reply to Bitbucket comment."""
        url = f"{self.base_url}/repositories/{repo_owner}/{repo_name}/pullrequests/{pr_number}/comments"
        response = httpx.post(
            url,
            json={"content": {"raw": body}, "parent": {"id": int(comment_id)}},
            timeout=10.0,
            **self._auth_kwargs(),
        )
        response.raise_for_status()
        return str(response.json()["id"])

    def get_diff(self, pr_id: int) -> list[FileChange]:
        """Fetch PR diff from Bitbucket and parse into FileChange objects.

        GET /2.0/repositories/{ws}/{slug}/pullrequests/{id}/diff
        Returns raw unified diff text; reuse diff_parser.parse_diff().
        """
        try:
            url = f"{self.base_url}/repositories/{self.workspace}/{self.repo_slug}/pullrequests/{pr_id}/diff"
            kw = self._auth_kwargs()
            kw.setdefault("headers", {})["Accept"] = "text/plain"
            response = httpx.get(url, **kw)
            response.raise_for_status()
            return parse_diff(response.text)
        except Exception as exc:
            _log.warning("get_diff failed for PR %s: %s", pr_id, exc)
            return []

    def set_pr_status(self, commit_sha: str, state: str, description: str = "") -> bool:
        """Post a build/review status against a commit (Bitbucket Commit Status API).

        POST /2.0/repositories/{ws}/{slug}/commit/{sha}/statuses/build

        Args:
            commit_sha: The HEAD commit SHA of the PR.
            state: "SUCCESSFUL", "FAILED", or "INPROGRESS".
            description: Short human-readable description shown in the UI.

        Returns:
            True on success, False on error.
        """
        try:
            payload: dict[str, Any] = {
                "key": "revue-io",
                "state": state,
                "name": "Revue.io AI Review",
                "description": description or f"Revue.io review {state.lower()}",
                "url": "https://revue-io.fly.dev",
            }
            sha_encoded = urllib.parse.quote(commit_sha, safe="")
            url = f"{self.base_url}/repositories/{self.workspace}/{self.repo_slug}/commit/{sha_encoded}/statuses/build"
            response = httpx.post(url, json=payload, **self._auth_kwargs())
            response.raise_for_status()
            return True
        except Exception as exc:
            _log.warning("set_pr_status failed for commit %s: %s", commit_sha, exc)
            return False

    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        """Verify Bitbucket webhook signature via HMAC-SHA256.

        Bitbucket sends X-Hub-Signature: sha256=<hex> (same as GitHub).
        Returns False immediately if no webhook_secret is set.

        Args:
            payload: Raw request body bytes.
            signature: Value of X-Hub-Signature header (sha256=<hex>).

        Returns:
            True if the signature is valid, False otherwise.
        """
        if not self.webhook_secret:
            return False
        expected = (
            "sha256="
            + hmac.new(
                self.webhook_secret.encode(),
                payload,
                hashlib.sha256,
            ).hexdigest()
        )
        return hmac.compare_digest(expected, signature)

    @staticmethod
    def parse_webhook_event(
        headers: dict[str, str], payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Parse a Bitbucket webhook event.

        Returns dict with {event_type, pr_id, workspace, repo_slug, action, commit_sha}
        for pullrequest:created / pullrequest:updated events.
        Returns None for everything else.

        Bitbucket webhook event key is in the X-Event-Key header.
        """
        event_key = headers.get("X-Event-Key", "")
        if not event_key.startswith("pullrequest:"):
            return None

        action = event_key.split(":", 1)[1]  # e.g. "created", "updated"
        if action not in ("created", "updated"):
            return None

        pr = payload.get("pullrequest", {})
        pr_id = pr.get("id")
        if not pr_id:
            return None

        repo = payload.get("repository", {})
        full_name = repo.get("full_name", "/")  # e.g. "cbscd/revue"
        parts = full_name.split("/", 1)
        workspace = parts[0] if len(parts) > 0 else ""
        repo_slug = parts[1] if len(parts) > 1 else ""

        return {
            "event_type": "pull_request",
            "pr_id": pr_id,
            "workspace": workspace,
            "repo_slug": repo_slug,
            "action": action,
            "commit_sha": pr.get("source", {}).get("commit", {}).get("hash", ""),
        }

    def resolve_conversation(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        comment_id: str,
    ) -> None:
        """Resolve a PR conversation (non-inline comment) via Bitbucket API."""
        url = f"{self.base_url}/repositories/{repo_owner}/{repo_name}/pullrequests/{pr_number}/comments/{comment_id}/resolve"
        try:
            response = httpx.post(url, timeout=10.0, **self._auth_kwargs())
            if response.status_code not in (200, 409):
                _log.error(
                    "Failed to resolve conversation %s on PR %s/%s/%s: HTTP %s",
                    comment_id,
                    repo_owner,
                    repo_name,
                    pr_number,
                    response.status_code,
                )
        except Exception as exc:
            _log.error(
                "Failed to resolve conversation %s on PR %s/%s/%s: %s",
                comment_id,
                repo_owner,
                repo_name,
                pr_number,
                exc,
            )

    def post_review_comment(
        self, pr_id: int, position: DiffPosition, body: str, replacement_line_count: int = 1
    ) -> str | None:
        """Post an inline comment on a PR (pipeline operation).

        POST /2.0/repositories/{ws}/{slug}/pullrequests/{id}/comments
        Bitbucket inline comments use an ``inline`` key with ``path`` and ``to``
        (the new-file line number).

        Returns:
            The Bitbucket comment ID as a string on success, None on failure.
        """
        payload: dict[str, Any] = {
            "content": {"raw": body},
            "inline": {
                "path": position.file_path,
                "to": position.line_number,
            },
        }
        try:
            url = f"{self.base_url}/repositories/{self.workspace}/{self.repo_slug}/pullrequests/{pr_id}/comments"
            response = httpx.post(url, json=payload, **self._auth_kwargs())
            response.raise_for_status()
            comment_id = response.json().get("id")
            return str(comment_id) if comment_id is not None else None
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:300] if exc.response.text else "(empty)"
            if exc.response.status_code == 403 and "200 comments" in exc.response.text:
                self.comment_limit_reached = True
                _log.warning("❌ post_review_comment: PR %s has reached Bitbucket's 200-comment limit", pr_id)
            else:
                _log.warning("❌ post_review_comment failed for PR %s: %s — response: %s", pr_id, exc, body)
            return None
        except Exception as exc:
            _log.warning("❌ post_review_comment failed for PR %s: %s", pr_id, exc)
            return None

    post_inline_comment = post_review_comment

    def post_summary_comment(self, pr_id: int, body: str) -> str | None:
        """Post a top-level PR comment (not inline).

        POST /2.0/repositories/{ws}/{slug}/pullrequests/{id}/comments
        Omitting the ``inline`` key makes it a general comment.

        Returns:
            The comment ID as a string on success, None on failure.
        """
        payload: dict[str, Any] = {"content": {"raw": body}}
        try:
            url = f"{self.base_url}/repositories/{self.workspace}/{self.repo_slug}/pullrequests/{pr_id}/comments"
            response = httpx.post(url, json=payload, **self._auth_kwargs())
            response.raise_for_status()
            comment_id = response.json().get("id")
            return str(comment_id) if comment_id is not None else None
        except Exception as exc:
            _log.warning("post_summary_comment failed for PR %s: %s", pr_id, exc)
            return None

    def update_comment(self, pr_id: int, comment_id: str, body: str) -> bool:
        """Update an existing top-level PR comment in-place.

        PUT /2.0/repositories/{ws}/{slug}/pullrequests/{id}/comments/{comment_id}

        Args:
            pr_id:      Pull request ID.
            comment_id: The Bitbucket comment ID (as string).
            body:       New markdown body for the comment.

        Returns:
            True on success, False on error (including 404 if comment was deleted).
        """
        payload: dict[str, Any] = {"content": {"raw": body}}
        try:
            url = f"{self.base_url}/repositories/{self.workspace}/{self.repo_slug}/pullrequests/{pr_id}/comments/{comment_id}"
            response = httpx.put(url, json=payload, **self._auth_kwargs())
            response.raise_for_status()
            return True
        except Exception as exc:
            _log.error(
                "update_comment failed for PR %s comment %s — will post new comment instead. "
                "Error: %s (type: %s)",
                pr_id, comment_id, exc, type(exc).__name__
            )
            return False

    def get_existing_comments(self, pr_id: int) -> list[dict]:
        """Fetch all comments on a PR (all pages).

        GET /2.0/repositories/{ws}/{slug}/pullrequests/{id}/comments
        Returns a flat list of comment objects. Returns [] on error.
        """
        try:
            all_comments: list[dict] = []
            url: Optional[str] = f"{self.base_url}/repositories/{self.workspace}/{self.repo_slug}/pullrequests/{pr_id}/comments"
            while url:
                response = httpx.get(url, **self._auth_kwargs())
                response.raise_for_status()
                data = response.json()
                all_comments.extend(data.get("values", []))
                url = data.get("next")
            return all_comments
        except Exception as exc:
            _log.warning("get_existing_comments failed for PR %s: %s", pr_id, exc)
            return []

    def resolve_position(
        self, file_path: str, line_number: int, diff: str
    ) -> DiffPosition:
        """Return a DiffPosition for Bitbucket.

        Validates line_number against diff hunk ranges. position=1 means the
        line is in the diff; position=0 means it falls outside all hunks and
        the caller should skip posting (Bitbucket returns 403 for out-of-range
        lines, matching the same sentinel used by the GitHub path).
        """
        valid = _line_in_diff(line_number, file_path, diff)
        return DiffPosition(
            file_path=file_path,
            line_number=line_number,
            side="RIGHT",
            position=1 if valid else 0,
        )

    def resolve_inline_comment(
        self, pr_id: int, comment_id: str, reply_body: str
    ) -> bool:
        """Resolve an inline comment thread by posting a reply then calling the native resolve API.

        POST /2.0/repositories/{ws}/{slug}/pullrequests/{id}/comments            (reply)
        POST /2.0/repositories/{ws}/{slug}/pullrequests/{id}/comments/{id}/resolve

        Args:
            pr_id:       Pull request ID.
            comment_id:  The Bitbucket comment ID to reply to and resolve.
            reply_body:  The reply message (e.g. "✅ Issue appears to be resolved").

        Returns:
            True on success, False on error.
        """
        if reply_body:
            payload: dict[str, Any] = {
                "content": {"raw": reply_body},
                "parent": {"id": int(comment_id)},
            }
            try:
                url = f"{self.base_url}/repositories/{self.workspace}/{self.repo_slug}/pullrequests/{pr_id}/comments"
                response = httpx.post(url, json=payload, **self._auth_kwargs())
                response.raise_for_status()
                _log.info("Posted resolution reply to comment %s on PR %s", comment_id, pr_id)
            except httpx.HTTPStatusError as exc:
                body = exc.response.text[:300] if exc.response.text else "(empty)"
                _log.warning("❌ resolve_inline_comment: reply failed for PR %s comment %s: %s — response: %s", pr_id, comment_id, exc, body)
                return False
            except Exception as exc:
                _log.warning("❌ resolve_inline_comment: reply failed for PR %s comment %s: %s", pr_id, comment_id, exc, exc_info=True)
                return False

        try:
            url = f"{self.base_url}/repositories/{self.workspace}/{self.repo_slug}/pullrequests/{pr_id}/comments/{comment_id}/resolve"
            response = httpx.post(url, **self._auth_kwargs())
            response.raise_for_status()
            return True
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 409:  # Already resolved — goal achieved
                return True
            _log.warning("❌ resolve_inline_comment: resolve failed for PR %s comment %s: HTTP %s", pr_id, comment_id, exc.response.status_code, exc_info=True)
            return False
        except Exception as exc:
            _log.warning("❌ resolve_inline_comment: resolve failed for PR %s comment %s: %s", pr_id, comment_id, exc, exc_info=True)
            return False

    _REVUE_FP_SENTINEL = "revue:fp:"
    _RESOLUTION_MARKERS = ("✅", "Issue appears to be resolved")

    def delete_comment(self, pr_id: int, comment_id: str) -> bool:
        """Delete a single PR comment.

        DELETE /2.0/repositories/{ws}/{slug}/pullrequests/{id}/comments/{comment_id}

        Returns True on success (including 404 — already gone).
        Returns False on error. 403 is treated as a silent skip (comment belongs
        to a different token identity) rather than a warning, since eviction
        iterates over all Revue comments without pre-filtering by owner.
        """
        url = f"{self.base_url}/repositories/{self.workspace}/{self.repo_slug}/pullrequests/{pr_id}/comments/{comment_id}"
        try:
            response = httpx.delete(url, **self._auth_kwargs())
            if response.status_code == 404:
                return True
            response.raise_for_status()
            return True
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403:
                _log.debug("delete_comment: skipping comment %s on PR %s — not owned by current token", comment_id, pr_id)
            else:
                _log.warning("delete_comment failed for PR %s comment %s: HTTP %s", pr_id, comment_id, exc.response.status_code)
            return False
        except Exception as exc:
            _log.warning("delete_comment failed for PR %s comment %s: %s", pr_id, comment_id, exc)
            return False

    def evict_resolved_revue_comments(self, pr_id: int) -> int:
        """Delete oldest resolved Revue inline comment threads to free up comment slots.

        A "resolved Revue thread" is a top-level inline comment that:
        - contains the ``revue:fp:`` fingerprint sentinel (posted by Revue), AND
        - has at least one reply containing a resolution marker (✅ / "Issue appears
          to be resolved"), indicating Revue already auto-resolved it.

        Attempts deletion of all matching threads (replies first, then parent),
        oldest first. Comments posted by a different token identity are silently
        skipped — delete_comment treats 403 as a soft skip, so no extra scope
        (e.g. ``account``) is required.

        Returns the number of parent comments successfully deleted.
        """
        all_comments = self.get_existing_comments(pr_id)
        if not all_comments:
            return 0

        children_by_parent: dict[int, list[dict]] = {}
        revue_parents: list[dict] = []

        for c in all_comments:
            parent_ref = c.get("parent")
            if parent_ref:
                pid = parent_ref.get("id")
                if pid is not None:
                    children_by_parent.setdefault(int(pid), []).append(c)
            else:
                raw = c.get("content", {}).get("raw", "")
                if self._REVUE_FP_SENTINEL in raw:
                    revue_parents.append(c)

        def _is_resolved(parent: dict) -> bool:
            for reply in children_by_parent.get(int(parent["id"]), []):
                raw = reply.get("content", {}).get("raw", "")
                if any(m in raw for m in self._RESOLUTION_MARKERS):
                    return True
            return False

        resolved = sorted(
            (p for p in revue_parents if _is_resolved(p)),
            key=lambda c: c.get("created_on", ""),
        )
        if not resolved:
            return 0

        deleted = 0
        for parent in resolved:
            parent_id = int(parent["id"])
            for reply in children_by_parent.get(parent_id, []):
                self.delete_comment(pr_id, str(reply["id"]))
            if self.delete_comment(pr_id, str(parent_id)):
                deleted += 1
                _log.info("evict: deleted resolved thread %s on PR %s", parent_id, pr_id)

        _log.info("evict: removed %d resolved Revue thread(s) from PR %s", deleted, pr_id)
        return deleted

    def get_comment_replies(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        comment_id: str
    ) -> list[dict]:
        """Fetch replies to Bitbucket comment, filtered by parent.id."""
        url = f"{self.base_url}/repositories/{repo_owner}/{repo_name}/pullrequests/{pr_number}/comments"
        response = httpx.get(url, **self._auth_kwargs())
        response.raise_for_status()
        all_comments = response.json().get("values", [])
        try:
            parent_id = int(comment_id)
        except ValueError:
            _log.warning("get_comment_replies: non-integer comment_id %r — returning []", comment_id)
            return []
        return [c for c in all_comments if c.get("parent", {}).get("id") == parent_id]
    
    def get_all_pr_comments(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
    ) -> list[dict]:
        """Fetch all Bitbucket PR comments, following pagination."""
        url: Optional[str] = (
            f"{self.base_url}/repositories/{repo_owner}/{repo_name}"
            f"/pullrequests/{pr_number}/comments?pagelen=100"
        )
        all_comments: list[dict] = []
        while url:
            response = httpx.get(url, timeout=10.0, **self._auth_kwargs())
            response.raise_for_status()
            data = response.json()
            all_comments.extend(data.get("values", []))
            url = data.get("next")
        return all_comments

    def is_comment_resolved(
        self,
        repo_owner: str,
        repo_name: str,
        comment_id: str
    ) -> bool:
        """Check if Bitbucket comment is resolved."""
        url = f"{self.base_url}/repositories/{repo_owner}/{repo_name}/pullrequests/comments/{comment_id}"

        response = httpx.get(url, **self._auth_kwargs())
        response.raise_for_status()

        return response.json().get('resolved', False)

    _BB_PR_TEMPLATE_PATH = ".bitbucket/pull_request_template.md"

    def get_pr_template(self, repo_owner: str, repo_name: str) -> Optional[str]:
        """Fetch Bitbucket PR template from .bitbucket/pull_request_template.md.
        Returns template text on 200, None on 404 or error."""
        url = (
            f"{self.base_url}/repositories/{repo_owner}/{repo_name}"
            f"/src/HEAD/{self._BB_PR_TEMPLATE_PATH}"
        )
        try:
            resp = httpx.get(url, **self._auth_kwargs())
            if resp.status_code == 200:
                return resp.text
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
        except Exception:
            _log.debug("get_pr_template: could not fetch Bitbucket PR template for %s/%s", repo_owner, repo_name)
        return None

    def ensure_lessons_pr(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        branch: str,
        revue_yml_content: str,
        commit_message: str,
        pr_title: str,
        pr_description: str,
    ) -> str:
        """Create or update Bitbucket lessons PR. Returns PR URL.

        If an open PR for ``branch`` already exists, commits the updated
        .revue.yml to it and returns the existing URL.  Otherwise commits
        and creates a new PR targeting main.
        """
        # Check for existing open PR on the lessons branch
        prs_url = (
            f"{self.base_url}/repositories/{repo_owner}/{repo_name}"
            f'/pullrequests?q=source.branch.name="{branch}" AND state="OPEN"'
        )
        existing_url: Optional[str] = None
        try:
            resp = httpx.get(prs_url, **self._auth_kwargs())
            resp.raise_for_status()
            values = resp.json().get("values", [])
            if values:
                existing_url = values[0].get("links", {}).get("html", {}).get("href", "")
        except Exception:
            _log.exception("Failed to search for existing Bitbucket lessons PR")

        # Always commit the updated .revue.yml to the branch
        src_url = f"{self.base_url}/repositories/{repo_owner}/{repo_name}/src"
        commit_resp = httpx.post(
            src_url,
            data={"message": commit_message, "branch": branch, ".revue.yml": revue_yml_content},
            **self._auth_kwargs(),
        )
        commit_resp.raise_for_status()

        if existing_url:
            return existing_url

        # Create a new PR
        create_url = f"{self.base_url}/repositories/{repo_owner}/{repo_name}/pullrequests"
        pr_resp = httpx.post(
            create_url,
            json={
                "title": pr_title,
                "description": pr_description,
                "source": {"branch": {"name": branch}},
                "destination": {"branch": {"name": "main"}},
                "close_source_branch": True,
            },
            **self._auth_kwargs(),
        )
        pr_resp.raise_for_status()
        return pr_resp.json().get("links", {}).get("html", {}).get("href", "")


class GitHubAdapter(PlatformAdapter):
    """GitHub API adapter (comment acknowledgment only - no resolution)."""

    def __init__(self, token: str, repo: str = ""):
        self.token = token
        self.repo = repo
        self.base_url = "https://api.github.com"
        self.webhook_secret = ""
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }
    
    def post_comment(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        file_path: str,
        line_number: int,
        body: str,
        commit_sha: str,
        replacement_line_count: int = 1,
    ) -> tuple[str, Optional[str]]:
        """Post review comment to GitHub PR.

        For multi-line suggestions (replacement_line_count > 1), includes start_line
        and line to span the full range.
        """
        url = f"{self.base_url}/repos/{repo_owner}/{repo_name}/pulls/{pr_number}/comments"

        payload = {
            "body": body,
            "commit_id": commit_sha,
            "path": file_path,
            "side": "RIGHT"
        }

        # GitHub requires start_line < line for multi-line ranges; single-line omits start_line.
        # rlc > 1 guarantees line = start_line + (rlc-1) >= start_line + 1, so start_line < line always.
        if replacement_line_count > 1:
            payload["start_line"] = line_number
            payload["start_side"] = "RIGHT"
            payload["line"] = line_number + replacement_line_count - 1
        else:
            payload["line"] = line_number

        response = httpx.post(url, json=payload, headers=self.headers)
        response.raise_for_status()

        data = response.json()
        return (str(data['id']), data.get('pull_request_review_id'))
    
    def resolve_comment(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        comment_id: str,
        thread_id: Optional[str] = None
    ) -> bool:
        """Resolve a GitHub PR review thread via GraphQL (REVUE-119 AC12).

        Looks up thread_id from comment_id via fetch_review_thread_ids,
        then calls resolve_thread(). Returns False gracefully if thread not found.
        """
        threads = self.fetch_review_thread_ids(pr_number, repo_owner, repo_name)
        for t in threads:
            if str(t.get("comment_id")) == str(comment_id):
                return self.resolve_thread(t["thread_id"])
        return False

    def post_reply(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        comment_id: str,
        thread_id: Optional[str],
        body: str
    ) -> str:
        """Post reply to GitHub PR comment."""
        url = f"{self.base_url}/repos/{repo_owner}/{repo_name}/pulls/{pr_number}/comments/{comment_id}/replies"

        response = httpx.post(
            url,
            json={"body": body},
            headers=self.headers
        )
        response.raise_for_status()

        return str(response.json()['id'])

    def get_diff(self, pr_id: int) -> list[FileChange]:
        raise NotImplementedError

    def set_pr_status(self, commit_sha: str, state: str, description: str = "") -> bool:
        raise NotImplementedError

    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        raise NotImplementedError

    @staticmethod
    def parse_webhook_event(
        headers: dict[str, str], payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        raise NotImplementedError

    def post_summary_comment(self, pr_id: int, body: str) -> "str | None":
        raise NotImplementedError

    def update_comment(self, pr_id: int, comment_id: str, body: str) -> bool:
        raise NotImplementedError

    def get_existing_comments(self, pr_id: int) -> list[dict]:
        raise NotImplementedError

    def get_all_pr_comments(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
    ) -> list[dict]:
        """Fetch all GitHub PR review comments and normalise to common shape.

        Returns a list of dicts with keys:
        - id: comment ID
        - inline: {"path": ..., "to": ...} (all are inline review comments)
        - parent: {"id": in_reply_to_id} or None
        - content: {"raw": body}
        - resolution: None (GitHub tracks resolution via threads, not comment state)
        """
        url = f"{self.base_url}/repos/{repo_owner}/{repo_name}/pulls/{pr_number}/comments?per_page=100"

        comments = []
        while url:
            response = httpx.get(url, headers=self.headers)
            response.raise_for_status()

            batch = response.json()
            for c in batch:
                normalised = {
                    "id": c["id"],
                    "inline": {
                        "path": c.get("path", ""),
                        "to": c.get("line") or c.get("original_line") or 0,
                    },
                    "parent": {"id": c["in_reply_to_id"]} if c.get("in_reply_to_id") else None,
                    "content": {"raw": c["body"]},
                    "resolution": None,
                }
                comments.append(normalised)

            # Handle pagination via Link header
            url = None
            if "link" in response.headers:
                import re
                links = response.headers.get("link", "")
                match = re.search(r'<([^>]+)>;\s*rel="next"', links)
                if match:
                    url = match.group(1)

        return comments

    def get_comment_replies(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        comment_id: str
    ) -> list[dict]:
        """Fetch replies to GitHub comment (placeholder)."""
        # GitHub API structure for replies needs investigation
        return []

    def is_comment_resolved(
        self,
        repo_owner: str,
        repo_name: str,
        comment_id: str
    ) -> bool:
        """GitHub resolution check (not accessible via PAT)."""
        return False

    # ── GraphQL helpers (REVUE-119 AC12) ──────────────────────────────────

    def _graphql(self, query: str, variables: dict) -> dict:
        """Execute a GraphQL query against the GitHub API.

        Raises RuntimeError if response contains 'errors' key.
        Returns the response JSON dict.
        """
        url = "https://api.github.com/graphql"
        headers = self.headers.copy()

        response = httpx.post(
            url,
            json={"query": query, "variables": variables},
            headers=headers
        )
        response.raise_for_status()

        data = response.json()
        if "errors" in data:
            raise RuntimeError(f"GraphQL error: {data['errors']}")
        return data

    _REVIEW_THREADS_QUERY = """
        query($owner: String!, $name: String!, $pr: Int!, $cursor: String) {
          repository(owner: $owner, name: $name) {
            pullRequest(number: $pr) {
              reviewThreads(first: 100, after: $cursor) {
                nodes {
                  id
                  isResolved
                  comments(first: 1) {
                    nodes {
                      databaseId
                    }
                  }
                }
                pageInfo {
                  hasNextPage
                  endCursor
                }
              }
            }
          }
        }
        """

    def _fetch_review_threads_page(
        self,
        pr_number: int,
        repo_owner: str,
        repo_name: str,
        cursor: "str | None",
    ) -> dict:
        """Execute one GraphQL page request and return the reviewThreads dict.

        Returns the raw ``reviewThreads`` object (``nodes`` + ``pageInfo``).
        """
        variables = {
            "owner": repo_owner,
            "name": repo_name,
            "pr": pr_number,
            "cursor": cursor,
        }
        result = self._graphql(self._REVIEW_THREADS_QUERY, variables)
        return (
            result.get("data", {})
            .get("repository", {})
            .get("pullRequest", {})
            .get("reviewThreads", {})
        )

    def fetch_review_thread_ids(
        self,
        pr_number: int,
        repo_owner: str,
        repo_name: str,
        max_pages: int = 50,
    ) -> list[dict]:
        """Fetch all review threads for a PR, paginating past the 100-node limit.

        Stops after max_pages to prevent infinite loops from stuck API cursors.
        Returns list of dicts:
        [{"thread_id": "PRRT_xxx", "comment_id": int, "is_resolved": bool}, ...]
        """
        normalised: list[dict] = []
        cursor = None
        for _ in range(max_pages):
            page = self._fetch_review_threads_page(pr_number, repo_owner, repo_name, cursor)
            for t in page.get("nodes", []):
                comment_id = t.get("comments", {}).get("nodes", [{}])[0].get("databaseId")
                if comment_id is not None:
                    normalised.append({
                        "thread_id": t["id"],
                        "comment_id": comment_id,
                        "is_resolved": t.get("isResolved", False),
                    })
            page_info = page.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            next_cursor = page_info.get("endCursor")
            if next_cursor is None:
                _log.warning(
                    "fetch_review_thread_ids: null endCursor with hasNextPage=True — stopping pagination",
                )
                break
            if next_cursor == cursor:
                _log.warning(
                    "fetch_review_thread_ids: stuck cursor '%s' — stopping pagination",
                    cursor,
                )
                break
            cursor = next_cursor
        else:
            _log.warning(
                "fetch_review_thread_ids: max_pages limit (%d) reached — stopping pagination",
                max_pages,
            )
        return normalised

    def resolve_thread(self, thread_id: str) -> bool:
        """Resolve a review thread via GraphQL mutation (idempotent).

        Returns True on success, False on failure.
        """
        mutation = """
        mutation($threadId: ID!) {
          resolveReviewThread(input: {threadId: $threadId}) {
            thread {
              isResolved
            }
          }
        }
        """
        variables = {"threadId": thread_id}

        try:
            result = self._graphql(mutation, variables)
            resolved = result.get("data", {}).get("resolveReviewThread", {}).get("thread", {}).get("isResolved", False)
            if not resolved:
                _log.warning("resolve_thread: mutation returned isResolved=False for thread %s", thread_id)
            return resolved
        except Exception as exc:
            _log.warning("resolve_thread: GraphQL mutation failed for thread %s: %s", thread_id, exc)
            return False

    def get_pr_template(self, repo_owner: str, repo_name: str) -> Optional[str]:
        """Fetch PR template from GitHub, trying multiple paths in order.

        Tries: .github/pull_request_template.md → pull_request_template.md → docs/pull_request_template.md
        Returns decoded content or None if not found.
        """
        import base64

        paths = [
            ".github/pull_request_template.md",
            "pull_request_template.md",
            "docs/pull_request_template.md",
        ]

        for path in paths:
            try:
                url = f"{self.base_url}/repos/{repo_owner}/{repo_name}/contents/{path}"
                response = httpx.get(url, headers=self.headers)
                response.raise_for_status()

                data = response.json()
                if data.get("encoding") == "base64":
                    content = base64.b64decode(data["content"]).decode("utf-8")
                else:
                    content = data.get("content", "")
                return content.rstrip()
            except Exception:
                continue

        return None


class GitLabAdapter(PlatformAdapter):
    """GitLab API adapter."""

    def __init__(self, token: str, base_url: str = "https://gitlab.com"):
        self.token = token
        self.base_url = f"{base_url}/api/v4"
        self.headers = {"PRIVATE-TOKEN": token}
        self.webhook_secret = ""
    
    def _get_mr_version_shas(self, project_id: str, pr_number: int) -> tuple[str, str, str]:
        """Return (base_commit_sha, start_commit_sha, head_commit_sha) from the latest MR version."""
        from revue.core.vcs_adapter import extract_gitlab_version_shas
        versions_url = f"{self.base_url}/projects/{project_id}/merge_requests/{pr_number}/versions"
        resp = httpx.get(versions_url, headers=self.headers)
        resp.raise_for_status()
        return extract_gitlab_version_shas(resp.json())

    def post_comment(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        file_path: str,
        line_number: int,
        body: str,
        commit_sha: str
    ) -> tuple[str, Optional[str]]:
        """Post discussion note to GitLab MR."""
        project_id = f"{repo_owner}%2F{repo_name}"

        base_sha, start_sha, head_sha = self._get_mr_version_shas(project_id, pr_number)

        url = f"{self.base_url}/projects/{project_id}/merge_requests/{pr_number}/discussions"

        payload = {
            "body": body,
            "position": {
                "base_sha": base_sha,
                "head_sha": head_sha,
                "start_sha": start_sha,
                "position_type": "text",
                "new_path": file_path,
                "new_line": line_number
            }
        }

        response = httpx.post(url, json=payload, headers=self.headers)
        response.raise_for_status()

        data = response.json()
        discussion_id = data['id']
        note_id = str(data['notes'][0]['id'])

        return (note_id, discussion_id)
    
    def resolve_comment(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        comment_id: str,
        thread_id: Optional[str] = None
    ) -> bool:
        """Resolve GitLab discussion thread."""
        if not thread_id:
            return False

        return self.resolve_discussion(repo_owner, repo_name, pr_number, thread_id)
    
    def resolve_discussion(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        discussion_id: str
    ) -> bool:
        """Resolve GitLab discussion via PUT with JSON body (AC12)."""
        project_id = f"{repo_owner}%2F{repo_name}"
        url = f"{self.base_url}/projects/{project_id}/merge_requests/{pr_number}/discussions/{discussion_id}"
        response = httpx.put(url, json={"resolved": True}, headers=self.headers)
        return response.status_code == 200

    def post_reply(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        comment_id: str,
        thread_id: Optional[str],
        body: str
    ) -> str:
        """Post a reply note to a GitLab discussion."""
        if not thread_id:
            _log.error("post_reply: thread_id is None for comment %s — skipping", comment_id)
            return ""
        project_id = f"{repo_owner}%2F{repo_name}"
        url = f"{self.base_url}/projects/{project_id}/merge_requests/{pr_number}/discussions/{thread_id}/notes"
        response = httpx.post(url, json={"body": body}, headers=self.headers)
        response.raise_for_status()
        return str(response.json()["id"])

    def _fetch_discussions(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
    ) -> list[dict]:
        """Fetch all MR discussions with pagination (X-Next-Page header)."""
        project_id = f"{repo_owner}%2F{repo_name}"
        base_url = f"{self.base_url}/projects/{project_id}/merge_requests/{pr_number}/discussions"
        url: Optional[str] = base_url
        discussions: list[dict] = []
        while url:
            response = httpx.get(url, headers=self.headers)
            response.raise_for_status()
            discussions.extend(response.json())
            next_page = response.headers.get("X-Next-Page", "")
            url = f"{base_url}?page={next_page}" if next_page else None
        return discussions

    def get_all_pr_comments(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
    ) -> list[dict]:
        """Fetch all non-system MR notes, normalised to the common comment shape.

        Shape: {id, thread_id, content: {raw}, parent: {id} | None, inline: {path, to}}
        """
        discussions = self._fetch_discussions(repo_owner, repo_name, pr_number)
        result: list[dict] = []
        for disc in discussions:
            disc_id = disc["id"]
            first_note_id: Optional[int] = None  # ID of first non-system note in this discussion
            for note in disc.get("notes", []):
                if note.get("system"):
                    continue
                position = note.get("position") or {}
                note_id = note["id"]
                # GitLab always returns in_reply_to_id=None even for reply notes.
                # Use position within the discussion instead: first non-system note
                # is the root; all subsequent ones are replies to it.
                if first_note_id is None:
                    parent = None
                    first_note_id = note_id
                else:
                    parent = {"id": first_note_id}
                result.append({
                    "id": note_id,
                    "thread_id": disc_id,
                    "content": {"raw": note.get("body", "")},
                    "parent": parent,
                    "inline": {
                        "path": position.get("new_path", ""),
                        "to": position.get("new_line", 0),
                    },
                    "resolution": None,
                })
        return result

    def get_comment_replies(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        comment_id: str
    ) -> list[dict]:
        """Return normalised reply notes for the discussion containing *comment_id*."""
        all_comments = self.get_all_pr_comments(repo_owner, repo_name, pr_number)
        try:
            root_id = int(comment_id)
        except ValueError:
            _log.warning("get_comment_replies: non-integer comment_id %r — returning []", comment_id)
            return []
        return [c for c in all_comments if (c.get("parent") or {}).get("id") == root_id]

    def get_pr_template(self, repo_owner: str, repo_name: str) -> Optional[str]:
        """Fetch MR description template from GitLab project templates (AC13).

        Tries 'Default' first; falls back to the first listed template.
        Returns None when no templates exist.
        """
        project_id = f"{repo_owner}%2F{repo_name}"
        base = f"{self.base_url}/projects/{project_id}/templates/merge_requests"
        try:
            resp = httpx.get(f"{base}/Default", headers=self.headers)
            resp.raise_for_status()
            return resp.json().get("content")
        except httpx.HTTPStatusError:
            pass
        # Fallback: list templates and use the first one
        try:
            list_resp = httpx.get(base, headers=self.headers)
            list_resp.raise_for_status()
            templates = list_resp.json()
            if not templates:
                return None
            name = templates[0]["name"]
            content_resp = httpx.get(f"{base}/{name}", headers=self.headers)
            content_resp.raise_for_status()
            return content_resp.json().get("content")
        except httpx.HTTPStatusError:
            return None

    def is_comment_resolved(
        self,
        repo_owner: str,
        repo_name: str,
        comment_id: str
    ) -> bool:
        """Check GitLab discussion resolution (placeholder)."""
        return False

    def get_diff(self, pr_id: int) -> list[FileChange]:
        raise NotImplementedError

    def set_pr_status(self, commit_sha: str, state: str, description: str = "") -> bool:
        raise NotImplementedError

    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        raise NotImplementedError

    @staticmethod
    def parse_webhook_event(
        headers: dict[str, str], payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        raise NotImplementedError

    def post_summary_comment(self, pr_id: int, body: str) -> "str | None":
        raise NotImplementedError

    def update_comment(self, pr_id: int, comment_id: str, body: str) -> bool:
        raise NotImplementedError

    def get_existing_comments(self, pr_id: int) -> list[dict]:
        raise NotImplementedError

    def ensure_lessons_pr(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        branch: str,
        revue_yml_content: str,
        commit_message: str,
        pr_title: str,
        pr_description: str,
    ) -> str:
        """Create or update a GitLab lessons MR. Returns MR web URL.

        Steps:
          1. Ensure the lessons branch exists (create from main if not).
          2. Commit .revue.yml to the branch (POST if new, PUT if exists).
          3. Find an open MR for the branch or create one.
        """
        project_id = f"{repo_owner}%2F{repo_name}"
        encoded_branch = urllib.parse.quote(branch, safe="")

        # 1. Ensure branch exists
        branch_url = f"{self.base_url}/projects/{project_id}/repository/branches/{encoded_branch}"
        branch_resp = httpx.get(branch_url, headers=self.headers)
        if branch_resp.status_code == 404:
            httpx.post(
                f"{self.base_url}/projects/{project_id}/repository/branches",
                json={"branch": branch, "ref": "main"},
                headers=self.headers,
            ).raise_for_status()
        elif branch_resp.status_code != 200:
            branch_resp.raise_for_status()

        # 2. Commit .revue.yml (POST if new, PUT if already on branch)
        encoded_file = urllib.parse.quote(".revue.yml", safe="")
        file_url = f"{self.base_url}/projects/{project_id}/repository/files/{encoded_file}"
        file_check = httpx.get(file_url, headers=self.headers, params={"ref": branch})
        if file_check.status_code == 404:
            httpx.post(
                file_url,
                json={"branch": branch, "commit_message": commit_message, "content": revue_yml_content},
                headers=self.headers,
            ).raise_for_status()
        else:
            file_check.raise_for_status()
            last_commit_id = file_check.json().get("last_commit_id", "")
            httpx.put(
                file_url,
                json={
                    "branch": branch,
                    "commit_message": commit_message,
                    "content": revue_yml_content,
                    "last_commit_id": last_commit_id,
                },
                headers=self.headers,
            ).raise_for_status()

        # 3. Find or create MR
        mrs_resp = httpx.get(
            f"{self.base_url}/projects/{project_id}/merge_requests",
            headers=self.headers,
            params={"source_branch": branch, "state": "opened"},
        )
        mrs_resp.raise_for_status()
        mrs = mrs_resp.json()
        if mrs:
            return mrs[0]["web_url"]

        mr_resp = httpx.post(
            f"{self.base_url}/projects/{project_id}/merge_requests",
            json={
                "source_branch": branch,
                "target_branch": "main",
                "title": pr_title,
                "description": pr_description,
            },
            headers=self.headers,
        )
        mr_resp.raise_for_status()
        return mr_resp.json()["web_url"]


def get_platform_adapter(platform: Platform) -> PlatformAdapter:
    """Factory function to get the correct platform adapter."""
    if platform == Platform.BITBUCKET:
        username = os.environ.get('BITBUCKET_USERNAME')
        password = os.environ.get('BITBUCKET_API_TOKEN')
        if not username or not password:
            raise ValueError("BITBUCKET_USERNAME and BITBUCKET_API_TOKEN must be set")
        return BitbucketAdapter(username, password)
    
    elif platform == Platform.GITHUB:
        token = os.environ.get('GITHUB_TOKEN')
        if not token:
            raise ValueError("GITHUB_TOKEN must be set")
        return GitHubAdapter(token)
    
    elif platform == Platform.GITLAB:
        token = os.environ.get('GITLAB_TOKEN')
        if not token:
            raise ValueError("GITLAB_TOKEN must be set")
        return GitLabAdapter(token)
    
    else:
        raise ValueError(f"Unsupported platform: {platform}")
