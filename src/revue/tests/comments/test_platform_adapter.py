"""Unit tests for BitbucketAdapter — TC9, TC10, TC11 (REVUE-112)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from revue.comments.platform_adapter import BitbucketAdapter


@pytest.fixture()
def adapter() -> BitbucketAdapter:
    return BitbucketAdapter(username="test_user", app_password="test_pass")


# ---------------------------------------------------------------------------
# TC9: get_comment_replies — returns replies filtered by parent.id
# ---------------------------------------------------------------------------

def test_get_comment_replies_filters_by_parent_id(adapter) -> None:
    """TC9: get_comment_replies returns only comments with parent.id == comment_id."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "values": [
            {"id": 201, "content": {"raw": "This is a reply"}, "parent": {"id": 100}},
            {"id": 202, "content": {"raw": "Another reply"}, "parent": {"id": 100}},
            {"id": 203, "content": {"raw": "Reply to different comment"}, "parent": {"id": 999}},
            {"id": 204, "content": {"raw": "Top-level comment"}, "parent": {}},
            {"id": 205, "content": {"raw": "No parent key"}},
        ]
    }

    with patch("httpx.get", return_value=mock_response) as mock_get:
        replies = adapter.get_comment_replies("workspace", "repo", 42, "100")

    mock_get.assert_called_once()
    call_url = mock_get.call_args[0][0]
    assert "/pullrequests/42/comments" in call_url

    assert len(replies) == 2
    reply_ids = {r["id"] for r in replies}
    assert reply_ids == {201, 202}


def test_get_comment_replies_returns_empty_when_no_replies(adapter) -> None:
    """TC9 edge: empty list when no comment has the target parent.id."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "values": [
            {"id": 10, "content": {"raw": "top-level"}, "parent": {}},
        ]
    }

    with patch("httpx.get", return_value=mock_response):
        replies = adapter.get_comment_replies("workspace", "repo", 7, "999")

    assert replies == []


# ---------------------------------------------------------------------------
# TC10: resolve_comment — POST to correct URL; True on 200, False on 400
# ---------------------------------------------------------------------------

def test_resolve_comment_returns_true_on_200(adapter) -> None:
    """TC10: resolve_comment returns True when Bitbucket responds 200."""
    mock_response = MagicMock()
    mock_response.status_code = 200

    with patch("httpx.post", return_value=mock_response) as mock_post:
        result = adapter.resolve_comment("workspace", "repo", 42, "100")

    assert result is True
    call_url = mock_post.call_args[0][0]
    assert "/pullrequests/42/comments/100/resolve" in call_url


def test_resolve_comment_returns_false_on_400_without_raising(adapter) -> None:
    """TC10: resolve_comment returns False on 400 (non-inline comment) — no exception."""
    mock_response = MagicMock()
    mock_response.status_code = 400

    with patch("httpx.post", return_value=mock_response):
        result = adapter.resolve_comment("workspace", "repo", 42, "100")

    assert result is False


def test_resolve_comment_uses_pr_number_in_url(adapter) -> None:
    """TC10: The URL includes the PR number at the correct position."""
    mock_response = MagicMock()
    mock_response.status_code = 200

    with patch("httpx.post", return_value=mock_response) as mock_post:
        adapter.resolve_comment("myworkspace", "myrepo", 99, "555")

    url = mock_post.call_args[0][0]
    assert "myworkspace/myrepo/pullrequests/99/comments/555/resolve" in url


# ---------------------------------------------------------------------------
# TC11: post_reply — uses correct URL with pr_number and parent field
# ---------------------------------------------------------------------------

def test_post_reply_uses_correct_url_with_pr_number(adapter) -> None:
    """TC11: post_reply calls the correct PR-scoped comments endpoint."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"id": 300}

    with patch("httpx.post", return_value=mock_response) as mock_post:
        result = adapter.post_reply("workspace", "repo", 42, "100", None, "Great point!")

    assert result == "300"
    call_url = mock_post.call_args[0][0]
    assert "/pullrequests/42/comments" in call_url
    # Must NOT be the old broken endpoint
    assert "/pullrequests/comments/" not in call_url


def test_post_reply_includes_parent_field(adapter) -> None:
    """TC11: post_reply JSON body contains parent.id set to comment_id."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"id": 301}

    with patch("httpx.post", return_value=mock_response) as mock_post:
        adapter.post_reply("workspace", "repo", 42, "100", None, "Reply text")

    _, kwargs = mock_post.call_args
    payload = kwargs["json"]
    assert payload["parent"]["id"] == 100
    assert payload["content"]["raw"] == "Reply text"


def test_post_reply_returns_new_comment_id(adapter) -> None:
    """TC11: post_reply returns the string ID of the newly created reply."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"id": 12345}

    with patch("httpx.post", return_value=mock_response):
        result = adapter.post_reply("ws", "repo", 1, "50", None, "Ack")

    assert result == "12345"


# ---------------------------------------------------------------------------
# REVUE-175 Pipeline Methods: get_diff, set_pr_status, verify_webhook_signature, parse_webhook_event
# ---------------------------------------------------------------------------

