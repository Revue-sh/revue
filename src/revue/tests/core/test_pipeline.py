#!/usr/bin/env python3
"""Tests for ReviewPipeline (SRP + DIP)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from revue.core.ai_config import AIConfig
from revue.core.license_validator import LicenseInfo
from revue.core.models import FileChange
from revue.core.pipeline import ReviewPipeline, ReviewResult
from revue.core.usage_tracker import ReviewLimitError


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


def _license_info(**kwargs) -> LicenseInfo:
    """Return a stub LicenseInfo for tests that don't care about licensing."""
    defaults = dict(
        valid=True,
        tier="pro",
        agents_allowed=["orchestrator", "code-quality-expert", "consolidator"],
        reviews_left=None,
        expires_at="2027-01-01T00:00:00Z",
        key="test-license-key",
    )
    defaults.update(kwargs)
    return LicenseInfo(**defaults)


def _fc(path: str, additions: int = 5, deletions: int = 2) -> FileChange:
    return FileChange(
        file_path=path, change_type="modified",
        additions=additions, deletions=deletions,
        diff="@@ -1 +1 @@\n-old\n+new",
    )


def _pipeline(config: AIConfig | None = None, client=None, **li_kwargs) -> ReviewPipeline:
    """Build a pipeline with mocked license and usage tracking."""
    cfg = config or _config()
    mc = client or MagicMock()
    if client is None:
        mc.complete.return_value = "ok"
    return ReviewPipeline(cfg, client=mc, license_info=_license_info(**li_kwargs))


# ---------------------------------------------------------------------------
# Core pipeline behaviour
# ---------------------------------------------------------------------------

def test_pipeline_uses_injected_client():
    """Injected mock client is used — not the real one (DIP)."""
    mock_client = MagicMock()
    mock_client.complete.return_value = '{"findings": []}'
    config = _config()
    pipeline = ReviewPipeline(config, client=mock_client, license_info=_license_info())

    with patch("revue.core.pipeline.parse_diff_file", return_value=[_fc("app.py")]), \
         patch("revue.core.pipeline.track_usage"):
        results, _ = pipeline.run("fake.diff")

    assert mock_client.complete.called
    assert len(results) == 1


def test_pipeline_runs_included_files():
    """complete() called once per included file."""
    mock_client = MagicMock()
    mock_client.complete.return_value = "ok"
    config = _config()
    pipeline = ReviewPipeline(config, client=mock_client, license_info=_license_info())

    with patch("revue.core.pipeline.parse_diff_file",
               return_value=[_fc("a.py"), _fc("b.py")]), \
         patch("revue.core.pipeline.track_usage"):
        results, excluded = pipeline.run("fake.diff")

    assert mock_client.complete.call_count == 2
    assert len(results) == 2
    assert len(excluded) == 0


def test_pipeline_excludes_filtered_files():
    """Files matching ignore_patterns are excluded — complete() not called for them."""
    mock_client = MagicMock()
    mock_client.complete.return_value = "ok"
    config = _config(ignore_patterns=["*.md"])
    pipeline = ReviewPipeline(config, client=mock_client, license_info=_license_info())

    with patch("revue.core.pipeline.parse_diff_file",
               return_value=[_fc("app.py"), _fc("README.md")]), \
         patch("revue.core.pipeline.track_usage"):
        results, excluded = pipeline.run("fake.diff")

    assert mock_client.complete.call_count == 1
    assert len(results) == 1
    assert results[0].file_path == "app.py"


def test_pipeline_returns_excluded_list():
    """Excluded files returned as second element of tuple."""
    mock_client = MagicMock()
    mock_client.complete.return_value = "ok"
    config = _config(ignore_patterns=["*.lock"])
    pipeline = ReviewPipeline(config, client=mock_client, license_info=_license_info())

    with patch("revue.core.pipeline.parse_diff_file",
               return_value=[_fc("main.py"), _fc("yarn.lock")]), \
         patch("revue.core.pipeline.track_usage"):
        results, excluded = pipeline.run("fake.diff")

    assert len(excluded) == 1
    assert excluded[0].file_path == "yarn.lock"


def test_pipeline_handles_client_error():
    """Client error sets result.error — pipeline does not raise."""
    mock_client = MagicMock()
    mock_client.complete.side_effect = RuntimeError("API down")
    config = _config()
    pipeline = ReviewPipeline(config, client=mock_client, license_info=_license_info())

    with patch("revue.core.pipeline.parse_diff_file", return_value=[_fc("app.py")]), \
         patch("revue.core.pipeline.track_usage"):
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
    pipeline = ReviewPipeline(config, client=mock_client, license_info=_license_info())

    # 2 files × 100 lines each = 200 lines > limit 100
    big_files = [
        FileChange(file_path=f"big{i}.py", change_type="modified",
                   additions=60, deletions=40, diff="")
        for i in range(2)
    ]
    with patch("revue.core.pipeline.parse_diff_file", return_value=big_files):
        results, excluded = pipeline.run("fake.diff")

    assert not mock_client.complete.called
    assert len(results) == 1
    assert results[0].file_path == "[diff-limit]"
    assert "too large" in results[0].response.lower()


# ---------------------------------------------------------------------------
# License + usage integration
# ---------------------------------------------------------------------------

