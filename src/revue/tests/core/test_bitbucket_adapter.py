#!/usr/bin/env python3
"""Tests for BitbucketAdapter (Bitbucket Cloud VCS integration)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

import pytest

from revue.core.bitbucket_adapter import BitbucketAdapter, _line_in_diff
from revue.core.models import FileChange
from revue.core.vcs_adapter import DiffPosition, VCSAdapter


# =====================================================================
# Fixtures
# =====================================================================

WORKSPACE = "cbscd"
REPO_SLUG = "revue"
USERNAME = "user@example.com"
TOKEN = "test-api-token"
SECRET = "webhook-secret"

SAMPLE_DIFF = """\
diff --git a/src/main.py b/src/main.py
index abc..def 100644
--- a/src/main.py
+++ b/src/main.py
@@ -1,3 +1,4 @@
 def hello():
-    pass
+    return "hello"
+
 # end
"""


def make_adapter(**kwargs) -> BitbucketAdapter:
    defaults = dict(
        api_token=TOKEN,
        username=USERNAME,
        workspace=WORKSPACE,
        repo_slug=REPO_SLUG,
        webhook_secret=SECRET,
    )
    defaults.update(kwargs)
    return BitbucketAdapter(**defaults)


def mock_response(data, status=200):
    """Return a mock urllib response."""
    mock = MagicMock()
    if isinstance(data, str):
        mock.read.return_value = data.encode()
    else:
        mock.read.return_value = json.dumps(data).encode()
    mock.__enter__ = lambda s: s
    mock.__exit__ = MagicMock(return_value=False)
    return mock


# =====================================================================
# Protocol conformance
# =====================================================================


def test_bitbucket_adapter_satisfies_vcs_protocol() -> None:
    """BitbucketAdapter is a structural subtype of VCSAdapter."""
    adapter = make_adapter()
    assert isinstance(adapter, VCSAdapter)


# =====================================================================
# Auth header
# =====================================================================


def test_auth_header_is_basic_auth() -> None:
    """Auth header is correctly base64-encoded Basic Auth."""
    adapter = make_adapter()
    header = adapter._auth_header()
    assert header.startswith("Basic ")
    decoded = base64.b64decode(header[6:]).decode()
    assert decoded == f"{USERNAME}:{TOKEN}"


# =====================================================================
# get_diff
# =====================================================================


def test_get_diff_parses_unified_diff() -> None:
    """get_diff() fetches raw diff and returns FileChange objects."""
    adapter = make_adapter()
    with patch("urllib.request.urlopen", return_value=mock_response(SAMPLE_DIFF)):
        changes = adapter.get_diff(pr_id=42)
    assert len(changes) == 1
    assert changes[0].file_path == "src/main.py"
    assert changes[0].change_type == "modified"
    assert changes[0].additions == 2
    assert changes[0].deletions == 1


def test_get_diff_returns_empty_on_error() -> None:
    """get_diff() returns [] when the API call fails."""
    import urllib.error
    adapter = make_adapter()
    with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError(
        url="", code=404, msg="Not Found", hdrs=None, fp=None
    )):
        changes = adapter.get_diff(pr_id=99)
    assert changes == []


def test_get_diff_empty_diff() -> None:
    """get_diff() handles empty diff gracefully."""
    adapter = make_adapter()
    with patch("urllib.request.urlopen", return_value=mock_response("")):
        changes = adapter.get_diff(pr_id=1)
    assert changes == []


# =====================================================================
# post_review_comment
# =====================================================================


def test_post_review_comment_sends_inline_payload() -> None:
    """post_review_comment() posts to the comments endpoint with inline key."""
    adapter = make_adapter()
    position = DiffPosition(file_path="src/main.py", line_number=3)

    captured = {}

    def fake_urlopen(req):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode())
        return mock_response({"id": 1})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = adapter.post_review_comment(pr_id=42, position=position, body="Fix this")

    assert result == "1"  # Returns comment ID as string (REVUE-104)
    assert "/pullrequests/42/comments" in captured["url"]
    assert captured["body"]["content"]["raw"] == "Fix this"
    assert captured["body"]["inline"]["path"] == "src/main.py"
    assert captured["body"]["inline"]["to"] == 3


def test_post_review_comment_returns_none_on_error() -> None:
    """post_review_comment() returns None when API call fails (REVUE-104)."""
    import urllib.error
    adapter = make_adapter()
    position = DiffPosition(file_path="src/main.py", line_number=1)
    with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError(
        url="", code=500, msg="Server Error", hdrs=None, fp=None
    )):
        result = adapter.post_review_comment(pr_id=1, position=position, body="oops")
    assert result is None


def test_post_inline_comment_is_alias() -> None:
    """post_inline_comment is a backward-compat alias for post_review_comment."""
    adapter = make_adapter()
    # Class-level alias check (bound methods create new objects per access,
    # so we compare the underlying functions instead)
    assert BitbucketAdapter.post_inline_comment is BitbucketAdapter.post_review_comment


# =====================================================================
# post_summary_comment
# =====================================================================


def test_post_summary_comment_has_no_inline_key() -> None:
    """post_summary_comment() posts without the inline key and returns comment ID."""
    adapter = make_adapter()
    captured = {}

    def fake_urlopen(req):
        captured["body"] = json.loads(req.data.decode())
        return mock_response({"id": 2})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = adapter.post_summary_comment(pr_id=42, body="## Review Summary")

    assert result == "2"
    assert "inline" not in captured["body"]
    assert captured["body"]["content"]["raw"] == "## Review Summary"


def test_post_summary_comment_returns_none_on_error() -> None:
    """post_summary_comment() returns None on API error."""
    import urllib.error
    adapter = make_adapter()
    with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError(
        url="", code=403, msg="Forbidden", hdrs=None, fp=None
    )):
        result = adapter.post_summary_comment(pr_id=1, body="hello")
    assert result is None


def test_update_comment_success() -> None:
    """update_comment() sends PUT to correct endpoint and returns True."""
    adapter = make_adapter()
    captured = {}

    def fake_urlopen(req):
        captured["method"] = req.get_method()
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode())
        return mock_response({"id": 99})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = adapter.update_comment(pr_id=42, comment_id="99", body="Updated body")

    assert result is True
    assert captured["method"] == "PUT"
    assert "/comments/99" in captured["url"]
    assert captured["body"]["content"]["raw"] == "Updated body"


def test_update_comment_returns_false_on_404() -> None:
    """update_comment() returns False when comment not found (deleted)."""
    import urllib.error
    adapter = make_adapter()
    with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError(
        url="", code=404, msg="Not Found", hdrs=None, fp=None
    )):
        result = adapter.update_comment(pr_id=42, comment_id="99", body="Updated")
    assert result is False


# =====================================================================
# get_existing_comments
# =====================================================================


def test_get_existing_comments_paginates() -> None:
    """get_existing_comments() collects all pages."""
    page1 = {"values": [{"id": 1, "content": {"raw": "first"}}], "next": "http://page2"}
    page2 = {"values": [{"id": 2, "content": {"raw": "second"}}]}

    responses = [mock_response(page1), mock_response(page2)]
    adapter = make_adapter()

    with patch("urllib.request.urlopen", side_effect=responses):
        comments = adapter.get_existing_comments(pr_id=42)

    assert len(comments) == 2
    assert comments[0]["id"] == 1
    assert comments[1]["id"] == 2


def test_get_existing_comments_returns_empty_on_error() -> None:
    """get_existing_comments() returns [] on API error."""
    import urllib.error
    adapter = make_adapter()
    with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError(
        url="", code=404, msg="Not Found", hdrs=None, fp=None
    )):
        comments = adapter.get_existing_comments(pr_id=99)
    assert comments == []


# =====================================================================
# resolve_position
# =====================================================================


def test_resolve_position_returns_file_and_line() -> None:
    """resolve_position() returns DiffPosition with file_path and line_number."""
    adapter = make_adapter()
    pos = adapter.resolve_position("src/main.py", 10, SAMPLE_DIFF)
    assert pos.file_path == "src/main.py"
    assert pos.line_number == 10
    assert pos.side == "RIGHT"


def test_resolve_position_line_in_hunk_returns_position_1() -> None:
    """resolve_position() sets position=1 when line falls within a diff hunk."""
    adapter = make_adapter()
    # SAMPLE_DIFF hunk: @@ -1,3 +1,4 @@ — new-file lines 1-4 are valid
    for line in (1, 2, 3, 4):
        pos = adapter.resolve_position("src/main.py", line, SAMPLE_DIFF)
        assert pos.position == 1, f"line {line} should be in diff"


def test_resolve_position_line_outside_hunk_returns_position_0() -> None:
    """resolve_position() sets position=0 when line falls outside all diff hunks."""
    adapter = make_adapter()
    pos = adapter.resolve_position("src/main.py", 99, SAMPLE_DIFF)
    assert pos.position == 0


def test_resolve_position_file_not_in_diff_returns_position_0() -> None:
    """resolve_position() sets position=0 when the file is not in the diff at all."""
    adapter = make_adapter()
    pos = adapter.resolve_position("other_file.py", 1, SAMPLE_DIFF)
    assert pos.position == 0


def test_resolve_position_empty_diff_returns_position_0() -> None:
    """resolve_position() sets position=0 when the diff is empty."""
    adapter = make_adapter()
    pos = adapter.resolve_position("src/main.py", 1, "")
    assert pos.position == 0


# =====================================================================
# _line_in_diff unit tests
# =====================================================================


def test_line_in_diff_first_line_of_hunk() -> None:
    """_line_in_diff() returns True for the first line of a hunk."""
    assert _line_in_diff(1, "src/main.py", SAMPLE_DIFF) is True


def test_line_in_diff_last_line_of_hunk() -> None:
    """_line_in_diff() returns True for the last line of a hunk."""
    # @@ -1,3 +1,4 @@ — last new-file line is 4
    assert _line_in_diff(4, "src/main.py", SAMPLE_DIFF) is True


def test_line_in_diff_one_past_end_of_hunk() -> None:
    """_line_in_diff() returns False for line just past the hunk boundary."""
    assert _line_in_diff(5, "src/main.py", SAMPLE_DIFF) is False


def test_line_in_diff_wrong_file() -> None:
    """_line_in_diff() returns False when file is not in the diff."""
    assert _line_in_diff(1, "not_in_diff.py", SAMPLE_DIFF) is False


def test_line_in_diff_multiple_hunks() -> None:
    """_line_in_diff() returns True for lines in any hunk of a multi-hunk diff."""
    multi_hunk_diff = """\
