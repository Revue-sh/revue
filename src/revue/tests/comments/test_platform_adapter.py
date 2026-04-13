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
                        ]
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
                    "reviewThreads": {"nodes": []}
                }
            }
        }
    }

    with patch.object(gh, "_graphql", return_value=graphql_response):
        result = gh.fetch_review_thread_ids(4, "owner", "repo")

    assert result == []


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