def test_pipeline_raises_when_reviews_exhausted():
    """Pipeline raises ReviewLimitError when reviews_left == 0."""
    mock_client = MagicMock()
    config = _config()
    pipeline = ReviewPipeline(
        config, client=mock_client,
        license_info=_license_info(reviews_left=0),
    )

    with pytest.raises(ReviewLimitError):
        with patch("revue.core.pipeline.parse_diff_file", return_value=[_fc("a.py")]):
            pipeline.run("fake.diff")


def test_pipeline_proceeds_when_reviews_left_positive():
    """Pipeline runs normally when reviews_left > 0."""
    mock_client = MagicMock()
    mock_client.complete.return_value = "ok"
    config = _config()
    pipeline = ReviewPipeline(
        config, client=mock_client,
        license_info=_license_info(reviews_left=5),
    )

    with patch("revue.core.pipeline.parse_diff_file", return_value=[_fc("a.py")]), \
         patch("revue.core.pipeline.track_usage"):
        results, _ = pipeline.run("fake.diff")

    assert len(results) == 1


def test_pipeline_calls_track_after_review():
    """track_usage is called after a successful review."""
    mock_client = MagicMock()
    mock_client.complete.return_value = "ok"
    config = _config()
    pipeline = ReviewPipeline(config, client=mock_client, license_info=_license_info())

    with patch("revue.core.pipeline.parse_diff_file", return_value=[_fc("a.py")]), \
         patch("revue.core.pipeline.track_usage") as mock_track:
        pipeline.run("fake.diff")

    assert mock_track.called
    call_kwargs = mock_track.call_args[1]
    assert call_kwargs["key"] == "test-license-key"
    assert "duration_ms" in call_kwargs


def test_pipeline_calls_validate_license_when_none_injected(monkeypatch):
    """When no license_info injected, validate_license() is called."""
    monkeypatch.setenv("REVUE_LICENSE_KEY", "env-key")
    mock_client = MagicMock()
    mock_client.complete.return_value = "ok"
    config = _config()
    pipeline = ReviewPipeline(config, client=mock_client)

    license_info = _license_info(key="env-key")
    with patch("revue.core.pipeline.validate_license", return_value=license_info) as mock_val, \
         patch("revue.core.pipeline.parse_diff_file", return_value=[_fc("a.py")]), \
         patch("revue.core.pipeline.track_usage"):
        pipeline.run("fake.diff")

    assert mock_val.called


# ---------------------------------------------------------------------------
# REVUE-81: agents_allowed enforcement
# ---------------------------------------------------------------------------

def test_pipeline_respects_free_tier_agents_allowed():
    """Free tier: only orchestrator, code-quality-expert, consolidator allowed."""
    mock_client = MagicMock()
    mock_client.complete.return_value = '{"findings": []}'
    config = _config()
    
    free_license = _license_info(
        tier="free",
        agents_allowed=["orchestrator", "code-quality-expert", "consolidator"],
    )
    pipeline = ReviewPipeline(config, client=mock_client, license_info=free_license)

    with patch("revue.core.pipeline.parse_diff_file", return_value=[_fc("app.py")]), \
         patch("revue.core.pipeline.track_usage") as mock_track:
        results, _ = pipeline.run("fake.diff")

    # Verify agents_used sent to track_usage includes only allowed agents
    call_kwargs = mock_track.call_args[1]
    agents_used = call_kwargs["agents_used"]
    
    assert "orchestrator" in agents_used
    assert "code-quality-expert" in agents_used
    # Ensure no premium agents tracked
    assert "security-expert" not in agents_used
    assert "performance-expert" not in agents_used


def test_pipeline_respects_pro_tier_agents_allowed():
    """Pro tier: all 9 agents allowed."""
    mock_client = MagicMock()
    mock_client.complete.return_value = '{"findings": []}'
    config = _config()
    
    pro_license = _license_info(
        tier="pro",
        agents_allowed=[
            "orchestrator", "code-quality-expert", "security-expert",
            "performance-expert", "architecture-expert", "consolidator",
            "sage", "cleo", "nova",
        ],
    )
    pipeline = ReviewPipeline(config, client=mock_client, license_info=pro_license)

    with patch("revue.core.pipeline.parse_diff_file", return_value=[_fc("app.py")]), \
         patch("revue.core.pipeline.track_usage") as mock_track:
        results, _ = pipeline.run("fake.diff")

    call_kwargs = mock_track.call_args[1]
    agents_used = call_kwargs["agents_used"]
    
    # At minimum, orchestrator and code-quality-expert should be used
    assert "orchestrator" in agents_used
    assert "code-quality-expert" in agents_used


def test_pipeline_logs_active_agents(capsys):
    """Pipeline logs active agents after license validation."""
    mock_client = MagicMock()
    mock_client.complete.return_value = '{"findings": []}'
    config = _config()
    
    license_info = _license_info(
        agents_allowed=["orchestrator", "code-quality-expert"],
    )
    pipeline = ReviewPipeline(config, client=mock_client, license_info=license_info)

    with patch("revue.core.pipeline.parse_diff_file", return_value=[_fc("app.py")]), \
         patch("revue.core.pipeline.track_usage"):
        pipeline.run("fake.diff")

    captured = capsys.readouterr()
    
    # Verify log output contains active agents
    assert "Active agents:" in captured.out
    assert "orchestrator" in captured.out
    assert "code-quality-expert" in captured.out
