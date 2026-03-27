#!/usr/bin/env python3
"""
VCS adapter protocol and position abstractions for AI Code Review Service.

This module defines:
- DiffPosition: platform-agnostic representation of a line position in a diff
- VCSAdapter: protocol that any VCS backend (GitHub, GitLab, etc.) must implement
- Translation helpers for GitHub sequential positions and GitLab line codes
"""

import re
from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

from AIReviewer.core.models import FileChange


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
        self, pr_id: int, position: DiffPosition, body: str
    ) -> bool: ...

    def post_summary_comment(self, pr_id: int, body: str) -> bool: ...

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


def translate_gitlab_line_code(
    base_commit_sha: str,
    head_commit_sha: str,
    file_path: str,
    line_number: int,
    line_type: str = "new",
) -> str:
    """Compute a GitLab ``line_code`` string.

    GitLab uses ``line_code`` to anchor inline discussion threads.  The
    canonical format is ``{base_sha}_{head_sha}_{line_number}``.

    Full SHA-1 hash computation is deferred to Story [013].
    """

    return f"{base_commit_sha}_{head_commit_sha}_{line_number}"
