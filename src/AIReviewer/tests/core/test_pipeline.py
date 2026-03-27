#!/usr/bin/env python3
"""Tests for ReviewPipeline (SRP + DIP)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from AIReviewer.core.ai_config import AIConfig
from AIReviewer.core.models import FileChange
from AIReviewer.core.pipeline import ReviewPipeline, ReviewResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _config(**kwargs) -> AIConfig:
    base = dict(
        gitlab_url="", gitlab_token="", gitlab_project_id="",
        gitlab_project_path="", gitlab_project_url="",
        genai_gateway_url="", openai_api_key="test",
        gen_ai_gateway_model="claude-sonnet-4-5-20250929",
        ai_temp=0.3, ai_confidence=70, ai_max_tokens=4096,
        provider="anthropic", api_key="test-key",
        ignore_patterns=[], max_diff_lines=2000,
    )
    base.update(kwargs)
    return AIConfig(**base)


def _fc(path: str, additions: int = 5, deletions: int = 2) -> FileChange:
    return FileChange(
        file_path=path, change_type="modified",
        additions=additions, deletions=deletions,
        diff="@@ -1 +1 @@\n-old\n+new",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_pipeline_uses_injected_client():
    """Injected mock client is used — not the real one (DIP)."""
    mock_client = MagicMock()
    mock_client.complete.return_value = '{"findings": []}'
    config = _config()
    pipeline = ReviewPipeline(config, client=mock_client)

    with patch("AIReviewer.core.pipeline.parse_diff_file", return_value=[_fc("app.py")]):
        results, _ = pipeline.run("fake.diff")

    assert mock_client.complete.called
    assert len(results) == 1


def test_pipeline_runs_included_files():
    """complete() called once per included file."""
    mock_client = MagicMock()
    mock_client.complete.return_value = "ok"
    config = _config()
    pipeline = ReviewPipeline(config, client=mock_client)

    with patch("AIReviewer.core.pipeline.parse_diff_file",
               return_value=[_fc("a.py"), _fc("b.py")]):
        results, excluded = pipeline.run("fake.diff")

    assert mock_client.complete.call_count == 2
    assert len(results) == 2
    assert len(excluded) == 0


def test_pipeline_excludes_filtered_files():
    """Files matching ignore_patterns are excluded — complete() not called for them."""
    mock_client = MagicMock()
    mock_client.complete.return_value = "ok"
    config = _config(ignore_patterns=["*.md"])
    pipeline = ReviewPipeline(config, client=mock_client)

    with patch("AIReviewer.core.pipeline.parse_diff_file",
               return_value=[_fc("app.py"), _fc("README.md")]):
        results, excluded = pipeline.run("fake.diff")

    assert mock_client.complete.call_count == 1
    assert len(results) == 1
    assert results[0].file_path == "app.py"


def test_pipeline_returns_excluded_list():
    """Excluded files returned as second element of tuple."""
    mock_client = MagicMock()
    mock_client.complete.return_value = "ok"
    config = _config(ignore_patterns=["*.lock"])
    pipeline = ReviewPipeline(config, client=mock_client)

    with patch("AIReviewer.core.pipeline.parse_diff_file",
               return_value=[_fc("main.py"), _fc("yarn.lock")]):
        results, excluded = pipeline.run("fake.diff")

    assert len(excluded) == 1
    assert excluded[0].file_path == "yarn.lock"


def test_pipeline_handles_client_error():
    """Client error sets result.error — pipeline does not raise."""
    mock_client = MagicMock()
    mock_client.complete.side_effect = RuntimeError("API down")
    config = _config()
    pipeline = ReviewPipeline(config, client=mock_client)

    with patch("AIReviewer.core.pipeline.parse_diff_file", return_value=[_fc("app.py")]):
        results, _ = pipeline.run("fake.diff")

    assert len(results) == 1
    assert results[0].error == "API down"
    assert results[0].response == ""
    assert not results[0].success


def test_pipeline_result_success_property():
    """ReviewResult.success is True when no error, False when error set."""
    ok = ReviewResult(file_path="a.py", response="good")
    err = ReviewResult(file_path="b.py", response="", error="boom")

    assert ok.success is True
    assert err.success is False


def test_pipeline_stops_at_diff_limit():
    """When diff exceeds limit, pipeline returns a single warning result without calling client."""
    mock_client = MagicMock()
    config = _config(max_diff_lines=100)
    pipeline = ReviewPipeline(config, client=mock_client)

    # 2 files × 100 lines each = 200 lines > limit 100
    big_files = [
        FileChange(file_path=f"big{i}.py", change_type="modified",
                   additions=60, deletions=40, diff="")
        for i in range(2)
    ]
    with patch("AIReviewer.core.pipeline.parse_diff_file", return_value=big_files):
        results, excluded = pipeline.run("fake.diff")

    assert not mock_client.complete.called
    assert len(results) == 1
    assert results[0].file_path == "[diff-limit]"
    assert "too large" in results[0].response.lower()
