"""TOML file-based storage for PR comment state (.revue/comments/PR-{number}.toml)."""
from __future__ import annotations

import os
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import tomli_w

from .models import CommentState, CommentStateTransition, Platform, PRComment, SummaryComment


class CommentFileStore:
    """Read/write comment state in .revue/comments/ TOML files within a customer repo."""

    def __init__(self, repo_path: str | Path):
        self.repo_path = Path(repo_path)
        self.comments_dir = self.repo_path / ".revue" / "comments"

    def _toml_path(self, pr_number: int) -> Path:
        return self.comments_dir / f"PR-{pr_number}.toml"

    def _ensure_dir(self) -> None:
        self.comments_dir.mkdir(parents=True, exist_ok=True)

    def _read_toml(self, pr_number: int) -> dict:
        path = self._toml_path(pr_number)
        if not path.exists():
            return {}
        with open(path, "rb") as f:
            return tomllib.load(f)

    def _write_toml(self, pr_number: int, data: dict) -> None:
        """Write TOML atomically: write to .tmp then os.replace()."""
        self._ensure_dir()
        path = self._toml_path(pr_number)
        tmp_path = path.with_suffix(".toml.tmp")
        with open(tmp_path, "wb") as f:
            tomli_w.dump(data, f)
        os.replace(tmp_path, path)

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
        platform: Platform,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
    ) -> list[PRComment]:
        data = self._read_toml(pr_number)
        if not data:
            return []
        raw_comments = data.get("comments", [])
        return [self._dict_to_comment(c, platform, repo_owner, repo_name, pr_number) for c in raw_comments]

    def create_comment(self, comment: PRComment) -> PRComment:
        data = self._read_toml(comment.pr_number)
        now = datetime.now(timezone.utc)

        if not data:
            data = {
                "pr_number": comment.pr_number,
                "platform": comment.platform.value,
                "repo_owner": comment.repo_owner,
                "repo_name": comment.repo_name,
                "created_at": self._dt_to_str(now),
                "updated_at": self._dt_to_str(now),
                "summary": {
                    "total_issues": 0,
                    "fixed_count": 0,
                    "discussed_count": 0,
                    "remaining_count": 0,
                    "progress_percentage": 0,
                },
                "comments": [],
            }

        comments_list: list[dict] = data.setdefault("comments", [])

        # Assign a local id (1-based index)
        next_id = max((c.get("id", 0) for c in comments_list), default=0) + 1
        comment.id = next_id
        if comment.created_at is None:
            comment.created_at = now
        if comment.updated_at is None:
            comment.updated_at = now

        comments_list.append(self._comment_to_dict(comment))
        data["updated_at"] = self._dt_to_str(now)
        self._write_toml(comment.pr_number, data)
        return comment

    def transition_state(
        self,
        comment_id: int,
        to_state: CommentState,
        reason: Optional[str] = None,
        developer_reply: Optional[str] = None,
    ) -> CommentStateTransition:
        # We need to find which PR file contains this comment — scan all files
        pr_number, data, idx = self._find_comment_by_id(comment_id)
        if data is None:
            raise ValueError(f"Comment {comment_id} not found")

        comment_dict = data["comments"][idx]
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
        data["updated_at"] = self._dt_to_str(now)
        self._write_toml(pr_number, data)

        return CommentStateTransition(
            id=None,
            comment_id=comment_id,
            from_state=from_state,
            to_state=to_state,
            reason=reason,
            developer_reply=developer_reply,
            transition_at=now,
        )

    def _find_comment_by_id(self, comment_id: int) -> tuple[int, Optional[dict], int]:
        """Scan all PR TOML files for a comment with the given id.

        NOTE: IDs are 1-based sequential integers scoped per PR file, so they
        are unique within a file but not globally. This scan is safe because
        comment IDs are only ever used after being fetched from a known PR
        (via get_comments_for_pr), and the service never mixes IDs across PRs.
        Future optimisation: pass pr_number here to skip the scan entirely.
        """
        if not self.comments_dir.exists():
            return (0, None, 0)
        for path in self.comments_dir.glob("PR-*.toml"):
            pr_number = int(path.stem.split("-", 1)[1])
            data = self._read_toml(pr_number)
            for i, c in enumerate(data.get("comments", [])):
                if c.get("id") == comment_id:
                    return (pr_number, data, i)
        return (0, None, 0)

    # -- Summary CRUD --

    def get_summary_for_pr(
        self,
        platform: Platform,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
    ) -> Optional[SummaryComment]:
        data = self._read_toml(pr_number)
        if not data or "summary" not in data:
            return None
        s = data["summary"]
        # Summary must have a platform_comment_id to be considered "existing"
        if "platform_comment_id" not in s:
            return None
        return SummaryComment(
            id=None,
            platform=platform,
            platform_comment_id=s["platform_comment_id"],
            pr_number=pr_number,
            repo_owner=repo_owner,
            repo_name=repo_name,
            total_issues=s.get("total_issues", 0),
            fixed_count=s.get("fixed_count", 0),
            discussed_count=s.get("discussed_count", 0),
            remaining_count=s.get("remaining_count", 0),
            last_updated_at=self._str_to_dt(data.get("updated_at", data.get("created_at", datetime.now(timezone.utc).isoformat()))),
            created_at=self._str_to_dt(data.get("created_at", datetime.now(timezone.utc).isoformat())),
        )

    def create_or_update_summary(self, summary: SummaryComment) -> SummaryComment:
        data = self._read_toml(summary.pr_number)
        now = datetime.now(timezone.utc)

        if not data:
            data = {
                "pr_number": summary.pr_number,
                "platform": summary.platform.value,
                "repo_owner": summary.repo_owner,
                "repo_name": summary.repo_name,
                "created_at": self._dt_to_str(now),
                "updated_at": self._dt_to_str(now),
                "comments": [],
            }

        data["summary"] = {
            "platform_comment_id": summary.platform_comment_id,
            "total_issues": summary.total_issues,
            "fixed_count": summary.fixed_count,
            "discussed_count": summary.discussed_count,
            "remaining_count": summary.remaining_count,
            "progress_percentage": summary.progress_percentage,
        }
        data["updated_at"] = self._dt_to_str(now)
        summary.last_updated_at = now
        if summary.created_at is None:
            summary.created_at = now

        self._write_toml(summary.pr_number, data)
        return summary

    # -- Serialization helpers --

    def _comment_to_dict(self, c: PRComment) -> dict:
        d: dict = {
            "id": c.id,
            "platform_comment_id": c.platform_comment_id,
            "file_path": c.file_path,
            "line_number": c.line_number,
            "comment_body": c.comment_body,
            "state": c.state.value,
            "created_at": self._dt_to_str(c.created_at),
            "updated_at": self._dt_to_str(c.updated_at),
        }
        if c.platform_thread_id:
            d["platform_thread_id"] = c.platform_thread_id
        if c.finding_id is not None:
            d["finding_id"] = c.finding_id
        if c.finding_fingerprint:
            d["finding_fingerprint"] = c.finding_fingerprint
        return d

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
