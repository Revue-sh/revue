#!/usr/bin/env python3
"""
VCS adapter protocol and position abstractions for Revue.

This module defines:
- DiffPosition: platform-agnostic representation of a line position in a diff
- VCSAdapter: protocol that any VCS backend (GitHub, GitLab, etc.) must implement
- Translation helpers for GitHub sequential positions and GitLab line codes
"""

import hashlib
import re
from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

from revue.core.models import FileChange


@dataclass
class DiffPosition:
    """Platform-agnostic representation of a line position within a diff.

    Carries both GitHub-specific fields (commit_id, diff_hunk, position)
    and GitLab-specific fields (line_code, new_line, old_line) so that
    a single object can be handed to any VCS backend.
    """

    file_path: str
    line_number: int
    side: str = "RIGHT"  # "LEFT" or "RIGHT"
    # GitHub fields
    commit_id: str = ""
    diff_hunk: str = ""
    position: int = 0  # sequential line index in diff
    # GitLab fields
    line_code: str = ""  # SHA hash used by GitLab
    new_line: Optional[int] = None
    old_line: Optional[int] = None


@runtime_checkable
class VCSAdapter(Protocol):
    """Protocol that every VCS backend must satisfy.

    Implementations provide concrete API calls for a specific platform
    (GitHub, GitLab, Bitbucket, etc.) while consumers depend only on
    this protocol.

    All five methods plus ``verify_webhook_signature`` must be implemented.
    The webhook method is declared here (not just on concrete adapters) so
    that the type system enforces it on every future adapter — a missing
    implementation will fail ``isinstance(adapter, VCSAdapter)`` at runtime
    and raise a type error at static analysis time.
    """

    def get_diff(self, pr_id: int) -> list[FileChange]: ...

    def post_review_comment(
        self, pr_id: int, position: DiffPosition, body: str, replacement_line_count: int = 1
    ) -> str | None:
        """Post an inline review comment.

        Returns:
            The platform comment ID as a string on success, None on failure.
        """
        ...

    def post_summary_comment(self, pr_id: int, body: str) -> str | None: ...

    def update_comment(self, pr_id: int, comment_id: str, body: str) -> bool: ...

    def get_existing_comments(self, pr_id: int) -> list[dict]: ...

    def resolve_position(
        self, file_path: str, line_number: int, diff: str
    ) -> DiffPosition: ...

    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        """Verify the webhook payload against its signature.

        Args:
            payload:   Raw request body bytes.
            signature: Platform-supplied signature header value.
                       GitHub: ``sha256=<hex>`` (X-Hub-Signature-256)
                       GitLab: bare token string (X-Gitlab-Token)

        Returns:
            True if the signature is valid, False otherwise.
            Must use a timing-safe comparison (hmac.compare_digest).
        """
        ...

    def resolve_inline_comment(
        self, pr_id: int, comment_id: str, reply_body: str
    ) -> bool:
        """Resolve an inline comment thread (platform-specific behavior).

        Args:
            pr_id:       Pull/merge request ID.
            comment_id:  Platform-specific comment identifier.
            reply_body:  Optional message to post when resolving.

        Platform behavior:
            - GitHub: Mark discussion thread as resolved + optional reply.
            - GitLab: Mark discussion as resolved + optional reply.
            - Bitbucket: Post reply (no native resolution, reply-based UX).

        Returns:
            True if successful, False otherwise.
        """
        ...


# ---------------------------------------------------------------------------
# Position translation helpers
# ---------------------------------------------------------------------------

_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+),?(\d*) \+(\d+),?(\d*) @@")


def translate_github_position(
    file_path: str, line_number: int, diff_hunk: str
) -> DiffPosition:
    """Translate a semantic *line_number* to a GitHub diff position.

    GitHub PR review comments require a sequential ``position`` that counts
    every line (header, context, +, -) from the first ``@@`` in the diff.
    This helper parses ``@@ -old,count +new,start @@`` headers, walks the
    lines, and returns the matching position.

    Full edge-case handling is deferred to Story [012].
    """

    position = 0
    current_new_line: Optional[int] = None

    for raw_line in diff_hunk.split("\n"):
        match = _HUNK_HEADER_RE.match(raw_line)
        if match:
            current_new_line = int(match.group(3))
            position += 1
            continue

        if current_new_line is None:
            # Lines before the first hunk header (e.g. diff --git lines)
            continue

        position += 1

        if raw_line.startswith("-"):
            # Deletion — does not advance new-file line counter
            continue

        if raw_line.startswith("+"):
            if current_new_line == line_number:
                return DiffPosition(
                    file_path=file_path,
                    line_number=line_number,
                    side="RIGHT",
                    diff_hunk=diff_hunk,
                    position=position,
                )
            current_new_line += 1
        else:
            # Context line (or empty line at end of hunk)
            if current_new_line == line_number:
                return DiffPosition(
                    file_path=file_path,
                    line_number=line_number,
                    side="RIGHT",
                    diff_hunk=diff_hunk,
                    position=position,
                )
            current_new_line += 1

    # Fallback — line not found in diff; return best-effort position 0
    return DiffPosition(
        file_path=file_path,
        line_number=line_number,
        side="RIGHT",
        diff_hunk=diff_hunk,
        position=0,
    )


