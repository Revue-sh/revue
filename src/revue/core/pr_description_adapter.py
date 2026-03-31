"""
PR Description Adapter — fetch and parse PR descriptions from VCS platforms.

Extends existing VCS adapters (Bitbucket, GitHub, GitLab) with PR description
fetching capabilities for smart context filtering in REVUE-84.
"""
from __future__ import annotations

import os
from typing import Optional
from dataclasses import dataclass

try:
    import httpx as _httpx
except ImportError:  # pragma: no cover
    _httpx = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class PRDescription:
    """Parsed PR description with structured sections."""
    title: str
    raw_description: str
    summary: str = ""
    background: str = ""
    changes: str = ""
    acceptance_criteria: str = ""
    testing: str = ""
    out_of_scope: str = ""
    dependencies: str = ""
    
    @classmethod
    def parse(cls, title: str, body: str) -> PRDescription:
        """Parse a PR description into structured sections.
        
        Looks for common section markers:
        - ## Summary / ## Background
        - ## Changes / ## What Changed
        - ## Acceptance Criteria / ## AC
        - ## Testing / ## Test Plan
        - ## Out of Scope
        - ## Dependencies
        """
        sections = {
            "summary": "",
            "background": "",
            "changes": "",
            "acceptance_criteria": "",
            "testing": "",
            "out_of_scope": "",
            "dependencies": "",
        }
        
        lines = body.split("\n")
        current_section = None
        current_content = []
        
        for line in lines:
            line_lower = line.lower().strip()
            
            # Section detection
            if line_lower.startswith("## ") or line_lower.startswith("**"):
                # Save previous section
                if current_section and current_content:
                    sections[current_section] = "\n".join(current_content).strip()
                    current_content = []
                
                # Detect new section (order matters — more specific first)
                if "out of scope" in line_lower or "not included" in line_lower:
                    current_section = "out_of_scope"
                elif "what changed" in line_lower or ("what" in line_lower and "change" in line_lower):
                    current_section = "changes"
                elif "background" in line_lower or "context" in line_lower:
                    current_section = "background"
                elif "change" in line_lower:
                    current_section = "changes"
                elif "acceptance" in line_lower or " ac " in line_lower or line_lower.endswith(" ac"):
                    current_section = "acceptance_criteria"
                elif "test" in line_lower:
                    current_section = "testing"
                elif "depend" in line_lower or "prerequisite" in line_lower:
                    current_section = "dependencies"
                elif "summary" in line_lower or "what" in line_lower:
                    current_section = "summary"
                else:
                    current_section = None
            elif current_section:
                current_content.append(line)
        
        # Save last section
        if current_section and current_content:
            sections[current_section] = "\n".join(current_content).strip()
        
        return cls(
            title=title,
            raw_description=body,
            **sections
        )


# ---------------------------------------------------------------------------
# PR Description Fetchers (extend existing adapters)
# ---------------------------------------------------------------------------

def get_bitbucket_pr_description(
    workspace: str,
    repo_slug: str,
    pr_id: int,
    username: str,
    token: str,
) -> Optional[PRDescription]:
    """Fetch PR description from Bitbucket API."""

    
    url = f"https://api.bitbucket.org/2.0/repositories/{workspace}/{repo_slug}/pullrequests/{pr_id}"
    
    try:
        response = _httpx.get(
            url,
            auth=(username, token),
            timeout=10.0,
        )
        response.raise_for_status()
        data = response.json()
        
        title = data.get("title", "")
        body = data.get("description", "")
        
        return PRDescription.parse(title, body)
    
    except Exception as e:
        print(f"[pr_description_adapter] Error fetching Bitbucket PR #{pr_id}: {e}")
        return None


def get_github_pr_description(
    owner: str,
    repo: str,
    pr_id: int,
    token: str,
) -> Optional[PRDescription]:
    """Fetch PR description from GitHub API."""

    
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_id}"
    
    try:
        response = _httpx.get(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github.v3+json",
            },
            timeout=10.0,
        )
        response.raise_for_status()
        data = response.json()
        
        title = data.get("title", "")
        body = data.get("body") or ""
        
        return PRDescription.parse(title, body)
    
    except Exception as e:
        print(f"[pr_description_adapter] Error fetching GitHub PR #{pr_id}: {e}")
        return None


def get_gitlab_pr_description(
    project_id: str,
    mr_id: int,
    token: str,
    base_url: str = "https://gitlab.com",
) -> Optional[PRDescription]:
    """Fetch MR description from GitLab API."""

    
    url = f"{base_url}/api/v4/projects/{project_id}/merge_requests/{mr_id}"
    
    try:
        response = _httpx.get(
            url,
            headers={"PRIVATE-TOKEN": token},
            timeout=10.0,
        )
        response.raise_for_status()
        data = response.json()
        
        title = data.get("title", "")
        body = data.get("description") or ""
        
        return PRDescription.parse(title, body)
    
    except Exception as e:
        print(f"[pr_description_adapter] Error fetching GitLab MR #{mr_id}: {e}")
        return None


# ---------------------------------------------------------------------------
# Auto-detection helpers
# ---------------------------------------------------------------------------

def detect_platform_from_env() -> Optional[str]:
    """Detect VCS platform from CI environment variables.
    
    Returns: "bitbucket" | "github" | "gitlab" | None
    """
    if os.getenv("BITBUCKET_WORKSPACE"):
        return "bitbucket"
    elif os.getenv("GITHUB_ACTIONS"):
        return "github"
    elif os.getenv("GITLAB_CI"):
        return "gitlab"
    return None


def get_pr_description_from_env(pr_id: int) -> Optional[PRDescription]:
    """Auto-detect platform and fetch PR description from environment variables.
    
    Environment variables:
    - Bitbucket: BITBUCKET_WORKSPACE, BITBUCKET_REPO_SLUG, 
                 BITBUCKET_USERNAME, BITBUCKET_API_TOKEN
    - GitHub: GITHUB_REPOSITORY (owner/repo), GITHUB_TOKEN
    - GitLab: CI_PROJECT_ID, GITLAB_TOKEN, CI_SERVER_URL
    
    Returns None if platform cannot be detected or required vars missing.
    """
    platform = detect_platform_from_env()
    
    if platform == "bitbucket":
        workspace = os.getenv("BITBUCKET_WORKSPACE")
        repo_slug = os.getenv("BITBUCKET_REPO_SLUG")
        username = os.getenv("BITBUCKET_USERNAME")
        token = os.getenv("BITBUCKET_API_TOKEN")
        
        if all([workspace, repo_slug, username, token]):
            return get_bitbucket_pr_description(workspace, repo_slug, pr_id, username, token)
    
    elif platform == "github":
        repo = os.getenv("GITHUB_REPOSITORY")  # owner/repo
        token = os.getenv("GITHUB_TOKEN")
        
        if repo and token and "/" in repo:
            owner, repo_name = repo.split("/", 1)
            return get_github_pr_description(owner, repo_name, pr_id, token)
    
    elif platform == "gitlab":
        project_id = os.getenv("CI_PROJECT_ID")
        token = os.getenv("GITLAB_TOKEN")
        base_url = os.getenv("CI_SERVER_URL", "https://gitlab.com")
        
        if project_id and token:
            return get_gitlab_pr_description(project_id, pr_id, token, base_url)
    
    return None