def test_bitbucket_get_diff_returns_file_changes(adapter) -> None:
    """REVUE-175 AC1: get_diff() returns list[FileChange] parsed from diff."""
    sample_diff = """\
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
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.text = sample_diff

    with patch("httpx.get", return_value=mock_response):
        changes = adapter.get_diff(pr_id=42)

    assert len(changes) == 1
    assert changes[0].file_path == "src/main.py"
    assert changes[0].change_type == "modified"


def test_bitbucket_get_diff_returns_empty_on_error(adapter) -> None:
    """REVUE-175: get_diff() returns [] when API call fails."""
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = Exception("Network error")

    with patch("httpx.get", return_value=mock_response):
        changes = adapter.get_diff(pr_id=99)

    assert changes == []


def test_bitbucket_set_pr_status_posts_to_build_status(adapter) -> None:
    """REVUE-175 AC1: set_pr_status() POSTs to build status endpoint."""
    adapter.workspace = "cbscd"
    adapter.repo_slug = "revue"

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.post", return_value=mock_response) as mock_post:
        result = adapter.set_pr_status("deadbeef", "SUCCESSFUL", "Review passed")

    assert result is True
    call_url = mock_post.call_args[0][0]
    assert "/commit/deadbeef/statuses/build" in call_url
    _, kwargs = mock_post.call_args
    body = kwargs["json"]
    assert body["key"] == "revue-io"
    assert body["state"] == "SUCCESSFUL"


def test_bitbucket_set_pr_status_returns_false_on_error(adapter) -> None:
    """REVUE-175: set_pr_status() returns False on API error."""
    adapter.workspace = "cbscd"
    adapter.repo_slug = "revue"

    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = Exception("API error")

    with patch("httpx.post", return_value=mock_response):
        result = adapter.set_pr_status("sha", "FAILED")

    assert result is False


def test_bitbucket_verify_webhook_signature_valid(adapter) -> None:
    """REVUE-175 AC1: verify_webhook_signature() validates HMAC-SHA256."""
    import hashlib
    import hmac

    adapter.webhook_secret = "test-secret"
    payload = b'{"event":"pullrequest:created"}'
    sig = "sha256=" + hmac.new(b"test-secret", payload, hashlib.sha256).hexdigest()

    result = adapter.verify_webhook_signature(payload, sig)
    assert result is True


def test_bitbucket_verify_webhook_signature_invalid(adapter) -> None:
    """REVUE-175: verify_webhook_signature() rejects tampered payload."""
    adapter.webhook_secret = "test-secret"
    result = adapter.verify_webhook_signature(b"payload", "sha256=badhash")
    assert result is False


def test_bitbucket_verify_webhook_signature_no_secret(adapter) -> None:
    """REVUE-175: verify_webhook_signature() returns False without secret."""
    adapter.webhook_secret = ""
    result = adapter.verify_webhook_signature(b"payload", "sha256=anything")
    assert result is False


def test_bitbucket_parse_webhook_event_pr_created(adapter) -> None:
    """REVUE-175 AC1: parse_webhook_event() parses PR create events."""
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


def test_bitbucket_parse_webhook_event_non_pr_returns_none(adapter) -> None:
    """REVUE-175: parse_webhook_event() returns None for non-PR events."""
    headers = {"X-Event-Key": "repo:push"}
    result = BitbucketAdapter.parse_webhook_event(headers, {})
    assert result is None


def test_bitbucket_post_summary_comment(adapter) -> None:
    """REVUE-175: post_summary_comment() POSTs to PR comments without inline key."""
    adapter.workspace = "cbscd"
    adapter.repo_slug = "revue"

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"id": 77}

    with patch("httpx.post", return_value=mock_response) as mock_post:
        result = adapter.post_summary_comment(42, "## Review Summary\n\nLooks good.")

    assert result == "77"
    call_url = mock_post.call_args[0][0]
    assert "/pullrequests/42/comments" in call_url
    _, kwargs = mock_post.call_args
    body = kwargs["json"]
    assert "inline" not in body
    assert body["content"]["raw"] == "## Review Summary\n\nLooks good."


def test_bitbucket_update_comment(adapter) -> None:
    """REVUE-175: update_comment() PUTs to comments/{id} and returns True on success."""
    adapter.workspace = "cbscd"
    adapter.repo_slug = "revue"

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.put", return_value=mock_response) as mock_put:
        result = adapter.update_comment(42, "77", "## Updated Review")

    assert result is True
    call_url = mock_put.call_args[0][0]
    assert "/pullrequests/42/comments/77" in call_url
    _, kwargs = mock_put.call_args
    assert kwargs["json"]["content"]["raw"] == "## Updated Review"


def test_github_pipeline_methods_raise_not_implemented() -> None:
    """REVUE-175 P2: GitHub pipeline stubs raise NotImplementedError, not silent no-ops."""
    from revue.comments.platform_adapter import GitHubAdapter

    gh = GitHubAdapter("ghp_tok")
    with pytest.raises(NotImplementedError):
        gh.get_diff(1)
    with pytest.raises(NotImplementedError):
        gh.set_pr_status("sha", "SUCCESSFUL")
    with pytest.raises(NotImplementedError):
        gh.verify_webhook_signature(b"payload", "sha256=sig")
    with pytest.raises(NotImplementedError):
        GitHubAdapter.parse_webhook_event({}, {})
    with pytest.raises(NotImplementedError):
        gh.post_summary_comment(1, "body")
    with pytest.raises(NotImplementedError):
        gh.update_comment(1, "id", "body")
    with pytest.raises(NotImplementedError):
        gh.get_existing_comments(1)


def test_gitlab_pipeline_methods_raise_not_implemented() -> None:
    """REVUE-175 P2: GitLab pipeline stubs raise NotImplementedError, not silent no-ops."""
    from revue.comments.platform_adapter import GitLabAdapter

    gl = GitLabAdapter("glpat-tok")
    with pytest.raises(NotImplementedError):
        gl.get_diff(1)
    with pytest.raises(NotImplementedError):
        gl.set_pr_status("sha", "SUCCESSFUL")
    with pytest.raises(NotImplementedError):
        gl.verify_webhook_signature(b"payload", "sig")
    with pytest.raises(NotImplementedError):
        GitLabAdapter.parse_webhook_event({}, {})
    with pytest.raises(NotImplementedError):
        gl.post_summary_comment(1, "body")
    with pytest.raises(NotImplementedError):
        gl.update_comment(1, "id", "body")
    with pytest.raises(NotImplementedError):
        gl.get_existing_comments(1)


# ---------------------------------------------------------------------------
# REVUE-119 T2: GitHubAdapter.get_all_pr_comments()
# ---------------------------------------------------------------------------

def test_github_adapter_get_all_pr_comments_returns_normalised_top_level() -> None:
    """T2.1: Top-level comment normalised to inline=True, parent=None, content.raw=body."""
    from revue.comments.platform_adapter import GitHubAdapter
    import httpx

    gh = GitHubAdapter("ghp_tok")

    raw_comments = [
        {
            "id": 101,
            "body": "**🔴 [HIGH] SQL injection in query builder",
            "in_reply_to_id": None,
            "path": "src/db.py",
            "line": 42,
        }
    ]

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = raw_comments
    mock_resp.headers = {}

    with patch("httpx.get", return_value=mock_resp):
        result = gh.get_all_pr_comments("owner", "repo", 4)

    assert len(result) == 1
    c = result[0]
    assert c["id"] == 101
    assert c["inline"] == {"path": "src/db.py", "to": 42}
    assert c["parent"] is None
    assert c["content"]["raw"] == "**🔴 [HIGH] SQL injection in query builder"
    assert c["resolution"] is None


def test_github_adapter_get_all_pr_comments_reply_has_parent() -> None:
    """T2.1: Reply with in_reply_to_id normalised to parent={"id": in_reply_to_id}."""
    from revue.comments.platform_adapter import GitHubAdapter

    gh = GitHubAdapter("ghp_tok")

    raw_comments = [
        {
            "id": 202,
            "body": "Won't fix — intentional pattern",
            "in_reply_to_id": 101,
        }
    ]

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = raw_comments
    mock_resp.headers = {}

    with patch("httpx.get", return_value=mock_resp):
        result = gh.get_all_pr_comments("owner", "repo", 4)

    assert result[0]["parent"] == {"id": 101}


def test_github_adapter_get_all_pr_comments_http_error_raises() -> None:
    """T2.1: HTTP 4xx from GitHub raises an exception."""
    from revue.comments.platform_adapter import GitHubAdapter
    import httpx

    gh = GitHubAdapter("ghp_tok")

    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "404", request=MagicMock(), response=MagicMock()
    )

    with patch("httpx.get", return_value=mock_resp):
        with pytest.raises(httpx.HTTPStatusError):
            gh.get_all_pr_comments("owner", "repo", 4)


def test_github_adapter_get_all_pr_comments_follows_pagination() -> None:
    """T2.2: get_all_pr_comments follows Link rel=next header across pages."""
    from revue.comments.platform_adapter import GitHubAdapter

    gh = GitHubAdapter("ghp_tok")

    page1_comment = {"id": 1, "body": "**🔴 [HIGH] Finding A", "in_reply_to_id": None, "path": "a.py", "line": 1}
    page2_comment = {"id": 2, "body": "**🟡 [MEDIUM] Finding B", "in_reply_to_id": None, "path": "b.py", "line": 2}

    page1 = MagicMock()
    page1.raise_for_status = MagicMock()
    page1.json.return_value = [page1_comment]
    page1.headers = {"link": '<https://api.github.com/repos/owner/repo/pulls/4/comments?page=2>; rel="next"'}

    page2 = MagicMock()
    page2.raise_for_status = MagicMock()
    page2.json.return_value = [page2_comment]
    page2.headers = {}

    with patch("httpx.get", side_effect=[page1, page2]) as mock_get:
        result = gh.get_all_pr_comments("owner", "repo", 4)

    assert mock_get.call_count == 2
    assert len(result) == 2
    assert result[0]["id"] == 1
    assert result[1]["id"] == 2
    # Second call must use the URL from the Link header
    second_url = mock_get.call_args_list[1][0][0]
    assert "page=2" in second_url


# ---------------------------------------------------------------------------
# REVUE-119 T4: GitHubAdapter GraphQL helpers
# ---------------------------------------------------------------------------

def test_graphql_helper_sends_correct_request() -> None:
    """T4.1: _graphql() POSTs to api.github.com/graphql with Bearer auth."""
    from revue.comments.platform_adapter import GitHubAdapter

    gh = GitHubAdapter("ghp_tok")
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"data": {"result": True}}

    with patch("httpx.post", return_value=mock_resp) as mock_post:
        result = gh._graphql("query { viewer { login } }", {})

    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args
    assert "api.github.com/graphql" in call_kwargs[0][0]
    assert "Bearer ghp_tok" in str(call_kwargs)
    assert result == {"data": {"result": True}}


def test_graphql_helper_raises_on_errors_key() -> None:
    """T4.1: _graphql() raises RuntimeError when response contains 'errors'."""
    from revue.comments.platform_adapter import GitHubAdapter

    gh = GitHubAdapter("ghp_tok")
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"errors": [{"message": "Field not found"}]}

    with patch("httpx.post", return_value=mock_resp):
        with pytest.raises(RuntimeError, match="GraphQL error"):
            gh._graphql("query { bad }", {})


def test_fetch_review_thread_ids_returns_list() -> None:
    """T4.1: fetch_review_thread_ids returns list of dicts with thread_id, comment_id, is_resolved."""
    from revue.comments.platform_adapter import GitHubAdapter

    gh = GitHubAdapter("ghp_tok")
    graphql_response = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "nodes": [
                            {
                                "id": "PRRT_abc123",
                                "isResolved": False,
                                "comments": {
                                    "nodes": [{"databaseId": 101}]
                                },
                            }
                        ],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            }
        }
    }

    with patch.object(gh, "_graphql", return_value=graphql_response):
        result = gh.fetch_review_thread_ids(4, "owner", "repo")

    assert len(result) == 1
    assert result[0]["thread_id"] == "PRRT_abc123"
    assert result[0]["comment_id"] == 101
    assert result[0]["is_resolved"] is False


def test_fetch_review_thread_ids_empty_pr() -> None:
    """T4.1: fetch_review_thread_ids returns [] for PR with no threads."""
    from revue.comments.platform_adapter import GitHubAdapter

    gh = GitHubAdapter("ghp_tok")
    graphql_response = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "nodes": [],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            }
        }
    }

    with patch.object(gh, "_graphql", return_value=graphql_response):
        result = gh.fetch_review_thread_ids(4, "owner", "repo")

    assert result == []


def test_fetch_review_thread_ids_paginates_beyond_100() -> None:
    """fetch_review_thread_ids follows pageInfo.hasNextPage to collect all threads."""
    from revue.comments.platform_adapter import GitHubAdapter

    gh = GitHubAdapter("ghp_tok")

    page1 = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "nodes": [
                            {"id": "PRRT_p1", "isResolved": False, "comments": {"nodes": [{"databaseId": 1}]}},
                        ],
                        "pageInfo": {"hasNextPage": True, "endCursor": "cursor_abc"},
                    }
                }
            }
        }
    }
    page2 = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "nodes": [
                            {"id": "PRRT_p2", "isResolved": True, "comments": {"nodes": [{"databaseId": 2}]}},
                        ],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            }
        }
    }

    with patch.object(gh, "_graphql", side_effect=[page1, page2]) as mock_gql:
        result = gh.fetch_review_thread_ids(7, "owner", "repo")

    assert mock_gql.call_count == 2
    # First call: cursor=None
    assert mock_gql.call_args_list[0][0][1]["cursor"] is None
    # Second call: cursor from page1
    assert mock_gql.call_args_list[1][0][1]["cursor"] == "cursor_abc"
    assert len(result) == 2
    assert result[0] == {"thread_id": "PRRT_p1", "comment_id": 1, "is_resolved": False}
    assert result[1] == {"thread_id": "PRRT_p2", "comment_id": 2, "is_resolved": True}


def test_fetch_review_thread_ids_breaks_on_null_end_cursor() -> None:
    """EC-3: When GitHub returns hasNextPage=True but endCursor=null (malformed API response),
    the loop must break rather than looping forever with cursor=None."""
    from revue.comments.platform_adapter import GitHubAdapter

    gh = GitHubAdapter("ghp_tok")

    # API returns hasNextPage=True but endCursor=null — infinite loop without fix
    malformed_page = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "nodes": [
                            {"id": "PRRT_x1", "isResolved": False,
                             "comments": {"nodes": [{"databaseId": 99}]}},
                        ],
                        "pageInfo": {"hasNextPage": True, "endCursor": None},
                    }
                }
            }
        }
    }

    with patch.object(gh, "_graphql", return_value=malformed_page) as mock_gql:
        result = gh.fetch_review_thread_ids(1, "owner", "repo")

    # Must terminate after exactly one call (not loop forever)
    assert mock_gql.call_count == 1
    assert len(result) == 1


def test_resolve_thread_calls_mutation() -> None:
    """T4.1: resolve_thread() calls resolveReviewThread GraphQL mutation."""
    from revue.comments.platform_adapter import GitHubAdapter

    gh = GitHubAdapter("ghp_tok")
    mutation_response = {
        "data": {"resolveReviewThread": {"thread": {"isResolved": True}}}
    }

    with patch.object(gh, "_graphql", return_value=mutation_response) as mock_gql:
        result = gh.resolve_thread("PRRT_abc")

    assert result is True
    called_query = mock_gql.call_args[0][0]
    assert "resolveReviewThread" in called_query
    assert mock_gql.call_args[0][1] == {"threadId": "PRRT_abc"}


def test_resolve_comment_looks_up_thread_id() -> None:
    """T4.1: resolve_comment looks up thread_id via fetch_review_thread_ids and resolves it."""
    from revue.comments.platform_adapter import GitHubAdapter

    gh = GitHubAdapter("ghp_tok")
    threads = [{"thread_id": "PRRT_x", "comment_id": 42, "is_resolved": False}]

    with patch.object(gh, "fetch_review_thread_ids", return_value=threads) as mock_fetch, \
         patch.object(gh, "resolve_thread", return_value=True) as mock_resolve:
        result = gh.resolve_comment("owner", "repo", 4, "42")

    mock_fetch.assert_called_once_with(4, "owner", "repo")
    mock_resolve.assert_called_once_with("PRRT_x")
    assert result is True


def test_resolve_comment_graceful_fallback_when_thread_not_found() -> None:
    """T4.1: resolve_comment returns False (no exception) when no thread matches comment_id."""
    from revue.comments.platform_adapter import GitHubAdapter

    gh = GitHubAdapter("ghp_tok")
    threads = [{"thread_id": "PRRT_other", "comment_id": 999, "is_resolved": False}]

    with patch.object(gh, "fetch_review_thread_ids", return_value=threads):
        result = gh.resolve_comment("owner", "repo", 4, "42")

    assert result is False

# ---------------------------------------------------------------------------
# REVUE-181: fetch_review_thread_ids safety guards
# ---------------------------------------------------------------------------

def test_fetch_review_thread_ids_stops_at_max_pages() -> None:
    """TC1: loop stops after max_pages even if API keeps returning hasNextPage=True."""
    from revue.comments.platform_adapter import GitHubAdapter

    gh = GitHubAdapter("ghp_tok")

    def make_page(cursor_out: str) -> dict:
        return {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": [{"id": f"PRRT_{cursor_out}", "isResolved": False,
                                       "comments": {"nodes": [{"databaseId": 1}]}}],
                            "pageInfo": {"hasNextPage": True, "endCursor": cursor_out},
                        }
                    }
                }
            }
        }

    pages = [make_page(f"cursor_{i}") for i in range(10)]

    with patch.object(gh, "_graphql", side_effect=pages) as mock_gql, \
            patch("revue.core.log.Log.cli.warning") as mock_log:
        result = gh.fetch_review_thread_ids(1, "owner", "repo", max_pages=3)

    assert mock_gql.call_count == 3
    mock_log.assert_called_once()
    assert "max_pages" in mock_log.call_args.args[0]
    assert len(result) == 3


def test_fetch_review_thread_ids_breaks_on_null_end_cursor() -> None:
    """P1 guard: loop stops when hasNextPage=True but endCursor is None mid-pagination."""
    from revue.comments.platform_adapter import GitHubAdapter

    gh = GitHubAdapter("ghp_tok")

    page1 = {
        "data": {"repository": {"pullRequest": {"reviewThreads": {
            "nodes": [{"id": "PRRT_1", "isResolved": False,
                       "comments": {"nodes": [{"databaseId": 1}]}}],
            "pageInfo": {"hasNextPage": True, "endCursor": "cursor_valid"},
        }}}}
    }
    page2 = {
        "data": {"repository": {"pullRequest": {"reviewThreads": {
            "nodes": [{"id": "PRRT_2", "isResolved": False,
                       "comments": {"nodes": [{"databaseId": 2}]}}],
            "pageInfo": {"hasNextPage": True, "endCursor": None},
        }}}}
    }

    with patch.object(gh, "_graphql", side_effect=[page1, page2]) as mock_gql, \
            patch("revue.core.log.Log.cli.warning") as mock_log:
        result = gh.fetch_review_thread_ids(1, "owner", "repo")

    assert mock_gql.call_count == 2
    mock_log.assert_called_once()
    assert "null endCursor" in mock_log.call_args.args[0]
    assert len(result) == 2


def test_fetch_review_thread_ids_breaks_on_stuck_cursor() -> None:
    """TC2: loop breaks immediately when the same cursor is returned twice."""
    from revue.comments.platform_adapter import GitHubAdapter

    gh = GitHubAdapter("ghp_tok")

    page1 = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "nodes": [{"id": "PRRT_1", "isResolved": False,
                                   "comments": {"nodes": [{"databaseId": 1}]}}],
                        "pageInfo": {"hasNextPage": True, "endCursor": "cursor_stuck"},
                    }
                }
            }
        }
    }
    page2 = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "nodes": [{"id": "PRRT_2", "isResolved": False,
                                   "comments": {"nodes": [{"databaseId": 2}]}}],
                        "pageInfo": {"hasNextPage": True, "endCursor": "cursor_stuck"},
                    }
                }
            }
        }
    }

    with patch.object(gh, "_graphql", side_effect=[page1, page2]) as mock_gql, \
            patch("revue.core.log.Log.cli.warning") as mock_log:
        result = gh.fetch_review_thread_ids(1, "owner", "repo")

    assert mock_gql.call_count == 2
    mock_log.assert_called_once()
    assert "stuck" in mock_log.call_args.args[0]
    assert len(result) == 2


# ---------------------------------------------------------------------------
# REVUE-119 T5: GitHubAdapter.get_pr_template()
# ---------------------------------------------------------------------------

def test_github_adapter_post_reply_uses_pr_number_in_url() -> None:
    """post_reply must include pr_number in URL — /pulls/{pr}/comments/{id}/replies."""
    from revue.comments.platform_adapter import GitHubAdapter

    gh = GitHubAdapter("ghp_tok")

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"id": 999}

    with patch("httpx.post", return_value=mock_resp) as mock_post:
        result = gh.post_reply("owner", "repo", 42, "101", None, "Acknowledged.")

    assert result == "999"
    called_url = mock_post.call_args[0][0]
    assert "/pulls/42/comments/101/replies" in called_url, (
        f"URL missing pr_number: {called_url}"
    )


def test_github_adapter_get_pr_template_primary_path() -> None:
    """T5.1: get_pr_template returns decoded content from .github/ path."""
    from revue.comments.platform_adapter import GitHubAdapter
    import base64

    gh = GitHubAdapter("ghp_tok")
    encoded = base64.b64encode(b"## PR Template\n\nDescribe your changes.").decode()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"content": encoded + "\n", "encoding": "base64"}

    with patch("httpx.get", return_value=mock_resp):
        result = gh.get_pr_template("owner", "repo")

    assert result == "## PR Template\n\nDescribe your changes."


def test_github_adapter_get_pr_template_fallback() -> None:
    """T5.1: get_pr_template tries root path when .github/ returns 404."""
    from revue.comments.platform_adapter import GitHubAdapter
    import base64
    import httpx

    gh = GitHubAdapter("ghp_tok")

    not_found = MagicMock()
    not_found.raise_for_status.side_effect = httpx.HTTPStatusError(
        "404", request=MagicMock(), response=MagicMock()
    )

    encoded = base64.b64encode(b"Root template").decode()
    found = MagicMock()
    found.raise_for_status = MagicMock()
    found.json.return_value = {"content": encoded, "encoding": "base64"}

    with patch("httpx.get", side_effect=[not_found, found]):
        result = gh.get_pr_template("owner", "repo")

    assert result == "Root template"


def test_github_adapter_get_pr_template_not_found_returns_none() -> None:
    """T5.1: get_pr_template returns None when all three paths return 404."""
    from revue.comments.platform_adapter import GitHubAdapter
    import httpx

    gh = GitHubAdapter("ghp_tok")

    not_found = MagicMock()
    not_found.raise_for_status.side_effect = httpx.HTTPStatusError(
        "404", request=MagicMock(), response=MagicMock()
    )

    with patch("httpx.get", side_effect=[not_found, not_found, not_found]):
        result = gh.get_pr_template("owner", "repo")

    assert result is None


def test_github_adapter_get_pr_template_third_path_fallback() -> None:
    """T5.2: get_pr_template returns content from docs/ when .github/ and root both 404."""
    from revue.comments.platform_adapter import GitHubAdapter
    import base64
    import httpx

    gh = GitHubAdapter("ghp_tok")

    not_found = MagicMock()
    not_found.raise_for_status.side_effect = httpx.HTTPStatusError(
        "404", request=MagicMock(), response=MagicMock()
    )

    encoded = base64.b64encode(b"Docs template").decode()
    found = MagicMock()
    found.raise_for_status = MagicMock()
    found.json.return_value = {"content": encoded, "encoding": "base64"}

    with patch("httpx.get", side_effect=[not_found, not_found, found]) as mock_get:
        result = gh.get_pr_template("owner", "repo")

    assert result == "Docs template"
    assert mock_get.call_count == 3
    third_url = mock_get.call_args_list[2][0][0]
    assert "docs/pull_request_template.md" in third_url


# ===========================================================================
# GitLabAdapter — REVUE-120
# ===========================================================================

# ---------------------------------------------------------------------------
# T1: resolve_discussion sends body {"resolved": True}, not query param
# ---------------------------------------------------------------------------

def test_gitlab_resolve_discussion_sends_body_not_query_param() -> None:
    """T1: resolve_discussion must PUT with JSON body, not ?resolved=true query param."""
    from revue.comments.platform_adapter import GitLabAdapter

    gl = GitLabAdapter("glpat-tok")
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("httpx.put", return_value=mock_resp) as mock_put:
        result = gl.resolve_discussion("owner", "repo", 1, "abc123")

    assert result is True
    call_kwargs = mock_put.call_args
    # Must NOT pass params= with resolved
    params = call_kwargs.kwargs.get("params") or (call_kwargs.args[1] if len(call_kwargs.args) > 1 else {})
    assert "resolved" not in str(params)
    # Must pass json body
    json_body = call_kwargs.kwargs.get("json", {})
    assert json_body.get("resolved") is True
    # URL must NOT contain ?resolved
    url = call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs.get("url", "")
    assert "?resolved" not in url


# ---------------------------------------------------------------------------
# T2: get_all_pr_comments — fetch, filter system notes, normalise shape
# ---------------------------------------------------------------------------

def test_gitlab_get_all_pr_comments_filters_system_notes() -> None:
    """T2a: system notes are excluded; only non-system notes returned."""
    from revue.comments.platform_adapter import GitLabAdapter

    gl = GitLabAdapter("glpat-tok")
    discussions = [
        {
            "id": "disc1",
            "notes": [
                {"id": 1, "body": "Revue finding", "system": False,
                 "in_reply_to_id": None, "position": {"new_path": "foo.py", "new_line": 10}},
            ],
        },
        {
            "id": "disc2",
            "notes": [
                {"id": 2, "body": "assigned to @dev", "system": True,
                 "in_reply_to_id": None, "position": None},
            ],
        },
    ]
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = discussions
    resp.headers = {}

    with patch("httpx.get", return_value=resp):
        result = gl.get_all_pr_comments("owner", "repo", 1)

    assert len(result) == 1
    assert result[0]["id"] == 1


def test_gitlab_get_all_pr_comments_normalises_shape() -> None:
    """T2b: each returned dict has id, thread_id, content.raw, parent, inline."""
    from revue.comments.platform_adapter import GitLabAdapter

    gl = GitLabAdapter("glpat-tok")
    discussions = [
        {
            "id": "disc42",
            "notes": [
                {"id": 7, "body": "Some finding", "system": False,
                 "in_reply_to_id": None,
                 "position": {"new_path": "app/main.py", "new_line": 55}},
                {"id": 8, "body": "I disagree", "system": False,
                 "in_reply_to_id": 7,
                 "position": None},
            ],
        },
    ]
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = discussions
    resp.headers = {}

    with patch("httpx.get", return_value=resp):
        result = gl.get_all_pr_comments("owner", "repo", 1)

    assert len(result) == 2
    root = next(r for r in result if r["id"] == 7)
    reply = next(r for r in result if r["id"] == 8)

    assert root["thread_id"] == "disc42"
    assert root["content"]["raw"] == "Some finding"
    assert root["parent"] is None
    assert root["inline"]["path"] == "app/main.py"
    assert root["inline"]["to"] == 55

    assert reply["parent"] == {"id": 7}
    assert reply["thread_id"] == "disc42"


def test_gitlab_get_all_pr_comments_reply_detected_when_in_reply_to_id_is_none() -> None:
    """T2b-regression: GitLab always returns in_reply_to_id=None for reply notes.
    Parent must be detected by position (second+ note in discussion), not the field.
    """
    from revue.comments.platform_adapter import GitLabAdapter

    gl = GitLabAdapter("glpat-tok")
    discussions = [
        {
            "id": "disc99",
            "notes": [
                # Root note — first in discussion
                {"id": 10, "body": "Revue finding", "system": False,
                 "in_reply_to_id": None,
                 "position": {"new_path": "foo.py", "new_line": 1}},
                # Reply note — GitLab returns in_reply_to_id=None even for real replies
                {"id": 11, "body": "Won't fix", "system": False,
                 "in_reply_to_id": None,
                 "position": None},
            ],
        },
    ]
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = discussions
    resp.headers = {}

    with patch("httpx.get", return_value=resp):
        result = gl.get_all_pr_comments("owner", "repo", 1)

    assert len(result) == 2
    root = next(r for r in result if r["id"] == 10)
    reply = next(r for r in result if r["id"] == 11)
    assert root["parent"] is None
    # Reply must be linked to root even though in_reply_to_id was None
    assert reply["parent"] == {"id": 10}


def test_gitlab_get_all_pr_comments_paginates() -> None:
    """T2c: follows X-Next-Page header to fetch all pages."""
    from revue.comments.platform_adapter import GitLabAdapter

    gl = GitLabAdapter("glpat-tok")

    page1_note = {"id": 1, "body": "p1", "system": False, "in_reply_to_id": None,
                  "position": {"new_path": "a.py", "new_line": 1}}
    page2_note = {"id": 2, "body": "p2", "system": False, "in_reply_to_id": None,
                  "position": {"new_path": "b.py", "new_line": 2}}

    resp1 = MagicMock()
    resp1.raise_for_status = MagicMock()
    resp1.json.return_value = [{"id": "d1", "notes": [page1_note]}]
    resp1.headers = {"X-Next-Page": "2"}

    resp2 = MagicMock()
    resp2.raise_for_status = MagicMock()
    resp2.json.return_value = [{"id": "d2", "notes": [page2_note]}]
    resp2.headers = {}

    with patch("httpx.get", side_effect=[resp1, resp2]):
        result = gl.get_all_pr_comments("owner", "repo", 1)

    assert len(result) == 2
    assert {r["id"] for r in result} == {1, 2}


# ---------------------------------------------------------------------------
# T3: get_comment_replies — returns replies for a given root note id
# ---------------------------------------------------------------------------

def test_gitlab_get_comment_replies_returns_replies_for_comment() -> None:
    """T3: replies with in_reply_to_id == comment_id are returned; root is excluded."""
    from revue.comments.platform_adapter import GitLabAdapter

    gl = GitLabAdapter("glpat-tok")
    discussions = [
        {
            "id": "disc1",
            "notes": [
                {"id": 10, "body": "root", "system": False, "in_reply_to_id": None,
                 "position": {"new_path": "x.py", "new_line": 1}},
                {"id": 11, "body": "reply1", "system": False, "in_reply_to_id": 10,
                 "position": None},
                {"id": 12, "body": "reply2", "system": False, "in_reply_to_id": 10,
                 "position": None},
            ],
        },
        {
            "id": "disc2",
            "notes": [
                {"id": 20, "body": "other root", "system": False, "in_reply_to_id": None,
                 "position": {"new_path": "y.py", "new_line": 5}},
            ],
        },
    ]
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = discussions
    resp.headers = {}

    with patch("httpx.get", return_value=resp):
        result = gl.get_comment_replies("owner", "repo", 1, "10")

    assert len(result) == 2
    assert all(r["parent"] == {"id": 10} for r in result)
    assert {r["id"] for r in result} == {11, 12}


# ---------------------------------------------------------------------------
# T4: post_reply — uses discussion_id in URL, returns note id
# ---------------------------------------------------------------------------

def test_gitlab_post_reply_uses_discussion_id_in_url() -> None:
    """T4a: POST URL contains discussions/{thread_id}/notes."""
    from revue.comments.platform_adapter import GitLabAdapter

    gl = GitLabAdapter("glpat-tok")
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"id": 99}

    with patch("httpx.post", return_value=resp) as mock_post:
        gl.post_reply("owner", "repo", 1, "10", "disc42", "Won't fix — REVUE-138")

    url = mock_post.call_args.args[0]
    assert "discussions/disc42/notes" in url


def test_gitlab_post_reply_returns_empty_string_when_thread_id_none() -> None:
    """T4c: post_reply returns '' and makes no HTTP call when thread_id is None."""
    from revue.comments.platform_adapter import GitLabAdapter

    gl = GitLabAdapter("glpat-tok")
    with patch("httpx.post") as mock_post:
        result = gl.post_reply("owner", "repo", 1, "10", None, "body")

    assert result == ""
    mock_post.assert_not_called()


def test_gitlab_post_reply_returns_note_id_as_string() -> None:
    """T4b: post_reply returns str(note_id) from response."""
    from revue.comments.platform_adapter import GitLabAdapter

    gl = GitLabAdapter("glpat-tok")
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"id": 42}

    with patch("httpx.post", return_value=resp):
        result = gl.post_reply("owner", "repo", 1, "10", "disc1", "body")

    assert result == "42"


# ---------------------------------------------------------------------------
# T5: get_pr_template — Default template, fallback, None when empty
# ---------------------------------------------------------------------------

def test_gitlab_get_pr_template_returns_default_template() -> None:
    """T5a: returns content from Default template when it exists."""
    from revue.comments.platform_adapter import GitLabAdapter

    gl = GitLabAdapter("glpat-tok")
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"content": "## Summary\n\nDescribe your change."}

    with patch("httpx.get", return_value=resp):
        result = gl.get_pr_template("owner", "repo")

    assert result == "## Summary\n\nDescribe your change."


def test_gitlab_get_pr_template_falls_back_to_first_template() -> None:
    """T5b: falls back to first listed template when Default returns 404."""
    import httpx as httpx_mod
    from revue.comments.platform_adapter import GitLabAdapter

    gl = GitLabAdapter("glpat-tok")

    not_found = MagicMock()
    not_found.raise_for_status.side_effect = httpx_mod.HTTPStatusError(
        "404", request=MagicMock(), response=MagicMock()
    )

    list_resp = MagicMock()
    list_resp.raise_for_status = MagicMock()
    list_resp.json.return_value = [{"name": "feature_request"}]

    content_resp = MagicMock()
    content_resp.raise_for_status = MagicMock()
    content_resp.json.return_value = {"content": "Feature request template"}

    with patch("httpx.get", side_effect=[not_found, list_resp, content_resp]):
        result = gl.get_pr_template("owner", "repo")

    assert result == "Feature request template"


def test_gitlab_get_pr_template_returns_none_when_no_templates() -> None:
    """T5c: returns None when no templates exist on the project."""
    import httpx as httpx_mod
    from revue.comments.platform_adapter import GitLabAdapter

    gl = GitLabAdapter("glpat-tok")

    not_found = MagicMock()
    not_found.raise_for_status.side_effect = httpx_mod.HTTPStatusError(
        "404", request=MagicMock(), response=MagicMock()
    )

    empty_list = MagicMock()
    empty_list.raise_for_status = MagicMock()
    empty_list.json.return_value = []

    with patch("httpx.get", side_effect=[not_found, empty_list]):
        result = gl.get_pr_template("owner", "repo")

    assert result is None


# ---------------------------------------------------------------------------
# T6: GitLabAdapter.ensure_lessons_pr — create branch/file/MR
# ---------------------------------------------------------------------------

def _gl_ok(json_data=None, text="") -> MagicMock:
    """Helper: mock httpx response with status_code=200."""
    m = MagicMock()
    m.status_code = 200
    m.raise_for_status = MagicMock()
    m.json.return_value = json_data or {}
    m.text = text
    return m


def _gl_404() -> MagicMock:
    """Helper: mock httpx response with status_code=404."""
    m = MagicMock()
    m.status_code = 404
    m.raise_for_status = MagicMock()
    m.json.return_value = {}
    return m


def test_gitlab_ensure_lessons_pr_creates_branch_file_and_mr(tmp_path) -> None:
    """T6a: new branch + new file + no open MR → creates all three, returns web_url."""
    from revue.comments.platform_adapter import GitLabAdapter

    gl = GitLabAdapter("glpat-tok")

    branch_missing = _gl_404()           # GET branch → 404
    create_branch = _gl_ok()             # POST branches
    file_missing = _gl_404()             # GET file → 404
    create_file = _gl_ok()               # POST file
    no_mrs = _gl_ok(json_data=[])        # GET MRs → empty
    created_mr = _gl_ok(json_data={"web_url": "https://gitlab.com/o/r/-/merge_requests/7"})

    with patch("httpx.get", side_effect=[branch_missing, file_missing, no_mrs]) as mock_get, \
         patch("httpx.post", side_effect=[create_branch, create_file, created_mr]) as mock_post:
        url = gl.ensure_lessons_pr(
            repo_owner="owner",
            repo_name="repo",
            pr_number=4,
            branch="chore/revue-lessons-4",
            revue_yml_content="noise_filters:\n  allowed_patterns: []\n",
            commit_message="chore: add pattern",
            pr_title="chore: Revue lessons from PR #4",
            pr_description="## Summary\n",
        )

    assert url == "https://gitlab.com/o/r/-/merge_requests/7"
    # Branch creation called with correct payload
    assert mock_post.call_args_list[0].kwargs["json"]["branch"] == "chore/revue-lessons-4"
    assert mock_post.call_args_list[0].kwargs["json"]["ref"] == "main"
    # MR creation called with correct source/target
    mr_payload = mock_post.call_args_list[2].kwargs["json"]
    assert mr_payload["source_branch"] == "chore/revue-lessons-4"
    assert mr_payload["target_branch"] == "main"


def test_gitlab_ensure_lessons_pr_skips_branch_creation_when_exists() -> None:
    """T6b: branch already exists → no POST /branches, still commits file and creates MR."""
    from revue.comments.platform_adapter import GitLabAdapter

    gl = GitLabAdapter("glpat-tok")

    branch_exists = _gl_ok({"name": "chore/revue-lessons-4"})  # GET branch → 200
    file_missing = _gl_404()                                     # GET file → 404
    create_file = _gl_ok()
    no_mrs = _gl_ok(json_data=[])
    created_mr = _gl_ok(json_data={"web_url": "https://gitlab.com/o/r/-/merge_requests/8"})

    with patch("httpx.get", side_effect=[branch_exists, file_missing, no_mrs]), \
         patch("httpx.post", side_effect=[create_file, created_mr]) as mock_post:
        url = gl.ensure_lessons_pr(
            "owner", "repo", 4, "chore/revue-lessons-4", "content", "msg", "title", "desc"
        )

    assert url == "https://gitlab.com/o/r/-/merge_requests/8"
    # Only file POST + MR POST (no branch POST)
    assert mock_post.call_count == 2


def test_gitlab_ensure_lessons_pr_updates_file_when_already_exists() -> None:
    """T6c: file already on branch → PUT with last_commit_id, not POST."""
    from revue.comments.platform_adapter import GitLabAdapter

    gl = GitLabAdapter("glpat-tok")

    branch_exists = _gl_ok({"name": "chore/revue-lessons-4"})
    file_exists = _gl_ok({"last_commit_id": "abc123", "content": "old"})  # GET file → 200
    update_file = _gl_ok()                                                  # PUT file
    open_mr = _gl_ok(json_data=[{"web_url": "https://gitlab.com/o/r/-/merge_requests/9"}])

    with patch("httpx.get", side_effect=[branch_exists, file_exists, open_mr]), \
         patch("httpx.put", return_value=update_file) as mock_put, \
         patch("httpx.post") as mock_post:
        url = gl.ensure_lessons_pr(
            "owner", "repo", 4, "chore/revue-lessons-4", "new-content", "msg", "title", "desc"
        )

    assert url == "https://gitlab.com/o/r/-/merge_requests/9"
    mock_post.assert_not_called()  # No new MR created
    assert mock_put.call_count == 1
    put_payload = mock_put.call_args.kwargs["json"]
    assert put_payload["last_commit_id"] == "abc123"
    assert put_payload["content"] == "new-content"


def test_gitlab_ensure_lessons_pr_returns_existing_mr_url() -> None:
    """T6d: open MR already exists → return its URL without creating a new one."""
    from revue.comments.platform_adapter import GitLabAdapter

    gl = GitLabAdapter("glpat-tok")

    branch_exists = _gl_ok({"name": "chore/revue-lessons-4"})
    file_missing = _gl_404()
    create_file = _gl_ok()
    existing_mr = _gl_ok(json_data=[{"web_url": "https://gitlab.com/o/r/-/merge_requests/3"}])

    with patch("httpx.get", side_effect=[branch_exists, file_missing, existing_mr]), \
         patch("httpx.post", return_value=create_file) as mock_post:
        url = gl.ensure_lessons_pr(
            "owner", "repo", 4, "chore/revue-lessons-4", "content", "msg", "title", "desc"
        )

    assert url == "https://gitlab.com/o/r/-/merge_requests/3"
    # Only one POST — the file commit, not MR creation
    assert mock_post.call_count == 1


# ---------------------------------------------------------------------------
# T7: BitbucketAdapter.get_pr_template and ensure_lessons_pr
# ---------------------------------------------------------------------------

def test_bitbucket_get_pr_template_returns_content_when_found(adapter) -> None:
    """T7a: returns template text when Bitbucket src API returns 200."""
    resp = MagicMock()
    resp.status_code = 200
    resp.text = "## Summary\n\nDescribe your change."
    resp.raise_for_status = MagicMock()

    with patch("httpx.get", return_value=resp):
        result = adapter.get_pr_template("owner", "repo")

    assert result == "## Summary\n\nDescribe your change."


def test_bitbucket_get_pr_template_returns_none_on_404(adapter) -> None:
    """T7b: returns None when template file is not found (404)."""
    resp = MagicMock()
    resp.status_code = 404
    resp.raise_for_status = MagicMock()

    with patch("httpx.get", return_value=resp):
        result = adapter.get_pr_template("owner", "repo")

    assert result is None


def test_bitbucket_ensure_lessons_pr_creates_pr_when_none_exists(adapter) -> None:
    """T7c: no open PR found → commit to branch, create PR, return URL."""
    empty_list = MagicMock()
    empty_list.raise_for_status = MagicMock()
    empty_list.json.return_value = {"values": []}

    commit_resp = MagicMock()
    commit_resp.raise_for_status = MagicMock()

    create_resp = MagicMock()
    create_resp.raise_for_status = MagicMock()
    create_resp.json.return_value = {
        "links": {"html": {"href": "https://bitbucket.org/ws/repo/pull-requests/5"}}
    }

    with patch("httpx.get", return_value=empty_list), \
         patch("httpx.post", side_effect=[commit_resp, create_resp]) as mock_post:
        url = adapter.ensure_lessons_pr(
            repo_owner="ws",
            repo_name="repo",
            pr_number=10,
            branch="chore/revue-lessons-10",
            revue_yml_content="noise_filters: {}",
            commit_message="chore: add pattern",
            pr_title="chore: Revue lessons from PR #10",
            pr_description="## Summary",
        )

    assert url == "https://bitbucket.org/ws/repo/pull-requests/5"
    # PR creation payload
    pr_payload = mock_post.call_args_list[1].kwargs["json"]
    assert pr_payload["source"]["branch"]["name"] == "chore/revue-lessons-10"
    assert pr_payload["destination"]["branch"]["name"] == "main"


def test_bitbucket_ensure_lessons_pr_returns_existing_url_without_creating(adapter) -> None:
    """T7d: open PR exists → commit to branch, return existing URL (no new PR)."""
    open_pr = MagicMock()
    open_pr.raise_for_status = MagicMock()
    open_pr.json.return_value = {
        "values": [
            {"links": {"html": {"href": "https://bitbucket.org/ws/repo/pull-requests/3"}}}
        ]
    }

    commit_resp = MagicMock()
    commit_resp.raise_for_status = MagicMock()

    with patch("httpx.get", return_value=open_pr), \
         patch("httpx.post", return_value=commit_resp) as mock_post:
        url = adapter.ensure_lessons_pr(
            repo_owner="ws",
            repo_name="repo",
            pr_number=10,
            branch="chore/revue-lessons-10",
            revue_yml_content="noise_filters: {}",
            commit_message="chore: add pattern",
            pr_title="chore: Revue lessons from PR #10",
            pr_description="## Summary",
        )

    assert url == "https://bitbucket.org/ws/repo/pull-requests/3"
    assert mock_post.call_count == 1  # Only the commit, not PR creation


# ---------------------------------------------------------------------------
# REVUE-161: resolve_conversation — PUT to resolve endpoint for PR comments
# ---------------------------------------------------------------------------

def test_bitbucket_resolve_conversation(adapter) -> None:
    """REVUE-161 T1.1: resolve_conversation calls POST with correct URL; no error logged on success."""
    mock_response = MagicMock()
    mock_response.status_code = 200

    with patch("httpx.post", return_value=mock_response) as mock_post, \
         patch("revue.core.log.Log.cli.error") as mock_log:
        adapter.resolve_conversation("workspace", "repo", 42, "100")

    mock_post.assert_called_once()
    call_url = mock_post.call_args[0][0]
    assert "/repositories/workspace/repo/pullrequests/42/comments/100/resolve" in call_url
    mock_log.assert_not_called()


def test_bitbucket_resolve_conversation_idempotent_on_409(adapter) -> None:
    """REVUE-161: resolve_conversation treats 409 Already Resolved as success."""
    mock_response = MagicMock()
    mock_response.status_code = 409

    with patch("httpx.post", return_value=mock_response), \
         patch("revue.core.log.Log.cli.error") as mock_log:
        adapter.resolve_conversation("workspace", "repo", 42, "100")

    mock_log.assert_not_called()


def test_bitbucket_resolve_conversation_no_error_on_failure(adapter) -> None:
    """REVUE-161 T2.1: resolve_conversation logs error but does not raise."""
    mock_response = MagicMock()
    mock_response.status_code = 500

    with patch("httpx.post", return_value=mock_response), \
         patch("revue.core.log.Log.cli.error") as mock_log:
        adapter.resolve_conversation("workspace", "repo", 42, "100")

    mock_log.assert_called_once()
    # No exception raised


def test_bitbucket_resolve_conversation_logs_on_request_error(adapter) -> None:
    """REVUE-161 T2.1: resolve_conversation logs httpx errors."""
    with patch("httpx.post", side_effect=Exception("Network error")), \
         patch("revue.core.log.Log.cli.error") as mock_log:
        adapter.resolve_conversation("workspace", "repo", 42, "100")

    mock_log.assert_called_once()
