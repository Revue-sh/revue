"""Platform-specific API adapters for comment resolution."""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Optional

import httpx

from .models import Platform


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
        comment_id: str,
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
        comment_id: str,
        thread_id: Optional[str],
        body: str
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
        comment_id: str
    ) -> list[dict]:
        """
        Fetch all replies to a comment.
        
        Returns:
            List of reply dicts with 'body', 'author', 'created_at'
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


class BitbucketAdapter(PlatformAdapter):
    """Bitbucket Cloud API adapter."""
    
    def __init__(self, username: str, app_password: str):
        self.username = username
        self.app_password = app_password
        self.base_url = "https://api.bitbucket.org/2.0"
    
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
        
        response = httpx.post(
            url,
            json=payload,
            auth=(self.username, self.app_password)
        )
        response.raise_for_status()
        
        data = response.json()
        return (str(data['id']), None)  # Bitbucket doesn't have thread IDs
    
    def resolve_comment(
        self,
        repo_owner: str,
        repo_name: str,
        comment_id: str,
        thread_id: Optional[str] = None
    ) -> bool:
        """Resolve comment via Bitbucket API."""
        url = f"{self.base_url}/repositories/{repo_owner}/{repo_name}/pullrequests/comments/{comment_id}"
        
        response = httpx.put(
            url,
            json={"resolved": True},
            auth=(self.username, self.app_password)
        )
        
        return response.status_code == 200
    
    def post_reply(
        self,
        repo_owner: str,
        repo_name: str,
        comment_id: str,
        thread_id: Optional[str],
        body: str
    ) -> str:
        """Post reply to Bitbucket comment."""
        url = f"{self.base_url}/repositories/{repo_owner}/{repo_name}/pullrequests/comments/{comment_id}"
        
        response = httpx.post(
            url,
            json={"content": {"raw": body}},
            auth=(self.username, self.app_password)
        )
        response.raise_for_status()
        
        return str(response.json()['id'])
    
    def get_comment_replies(
        self,
        repo_owner: str,
        repo_name: str,
        comment_id: str
    ) -> list[dict]:
        """Fetch replies to Bitbucket comment."""
        url = f"{self.base_url}/repositories/{repo_owner}/{repo_name}/pullrequests/comments/{comment_id}"
        
        response = httpx.get(url, auth=(self.username, self.app_password))
        response.raise_for_status()
        
        # Bitbucket nests replies differently - adjust based on actual API
        # For now, return empty list (implement based on actual response structure)
        return []
    
    def is_comment_resolved(
        self,
        repo_owner: str,
        repo_name: str,
        comment_id: str
    ) -> bool:
        """Check if Bitbucket comment is resolved."""
        url = f"{self.base_url}/repositories/{repo_owner}/{repo_name}/pullrequests/comments/{comment_id}"
        
        response = httpx.get(url, auth=(self.username, self.app_password))
        response.raise_for_status()
        
        return response.json().get('resolved', False)


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
        comment_id: str,
        thread_id: Optional[str] = None
    ) -> bool:
        """
        GitHub doesn't support resolution via PAT.
        
        Returns False to trigger fallback to comment acknowledgment.
        """
        return False
    
    def post_reply(
        self,
        repo_owner: str,
        repo_name: str,
        comment_id: str,
        thread_id: Optional[str],
        body: str
    ) -> str:
        """Post reply to GitHub PR comment."""
        url = f"{self.base_url}/repos/{repo_owner}/{repo_name}/pulls/comments/{comment_id}/replies"
        
        response = httpx.post(
            url,
            json={"body": body},
            headers=self.headers
        )
        response.raise_for_status()
        
        return str(response.json()['id'])
    
    def get_comment_replies(
        self,
        repo_owner: str,
        repo_name: str,
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


class GitLabAdapter(PlatformAdapter):
    """GitLab API adapter."""
    
    def __init__(self, token: str, base_url: str = "https://gitlab.com"):
        self.token = token
        self.base_url = f"{base_url}/api/v4"
        self.headers = {"PRIVATE-TOKEN": token}
    
    def _get_mr_version_shas(self, project_id: str, pr_number: int) -> tuple[str, str, str]:
        """Return (base_commit_sha, start_commit_sha, head_commit_sha) from the latest MR version.

        GitLab's discussions API requires these exact SHAs — computing them from the commits
        list produces wrong values and causes HTTP 400 rejections.
        """
        versions_url = f"{self.base_url}/projects/{project_id}/merge_requests/{pr_number}/versions"
        resp = httpx.get(versions_url, headers=self.headers)
        resp.raise_for_status()
        versions = resp.json()
        if not versions:
            raise ValueError(f"No MR versions found for MR {pr_number}")
        latest = versions[0]  # most recent version is first
        return latest["base_commit_sha"], latest["start_commit_sha"], latest["head_commit_sha"]

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
        comment_id: str,
        thread_id: Optional[str] = None
    ) -> bool:
        """Resolve GitLab discussion thread."""
        if not thread_id:
            return False
        
        project_id = f"{repo_owner}%2F{repo_name}"
        
        # Extract MR number from context (this is a design limitation - need to pass it)
        # For now, return False and handle in service layer
        # TODO: Refactor to pass pr_number to resolve_comment
        return False
    
    def resolve_discussion(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        discussion_id: str
    ) -> bool:
        """Resolve GitLab discussion (correct API call)."""
        project_id = f"{repo_owner}%2F{repo_name}"
        url = f"{self.base_url}/projects/{project_id}/merge_requests/{pr_number}/discussions/{discussion_id}?resolved=true"
        
        response = httpx.put(url, headers=self.headers)
        return response.status_code == 200
    
    def post_reply(
        self,
        repo_owner: str,
        repo_name: str,
        comment_id: str,
        thread_id: Optional[str],
        body: str
    ) -> str:
        """Post reply to GitLab discussion (placeholder)."""
        # Implement based on actual API needs
        return ""
    
    def get_comment_replies(
        self,
        repo_owner: str,
        repo_name: str,
        comment_id: str
    ) -> list[dict]:
        """Fetch GitLab discussion replies (placeholder)."""
        return []
    
    def is_comment_resolved(
        self,
        repo_owner: str,
        repo_name: str,
        comment_id: str
    ) -> bool:
        """Check GitLab discussion resolution (placeholder)."""
        return False


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
