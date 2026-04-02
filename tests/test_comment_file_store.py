"""Tests for TOML file-based comment store."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.revue.comments.file_store import CommentFileStore
from src.revue.comments.fingerprint import fingerprint
from src.revue.comments.models import CommentState, Platform, PRComment, SummaryComment


@pytest.fixture
def store(tmp_path):
    """CommentFileStore backed by a temp directory."""
    return CommentFileStore(tmp_path)


def _make_comment(**overrides) -> PRComment:
    defaults = dict(
        id=None,
        platform=Platform.BITBUCKET,
        platform_comment_id="abc123",
        platform_thread_id=None,
        pr_number=42,
        repo_owner="acme",
        repo_name="api",
        file_path="src/main.py",
        line_number=10,
        comment_body="Extract this to a utility function",
        finding_id=None,
        state=CommentState.UNRESOLVED,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return PRComment(**defaults)


# -- Create & read back --

class TestCreateAndRead:
    def test_create_comment_reads_back(self, store):
        comment = _make_comment()
        created = store.create_comment(comment)

        assert created.id == 1

        comments = store.get_comments_for_pr(
            Platform.BITBUCKET, "acme", "api", 42
        )
        assert len(comments) == 1
        c = comments[0]
        assert c.platform_comment_id == "abc123"
        assert c.file_path == "src/main.py"
        assert c.line_number == 10
        assert c.state == CommentState.UNRESOLVED

    def test_create_multiple_comments_increments_id(self, store):
        store.create_comment(_make_comment(platform_comment_id="c1"))
        store.create_comment(_make_comment(platform_comment_id="c2"))

        comments = store.get_comments_for_pr(
            Platform.BITBUCKET, "acme", "api", 42
        )
        assert len(comments) == 2
        assert comments[0].id == 1
        assert comments[1].id == 2

    def test_get_comments_empty_pr(self, store):
        comments = store.get_comments_for_pr(
            Platform.BITBUCKET, "acme", "api", 999
        )
        assert comments == []


# -- State transitions --

class TestStateTransition:
    def test_transition_updates_state(self, store):
        created = store.create_comment(_make_comment())

        transition = store.transition_state(
            created.id,
            CommentState.AUTO_RESOLVED,
            reason="Code changed at line 10 in commit abc789",
        )

        assert transition.from_state == CommentState.UNRESOLVED
        assert transition.to_state == CommentState.AUTO_RESOLVED
        assert transition.reason == "Code changed at line 10 in commit abc789"

        # Read back — state should be updated
        comments = store.get_comments_for_pr(
            Platform.BITBUCKET, "acme", "api", 42
        )
        assert comments[0].state == CommentState.AUTO_RESOLVED

    def test_transition_appends_to_history(self, store, tmp_path):
        created = store.create_comment(_make_comment())

        store.transition_state(
            created.id, CommentState.AUTO_RESOLVED, reason="code changed"
        )
        store.transition_state(
            created.id,
            CommentState.MANUALLY_RESOLVED_WITH_REPLY,
            reason="developer reopened",
            developer_reply="Fixed differently",
        )

        # Read raw TOML to verify transitions array
        import tomllib

        toml_path = tmp_path / ".revue" / "comments" / "PR-42.toml"
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)

        transitions = data["comments"][0]["transitions"]
        assert len(transitions) == 2
        assert transitions[0]["from_state"] == "unresolved"
        assert transitions[0]["to_state"] == "auto_resolved"
        assert transitions[1]["from_state"] == "auto_resolved"
        assert transitions[1]["to_state"] == "manually_resolved_with_reply"
        assert transitions[1]["developer_reply"] == "Fixed differently"

    def test_transition_nonexistent_comment_raises(self, store):
        with pytest.raises(ValueError, match="Comment 999 not found"):
            store.transition_state(999, CommentState.AUTO_RESOLVED)


# -- Summary upsert --

class TestSummary:
    def test_create_summary(self, store):
        summary = SummaryComment(
            id=None,
            platform=Platform.GITHUB,
            platform_comment_id="summary_1",
            pr_number=42,
            repo_owner="acme",
            repo_name="api",
            total_issues=10,
            fixed_count=7,
            discussed_count=2,
            remaining_count=1,
            last_updated_at=None,
            created_at=None,
        )
        result = store.create_or_update_summary(summary)

        assert result.last_updated_at is not None
        assert result.progress_percentage == 90

    def test_summary_roundtrip(self, store):
        summary = SummaryComment(
            id=None,
            platform=Platform.GITHUB,
            platform_comment_id="summary_1",
            pr_number=42,
            repo_owner="acme",
            repo_name="api",
            total_issues=10,
            fixed_count=7,
            discussed_count=2,
            remaining_count=1,
            last_updated_at=None,
            created_at=None,
        )
        store.create_or_update_summary(summary)

        loaded = store.get_summary_for_pr(
            Platform.GITHUB, "acme", "api", 42
        )
        assert loaded is not None
        assert loaded.total_issues == 10
        assert loaded.fixed_count == 7
        assert loaded.discussed_count == 2
        assert loaded.remaining_count == 1

    def test_update_summary_counts(self, store):
        summary = SummaryComment(
            id=None,
            platform=Platform.GITHUB,
            platform_comment_id="summary_1",
            pr_number=42,
            repo_owner="acme",
            repo_name="api",
            total_issues=10,
            fixed_count=3,
            discussed_count=0,
            remaining_count=7,
            last_updated_at=None,
            created_at=None,
        )
        store.create_or_update_summary(summary)

        # Update counts
        summary.fixed_count = 8
        summary.remaining_count = 2
        store.create_or_update_summary(summary)

        loaded = store.get_summary_for_pr(
            Platform.GITHUB, "acme", "api", 42
        )
        assert loaded.fixed_count == 8
        assert loaded.remaining_count == 2

    def test_get_summary_nonexistent(self, store):
        assert store.get_summary_for_pr(
            Platform.GITHUB, "acme", "api", 999
        ) is None


# -- Atomic write --

class TestAtomicWrite:
    def test_no_temp_file_left_behind(self, store, tmp_path):
        store.create_comment(_make_comment())

        comments_dir = tmp_path / ".revue" / "comments"
        tmp_files = list(comments_dir.glob("*.tmp"))
        assert tmp_files == [], f"Temp files left behind: {tmp_files}"


# -- Fingerprint --

class TestFingerprint:
    def test_deterministic(self):
        fp1 = fingerprint("src/main.py", 42, "Extract utility function")
        fp2 = fingerprint("src/main.py", 42, "Extract utility function")
        assert fp1 == fp2

    def test_different_inputs(self):
        fp1 = fingerprint("src/main.py", 42, "Extract utility function")
        fp2 = fingerprint("src/main.py", 43, "Extract utility function")
        assert fp1 != fp2

    def test_length(self):
        fp = fingerprint("a.py", 1, "issue")
        assert len(fp) == 16

    def test_truncates_issue_text(self):
        long_text = "x" * 200
        fp1 = fingerprint("a.py", 1, long_text)
        fp2 = fingerprint("a.py", 1, long_text[:50] + "DIFFERENT")
        # First 50 chars are the same, so fingerprints should match
        assert fp1 == fp2
