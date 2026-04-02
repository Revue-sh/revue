#!/usr/bin/env python3
"""Tests for CommentStateStore (REVUE-104 DoD — Gap 1)."""

from __future__ import annotations

import json

import pytest

from revue.comments.models import CommentState, Platform
from revue.comments.state_store import CommentStateStore


# =====================================================================
# Fixtures
# =====================================================================

PLATFORM = "bitbucket"
REPO = "workspace/repo"
PR_NUM = 42


@pytest.fixture()
def store(tmp_path):
    """Return a CommentStateStore rooted in a temporary directory."""
    return CommentStateStore(tmp_path)


def _state_file(store: CommentStateStore):
    """Return the Path to the JSON state file."""
    return store.state_file


# =====================================================================
# save_comment — basic persistence
# =====================================================================


def test_save_comment_writes_to_state_file(store, tmp_path) -> None:
    """save_comment() creates .revue/state/comments.json with correct data."""
    store.save_comment(
        platform=PLATFORM,
        repo_full_name=REPO,
        pr_number=PR_NUM,
        fingerprint="fp_aaa",
        platform_comment_id="101",
        file_path="src/main.py",
        line_number=10,
        comment_body="Fix this",
    )

    state_file = _state_file(store)
    assert state_file.exists()

    data = json.loads(state_file.read_text(encoding="utf-8"))
    pr_key = f"{PLATFORM}/{REPO}/{PR_NUM}"
    assert pr_key in data

    comments = data[pr_key]["inline_comments"]
    assert len(comments) == 1
    assert comments[0]["finding_fingerprint"] == "fp_aaa"
    assert comments[0]["platform_comment_id"] == "101"
    assert comments[0]["file_path"] == "src/main.py"
    assert comments[0]["line_number"] == 10
    assert comments[0]["comment_body"] == "Fix this"
    assert comments[0]["state"] == "active"


# =====================================================================
# get_comments_for_pr — round-trip
# =====================================================================


def test_get_comments_for_pr_returns_saved_comments(store) -> None:
    """get_comments_for_pr() returns PRComment objects matching saved data."""
    store.save_comment(
        platform=PLATFORM,
        repo_full_name=REPO,
        pr_number=PR_NUM,
        fingerprint="fp_bbb",
        platform_comment_id="202",
        file_path="lib/utils.py",
        line_number=5,
        comment_body="Looks wrong",
    )

    comments = store.get_comments_for_pr(PLATFORM, REPO, PR_NUM)
    assert len(comments) == 1

    c = comments[0]
    assert c.finding_fingerprint == "fp_bbb"
    assert c.platform_comment_id == "202"
    assert c.file_path == "lib/utils.py"
    assert c.line_number == 5
    assert c.comment_body == "Looks wrong"
    assert c.state == CommentState.ACTIVE
    assert c.platform == Platform.BITBUCKET
    assert c.repo_owner == "workspace"
    assert c.repo_name == "repo"
    assert c.pr_number == PR_NUM


# =====================================================================
# save_comment — multiple fingerprints
# =====================================================================


def test_save_comment_two_fingerprints_creates_two_entries(store) -> None:
    """Two save_comment() calls with different fingerprints = 2 entries."""
    store.save_comment(
        platform=PLATFORM, repo_full_name=REPO, pr_number=PR_NUM,
        fingerprint="fp_1", platform_comment_id="301",
    )
    store.save_comment(
        platform=PLATFORM, repo_full_name=REPO, pr_number=PR_NUM,
        fingerprint="fp_2", platform_comment_id="302",
    )

    comments = store.get_comments_for_pr(PLATFORM, REPO, PR_NUM)
    assert len(comments) == 2
    fps = {c.finding_fingerprint for c in comments}
    assert fps == {"fp_1", "fp_2"}


# =====================================================================
# save_comment — atomic write (no corruption)
# =====================================================================


