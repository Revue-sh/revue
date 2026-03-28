#!/usr/bin/env python3
"""Tests for VCSAdapter protocol and DiffPosition abstraction."""

from __future__ import annotations

from revue.core.models import FileChange
from revue.core.vcs_adapter import (
    DiffPosition,
    VCSAdapter,
    translate_github_position,
    translate_gitlab_line_code,
)
from revue.core.github_adapter import GitHubAdapter
from revue.core.gitlab_adapter import GitLabAdapter


# ---------------------------------------------------------------------------
# DiffPosition dataclass tests
# ---------------------------------------------------------------------------


def test_diff_position_defaults() -> None:
    """Created with file_path + line_number; defaults are sensible."""
    pos = DiffPosition(file_path="src/main.py", line_number=42)
    assert pos.file_path == "src/main.py"
    assert pos.line_number == 42
    assert pos.side == "RIGHT"
    assert pos.position == 0
    assert pos.line_code == ""
    assert pos.commit_id == ""
    assert pos.diff_hunk == ""
    assert pos.new_line is None
    assert pos.old_line is None


def test_diff_position_github_fields() -> None:
    """All GitHub-specific fields are stored correctly."""
    pos = DiffPosition(
        file_path="app.py",
        line_number=10,
        side="RIGHT",
        commit_id="abc123",
        diff_hunk="@@ -1,3 +1,4 @@\n context\n+added",
        position=5,
    )
    assert pos.commit_id == "abc123"
    assert pos.diff_hunk.startswith("@@")
    assert pos.position == 5


def test_diff_position_gitlab_fields() -> None:
    """All GitLab-specific fields are stored correctly."""
    pos = DiffPosition(
        file_path="lib/utils.rb",
        line_number=20,
        line_code="abc_def_20",
        new_line=20,
        old_line=18,
    )
    assert pos.line_code == "abc_def_20"
    assert pos.new_line == 20
    assert pos.old_line == 18


# ---------------------------------------------------------------------------
# VCSAdapter protocol structural check
# ---------------------------------------------------------------------------


class _MinimalAdapter:
    """Minimal concrete class satisfying VCSAdapter structurally (all 6 methods)."""

    def get_diff(self, pr_id: int) -> list[FileChange]:
        return []

    def post_review_comment(
        self, pr_id: int, position: DiffPosition, body: str
    ) -> bool:
        return True

    def post_summary_comment(self, pr_id: int, body: str) -> bool:
        return True

    def get_existing_comments(self, pr_id: int) -> list[dict]:
        return []

    def resolve_position(
        self, file_path: str, line_number: int, diff: str
    ) -> DiffPosition:
        return DiffPosition(file_path=file_path, line_number=line_number)

    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        return True


class _MissingWebhookAdapter:
    """Adapter that omits verify_webhook_signature — must NOT satisfy protocol."""

    def get_diff(self, pr_id: int) -> list[FileChange]: return []
    def post_review_comment(self, pr_id, position, body) -> bool: return True
    def post_summary_comment(self, pr_id, body) -> bool: return True
    def get_existing_comments(self, pr_id) -> list[dict]: return []
    def resolve_position(self, file_path, line_number, diff) -> DiffPosition:
        return DiffPosition(file_path=file_path, line_number=line_number)


def test_vcs_adapter_protocol_structural() -> None:
    """A class implementing all six methods passes isinstance check."""
    adapter = _MinimalAdapter()
    assert isinstance(adapter, VCSAdapter)


def test_vcs_adapter_protocol_missing_webhook_fails() -> None:
    """A class omitting verify_webhook_signature fails the protocol check."""
    adapter = _MissingWebhookAdapter()
    assert not isinstance(adapter, VCSAdapter)


# ---------------------------------------------------------------------------
# GitHub adapter — verify_webhook_signature protocol compliance
# ---------------------------------------------------------------------------

import hashlib
import hmac as _hmac


