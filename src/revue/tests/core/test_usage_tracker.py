"""Tests for revue.core.usage_tracker — no real HTTP calls."""
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from revue.core.usage_tracker import (
    UPGRADE_URL,
    ReviewLimitError,
    _post_usage,
    check_reviews_left,
    track,
)

_TEST_TRACK_URL = "https://test.example.com/usage/track"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_http_client(status_code: int = 202) -> httpx.Client:
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = status_code
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.return_value = mock_resp
    return mock_client


# ---------------------------------------------------------------------------
# check_reviews_left()
# ---------------------------------------------------------------------------

class TestCheckReviewsLeft:
    def test_none_means_unlimited(self):
        check_reviews_left(None)  # must not raise

    def test_positive_count_allowed(self):
        check_reviews_left(1)   # 1 remaining — must not raise
        check_reviews_left(25)  # full month — must not raise

    def test_zero_raises_review_limit_error(self):
        with pytest.raises(ReviewLimitError, match="used all of your free reviews"):
            check_reviews_left(0)

    def test_negative_raises_review_limit_error(self):
        with pytest.raises(ReviewLimitError):
            check_reviews_left(-1)

    def test_error_message_includes_upgrade_url(self):
        with pytest.raises(ReviewLimitError, match=UPGRADE_URL):
            check_reviews_left(0)


# ---------------------------------------------------------------------------
# track() — fire-and-forget (synchronous via injected client)
# ---------------------------------------------------------------------------

class TestTrack:
    def test_posts_correct_payload(self):
        client = _make_http_client()
        track("my-key", "org/repo", ["orchestrator", "maya"], 3200, track_url=_TEST_TRACK_URL, _http_client=client)
        payload = client.post.call_args[1]["json"]
        assert payload["key"] == "my-key"
        assert payload["repo_id"] == "org/repo"
        assert payload["agents_used"] == ["orchestrator", "maya"]
        assert payload["duration_ms"] == 3200

    def test_posts_to_correct_url(self):
        client = _make_http_client()
        track("k", "r", [], 0, track_url=_TEST_TRACK_URL, _http_client=client)
        url = client.post.call_args[0][0]
        assert url == _TEST_TRACK_URL

    def test_does_not_raise_on_server_error(self):
        client = _make_http_client(status_code=500)
        track("key", "repo", ["orchestrator"], 100, track_url=_TEST_TRACK_URL, _http_client=client)  # must not raise

    def test_does_not_raise_on_network_error(self):
        client = MagicMock(spec=httpx.Client)
        client.post.side_effect = httpx.ConnectError("down")
        track("key", "repo", [], 0, track_url=_TEST_TRACK_URL, _http_client=client)  # must not raise

    def test_does_not_raise_on_timeout(self):
        client = MagicMock(spec=httpx.Client)
        client.post.side_effect = httpx.TimeoutException("timeout")
        track("key", "repo", [], 0, track_url=_TEST_TRACK_URL, _http_client=client)  # must not raise

    def test_warning_logged_on_unexpected_status(self, caplog):
        import logging
        client = _make_http_client(status_code=400)
        with caplog.at_level(logging.WARNING, logger="revue.core.usage_tracker"):
            track("key", "repo", [], 0, track_url=_TEST_TRACK_URL, _http_client=client)
        assert any("unexpected status" in r.message for r in caplog.records)

    def test_warning_logged_on_network_failure(self, caplog):
        import logging
        client = MagicMock(spec=httpx.Client)
        client.post.side_effect = httpx.ConnectError("err")
        with caplog.at_level(logging.WARNING, logger="revue.core.usage_tracker"):
            track("key", "repo", [], 0, track_url=_TEST_TRACK_URL, _http_client=client)
        assert any("non-blocking" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# _post_usage() internals
# ---------------------------------------------------------------------------

class TestPostUsageInternal:
    def test_success_response_does_not_log_warning(self, caplog):
        import logging
        client = _make_http_client(status_code=200)
        with caplog.at_level(logging.WARNING, logger="revue.core.usage_tracker"):
            _post_usage({"key": "k", "repo_id": "r", "agents_used": [], "duration_ms": 0}, client, _TEST_TRACK_URL)
        assert not caplog.records

    def test_204_accepted_silently(self, caplog):
        import logging
        client = _make_http_client(status_code=204)
        with caplog.at_level(logging.WARNING, logger="revue.core.usage_tracker"):
            _post_usage({"key": "k", "repo_id": "r", "agents_used": [], "duration_ms": 0}, client, _TEST_TRACK_URL)
        assert not caplog.records


# ---------------------------------------------------------------------------
# REVUE-109: Usage tracking disabled when REVUE_USAGE_API_URL not set
# ---------------------------------------------------------------------------

class TestTrackingDisabledWithoutUrl:
    def test_post_usage_skips_when_url_not_set(self, caplog):
        import logging
        client = _make_http_client()
        with caplog.at_level(logging.DEBUG, logger="revue.core.usage_tracker"):
            _post_usage({"key": "k"}, client, None)
        client.post.assert_not_called()
        assert any("Usage tracking disabled" in r.message for r in caplog.records)

    def test_track_skips_silently_when_url_not_set(self, monkeypatch):
        monkeypatch.delenv("REVUE_USAGE_API_URL", raising=False)
        client = _make_http_client()
        track("key", "repo", ["orchestrator"], 100, _http_client=client)
        client.post.assert_not_called()

    def test_track_url_defaults_to_env_var(self, monkeypatch):
        monkeypatch.setenv("REVUE_USAGE_API_URL", _TEST_TRACK_URL)
        client = _make_http_client()
        track("key", "repo", [], 0, _http_client=client)
        url = client.post.call_args[0][0]
        assert url == _TEST_TRACK_URL
