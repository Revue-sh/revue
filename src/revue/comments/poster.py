"""Stub module for the Poster pipeline stage (REVUE-208).

Full implementation delivered in REVUE-214.
Architecture spec: docs/architecture/comment-posting.md
"""
from __future__ import annotations


class Poster:
    """I/O: position resolution + VCSAdapter call + dedup against existing comments.

    Responsible for: resolving diff positions, calling VCSAdapter.post_comment(),
    and deduplicating against already-posted comments.
    """

    ...
