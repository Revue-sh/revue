"""Per-PR JSON comment store for duplicate detection (REVUE-110).

File path: .revue/comments/{platform}-PR-{number}.json
Schema:
    {
        "pr_number": 42,
        "platform": "bitbucket",
        "files": {
            "src/revue/core/cli.py": {
                "<fingerprint_hex>": {
                    "state": "unresolved",
                    "platform_comment_id": "12345",
                    "platform_thread_id": null,
                    "line_number": 42,
                    "comment_body": "...",
                    "created_at": "...",
                    "updated_at": "..."
                }
            }
        }
    }

Platform is encoded in the filename only — not repeated inside the JSON body
beyond the top-level "platform" field (for human readability).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models import CommentState


class PerPRCommentStore:
    """Read/write per-PR comment state as JSON files in .revue/comments/.

    One file per (platform, pr_number) pair:
        .revue/comments/bitbucket-PR-42.json
        .revue/comments/github-PR-7.json
        .revue/comments/gitlab-PR-3.json

    Deduplication lookup is O(1): files[file_path][fingerprint].
    """

    def __init__(self, repo_path: str | Path) -> None:
        self.repo_path = Path(repo_path)
        self.comments_dir = self.repo_path / ".revue" / "comments"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _json_path(self, platform: str, pr_number: int) -> Path:
        return self.comments_dir / f"{platform}-PR-{pr_number}.json"

    def _ensure_dir(self) -> None:
        self.comments_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _read(self, platform: str, pr_number: int) -> dict:
        path = self._json_path(platform, pr_number)
        if not path.exists():
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            import sys
            print(
                f"[revue] Warning: corrupt JSON store at {path} — treating as empty. "
                "Re-review will re-post all findings for this PR.",
                file=sys.stderr,
            )
            return {}

    def _write(self, platform: str, pr_number: int, data: dict) -> None:
        """Write atomically: write to .tmp then os.replace()."""
        self._ensure_dir()
        path = self._json_path(platform, pr_number)
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)

    def _get_or_create(self, platform: str, pr_number: int) -> dict:
        data = self._read(platform, pr_number)
        if not data:
            data = {
                "pr_number": pr_number,
                "platform": platform,
                "files": {},
            }
        return data

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def has_fingerprint(
        self,
        platform: str,
        pr_number: int,
        file_path: str,
        fingerprint: str,
    ) -> bool:
        """Return True if this fingerprint was already posted on this PR.

        Used as the duplicate gate: if True, skip posting.
        """
        data = self._read(platform, pr_number)
        if not data:
            return False
        return fingerprint in data.get("files", {}).get(file_path, {})

    def save_finding(
        self,
        platform: str,
        pr_number: int,
        file_path: str,
        fingerprint: str,
        platform_comment_id: str,
        line_number: int,
        comment_body: str,
        platform_thread_id: Optional[str] = None,
    ) -> None:
        """Record a newly posted finding in the store.

        Called after a comment is successfully posted to the platform.
        """
        data = self._get_or_create(platform, pr_number)
        now = self._now_iso()
        files = data.setdefault("files", {})
        file_findings = files.setdefault(file_path, {})
        file_findings[fingerprint] = {
            "state": CommentState.UNRESOLVED.value,
            "platform_comment_id": platform_comment_id,
            "platform_thread_id": platform_thread_id,
            "line_number": line_number,
            "comment_body": comment_body,
            "created_at": now,
            "updated_at": now,
        }
        self._write(platform, pr_number, data)

    def mark_resolved(
        self,
        platform: str,
        pr_number: int,
        file_path: str,
        fingerprint: str,
        state: CommentState,
        reason: Optional[str] = None,
    ) -> bool:
        """Transition a finding to a resolved state.

        Returns True if the entry was found and updated, False otherwise.
        """
        data = self._read(platform, pr_number)
        if not data:
            return False
        entry = data.get("files", {}).get(file_path, {}).get(fingerprint)
        if entry is None:
            return False
        entry["state"] = state.value
        entry["updated_at"] = self._now_iso()
        if reason:
            entry["resolution_reason"] = reason
        self._write(platform, pr_number, data)
        return True

    def get_unresolved_fingerprints(
        self,
        platform: str,
        pr_number: int,
    ) -> dict[str, dict]:
        """Return all unresolved fingerprints as {fingerprint: entry} across all files.

        Used by the auto-resolve flow (AC5): compare against new review fingerprints
        to find findings that are now fixed.
        """
        data = self._read(platform, pr_number)
        if not data:
            return {}
        result: dict[str, dict] = {}
        for file_path, findings in data.get("files", {}).items():
            for fp, entry in findings.items():
                if entry.get("state") == CommentState.UNRESOLVED.value:
                    result[fp] = {**entry, "file_path": file_path}
        return result