def extract_gitlab_version_shas(versions: list) -> tuple[str, str, str]:
    """Return (base_commit_sha, start_commit_sha, head_commit_sha) from a GitLab
    MR versions API response (list of version dicts, most recent first).

    Both GitLabAdapter implementations use this — single source of truth for the
    SHA extraction logic required by GitLab's discussions positioning API.

    Raises ValueError if the list is empty or required fields are missing.
    """
    if not versions:
        raise ValueError("No MR versions found — cannot determine diff SHAs")
    latest = versions[0]
    try:
        return (
            latest["base_commit_sha"],
            latest["start_commit_sha"],
            latest["head_commit_sha"],
        )
    except KeyError as exc:
        raise ValueError(f"MR version response missing expected SHA field: {exc}") from exc


_DIFF_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _map_diff_lines(diff_content: str) -> list[tuple[int, int]]:
    """Return all (old_line, new_line) pairs reachable in the diff.

    Added lines (+) have old_line=0 (no corresponding old position).
    Context lines carry both counters.  Removed lines are excluded
    (they have no new_line and cannot be commented on by a reviewer
    looking at the new version).
    """
    result: list[tuple[int, int]] = []
    cur_old = cur_new = 0

    for raw in diff_content.splitlines():
        m = _DIFF_HUNK_RE.match(raw)
        if m:
            cur_old = int(m.group(1))
            cur_new = int(m.group(2))
            continue
        if raw.startswith(("+++", "---")):
            continue
        if cur_new == 0 and cur_old == 0:
            continue  # before first hunk
        if raw.startswith("+"):
            result.append((0, cur_new))
            cur_new += 1
        elif raw.startswith("-"):
            cur_old += 1
        else:  # context
            result.append((cur_old, cur_new))
            cur_old += 1
            cur_new += 1

    return result


def compute_gitlab_line_code(
    file_path: str,
    diff_content: str,
    new_line: int,
) -> tuple[str, int, int]:
    """Compute a valid GitLab ``line_code`` for an inline diff comment.

    GitLab validates line_code against ``/[0-9a-f]{8}_\\d+_\\d+/``.
    The format is ``SHA1(file_path)[0:8]_{old_line}_{new_line}``.

    ``old_line`` is 0 for purely-added lines.  If ``new_line`` falls
    outside every diff hunk (the AI suggested a line not in the diff),
    the position is snapped to the closest valid hunk line so the
    comment still lands near the intended location.

    Returns:
        ``(line_code, resolved_new_line, old_line)`` — use
        ``resolved_new_line`` (not the original) as ``new_line`` in the
        GitLab position object, since it may have been snapped.
    """
    file_hash = hashlib.sha1(file_path.encode()).hexdigest()[:8]
    pairs = _map_diff_lines(diff_content)

    if pairs:
        # Exact match first
        for old, new in pairs:
            if new == new_line:
                return f"{file_hash}_{old}_{new}", new, old
        # Snap to nearest valid hunk line
        old, snapped = min(pairs, key=lambda p: abs(p[1] - new_line))
        return f"{file_hash}_{old}_{snapped}", snapped, old

    # Empty diff — best-effort fallback (no snapping possible)
    return f"{file_hash}_0_{new_line}", new_line, 0


def translate_gitlab_line_code(
    base_commit_sha: str,
    head_commit_sha: str,
    file_path: str,
    line_number: int,
    line_type: str = "new",
) -> str:
    """Legacy stub — kept for backward compatibility with resolve_position.

    Prefer ``compute_gitlab_line_code`` for new call sites; it uses the
    correct SHA1(file_path) format that GitLab's validator accepts.
    """
    file_hash = hashlib.sha1(file_path.encode()).hexdigest()[:8]
    return f"{file_hash}_0_{line_number}"
