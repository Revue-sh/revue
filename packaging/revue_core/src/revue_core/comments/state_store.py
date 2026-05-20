"""JSON file-based storage for PR comment state (.revue/state/comments.json)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models import CommentState, CommentStateTransition, Platform, PRComment, SummaryComment


class CommentStateStore:
    """Read/write comment state in .revue/state/comments.json within a customer repo.
    
    State file format:
    {
        "bitbucket/workspace/repo/123": {
            "platform": "bitbucket",
            "repo_full_name": "workspace/repo",
            "pr_number": 123,
            "created_at": "2026-04-02T14:00:00+00:00",
            "updated_at": "2026-04-02T14:30:00+00:00",
            "summary": {
                "platform_comment_id": "999",
                "revision": 2,
                "total_issues": 5,
                "fixed_count": 2,
                "discussed_count": 0,
                "remaining_count": 3,
                "progress_percentage": 40
            },
            "inline_comments": [
                {
                    "id": 1,
                    "platform_comment_id": "42",
                    "platform_thread_id": null,
                    "file_path": "src/core.py",
                    "line_number": 105,
                    "comment_body": "Potential null pointer...",
                    "finding_id": null,
                    "finding_fingerprint": "abc123...",
                    "state": "active",
                    "created_at": "2026-04-02T14:00:00+00:00",
                    "updated_at": "2026-04-02T14:00:00+00:00",
                    "transitions": []
                }
            ]
        }
    }
    """

    def __init__(self, repo_path: str | Path):
        self.repo_path = Path(repo_path)
        self.state_dir = self.repo_path / ".revue" / "state"
        self.state_file = self.state_dir / "comments.json"

    def _ensure_dir(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def _read_state(self) -> dict:
        """Read entire state file (all PRs)."""
        if not self.state_file.exists():
            return {}
        with open(self.state_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write_state(self, data: dict) -> None:
        """Write state atomically: write to .tmp then os.replace()."""
        self._ensure_dir()
        tmp_path = self.state_file.with_suffix(".json.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, self.state_file)

    @staticmethod
    def _pr_key(platform: str, repo_full_name: str, pr_number: int) -> str:
        """Generate state key: platform/repo_full_name/pr_number."""
        return f"{platform}/{repo_full_name}/{pr_number}"

    @staticmethod
    def _dt_to_str(dt: datetime) -> str:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()

    @staticmethod
    def _str_to_dt(s: str) -> datetime:
        if isinstance(s, datetime):
            return s
        return datetime.fromisoformat(s)

    # -- Comment CRUD --

    def get_comments_for_pr(
        self,
        platform: Platform | str,
        repo_full_name: str,
        pr_number: int,
    ) -> list[PRComment]:
        """Fetch all inline comments for a PR.
        
        Args:
            platform: Platform enum or string (bitbucket, github, gitlab)
            repo_full_name: e.g. "workspace/repo" (Bitbucket), "owner/repo" (GitHub)
            pr_number: PR/MR number
        """
        if isinstance(platform, Platform):
            platform_str = platform.value
        else:
            platform_str = platform

        state = self._read_state()
        pr_key = self._pr_key(platform_str, repo_full_name, pr_number)
        pr_data = state.get(pr_key, {})
        
        if not pr_data:
            return []

        raw_comments = pr_data.get("inline_comments", [])
        
        # Parse repo_full_name for owner/name split (required by PRComment model)
        parts = repo_full_name.split("/", 1)
        repo_owner = parts[0] if len(parts) > 0 else ""
        repo_name = parts[1] if len(parts) > 1 else ""
        
        return [
            self._dict_to_comment(c, Platform(platform_str), repo_owner, repo_name, pr_number)
            for c in raw_comments
        ]

    def save_comment(
        self,
        platform: Platform | str,
        repo_full_name: str,
        pr_number: int,
        fingerprint: str,
        platform_comment_id: str,
        comment_type: str = "inline",
        file_path: str = "",
        line_number: int = 0,
        comment_body: str = "",
    ) -> PRComment:
        """Save a new comment to state.
        
        Args:
            platform: Platform enum or string
            repo_full_name: e.g. "workspace/repo"
            pr_number: PR/MR number
            fingerprint: Finding fingerprint (for de-duplication)
            platform_comment_id: Platform-returned comment ID
            comment_type: "inline" or "summary"
            file_path: File path (for inline comments)
            line_number: Line number (for inline comments)
            comment_body: Comment text
        """
        if isinstance(platform, Platform):
            platform_str = platform.value
        else:
            platform_str = platform

        state = self._read_state()
        pr_key = self._pr_key(platform_str, repo_full_name, pr_number)
        now = datetime.now(timezone.utc)

        if pr_key not in state:
            state[pr_key] = {
                "platform": platform_str,
                "repo_full_name": repo_full_name,
                "pr_number": pr_number,
                "created_at": self._dt_to_str(now),
                "updated_at": self._dt_to_str(now),
                "inline_comments": [],
            }

        pr_data = state[pr_key]
        comments_list = pr_data.setdefault("inline_comments", [])

        # Assign local ID
        next_id = max((c.get("id", 0) for c in comments_list), default=0) + 1

        comment_dict = {
            "id": next_id,
            "platform_comment_id": platform_comment_id,
            "platform_thread_id": None,
            "file_path": file_path,
            "line_number": line_number,
            "comment_body": comment_body,
            "finding_id": None,
            "finding_fingerprint": fingerprint,
            "state": CommentState.ACTIVE.value,
            "created_at": self._dt_to_str(now),
            "updated_at": self._dt_to_str(now),
            "transitions": [],
        }

        comments_list.append(comment_dict)
        pr_data["updated_at"] = self._dt_to_str(now)
        self._write_state(state)

        # Parse repo_full_name for owner/name
        parts = repo_full_name.split("/", 1)
        repo_owner = parts[0] if len(parts) > 0 else ""
        repo_name = parts[1] if len(parts) > 1 else ""

        return PRComment(
            id=next_id,
            platform=Platform(platform_str),
            platform_comment_id=platform_comment_id,
            platform_thread_id=None,
            pr_number=pr_number,
            repo_owner=repo_owner,
            repo_name=repo_name,
            file_path=file_path,
            line_number=line_number,
            comment_body=comment_body,
            finding_id=None,
            finding_fingerprint=fingerprint,
            state=CommentState.ACTIVE,
            created_at=now,
            updated_at=now,
        )

    def transition_state(
        self,
        platform: Platform | str,
        repo_full_name: str,
        pr_number: int,
        fingerprint: str,
        to_state: CommentState,
        reason: Optional[str] = None,
        developer_reply: Optional[str] = None,
    ) -> bool:
        """Transition a comment to a new state.
        
        Args:
            platform: Platform enum or string
            repo_full_name: e.g. "workspace/repo"
            pr_number: PR/MR number
            fingerprint: Finding fingerprint (identifies the comment)
            to_state: New state (RESOLVED, WONT_FIX, etc.)
            reason: Optional reason for transition
            developer_reply: Optional developer reply text
            
        Returns:
            True if successful, False if comment not found
        """
        if isinstance(platform, Platform):
            platform_str = platform.value
        else:
            platform_str = platform

        state = self._read_state()
        pr_key = self._pr_key(platform_str, repo_full_name, pr_number)
        
        if pr_key not in state:
            return False
        
        pr_data = state[pr_key]
        comments_list = pr_data.get("inline_comments", [])
        
        # Find comment by fingerprint
        comment_dict = None
        for c in comments_list:
            if c.get("finding_fingerprint") == fingerprint:
                comment_dict = c
                break
        
        if not comment_dict:
            return False
        
        from_state = CommentState(comment_dict["state"])
        now = datetime.now(timezone.utc)
        
        comment_dict["state"] = to_state.value
        comment_dict["updated_at"] = self._dt_to_str(now)
        
        transition = {
            "from_state": from_state.value,
            "to_state": to_state.value,
            "timestamp": self._dt_to_str(now),
        }
        if reason:
            transition["reason"] = reason
        if developer_reply:
            transition["developer_reply"] = developer_reply
        
        comment_dict.setdefault("transitions", []).append(transition)
        pr_data["updated_at"] = self._dt_to_str(now)
        self._write_state(state)
        
        return True

    # -- Summary CRUD --

    def get_summary_for_pr(
        self,
        platform: Platform | str,
        repo_full_name: str,
        pr_number: int,
    ) -> Optional[SummaryComment]:
        """Fetch summary comment for a PR."""
        if isinstance(platform, Platform):
            platform_str = platform.value
        else:
            platform_str = platform

        state = self._read_state()
        pr_key = self._pr_key(platform_str, repo_full_name, pr_number)
        pr_data = state.get(pr_key, {})
        
        if not pr_data or "summary" not in pr_data:
            return None
        
        s = pr_data["summary"]
        if "platform_comment_id" not in s:
            return None
        
        # Parse repo_full_name
        parts = repo_full_name.split("/", 1)
        repo_owner = parts[0] if len(parts) > 0 else ""
        repo_name = parts[1] if len(parts) > 1 else ""
        
        return SummaryComment(
            id=None,
            platform=Platform(platform_str),
            platform_comment_id=s["platform_comment_id"],
            pr_number=pr_number,
            repo_owner=repo_owner,
            repo_name=repo_name,
            total_issues=s.get("total_issues", 0),
            fixed_count=s.get("fixed_count", 0),
            discussed_count=s.get("discussed_count", 0),
            remaining_count=s.get("remaining_count", 0),
            last_updated_at=self._str_to_dt(pr_data.get("updated_at", pr_data.get("created_at", datetime.now(timezone.utc).isoformat()))),
            created_at=self._str_to_dt(pr_data.get("created_at", datetime.now(timezone.utc).isoformat())),
            revision=s.get("revision", 1),
        )

    def create_or_update_summary(
        self,
        platform: Platform | str,
        repo_full_name: str,
        pr_number: int,
        platform_comment_id: str,
        total_issues: int,
        fixed_count: int,
        discussed_count: int,
        remaining_count: int,
        revision: int = 1,
    ) -> SummaryComment:
        """Create or update summary comment."""
        if isinstance(platform, Platform):
            platform_str = platform.value
        else:
            platform_str = platform

        state = self._read_state()
        pr_key = self._pr_key(platform_str, repo_full_name, pr_number)
        now = datetime.now(timezone.utc)

        if pr_key not in state:
            state[pr_key] = {
                "platform": platform_str,
                "repo_full_name": repo_full_name,
                "pr_number": pr_number,
                "created_at": self._dt_to_str(now),
                "updated_at": self._dt_to_str(now),
                "inline_comments": [],
            }

        pr_data = state[pr_key]
        progress_percentage = int((fixed_count / total_issues * 100) if total_issues > 0 else 0)

        pr_data["summary"] = {
            "platform_comment_id": platform_comment_id,
            "revision": revision,
            "total_issues": total_issues,
            "fixed_count": fixed_count,
            "discussed_count": discussed_count,
            "remaining_count": remaining_count,
            "progress_percentage": progress_percentage,
        }
        pr_data["updated_at"] = self._dt_to_str(now)
        self._write_state(state)

        # Parse repo_full_name
        parts = repo_full_name.split("/", 1)
        repo_owner = parts[0] if len(parts) > 0 else ""
        repo_name = parts[1] if len(parts) > 1 else ""

        return SummaryComment(
            id=None,
            platform=Platform(platform_str),
            platform_comment_id=platform_comment_id,
            pr_number=pr_number,
            repo_owner=repo_owner,
            repo_name=repo_name,
            total_issues=total_issues,
            fixed_count=fixed_count,
            discussed_count=discussed_count,
            remaining_count=remaining_count,
            last_updated_at=now,
            created_at=self._str_to_dt(pr_data.get("created_at", self._dt_to_str(now))),
            revision=revision,
        )

    # -- Serialization helpers --

    @staticmethod
    def _dict_to_comment(
        d: dict,
        platform: Platform,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
    ) -> PRComment:
        created_at = d.get("created_at")
        updated_at = d.get("updated_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        if isinstance(updated_at, str):
            updated_at = datetime.fromisoformat(updated_at)
        return PRComment(
            id=d.get("id"),
            platform=platform,
            platform_comment_id=d["platform_comment_id"],
            platform_thread_id=d.get("platform_thread_id"),
            pr_number=pr_number,
            repo_owner=repo_owner,
            repo_name=repo_name,
            file_path=d["file_path"],
            line_number=d["line_number"],
            comment_body=d["comment_body"],
            finding_id=d.get("finding_id"),
            state=CommentState(d["state"]),
            created_at=created_at,
            updated_at=updated_at,
            finding_fingerprint=d.get("finding_fingerprint"),
        )