def _make_github_sig(payload: bytes, secret: str) -> str:
    digest = _hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_github_adapter_verify_webhook_signature_valid() -> None:
    secret = "my-webhook-secret"
    payload = b'{"action":"opened"}'
    sig = _make_github_sig(payload, secret)
    adapter = GitHubAdapter(token="tok", repo="owner/repo", webhook_secret=secret)
    assert adapter.verify_webhook_signature(payload, sig) is True


def test_github_adapter_verify_webhook_signature_invalid() -> None:
    adapter = GitHubAdapter(token="tok", repo="owner/repo", webhook_secret="correct-secret")
    assert adapter.verify_webhook_signature(b"payload", "sha256=wronghex") is False


def test_github_adapter_verify_webhook_signature_missing_prefix() -> None:
    adapter = GitHubAdapter(token="tok", repo="owner/repo", webhook_secret="secret")
    assert adapter.verify_webhook_signature(b"payload", "nohashprefix") is False


def test_github_adapter_satisfies_vcs_adapter_protocol() -> None:
    adapter = GitHubAdapter(token="tok", repo="owner/repo", webhook_secret="s")
    assert isinstance(adapter, VCSAdapter)


# ---------------------------------------------------------------------------
# GitLab adapter — verify_webhook_signature protocol compliance
# ---------------------------------------------------------------------------

def test_gitlab_adapter_verify_webhook_signature_valid() -> None:
    secret = "gitlab-token-secret"
    adapter = GitLabAdapter(token="tok", project_id=1, webhook_secret=secret)
    assert adapter.verify_webhook_signature(b"any-payload", secret) is True


def test_gitlab_adapter_verify_webhook_signature_invalid() -> None:
    adapter = GitLabAdapter(token="tok", project_id=1, webhook_secret="correct")
    assert adapter.verify_webhook_signature(b"any-payload", "wrong-token") is False


def test_gitlab_adapter_satisfies_vcs_adapter_protocol() -> None:
    adapter = GitLabAdapter(token="tok", project_id=1, webhook_secret="s")
    assert isinstance(adapter, VCSAdapter)


# ---------------------------------------------------------------------------
# GitHub position translation
# ---------------------------------------------------------------------------


def test_translate_github_position_simple() -> None:
    """Single hunk — line in the first (and only) hunk returns correct position."""
    diff = (
        "@@ -1,3 +1,4 @@\n"
        " line1\n"
        " line2\n"
        "+new_line3\n"
        " line3\n"
    )
    pos = translate_github_position("app.py", 3, diff)
    assert pos.position == 4  # @@ header(1), line1(2), line2(3), +new_line3(4)
    assert pos.file_path == "app.py"
    assert pos.line_number == 3


def test_translate_github_position_multi_hunk() -> None:
    """Two @@ headers — line in second hunk, position counts across both."""
    diff = (
        "@@ -1,2 +1,3 @@\n"
        " line1\n"
        "+added_a\n"
        " line2\n"
        "@@ -10,2 +11,3 @@\n"
        " line10\n"
        "+added_b\n"
        " line11\n"
    )
    # Second hunk starts new-file line at 11.  added_b is new-file line 12.
    pos = translate_github_position("app.py", 12, diff)
    # Hunk1: header(1) + line1(2) + added_a(3) + line2(4) = 4
    # Hunk2: header(5) + line10(6) + added_b(7)
    assert pos.position == 7
    assert pos.line_number == 12


# ---------------------------------------------------------------------------
# GitLab line_code translation
# ---------------------------------------------------------------------------


def test_translate_gitlab_line_code_format() -> None:
    """Returned string contains both SHAs and the line number."""
    code = translate_gitlab_line_code("base123", "head456", "f.py", 42)
    assert "base123" in code
    assert "head456" in code
    assert "42" in code


def test_translate_gitlab_line_code_deterministic() -> None:
    """Same inputs always produce the same output."""
    a = translate_gitlab_line_code("b", "h", "f.py", 7)
    b = translate_gitlab_line_code("b", "h", "f.py", 7)
    assert a == b
