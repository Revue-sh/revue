#!/usr/bin/env python3
"""Bitbucket Cloud VCS adapter — implements VCSAdapter for the Bitbucket REST API v2.

Uses only ``urllib.request`` (stdlib) for HTTP calls.  Authenticates via
Basic Auth (username + API token).  Webhook signatures use HMAC-SHA256
(same as GitHub) via the ``X-Hub-Signature`` header.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from revue.core.diff_parser import parse_diff
from revue.core.models import FileChange
from revue.core.vcs_adapter import DiffPosition

_LOG = logging.getLogger(__name__)

_BB_API = "https://api.bitbucket.org/2.0"


class BitbucketAdapter:
    """Implements VCSAdapter for Bitbucket Cloud using the Bitbucket REST API v2.

    Auth: HTTP Basic Auth — ``username`` and ``api_token`` (API token replaces
    app passwords as of September 2025).

    Args:
        api_token:      Bitbucket API token (created at bitbucket.org/account/settings/api-tokens).
        username:       Bitbucket account username (or email used for auth).
        workspace:      Bitbucket workspace slug (e.g. ``"cbscd"``).
        repo_slug:      Repository slug (e.g. ``"revue"``).
        webhook_secret: Secret configured on the Bitbucket webhook for HMAC-SHA256
                        signature verification.  Leave empty to skip verification.
    """

    def __init__(
        self,
        api_token: str,
        username: str,
        workspace: str,
        repo_slug: str,
        webhook_secret: str = "",
    ) -> None:
        self._api_token = api_token
        self._username = username
        self._workspace = workspace
        self._repo_slug = repo_slug
        self._webhook_secret = webhook_secret

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _auth_header(self) -> str:
        """Return a Basic Auth header value."""
        credentials = f"{self._username}:{self._api_token}"
        encoded = base64.b64encode(credentials.encode()).decode()
        return f"Basic {encoded}"

    def _repo_base(self) -> str:
        """Return the repository-scoped API prefix."""
        ws = urllib.parse.quote(self._workspace, safe="")
        slug = urllib.parse.quote(self._repo_slug, safe="")
        return f"{_BB_API}/repositories/{ws}/{slug}"

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        accept: str = "application/json",
    ) -> Any:
        """Issue an HTTP request and return parsed JSON (or raw text for diffs)."""
        url = f"{self._repo_base()}{path}"
        data = json.dumps(body).encode() if body is not None else None
        headers = {
            "Authorization": self._auth_header(),
            "Content-Type": "application/json",
            "Accept": accept,
        }
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as resp:
                raw = resp.read().decode()
                if accept == "application/json":
                    return json.loads(raw)
                return raw
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise ValueError(
                    f"Bitbucket auth error {exc.code}: {exc.reason}"
                ) from exc
            if exc.code == 404:
                raise RuntimeError(
                    f"Bitbucket resource not found: {url}"
                ) from exc
            if exc.code >= 500:
                raise RuntimeError(
                    f"Bitbucket server error {exc.code}: {exc.reason}"
                ) from exc
            raise

    def _paginate(self, path: str) -> list[dict[str, Any]]:
        """Fetch all pages of a Bitbucket paginated endpoint.

        Bitbucket uses a ``next`` URL in the response body for pagination.
        Returns the combined ``values`` list from all pages.
        """
        results: list[dict[str, Any]] = []
        url: str | None = f"{self._repo_base()}{path}"
        headers = {
            "Authorization": self._auth_header(),
            "Accept": "application/json",
        }
        while url:
            req = urllib.request.Request(url, headers=headers, method="GET")
            try:
                with urllib.request.urlopen(req) as resp:
                    data = json.loads(resp.read().decode())
            except urllib.error.HTTPError as exc:
                _LOG.warning("_paginate error at %s: %s", url, exc)
                break
            results.extend(data.get("values", []))
            url = data.get("next")
        return results

    # ------------------------------------------------------------------
    # VCSAdapter interface
    # ------------------------------------------------------------------

    def get_diff(self, pr_id: int) -> list[FileChange]:
        """Fetch PR diff from Bitbucket and parse into FileChange objects.

        GET /2.0/repositories/{ws}/{slug}/pullrequests/{id}/diff
        Returns raw unified diff text; we reuse diff_parser.parse_diff().
        """
        try:
            raw_diff = self._request(
                "GET",
                f"/pullrequests/{pr_id}/diff",
                accept="text/plain",
            )
            return parse_diff(raw_diff)
        except Exception as exc:
            _LOG.warning("get_diff failed for PR %s: %s", pr_id, exc)
            return []

    def post_review_comment(
        self, pr_id: int, position: DiffPosition, body: str
    ) -> bool:
        """Post an inline comment on a PR.

        POST /2.0/repositories/{ws}/{slug}/pullrequests/{id}/comments
        Bitbucket inline comments use an ``inline`` key with ``path`` and ``to``
        (the new-file line number).
        """
        payload: dict[str, Any] = {
            "content": {"raw": body},
            "inline": {
                "path": position.file_path,
                "to": position.line_number,
            },
        }
        try:
            self._request("POST", f"/pullrequests/{pr_id}/comments", body=payload)
            return True
        except Exception as exc:
            _LOG.warning("post_review_comment failed for PR %s: %s", pr_id, exc)
            return False

    # Backward-compat alias
    post_inline_comment = post_review_comment

    def post_summary_comment(self, pr_id: int, body: str) -> bool:
        """Post a top-level PR comment (not inline).

        POST /2.0/repositories/{ws}/{slug}/pullrequests/{id}/comments
        Omitting the ``inline`` key makes it a general comment.
        """
        payload: dict[str, Any] = {"content": {"raw": body}}
        try:
            self._request("POST", f"/pullrequests/{pr_id}/comments", body=payload)
            return True
        except Exception as exc:
            _LOG.warning("post_summary_comment failed for PR %s: %s", pr_id, exc)
            return False

    def get_existing_comments(self, pr_id: int) -> list[dict]:
        """Fetch all comments on a PR (all pages).

        GET /2.0/repositories/{ws}/{slug}/pullrequests/{id}/comments
        Returns a flat list of comment objects.  Returns [] on error.
        """
        try:
            return self._paginate(f"/pullrequests/{pr_id}/comments")
        except Exception as exc:
            _LOG.warning("get_existing_comments failed for PR %s: %s", pr_id, exc)
            return []

    def resolve_position(
        self, file_path: str, line_number: int, diff: str
    ) -> DiffPosition:
        """Return a DiffPosition for Bitbucket.

        Bitbucket inline comments use file path + line number directly —
        no sequential position index or line_code needed.
        """
        return DiffPosition(
            file_path=file_path,
            line_number=line_number,
            side="RIGHT",
        )

    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        """Verify Bitbucket webhook signature via HMAC-SHA256.

        Bitbucket sends ``X-Hub-Signature: sha256=<hex>`` (same convention
        as GitHub).  Returns False immediately if no webhook_secret is set.

        Args:
            payload:   Raw request body bytes.
            signature: Value of ``X-Hub-Signature`` header (``sha256=<hex>``).

        Returns:
            True if the signature is valid, False otherwise.
        """
        if not self._webhook_secret:
            return False
        expected = (
            "sha256="
            + hmac.new(
                self._webhook_secret.encode(),
                payload,
                hashlib.sha256,
            ).hexdigest()
        )
        return hmac.compare_digest(expected, signature)

    # ------------------------------------------------------------------
    # Bitbucket-specific extras
    # ------------------------------------------------------------------

    def set_pr_status(self, commit_sha: str, state: str, description: str = "") -> bool:
        """Post a build/review status against a commit (Bitbucket Commit Status API).

        This is how we surface pass/fail in Bitbucket Pipelines — equivalent
        to GitHub check runs and GitLab pipeline stages.

        POST /2.0/repositories/{ws}/{slug}/commit/{sha}/statuses/build

        Args:
            commit_sha:  The HEAD commit SHA of the PR.
            state:       ``"SUCCESSFUL"``, ``"FAILED"``, or ``"INPROGRESS"``.
            description: Short human-readable description shown in the UI.

        Returns:
            True on success, False on error.
        """
        payload: dict[str, Any] = {
            "key": "revue-io",
            "state": state,
            "name": "Revue.io AI Review",
            "description": description or f"Revue.io review {state.lower()}",
            "url": "https://revue-io.fly.dev",
        }
        sha = urllib.parse.quote(commit_sha, safe="")
        url = f"{self._repo_base()}/commit/{sha}/statuses/build"
        data = json.dumps(payload).encode()
        headers = {
            "Authorization": self._auth_header(),
            "Content-Type": "application/json",
        }
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req) as resp:
                resp.read()
            return True
        except Exception as exc:
            _LOG.warning("set_pr_status failed for commit %s: %s", commit_sha, exc)
            return False

    @staticmethod
    def parse_webhook_event(
        headers: dict[str, str], payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Parse a Bitbucket webhook event.

        Returns dict with ``{event_type, pr_id, workspace, repo_slug, action}``
        for pullrequest:created / pullrequest:updated events.
        Returns ``None`` for everything else.

        Bitbucket webhook event key is in the ``X-Event-Key`` header.
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
