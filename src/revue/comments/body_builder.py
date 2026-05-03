"""Stub module for the BodyBuilder pipeline stage (REVUE-208).

Full implementation delivered in REVUE-212.
Architecture spec: docs/architecture/comment-posting.md
"""
from __future__ import annotations


class BodyBuilder:
    """Pure rendering: typed ConsolidatedFinding → platform-specific comment body.

    Per-platform kind-switching: GitHub suggestion blocks, GitLab suggestions, Bitbucket prose.
    Reads summary_sink for the PR-level summary comment (Decision 6).
    """

    ...
