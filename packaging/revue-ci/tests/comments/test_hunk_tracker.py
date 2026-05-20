"""Tests for HunkTracker state machine (REVUE-211).

Covers all 14 legal state machine paths, all 6 forbidden transitions,
sentinel parsing, and sentinel-based prior-state reconstruction.

Design contract:
- build_prior() reads sentinels from thread replies and encodes them in
  the returned dict as "sentinel_state".
- resolution_status() reads "sentinel_state" from prior_entry — it does NOT
  call get_thread_replies() directly (that's build_prior()'s job).
- This separation keeps resolution_status() pure and pr_num-free.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from revue_core.comments.hunk_tracker import (
    HunkTracker,
    InvalidStateTransitionError,
    NovaSingleShotResolutionStrategy,
)
from revue_core.comments.models import HunkState, ResolutionResult, ResolutionVerdict


# ---------------------------------------------------------------------------
# Fingerprint constants — 16-char hex matching production fingerprint format
# ---------------------------------------------------------------------------

_FP_PATH_1   = "a1b2c3d4e5f6a7b8"
_FP_PATH_2   = "b2c3d4e5f6a7b8c9"
_FP_PATH_3   = "c3d4e5f6a7b8c9d0"
_FP_PATH_4   = "d4e5f6a7b8c9d0e1"
_FP_PATH_5   = "e5f6a7b8c9d0e1f2"
_FP_PATH_6   = "f6a7b8c9d0e1f2a3"
_FP_PATH_6B  = "f6b0c9d8e7a1b2c3"
_FP_PATH_7   = "a7b8c9d0e1f2a3b4"
_FP_PATH_8   = "b8c9d0e1f2a3b4c5"
_FP_PATH_9   = "c9d0e1f2a3b4c5d6"
_FP_PATH_9B  = "c9e0f1a2b3c4d5e6"
_FP_PATH_10  = "d0e1f2a3b4c5d6e7"
_FP_PATH_11  = "e1f2a3b4c5d6e7f8"
_FP_PATH_11F = "e1a2b3c4d5e6f7a8"
_FP_PATH_12  = "f2a3b4c5d6e7f8a9"
_FP_PATH_12F = "f2b3c4d5e6f7a8b9"
_FP_PATH_13  = "a3b4c5d6e7f8a9b0"
_FP_PATH_13F = "a3c4d5e6f7a8b9c0"
_FP_PATH_14  = "b4c5d6e7f8a9b0c1"
_FP_PATH_AC5 = "ac50b6c7d8e9f0a1"
_FP_TERMINAL = "deadbeefdeadbeef"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter(
    existing_comments: list[dict] | None = None,
    thread_replies_map: dict[str, list[dict]] | None = None,
) -> MagicMock:
    """Return a stub VCSAdapter with preset comment/reply data."""
    adapter = MagicMock()
    adapter.get_existing_comments.return_value = existing_comments or []
    thread_replies_map = thread_replies_map or {}

    def _replies(pr_id: int, comment_id: str) -> list[dict]:
        return thread_replies_map.get(str(comment_id), [])

    adapter.get_thread_replies.side_effect = _replies
    adapter.resolve_inline_comment.return_value = True
    return adapter


def _make_dedup_store(unresolved: dict | None = None) -> MagicMock:
    store = MagicMock()
    store.get_unresolved_fingerprints.return_value = unresolved or {}
    return store


_VERDICT_MAP = {
    "fully": ResolutionVerdict.FULLY,
    "partial": ResolutionVerdict.PARTIAL,
    "not": ResolutionVerdict.UNRESOLVED,
}


def _make_resolution_strategy(verdict: str = "fully", guidance: str = "Issue addressed.") -> MagicMock:
    strategy = MagicMock()
    strategy.resolve.return_value = ResolutionResult(
        verdict=_VERDICT_MAP[verdict],
        guidance=guidance,
    )
    return strategy


def _sentinel(state: str, fp: str = "abcd1234efab5678", ts: str = "2026-05-04T10:00:00Z") -> str:
    return f"[//]: # (revue:state={state}:fp={fp}:ts={ts})"


def _prior(
    comment_id: str = "42",
    file_path: str = "a.py",
    line_number: int = 10,
    resolved: bool = False,
    sentinel_state: str | None = None,
) -> dict:
    """Build a prior_entry dict as build_prior() would produce."""
    entry: dict = {
        "platform_comment_id": comment_id,
        "file_path": file_path,
        "line_number": line_number,
        "resolved": resolved,
    }
    if sentinel_state is not None:
        entry["sentinel_state"] = sentinel_state
    return entry


# ---------------------------------------------------------------------------
# Path 1: PLATFORM_RESOLVED — human resolved thread
# ---------------------------------------------------------------------------


def test_path_platform_resolved_skips_analysis():
    """Prior entry with resolved=True → PLATFORM_RESOLVED, no Nova call."""
    # Arrange
    strategy = MagicMock()
    adapter = MagicMock()
    tracker = HunkTracker(
        adapter=adapter,
        dedup_store=MagicMock(),
        resolution_strategy=strategy,
    )

    # Act
    result = tracker.resolution_status(_FP_PATH_1, _prior(resolved=True), new_diff="")

    # Assert
    assert result == HunkState.PLATFORM_RESOLVED
    strategy.resolve.assert_not_called()
    adapter.resolve_inline_comment.assert_not_called()
    adapter.reply_to_comment.assert_not_called()


# ---------------------------------------------------------------------------
# Path 2: AUTO_RESOLVED sentinel — terminal, skip entirely
# ---------------------------------------------------------------------------


def test_path_auto_resolved_sentinel_skips_analysis():
    """Prior entry with sentinel_state='auto_resolved' → terminal, no Nova."""
    # Arrange
    strategy = MagicMock()
    adapter = MagicMock()
    tracker = HunkTracker(
        adapter=adapter,
        dedup_store=MagicMock(),
        resolution_strategy=strategy,
    )

    # Act
    result = tracker.resolution_status(
        _FP_PATH_2, _prior(sentinel_state="auto_resolved"), new_diff=""
    )

    # Assert
    assert result == HunkState.AUTO_RESOLVED
    strategy.resolve.assert_not_called()
    adapter.resolve_inline_comment.assert_not_called()
    adapter.reply_to_comment.assert_not_called()


# ---------------------------------------------------------------------------
# Path 3: UNTOUCHED — file not in current diff
# ---------------------------------------------------------------------------


def test_path_untouched_no_action():
    """Empty new_diff → UNTOUCHED, no Nova, no reply."""
    # Arrange
    strategy = MagicMock()
    adapter = MagicMock()
    tracker = HunkTracker(
        adapter=adapter,
        dedup_store=MagicMock(),
        resolution_strategy=strategy,
    )

    # Act
    result = tracker.resolution_status(_FP_PATH_3, _prior(), new_diff="")

    # Assert
    assert result == HunkState.UNTOUCHED
    strategy.resolve.assert_not_called()
    adapter.resolve_inline_comment.assert_not_called()
    adapter.reply_to_comment.assert_not_called()


# ---------------------------------------------------------------------------
# Path 4: CODE_REMOVED — hunk deleted, auto-resolve without Nova
# ---------------------------------------------------------------------------


def test_path_code_removed_auto_resolves_without_nova():
    """Lines removed from diff → CODE_REMOVED → RESOLVE_REPLY_POSTED, no Nova call."""
    # Arrange — all three deleted lines fall before new_line=10 which is absent in new file
    strategy = MagicMock()
    adapter = MagicMock()
    adapter.resolve_inline_comment.return_value = True
    tracker = HunkTracker(
        adapter=adapter,
        dedup_store=MagicMock(),
        resolution_strategy=strategy,
    )
    diff_deletion = "@@ -8,5 +8,0 @@ def foo():\n-    x = 1\n-    y = 2\n-    return x + y\n"

    # Act
    result = tracker.resolution_status(
        _FP_PATH_4, _prior(comment_id="60", line_number=10), new_diff=diff_deletion
    )

    # Assert — state machine outcome
    assert result == HunkState.RESOLVE_REPLY_POSTED
    # Assert — Nova was not consulted (code removal needs no semantic check)
    strategy.resolve.assert_not_called()
    # Assert — resolve was called with an auto_resolved sentinel in the body
    adapter.resolve_inline_comment.assert_called_once()
    resolve_body = adapter.resolve_inline_comment.call_args[1]["reply_body"]
    assert "revue:state=auto_resolved" in resolve_body
    assert _FP_PATH_4 in resolve_body


# ---------------------------------------------------------------------------
# Path 5: CHANGED → Nova confirms fully addressed
# ---------------------------------------------------------------------------


def test_path_nova_fully_addressed_resolves():
    """Lines changed, Nova says fully addressed → RESOLVE_REPLY_POSTED."""
    # Arrange — @@ -3,5 +3,5 @@ puts new_line=3 in the diff; line_number=3 matches → CHANGED path
    strategy = _make_resolution_strategy("fully")
    adapter = MagicMock()
    adapter.resolve_inline_comment.return_value = True
    tracker = HunkTracker(adapter=adapter, dedup_store=MagicMock(), resolution_strategy=strategy)
    diff_changed = "@@ -3,5 +3,5 @@ def bar():\n-    old_code()\n+    new_code()\n"

    # Act
    result = tracker.resolution_status(
        _FP_PATH_5, _prior(comment_id="70", line_number=3), new_diff=diff_changed
    )

    # Assert — state machine outcome
    assert result == HunkState.RESOLVE_REPLY_POSTED
    # Assert — Nova was consulted and the code change was evaluated
    strategy.resolve.assert_called_once()
    # Assert — thread resolved with auto_resolved sentinel embedded in body
    adapter.resolve_inline_comment.assert_called_once()
    resolve_body = adapter.resolve_inline_comment.call_args[1]["reply_body"]
    assert "revue:state=auto_resolved" in resolve_body
    assert _FP_PATH_5 in resolve_body


# ---------------------------------------------------------------------------
# Path 6: CHANGED → Nova says not fully addressed → FOLLOW_UP_POSTED
# ---------------------------------------------------------------------------


def test_resolution_partial_posts_followup_reply():
    """Lines changed, Nova says partial → FOLLOW_UP_POSTED, follow_up sentinel in reply body."""
    # Arrange — @@ -5,5 +5,5 @@ puts new_line=5 in the diff → CHANGED path
    strategy = _make_resolution_strategy("partial", guidance="The null check is still missing.")
    adapter = MagicMock()
    adapter.reply_to_comment.return_value = "reply-id"
    tracker = HunkTracker(adapter=adapter, dedup_store=MagicMock(), resolution_strategy=strategy)
    diff_changed = "@@ -5,5 +5,5 @@ def baz():\n-    bad()\n+    better()\n"

    # Act
    result = tracker.resolution_status(
        _FP_PATH_6, _prior(comment_id="80", line_number=5), new_diff=diff_changed
    )

    # Assert — state machine outcome
    assert result == HunkState.FOLLOW_UP_POSTED
    # Assert — Nova evaluated the change
    strategy.resolve.assert_called_once()
    # Assert — follow-up reply posted with follow_up_posted sentinel; thread NOT resolved
    adapter.reply_to_comment.assert_called_once()
    reply_body = adapter.reply_to_comment.call_args[0][2]
    assert "revue:state=follow_up_posted" in reply_body
    assert _FP_PATH_6 in reply_body
    assert "The null check is still missing." in reply_body
    adapter.resolve_inline_comment.assert_not_called()


def test_resolution_partial_does_not_auto_resolve():
    """Partial resolution must not resolve the thread — only a follow-up is posted."""
    # Arrange
    strategy = _make_resolution_strategy("partial")
    adapter = MagicMock()
    adapter.reply_to_comment.return_value = "reply-id"
    tracker = HunkTracker(adapter=adapter, dedup_store=MagicMock(), resolution_strategy=strategy)
    diff_changed = "@@ -6,5 +6,5 @@ def qux():\n-    old()\n+    partial_fix()\n"

    # Act
    result = tracker.resolution_status(
        _FP_PATH_6B, _prior(comment_id="81", line_number=6), new_diff=diff_changed
    )

    # Assert — partial verdict must never auto-resolve
    assert result == HunkState.FOLLOW_UP_POSTED
    adapter.resolve_inline_comment.assert_not_called()


def test_resolution_full_proceeds_to_ac5():
    """Full resolution via Nova triggers auto-resolve (AC5) with auto_resolved sentinel."""
    # Arrange — @@ -1,5 +1,5 @@ puts new_line=1 in the diff; line_number=1 matches → CHANGED path
    strategy = _make_resolution_strategy("fully")
    adapter = MagicMock()
    adapter.resolve_inline_comment.return_value = True
    tracker = HunkTracker(adapter=adapter, dedup_store=MagicMock(), resolution_strategy=strategy)
    diff_changed = "@@ -1,5 +1,5 @@ def func():\n-    wrong()\n+    correct()\n"

    # Act
    result = tracker.resolution_status(
        _FP_PATH_AC5, _prior(comment_id="82", line_number=1), new_diff=diff_changed
    )

    # Assert — Nova path taken (not code-removal shortcut)
    assert result == HunkState.RESOLVE_REPLY_POSTED
    strategy.resolve.assert_called_once()
    # Assert — thread resolved with auto_resolved sentinel
    adapter.resolve_inline_comment.assert_called_once()
    resolve_body = adapter.resolve_inline_comment.call_args[1]["reply_body"]
    assert "revue:state=auto_resolved" in resolve_body
    assert _FP_PATH_AC5 in resolve_body


# ---------------------------------------------------------------------------
# Path 7: CHANGED → Nova fails → NOVA_ERROR (safe no-op)
# ---------------------------------------------------------------------------


def test_path_nova_error_leaves_thread_untouched():
    """Nova API fails → NOVA_ERROR, no comment posted, no resolve."""
    # Arrange — @@ -13,5 +13,5 @@ → the + line lands at new_line=13
    strategy = MagicMock()
    strategy.resolve.side_effect = RuntimeError("Nova timeout")
    adapter = MagicMock()
    tracker = HunkTracker(adapter=adapter, dedup_store=MagicMock(), resolution_strategy=strategy)
    diff_changed = "@@ -13,5 +13,5 @@ def something():\n-    fail()\n+    retry()\n"

    # Act
    result = tracker.resolution_status(
        _FP_PATH_7, _prior(comment_id="90", line_number=13), new_diff=diff_changed
    )

    # Assert
    assert result == HunkState.NOVA_ERROR
    adapter.resolve_inline_comment.assert_not_called()
    adapter.reply_to_comment.assert_not_called()


# ---------------------------------------------------------------------------
# Path 9: REPLY_FAILED — reply/resolve API fails, no sentinel written
# ---------------------------------------------------------------------------


def test_path_reply_failed_does_not_write_sentinel():
    """FULLY_ADDRESSED → resolve API fails → REPLY_FAILED; sentinel not persisted, retried next run."""
    # Arrange — @@ -18,5 +18,5 @@ puts new_line=18 in diff; line_number=18 → CHANGED path
    strategy = _make_resolution_strategy("fully")
    adapter = MagicMock()
    adapter.resolve_inline_comment.return_value = False  # API failure
    tracker = HunkTracker(adapter=adapter, dedup_store=MagicMock(), resolution_strategy=strategy)
    diff_changed = "@@ -18,5 +18,5 @@ class Foo:\n-    broken()\n+    fixed()\n"

    # Act
    result = tracker.resolution_status(
        _FP_PATH_9, _prior(comment_id="95", line_number=18), new_diff=diff_changed
    )

    # Assert — state machine outcome: attempt was made but API failed
    assert result == HunkState.REPLY_FAILED
    # Assert — Nova was consulted (this is path 9, not path 8 code-removal)
    strategy.resolve.assert_called_once()
    # Assert — resolve was attempted with the auto_resolved sentinel body
    adapter.resolve_inline_comment.assert_called_once()
    resolve_body = adapter.resolve_inline_comment.call_args[1]["reply_body"]
    assert "revue:state=auto_resolved" in resolve_body
    assert _FP_PATH_9 in resolve_body


def test_path_not_fully_addressed_reply_failed():
    """NOT_FULLY_ADDRESSED → reply API fails → REPLY_FAILED; sentinel not persisted, retried next run."""
    # Arrange — @@ -7,5 +7,5 @@ puts new_line=7 in diff; line_number=7 → CHANGED path
    strategy = _make_resolution_strategy("partial", guidance="Error handling still absent.")
    adapter = MagicMock()
    adapter.reply_to_comment.return_value = None  # reply API failure
    tracker = HunkTracker(adapter=adapter, dedup_store=MagicMock(), resolution_strategy=strategy)
    diff_changed = "@@ -7,5 +7,5 @@ def qux():\n-    old()\n+    partial()\n"

    # Act
    result = tracker.resolution_status(
        _FP_PATH_9B, _prior(comment_id="96", line_number=7), new_diff=diff_changed
    )

    # Assert — state machine outcome: reply attempted but failed
    assert result == HunkState.REPLY_FAILED
    # Assert — Nova evaluated the change (CHANGED path, not CODE_REMOVED)
    strategy.resolve.assert_called_once()
    # Assert — follow-up reply was attempted with the correct sentinel body
    adapter.reply_to_comment.assert_called_once()
    reply_body = adapter.reply_to_comment.call_args[0][2]
    assert "revue:state=follow_up_posted" in reply_body
    assert _FP_PATH_9B in reply_body
    # Assert — thread was NOT resolved despite the failure
    adapter.resolve_inline_comment.assert_not_called()


# ---------------------------------------------------------------------------
# Path 8: CODE_REMOVED → REPLY_FAILED — hunk deleted but resolve API fails
# ---------------------------------------------------------------------------


def test_path_code_removed_reply_failed():
    """Lines removed from diff, resolve API fails → CODE_REMOVED → REPLY_FAILED; retried next run."""
    # Arrange — deletion diff removes line 10; resolve call fails
    strategy = MagicMock()
    adapter = MagicMock()
    adapter.resolve_inline_comment.return_value = False  # API failure
    tracker = HunkTracker(
        adapter=adapter,
        dedup_store=MagicMock(),
        resolution_strategy=strategy,
    )
    diff_deletion = "@@ -8,5 +8,0 @@ def foo():\n-    x = 1\n-    y = 2\n-    return x + y\n"

    # Act
    result = tracker.resolution_status(
        _FP_PATH_8, _prior(comment_id="61", line_number=10), new_diff=diff_deletion
    )

    # Assert — state machine outcome: resolve attempted but failed
    assert result == HunkState.REPLY_FAILED
    # Assert — Nova was not consulted (code-removal needs no semantic check)
    strategy.resolve.assert_not_called()
    # Assert — resolve was attempted with auto_resolved sentinel body before the API failure
    adapter.resolve_inline_comment.assert_called_once()
    resolve_body = adapter.resolve_inline_comment.call_args[1]["reply_body"]
    assert "revue:state=auto_resolved" in resolve_body
    assert _FP_PATH_8 in resolve_body


# ---------------------------------------------------------------------------
# Path 10: [FOLLOW_UP_POSTED prior] → hunk still untouched
# ---------------------------------------------------------------------------


def test_path_follow_up_continuation_untouched():
    """Prior sentinel=follow_up_posted, empty new_diff → UNTOUCHED."""
    # Arrange
    strategy = MagicMock()
    adapter = MagicMock()
    tracker = HunkTracker(adapter=adapter, dedup_store=MagicMock(), resolution_strategy=strategy)

    # Act
    result = tracker.resolution_status(
        _FP_PATH_10,
        _prior(comment_id="200", sentinel_state="follow_up_posted"),
        new_diff="",
    )

    # Assert
    assert result == HunkState.UNTOUCHED
    strategy.resolve.assert_not_called()
    adapter.resolve_inline_comment.assert_not_called()
    adapter.reply_to_comment.assert_not_called()


# ---------------------------------------------------------------------------
# Path 12: [FOLLOW_UP_POSTED prior] → code changed → Nova confirms addressed
# ---------------------------------------------------------------------------


def test_path_follow_up_continuation_eventually_resolves():
    """Prior follow_up_posted, new diff fully fixes issue → Nova confirms → RESOLVE_REPLY_POSTED."""
    # Arrange — @@ -5,3 +5,3 @@ puts new_line=5 in diff; line_number=5 → CHANGED path (not CODE_REMOVED)
    strategy = _make_resolution_strategy("fully")
    adapter = MagicMock()
    adapter.resolve_inline_comment.return_value = True
    tracker = HunkTracker(adapter=adapter, dedup_store=MagicMock(), resolution_strategy=strategy)
    diff_fixed = "@@ -5,3 +5,3 @@ def final():\n-    partial_fix()\n+    complete_fix()\n"

    # Act
    result = tracker.resolution_status(
        _FP_PATH_12,
        _prior(comment_id="300", line_number=5, sentinel_state="follow_up_posted"),
        new_diff=diff_fixed,
    )

    # Assert — state machine outcome
    assert result == HunkState.RESOLVE_REPLY_POSTED
    # Assert — Nova confirmed the fix (CHANGED path taken, not code-removal shortcut)
    strategy.resolve.assert_called_once()
    # Assert — thread resolved with auto_resolved sentinel
    adapter.resolve_inline_comment.assert_called_once()
    resolve_body = adapter.resolve_inline_comment.call_args[1]["reply_body"]
    assert "revue:state=auto_resolved" in resolve_body
    assert _FP_PATH_12 in resolve_body


def test_path_follow_up_fully_addressed_reply_failed():
    """Prior follow_up, Nova fully addressed, resolve API fails → REPLY_FAILED; retried next run."""
    # Arrange — @@ -5,3 +5,3 @@ puts new_line=5 in diff; line_number=5 → CHANGED path
    strategy = _make_resolution_strategy("fully")
    adapter = MagicMock()
    adapter.resolve_inline_comment.return_value = False  # API failure
    tracker = HunkTracker(adapter=adapter, dedup_store=MagicMock(), resolution_strategy=strategy)
    diff_fixed = "@@ -5,3 +5,3 @@ def final():\n-    partial_fix()\n+    complete_fix()\n"

    # Act
    result = tracker.resolution_status(
        _FP_PATH_12F,
        _prior(comment_id="305", line_number=5, sentinel_state="follow_up_posted"),
        new_diff=diff_fixed,
    )

    # Assert — state machine outcome: Nova confirmed fix but resolve API failed
    assert result == HunkState.REPLY_FAILED
    # Assert — Nova was consulted (CHANGED path, not CODE_REMOVED)
    strategy.resolve.assert_called_once()
    # Assert — resolve was attempted with auto_resolved sentinel before API failure
    adapter.resolve_inline_comment.assert_called_once()
    resolve_body = adapter.resolve_inline_comment.call_args[1]["reply_body"]
    assert "revue:state=auto_resolved" in resolve_body
    assert _FP_PATH_12F in resolve_body


# ---------------------------------------------------------------------------
# Path 11: [FOLLOW_UP_POSTED prior] → code removed → auto-resolved
# ---------------------------------------------------------------------------


def test_path_follow_up_code_removed_auto_resolves():
    """Prior follow_up_posted, lines now deleted → CODE_REMOVED → RESOLVE_REPLY_POSTED."""
    # Arrange — deletion diff; line 10 absent from new file
    strategy = MagicMock()
    adapter = MagicMock()
    adapter.resolve_inline_comment.return_value = True
    tracker = HunkTracker(
        adapter=adapter,
        dedup_store=MagicMock(),
        resolution_strategy=strategy,
    )
    diff_deletion = "@@ -8,5 +8,0 @@ def foo():\n-    x = 1\n-    y = 2\n-    return x + y\n"

    # Act
    result = tracker.resolution_status(
        _FP_PATH_11,
        _prior(comment_id="310", line_number=10, sentinel_state="follow_up_posted"),
        new_diff=diff_deletion,
    )

    # Assert — state machine outcome
    assert result == HunkState.RESOLVE_REPLY_POSTED
    # Assert — Nova not consulted (code removal is self-evident)
    strategy.resolve.assert_not_called()
    # Assert — resolve attempted with auto_resolved sentinel
    adapter.resolve_inline_comment.assert_called_once()
    resolve_body = adapter.resolve_inline_comment.call_args[1]["reply_body"]
    assert "revue:state=auto_resolved" in resolve_body
    assert _FP_PATH_11 in resolve_body


def test_path_follow_up_code_removed_reply_failed():
    """Prior follow_up_posted, lines deleted, resolve API fails → CODE_REMOVED → REPLY_FAILED."""
    # Arrange — deletion diff; resolve call fails
    strategy = MagicMock()
    adapter = MagicMock()
    adapter.resolve_inline_comment.return_value = False  # API failure
    tracker = HunkTracker(
        adapter=adapter,
        dedup_store=MagicMock(),
        resolution_strategy=strategy,
    )
    diff_deletion = "@@ -8,5 +8,0 @@ def foo():\n-    x = 1\n-    y = 2\n-    return x + y\n"

    # Act
    result = tracker.resolution_status(
        _FP_PATH_11F,
        _prior(comment_id="315", line_number=10, sentinel_state="follow_up_posted"),
        new_diff=diff_deletion,
    )

    # Assert — state machine outcome: resolve attempted but API failed
    assert result == HunkState.REPLY_FAILED
    # Assert — Nova not consulted (code-removal path)
    strategy.resolve.assert_not_called()
    # Assert — resolve was attempted with correct sentinel body before failure
    adapter.resolve_inline_comment.assert_called_once()
    resolve_body = adapter.resolve_inline_comment.call_args[1]["reply_body"]
    assert "revue:state=auto_resolved" in resolve_body
    assert _FP_PATH_11F in resolve_body


# ---------------------------------------------------------------------------
# Path 13: [FOLLOW_UP_POSTED prior] → code changed → Nova: not fully resolved
# ---------------------------------------------------------------------------


def test_path_follow_up_nova_still_not_resolved():
    """Prior follow_up, Nova partial → updated FOLLOW_UP_POSTED with fresh sentinel and guidance."""
    # Arrange — @@ -3,5 +3,5 @@ puts new_line=3 in diff; line_number=3 → CHANGED path
    strategy = _make_resolution_strategy("partial", guidance="Still missing error handling.")
    adapter = MagicMock()
    adapter.reply_to_comment.return_value = "reply-id-13"
    tracker = HunkTracker(adapter=adapter, dedup_store=MagicMock(), resolution_strategy=strategy)
    diff_partial = "@@ -3,5 +3,5 @@ def bar():\n-    old_code()\n+    partial_fix()\n"

    # Act
    result = tracker.resolution_status(
        _FP_PATH_13,
        _prior(comment_id="320", line_number=3, sentinel_state="follow_up_posted"),
        new_diff=diff_partial,
    )

    # Assert — state machine outcome
    assert result == HunkState.FOLLOW_UP_POSTED
    # Assert — Nova re-evaluated the change
    strategy.resolve.assert_called_once()
    # Assert — follow-up reply posted with updated sentinel and Nova guidance
    adapter.reply_to_comment.assert_called_once()
    reply_body = adapter.reply_to_comment.call_args[0][2]
    assert "revue:state=follow_up_posted" in reply_body
    assert _FP_PATH_13 in reply_body
    assert "Still missing error handling." in reply_body
    # Assert — thread NOT resolved
    adapter.resolve_inline_comment.assert_not_called()


def test_path_follow_up_not_fully_addressed_reply_failed():
    """Prior follow_up, Nova partial, reply API fails → REPLY_FAILED; sentinel not updated."""
    # Arrange — @@ -3,5 +3,5 @@ puts new_line=3 in diff; line_number=3 → CHANGED path
    strategy = _make_resolution_strategy("partial", guidance="Still missing error handling.")
    adapter = MagicMock()
    adapter.reply_to_comment.return_value = None  # reply API failure
    tracker = HunkTracker(adapter=adapter, dedup_store=MagicMock(), resolution_strategy=strategy)
    diff_partial = "@@ -3,5 +3,5 @@ def bar():\n-    old_code()\n+    partial_fix()\n"

    # Act
    result = tracker.resolution_status(
        _FP_PATH_13F,
        _prior(comment_id="325", line_number=3, sentinel_state="follow_up_posted"),
        new_diff=diff_partial,
    )

    # Assert — state machine outcome: reply attempted but API failed
    assert result == HunkState.REPLY_FAILED
    # Assert — Nova was consulted (CHANGED path)
    strategy.resolve.assert_called_once()
    # Assert — reply was attempted with follow_up_posted sentinel before API failure
    adapter.reply_to_comment.assert_called_once()
    reply_body = adapter.reply_to_comment.call_args[0][2]
    assert "revue:state=follow_up_posted" in reply_body
    assert _FP_PATH_13F in reply_body
    # Assert — thread was NOT resolved
    adapter.resolve_inline_comment.assert_not_called()


# ---------------------------------------------------------------------------
# Path 14: [FOLLOW_UP_POSTED prior] → code changed → Nova API error
# ---------------------------------------------------------------------------


def test_path_follow_up_nova_error():
    """Prior follow_up, Nova raises → NOVA_ERROR, no reply posted."""
    # Arrange
    strategy = MagicMock()
    strategy.resolve.side_effect = RuntimeError("LLM timeout")
    adapter = MagicMock()
    tracker = HunkTracker(adapter=adapter, dedup_store=MagicMock(), resolution_strategy=strategy)
    diff_changed = "@@ -3,5 +3,5 @@ def bar():\n-    old_code()\n+    new_code()\n"

    # Act
    result = tracker.resolution_status(
        _FP_PATH_14,
        _prior(comment_id="330", line_number=3, sentinel_state="follow_up_posted"),
        new_diff=diff_changed,
    )

    # Assert
    assert result == HunkState.NOVA_ERROR
    strategy.resolve.assert_called_once()
    adapter.reply_to_comment.assert_not_called()
    adapter.resolve_inline_comment.assert_not_called()


# ---------------------------------------------------------------------------
# Guard tests — forbidden transitions
# ---------------------------------------------------------------------------


def test_guard_auto_resolved_is_terminal():
    """AUTO_RESOLVED → any transition raises InvalidStateTransitionError."""
    # Arrange
    tracker = HunkTracker(adapter=MagicMock(), dedup_store=MagicMock(), resolution_strategy=MagicMock())

    # Act / Assert
    with pytest.raises(InvalidStateTransitionError):
        tracker._transition(HunkState.AUTO_RESOLVED, HunkState.CHANGED)


def test_guard_platform_resolved_is_terminal():
    """PLATFORM_RESOLVED → any transition raises InvalidStateTransitionError."""
    # Arrange
    tracker = HunkTracker(adapter=MagicMock(), dedup_store=MagicMock(), resolution_strategy=MagicMock())

    # Act / Assert
    with pytest.raises(InvalidStateTransitionError):
        tracker._transition(HunkState.PLATFORM_RESOLVED, HunkState.CHANGED)


def test_guard_nova_error_cannot_transition_to_posted():
    """NOVA_ERROR → RESOLVE_REPLY_POSTED raises InvalidStateTransitionError."""
    # Arrange
    tracker = HunkTracker(adapter=MagicMock(), dedup_store=MagicMock(), resolution_strategy=MagicMock())

    # Act / Assert
    with pytest.raises(InvalidStateTransitionError):
        tracker._transition(HunkState.NOVA_ERROR, HunkState.RESOLVE_REPLY_POSTED)


def test_guard_untouched_cannot_call_nova():
    """UNTOUCHED → NOVA_CALLED raises InvalidStateTransitionError."""
    # Arrange
    tracker = HunkTracker(adapter=MagicMock(), dedup_store=MagicMock(), resolution_strategy=MagicMock())

    # Act / Assert
    with pytest.raises(InvalidStateTransitionError):
        tracker._transition(HunkState.UNTOUCHED, HunkState.NOVA_CALLED)


def test_forbidden_transition_follow_up_cannot_skip_to_resolve():
    """FOLLOW_UP_POSTED → RESOLVE_REPLY_POSTED directly raises (must pass through NOVA_CALLED)."""
    # Arrange
    tracker = HunkTracker(adapter=MagicMock(), dedup_store=MagicMock(), resolution_strategy=MagicMock())

    # Act / Assert
    with pytest.raises(InvalidStateTransitionError):
        tracker._transition(HunkState.FOLLOW_UP_POSTED, HunkState.RESOLVE_REPLY_POSTED)


def test_guard_nova_error_cannot_post_follow_up():
    """NOVA_ERROR → FOLLOW_UP_POSTED raises InvalidStateTransitionError."""
    # Arrange
    tracker = HunkTracker(adapter=MagicMock(), dedup_store=MagicMock(), resolution_strategy=MagicMock())

    # Act / Assert
    with pytest.raises(InvalidStateTransitionError):
        tracker._transition(HunkState.NOVA_ERROR, HunkState.FOLLOW_UP_POSTED)


# ---------------------------------------------------------------------------
# Sentinel tests
# ---------------------------------------------------------------------------


def test_sentinel_parsed_from_thread_reply():
    """Sentinel embedded in a reply body is correctly parsed."""
    # Arrange
    tracker = HunkTracker(adapter=MagicMock(), dedup_store=MagicMock(), resolution_strategy=MagicMock())
    body = f"Some reply text.\n{_sentinel('auto_resolved', fp='deadbeef01234567')}\nMore text."

    # Act
    result = tracker._parse_sentinel(body)

    # Assert
    assert result is not None
    assert result["state"] == "auto_resolved"
    assert result["fp"] == "deadbeef01234567"


def test_most_recent_sentinel_is_authoritative():
    """When multiple sentinel replies exist, the one with the latest ts wins."""
    # Arrange
    tracker = HunkTracker(adapter=MagicMock(), dedup_store=MagicMock(), resolution_strategy=MagicMock())
    older = _sentinel("follow_up_posted", fp="deadbeef01234567", ts="2026-05-01T10:00:00Z")
    newer = _sentinel("auto_resolved", fp="deadbeef01234567", ts="2026-05-04T12:00:00Z")

    # Act
    result = tracker._most_recent_sentinel([
        {"id": "1", "body": older, "created_at": "2026-05-01T10:00:00Z"},
        {"id": "2", "body": newer, "created_at": "2026-05-04T12:00:00Z"},
    ])

    # Assert
    assert result is not None
    assert result["state"] == "auto_resolved"


def test_no_sentinel_means_initial_state():
    """Thread with no sentinel replies → _most_recent_sentinel returns None."""
    # Arrange
    tracker = HunkTracker(adapter=MagicMock(), dedup_store=MagicMock(), resolution_strategy=MagicMock())

    # Act
    result = tracker._most_recent_sentinel([
        {"id": "1", "body": "Just a normal reply", "created_at": "2026-05-01T10:00:00Z"},
    ])

    # Assert
    assert result is None


def test_terminal_auto_resolved_skipped_on_next_run():
    """Finding with sentinel_state='auto_resolved' in prior_entry is never re-analysed."""
    # Arrange
    strategy = MagicMock()
    adapter = MagicMock()
    tracker = HunkTracker(adapter=adapter, dedup_store=MagicMock(), resolution_strategy=strategy)
    diff_changed = "@@ -1,5 +1,5 @@ def z():\n-    old()\n+    new()\n"

    # Act
    result = tracker.resolution_status(
        _FP_TERMINAL,
        _prior(comment_id="500", line_number=1, sentinel_state="auto_resolved"),
        new_diff=diff_changed,
    )

    # Assert
    assert result == HunkState.AUTO_RESOLVED
    strategy.resolve.assert_not_called()
    adapter.resolve_inline_comment.assert_not_called()
    adapter.reply_to_comment.assert_not_called()


# ---------------------------------------------------------------------------
# Integration: get_thread_replies called for each prior comment in build_prior
# ---------------------------------------------------------------------------


def test_get_thread_replies_called_for_each_prior_comment():
    """build_prior() calls get_thread_replies for every comment from get_existing_comments."""
    # Arrange
    comments = [
        {"id": "10", "body": "Issue A", "_discussion_resolved": False, "_discussion_id": "10"},
        {"id": "20", "body": "Issue B", "_discussion_resolved": False, "_discussion_id": "20"},
    ]
    adapter = _make_adapter(existing_comments=comments)
    store = _make_dedup_store()
    tracker = HunkTracker(adapter=adapter, dedup_store=store, resolution_strategy=MagicMock())

    # Act
    tracker.build_prior(platform_str="bitbucket", pr_num=42)

    # Assert
    adapter.get_thread_replies.assert_any_call(42, "10")
    adapter.get_thread_replies.assert_any_call(42, "20")
    assert adapter.get_thread_replies.call_count == 2


def test_build_prior_encodes_sentinel_state_in_entry():
    """build_prior() reads auto_resolved sentinel from reply and encodes it in entry."""
    # Arrange
    fp = "abcd1234efab5678"  # 16-char hex fingerprint
    sentinel = _sentinel("auto_resolved", fp=fp)
    adapter = _make_adapter(
        existing_comments=[{"id": "500", "body": "Old issue", "_discussion_resolved": False}],
        thread_replies_map={"500": [{"id": "501", "body": sentinel, "created_at": "2026-05-03T15:30:00Z"}]},
    )
    store = _make_dedup_store({fp: {"platform_comment_id": "500", "file_path": "z.py", "resolved": False}})
    tracker = HunkTracker(adapter=adapter, dedup_store=store, resolution_strategy=MagicMock())

    # Act
    prior = tracker.build_prior(platform_str="bitbucket", pr_num=42)

    # Assert — the API-scanned sentinel_state must be merged into the returned entry so
    # resolution_status() can act on it without re-fetching thread replies.
    assert fp in prior, "fingerprint missing from build_prior result"
    assert prior[fp]["sentinel_state"] == "auto_resolved", (
        f"expected sentinel_state='auto_resolved', got {prior[fp].get('sentinel_state')!r}"
    )


# ---------------------------------------------------------------------------
# Phase 7: HunkTracker debug logging — [HunkTracker] prefix distinguishability
# ---------------------------------------------------------------------------


def test_debug_logs_platform_resolved_path():
    """resolution_status() logs a [HunkTracker] debug message when path is PLATFORM_RESOLVED."""
    # Arrange
    tracker = HunkTracker(
        adapter=MagicMock(),
        dedup_store=MagicMock(),
        resolution_strategy=MagicMock(),
    )

    # Act
    with patch("revue_core.comments.hunk_tracker.Log") as mock_log:
        tracker.resolution_status(_FP_PATH_1, _prior(resolved=True), new_diff="")

    # Assert — at least one debug call with [HunkTracker] prefix and fingerprint
    assert mock_log.pipeline.debug.call_count >= 1
    debug_calls = " ".join(str(c) for c in mock_log.pipeline.debug.call_args_list)
    assert "[HunkTracker]" in debug_calls, f"[HunkTracker] prefix missing in: {debug_calls!r}"
    assert _FP_PATH_1 in debug_calls, f"fingerprint {_FP_PATH_1!r} missing in log output"


def test_debug_logs_untouched_path():
    """resolution_status() logs a [HunkTracker] debug message when path is UNTOUCHED."""
    # Arrange
    tracker = HunkTracker(
        adapter=MagicMock(),
        dedup_store=MagicMock(),
        resolution_strategy=MagicMock(),
    )

    # Act — empty diff → file not touched
    with patch("revue_core.comments.hunk_tracker.Log") as mock_log:
        tracker.resolution_status(_FP_PATH_3, _prior(resolved=False), new_diff="")

    # Assert
    assert mock_log.pipeline.debug.call_count >= 1
    debug_calls = " ".join(str(c) for c in mock_log.pipeline.debug.call_args_list)
    assert "[HunkTracker]" in debug_calls
    assert _FP_PATH_3 in debug_calls


def test_debug_logs_code_removed_path():
    """resolution_status() logs a [HunkTracker] debug message when path is CODE_REMOVED."""
    # Arrange
    adapter = MagicMock()
    adapter.resolve_inline_comment.return_value = True
    tracker = HunkTracker(
        adapter=adapter,
        dedup_store=MagicMock(),
        resolution_strategy=MagicMock(),
    )
    diff = "@@ -8,5 +8,0 @@ def foo():\n-    x = 1\n-    y = 2\n"

    # Act — file in diff but line 10 absent → CODE_REMOVED
    with patch("revue_core.comments.hunk_tracker.Log") as mock_log:
        tracker.resolution_status(_FP_PATH_4, _prior(line_number=10), new_diff=diff)

    # Assert
    assert mock_log.pipeline.debug.call_count >= 1
    debug_calls = " ".join(str(c) for c in mock_log.pipeline.debug.call_args_list)
    assert "[HunkTracker]" in debug_calls
    assert _FP_PATH_4 in debug_calls


def test_debug_logs_every_state_transition():
    """_transition() logs a [HunkTracker] debug record for every legal state change."""
    # Arrange
    tracker = HunkTracker(
        adapter=MagicMock(),
        dedup_store=MagicMock(),
        resolution_strategy=MagicMock(),
    )

    # Act — INITIAL → PLATFORM_RESOLVED is one legal transition
    with patch("revue_core.comments.hunk_tracker.Log") as mock_log:
        tracker._transition(HunkState.INITIAL, HunkState.PLATFORM_RESOLVED)

    # Assert — transition record includes both state names
    assert mock_log.pipeline.debug.call_count >= 1
    debug_calls = " ".join(str(c) for c in mock_log.pipeline.debug.call_args_list)
    assert "[HunkTracker]" in debug_calls
    assert "platform_resolved" in debug_calls.lower()


def test_debug_logs_build_prior_fingerprint_counts():
    """build_prior() logs [HunkTracker] debug entries with dedup and api fingerprint counts."""
    # Arrange
    fp = "abcd1234efab5678"
    sentinel = _sentinel("auto_resolved", fp=fp)
    adapter = _make_adapter(
        existing_comments=[{"id": "300", "body": f"Finding\n[//]: # (revue:fp:{fp})", "_discussion_resolved": False}],
        thread_replies_map={"300": [{"id": "301", "body": sentinel}]},
    )
    store = _make_dedup_store({fp: {"platform_comment_id": "300", "file_path": "x.py", "resolved": False}})
    tracker = HunkTracker(adapter=adapter, dedup_store=store, resolution_strategy=MagicMock())

    # Act
    with patch("revue_core.comments.hunk_tracker.Log") as mock_log:
        tracker.build_prior(platform_str="bitbucket", pr_num=99)

    # Assert — at least two build_prior debug records (entry + result)
    assert mock_log.pipeline.debug.call_count >= 2, (
        f"expected ≥2 DEBUG records from build_prior(), got {mock_log.pipeline.debug.call_count}"
    )
    debug_calls = " ".join(str(c) for c in mock_log.pipeline.debug.call_args_list)
    assert "[HunkTracker]" in debug_calls
