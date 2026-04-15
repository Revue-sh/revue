"""Platform-specific API adapters for comment resolution."""
from __future__ import annotations

import logging
import os
import urllib.parse
from abc import ABC, abstractmethod
from typing import Optional

import httpx

from .models import Platform

_log = logging.getLogger(__name__)


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


class BitbucketAdapter(PlatformAdapter):
    """Bitbucket Cloud API adapter."""

    # Bitbucket Repository/Workspace Access Tokens (OAuth) start with this prefix.
    # App Passwords do not — they use HTTP Basic Auth instead.
    _BEARER_PREFIX = "ATCTT3"

    def __init__(self, username: str, app_password: str):
        self.username = username
        self.app_password = app_password
        self.base_url = "https://api.bitbucket.org/2.0"
        # Repository/Workspace Access Tokens must be sent as Bearer, not Basic Auth.
        self._is_bearer = app_password.startswith(self._BEARER_PREFIX)

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
            _log.exception("[REVUE-112] Failed to search for existing Bitbucket lessons PR")

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
    
    def __init__(self, token: str):
        self.token = token
        self.base_url = "https://api.github.com"
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
        commit_sha: str
    ) -> tuple[str, Optional[str]]:
        """Post review comment to GitHub PR."""
        url = f"{self.base_url}/repos/{repo_owner}/{repo_name}/pulls/{pr_number}/comments"
        
        payload = {
            "body": body,
            "commit_id": commit_sha,
            "path": file_path,
            "line": line_number,
            "side": "RIGHT"
        }
        
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

    def fetch_review_thread_ids(
        self,
        pr_number: int,
        repo_owner: str,
        repo_name: str,
    ) -> list[dict]:
        """Fetch all review threads for a PR via GraphQL.

        Returns list of dicts:
        [{"thread_id": "PRRT_xxx", "comment_id": int, "is_resolved": bool}, ...]
        """
        query = """
        query($owner: String!, $name: String!, $pr: Int!) {
          repository(owner: $owner, name: $name) {
            pullRequest(number: $pr) {
              reviewThreads(first: 100) {
                nodes {
                  id
                  isResolved
                  comments(first: 1) {
                    nodes {
                      databaseId
                    }
                  }
                }
              }
            }
          }
        }
        """
        variables = {
            "owner": repo_owner,
            "name": repo_name,
            "pr": pr_number,
        }

        result = self._graphql(query, variables)
        threads = result.get("data", {}).get("repository", {}).get("pullRequest", {}).get("reviewThreads", {}).get("nodes", [])

        normalised = []
        for t in threads:
            comment_id = t.get("comments", {}).get("nodes", [{}])[0].get("databaseId")
            if comment_id is not None:
                normalised.append({
                    "thread_id": t["id"],
                    "comment_id": comment_id,
                    "is_resolved": t.get("isResolved", False),
                })
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
