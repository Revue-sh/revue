"""Tests for comments domain models (REVUE-110)."""

from revue.comments.models import CommentState


def test_wont_fix_state_exists() -> None:
    """CommentState must include wont_fix (required by Story B, REVUE-112)."""
    assert CommentState.WONT_FIX.value == "wont_fix"


def test_all_required_states_present() -> None:
    """All states required by Epic 87 must be defined."""
    required = {"unresolved", "auto_resolved", "manually_resolved_with_reply",
                "manually_resolved_no_reply", "wont_fix"}
    values = {s.value for s in CommentState}
    assert required <= values, f"Missing states: {required - values}"