def test_save_comment_is_idempotent_on_file(store) -> None:
    """Multiple sequential writes produce a valid, non-corrupt JSON file."""
    for i in range(5):
        store.save_comment(
            platform=PLATFORM, repo_full_name=REPO, pr_number=PR_NUM,
            fingerprint=f"fp_{i}", platform_comment_id=str(400 + i),
        )

    # File must still be valid JSON after all writes
    data = json.loads(_state_file(store).read_text(encoding="utf-8"))
    pr_key = f"{PLATFORM}/{REPO}/{PR_NUM}"
    assert len(data[pr_key]["inline_comments"]) == 5

    # No .tmp file should remain (atomic os.replace)
    tmp_file = _state_file(store).with_suffix(".json.tmp")
    assert not tmp_file.exists()


# =====================================================================
# transition_state — success
# =====================================================================


def test_transition_state_updates_state_and_appends_transition(store) -> None:
    """transition_state() sets new state and records transition in list."""
    store.save_comment(
        platform=PLATFORM, repo_full_name=REPO, pr_number=PR_NUM,
        fingerprint="fp_trans", platform_comment_id="500",
    )

    result = store.transition_state(
        platform=PLATFORM,
        repo_full_name=REPO,
        pr_number=PR_NUM,
        fingerprint="fp_trans",
        to_state=CommentState.RESOLVED,
        reason="auto-resolved",
    )
    assert result is True

    # Verify on-disk state
    data = json.loads(_state_file(store).read_text(encoding="utf-8"))
    pr_key = f"{PLATFORM}/{REPO}/{PR_NUM}"
    comment = data[pr_key]["inline_comments"][0]

    assert comment["state"] == "resolved"
    assert len(comment["transitions"]) == 1

    t = comment["transitions"][0]
    assert t["from_state"] == "active"
    assert t["to_state"] == "resolved"
    assert t["reason"] == "auto-resolved"


# =====================================================================
# transition_state — unknown fingerprint
# =====================================================================


def test_transition_state_returns_false_for_unknown_fingerprint(store) -> None:
    """transition_state() returns False when fingerprint is not found."""
    store.save_comment(
        platform=PLATFORM, repo_full_name=REPO, pr_number=PR_NUM,
        fingerprint="fp_known", platform_comment_id="600",
    )

    result = store.transition_state(
        platform=PLATFORM,
        repo_full_name=REPO,
        pr_number=PR_NUM,
        fingerprint="fp_unknown",
        to_state=CommentState.RESOLVED,
    )
    assert result is False


# =====================================================================
# PR key format
# =====================================================================


def test_pr_key_format_is_platform_repo_pr() -> None:
    """PR key is platform/repo_full_name/pr_number."""
    key = CommentStateStore._pr_key("github", "owner/repo", 99)
    assert key == "github/owner/repo/99"


# =====================================================================
# Different PRs — isolation
# =====================================================================


def test_different_prs_stored_under_different_keys(store) -> None:
    """Comments for different PRs are stored under separate keys."""
    store.save_comment(
        platform=PLATFORM, repo_full_name=REPO, pr_number=1,
        fingerprint="fp_pr1", platform_comment_id="700",
    )
    store.save_comment(
        platform=PLATFORM, repo_full_name=REPO, pr_number=2,
        fingerprint="fp_pr2", platform_comment_id="701",
    )

    data = json.loads(_state_file(store).read_text(encoding="utf-8"))
    assert f"{PLATFORM}/{REPO}/1" in data
    assert f"{PLATFORM}/{REPO}/2" in data

    pr1_comments = store.get_comments_for_pr(PLATFORM, REPO, 1)
    pr2_comments = store.get_comments_for_pr(PLATFORM, REPO, 2)
    assert len(pr1_comments) == 1
    assert len(pr2_comments) == 1
    assert pr1_comments[0].finding_fingerprint == "fp_pr1"
    assert pr2_comments[0].finding_fingerprint == "fp_pr2"


# =====================================================================
# get_comments_for_pr — unknown PR
# =====================================================================


def test_get_comments_for_pr_returns_empty_for_unknown_pr(store) -> None:
    """get_comments_for_pr() returns [] when PR has no saved comments."""
    comments = store.get_comments_for_pr(PLATFORM, REPO, 999)
    assert comments == []