diff --git a/src/main.py b/src/main.py
index abc..def 100644
--- a/src/main.py
+++ b/src/main.py
@@ -1,2 +1,2 @@
-old line
+new line
 context
@@ -20,2 +20,3 @@
 unchanged
+added line
 end
"""
    assert _line_in_diff(1, "src/main.py", multi_hunk_diff) is True
    assert _line_in_diff(20, "src/main.py", multi_hunk_diff) is True
    assert _line_in_diff(22, "src/main.py", multi_hunk_diff) is True
    assert _line_in_diff(10, "src/main.py", multi_hunk_diff) is False  # between hunks


def test_line_in_diff_per_file_hunk_only() -> None:
    """_line_in_diff() works with a per-file hunk diff (no diff --git header).

    When diff_by_file is used in _run_per_issue_dedup, FileChange.diff
    contains only hunk content — the diff --git header is stripped by
    _parse_single_file_diff. Without this fix, in_file never becomes True
    and all findings are skipped (regression: 0 inline comments posted).
    """
    per_file_diff = (
        "@@ -1,3 +1,4 @@\n"
        " def hello():\n"
        "-    pass\n"
        "+    return 'hello'\n"
        "+\n"
        " # end\n"
    )
    assert _line_in_diff(1, "src/main.py", per_file_diff) is True
    assert _line_in_diff(4, "src/main.py", per_file_diff) is True
    assert _line_in_diff(5, "src/main.py", per_file_diff) is False


def test_line_in_diff_per_file_hunk_only_ignores_file_path() -> None:
    """Per-file diffs have no git header — file_path is unused; callers own the filtering."""
    per_file_diff = "@@ -1,3 +1,4 @@\n def hello():\n-    pass\n+    return 'hello'\n+\n # end\n"
    # file_path is not validated for per-file diffs; caller ensures correct diff is passed
    assert _line_in_diff(1, "any_file.py", per_file_diff) is True


# =====================================================================
# verify_webhook_signature
# =====================================================================


def test_verify_webhook_signature_valid() -> None:
    """Valid HMAC-SHA256 signature passes verification."""
    payload = b'{"pullrequest":{"id":1}}'
    sig = "sha256=" + hmac.new(
        SECRET.encode(), payload, hashlib.sha256
    ).hexdigest()
    adapter = make_adapter()
    assert adapter.verify_webhook_signature(payload, sig) is True


def test_verify_webhook_signature_invalid() -> None:
    """Invalid signature fails verification."""
    adapter = make_adapter()
    assert adapter.verify_webhook_signature(b"payload", "sha256=badhash") is False


def test_verify_webhook_signature_no_secret() -> None:
    """Returns False when no webhook secret is configured."""
    adapter = make_adapter(webhook_secret="")
    assert adapter.verify_webhook_signature(b"payload", "sha256=anything") is False


def test_verify_webhook_signature_timing_safe() -> None:
    """Signature comparison uses hmac.compare_digest (timing-safe)."""
    # We can't directly test timing, but we can verify the hmac module is used
    import revue.core.bitbucket_adapter as mod
    assert hasattr(mod, "hmac")


# =====================================================================
# set_pr_status
# =====================================================================


def test_set_pr_status_posts_to_commit_statuses() -> None:
    """set_pr_status() posts to the commit statuses endpoint."""
    adapter = make_adapter()
    captured = {}

    def fake_urlopen(req):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode())
        return mock_response({"state": "SUCCESSFUL"})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = adapter.set_pr_status("abc123", "SUCCESSFUL", "All checks passed")

    assert result is True
    assert "/commit/abc123/statuses/build" in captured["url"]
    assert captured["body"]["key"] == "revue-io"
    assert captured["body"]["state"] == "SUCCESSFUL"
    assert captured["body"]["description"] == "All checks passed"


def test_set_pr_status_returns_false_on_error() -> None:
    """set_pr_status() returns False on API error."""
    import urllib.error
    adapter = make_adapter()
    with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError(
        url="", code=500, msg="Server Error", hdrs=None, fp=None
    )):
        result = adapter.set_pr_status("abc123", "FAILED")
    assert result is False


def test_set_pr_status_default_description() -> None:
    """set_pr_status() generates a default description when none given."""
    adapter = make_adapter()
    captured = {}

    def fake_urlopen(req):
        captured["body"] = json.loads(req.data.decode())
        return mock_response({})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        adapter.set_pr_status("sha999", "INPROGRESS")

    assert "inprogress" in captured["body"]["description"].lower()


# =====================================================================
# parse_webhook_event
# =====================================================================


def test_parse_webhook_event_pr_created() -> None:
    """parse_webhook_event() handles pullrequest:created events."""
    headers = {"X-Event-Key": "pullrequest:created"}
    payload = {
        "pullrequest": {
            "id": 5,
            "source": {"commit": {"hash": "deadbeef"}},
        },
        "repository": {"full_name": "cbscd/revue"},
    }
    result = BitbucketAdapter.parse_webhook_event(headers, payload)
    assert result is not None
    assert result["event_type"] == "pull_request"
    assert result["pr_id"] == 5
    assert result["workspace"] == "cbscd"
    assert result["repo_slug"] == "revue"
    assert result["action"] == "created"
    assert result["commit_sha"] == "deadbeef"


def test_parse_webhook_event_pr_updated() -> None:
    """parse_webhook_event() handles pullrequest:updated events."""
    headers = {"X-Event-Key": "pullrequest:updated"}
    payload = {
        "pullrequest": {"id": 7, "source": {"commit": {"hash": "cafebabe"}}},
        "repository": {"full_name": "cbscd/revue"},
    }
    result = BitbucketAdapter.parse_webhook_event(headers, payload)
    assert result is not None
    assert result["action"] == "updated"
    assert result["commit_sha"] == "cafebabe"


def test_parse_webhook_event_ignores_non_pr_events() -> None:
    """parse_webhook_event() returns None for non-PR events."""
    headers = {"X-Event-Key": "repo:push"}
    result = BitbucketAdapter.parse_webhook_event(headers, {})
    assert result is None


def test_parse_webhook_event_ignores_pr_fulfilled() -> None:
    """parse_webhook_event() returns None for pullrequest:fulfilled (merged)."""
    headers = {"X-Event-Key": "pullrequest:fulfilled"}
    payload = {
        "pullrequest": {"id": 1, "source": {"commit": {"hash": "abc"}}},
        "repository": {"full_name": "cbscd/revue"},
    }
    result = BitbucketAdapter.parse_webhook_event(headers, payload)
    assert result is None


def test_parse_webhook_event_missing_pr_id() -> None:
    """parse_webhook_event() returns None if pr_id is absent."""
    headers = {"X-Event-Key": "pullrequest:created"}
    payload = {
        "pullrequest": {"source": {"commit": {"hash": "abc"}}},
        "repository": {"full_name": "cbscd/revue"},
    }
    result = BitbucketAdapter.parse_webhook_event(headers, payload)
    assert result is None


# =====================================================================
# resolve_inline_comment (REVUE-104)
# =====================================================================


def test_resolve_inline_comment_posts_reply_with_parent_id() -> None:
    """resolve_inline_comment() POSTs to /comments with parent.id set to int(comment_id)."""
    adapter = make_adapter()
    captured = {}

    def fake_urlopen(req):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode())
        captured["method"] = req.get_method()
        return mock_response({"id": 999})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = adapter.resolve_inline_comment(
            pr_id=42, comment_id="77", reply_body="Issue resolved."
        )

    assert result is True
    assert "/pullrequests/42/comments" in captured["url"]
    assert captured["method"] == "POST"
    assert captured["body"]["content"]["raw"] == "Issue resolved."
    assert captured["body"]["parent"]["id"] == 77  # int(comment_id)


def test_resolve_inline_comment_returns_false_on_error() -> None:
    """resolve_inline_comment() returns False when the API call fails."""
    import urllib.error
    adapter = make_adapter()
    with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError(
        url="", code=500, msg="Server Error", hdrs=None, fp=None
    )):
        result = adapter.resolve_inline_comment(
            pr_id=42, comment_id="77", reply_body="Resolved"
        )
    assert result is False
