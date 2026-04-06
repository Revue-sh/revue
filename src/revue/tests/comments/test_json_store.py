"""Unit tests for PerPRCommentStore (REVUE-110 AC2)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from revue.comments.json_store import PerPRCommentStore
from revue.comments.models import CommentState


@pytest.fixture()
def store(tmp_path):
    return PerPRCommentStore(tmp_path)


def _read_json(store: PerPRCommentStore, platform: str, pr_number: int) -> dict:
    path = store.repo_path / ".revue" / "comments" / f"{platform}-PR-{pr_number}.json"
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# TC2: First review — no file → all findings posted, file created
# ---------------------------------------------------------------------------

def test_has_fingerprint_returns_false_when_no_file(store) -> None:
    assert store.has_fingerprint("bitbucket", 42, "src/foo.py", "deadbeef") is False


def test_save_finding_creates_file_with_correct_structure(store) -> None:
    store.save_finding(
        platform="bitbucket",
        pr_number=42,
        file_path="src/revue/core/cli.py",
        fingerprint="abc123",
        platform_comment_id="999",
        line_number=10,
        comment_body="Potential null pointer",
    )
    data = _read_json(store, "bitbucket", 42)
    assert data["pr_number"] == 42
    assert data["platform"] == "bitbucket"
    entry = data["files"]["src/revue/core/cli.py"]["abc123"]
    assert entry["platform_comment_id"] == "999"
    assert entry["line_number"] == 10
    assert entry["state"] == "unresolved"


# ---------------------------------------------------------------------------
# TC1: Existing fingerprint → has_fingerprint returns True (post must be skipped)
# ---------------------------------------------------------------------------

def test_has_fingerprint_returns_true_after_save(store) -> None:
    store.save_finding("bitbucket", 42, "src/foo.py", "fp_exists", "101", 5, "body")
    assert store.has_fingerprint("bitbucket", 42, "src/foo.py", "fp_exists") is True


def test_has_fingerprint_returns_false_for_different_fingerprint(store) -> None:
    store.save_finding("bitbucket", 42, "src/foo.py", "fp_a", "101", 5, "body")
    assert store.has_fingerprint("bitbucket", 42, "src/foo.py", "fp_b") is False


# ---------------------------------------------------------------------------
# TC4: New finding saved with platform_comment_id
# ---------------------------------------------------------------------------

def test_save_finding_records_platform_comment_id(store) -> None:
    store.save_finding("github", 7, "lib/utils.py", "fp_new", "gh_555", 20, "Issue here")
    data = _read_json(store, "github", 7)
    assert data["files"]["lib/utils.py"]["fp_new"]["platform_comment_id"] == "gh_555"


# ---------------------------------------------------------------------------
# Platform isolation — different platforms, same PR number
# ---------------------------------------------------------------------------

def test_platform_isolation(store) -> None:
    store.save_finding("bitbucket", 1, "src/a.py", "fp1", "bb_1", 1, "body")
    store.save_finding("github", 1, "src/a.py", "fp1", "gh_1", 1, "body")
    assert (store.repo_path / ".revue" / "comments" / "bitbucket-PR-1.json").exists()
    assert (store.repo_path / ".revue" / "comments" / "github-PR-1.json").exists()
    # fp1 exists in bitbucket store but is isolated from github store
    assert store.has_fingerprint("bitbucket", 1, "src/a.py", "fp1") is True
    assert store.has_fingerprint("gitlab", 1, "src/a.py", "fp1") is False


# ---------------------------------------------------------------------------
# get_unresolved_fingerprints — returns only unresolved entries
# ---------------------------------------------------------------------------

def test_get_unresolved_fingerprints(store) -> None:
    store.save_finding("bitbucket", 5, "src/a.py", "fp_open", "1", 1, "open")
    store.save_finding("bitbucket", 5, "src/a.py", "fp_fixed", "2", 2, "fixed")
    store.mark_resolved("bitbucket", 5, "src/a.py", "fp_fixed", CommentState.AUTO_RESOLVED)

    fps = store.get_unresolved_fingerprints("bitbucket", 5)
    assert "fp_open" in fps
    assert "fp_fixed" not in fps


# ---------------------------------------------------------------------------
# Atomic write — no .tmp file left behind
# ---------------------------------------------------------------------------

def test_atomic_write_no_tmp_file_remains(store) -> None:
    store.save_finding("bitbucket", 99, "src/x.py", "fp_x", "1", 1, "body")
    tmp = store.repo_path / ".revue" / "comments" / "bitbucket-PR-99.json.tmp"
    assert not tmp.exists()


# ---------------------------------------------------------------------------
# Corrupted JSON store — graceful fallback (Quinn #2)
# ---------------------------------------------------------------------------

def test_corrupted_json_returns_false_for_has_fingerprint(store, capsys) -> None:
    """Malformed JSON in store file → has_fingerprint returns False, no crash."""
    path = store.repo_path / ".revue" / "comments" / "bitbucket-PR-42.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"incomplete":', encoding="utf-8")

    result = store.has_fingerprint("bitbucket", 42, "src/foo.py", "anyfingerprint")

    assert result is False
    assert "corrupt" in capsys.readouterr().err.lower()


def test_corrupted_json_returns_empty_for_get_unresolved(store) -> None:
    """Malformed JSON → get_unresolved_fingerprints returns empty dict, no crash."""
    path = store.repo_path / ".revue" / "comments" / "bitbucket-PR-10.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json at all", encoding="utf-8")

    result = store.get_unresolved_fingerprints("bitbucket", 10)

    assert result == {}
