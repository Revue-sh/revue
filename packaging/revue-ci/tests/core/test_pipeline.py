#!/usr/bin/env python3
"""Tests for ReviewPipeline (SRP + DIP)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from revue_core.core.ai_client import CompletionResult, TokenUsage
from revue_core.core.ai_config import AIConfig
from revue_core.core.license_validator import LicenseInfo
from revue_core.core.models import FileChange
from revue_core.core.pipeline import ReviewPipeline, ReviewResult
from revue_core.core.usage_tracker import ReviewLimitError


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


def _cr(text: str) -> CompletionResult:
    return CompletionResult(text=text, usage=TokenUsage())


_CLEAN_RESPONSE = '{"status": "clean", "summary": "ok", "confidence": 1.0}'


def _stub_both_paths(mock_client, text: str = _CLEAN_RESPONSE) -> None:
    """REVUE-246: many orchestration tests stub ``complete`` but the
    orchestration path actually calls ``complete_with_tools``. Pre-REVUE-246
    the unmocked path returned MagicMock objects that parsed as ``findings=[]``
    silently; the three-state contract now classifies that as an error and
    fails the agent. Stubbing both paths preserves the pre-REVUE-246 effective
    behaviour for tests that don't care about the response shape."""
    cr = _cr(text)
    mock_client.complete.return_value = cr
    mock_client.complete_with_tools.return_value = cr


def _pipeline(config: AIConfig | None = None, client=None, **li_kwargs) -> ReviewPipeline:
    """Build a pipeline with mocked license and usage tracking."""
    cfg = config or _config()
    mc = client or MagicMock()
    if client is None:
        mc.complete.return_value = _cr("ok")
    return ReviewPipeline(cfg, client=mc, license_info=_license_info(**li_kwargs))


# ---------------------------------------------------------------------------
# Core pipeline behaviour
# ---------------------------------------------------------------------------

def test_pipeline_uses_injected_client():
    """Injected mock client is used — not the real one (DIP)."""
    mock_client = MagicMock()
    mock_client.complete.return_value = _cr('{"status": "clean", "summary": "ok", "confidence": 1.0}')
    config = _config()
    pipeline = ReviewPipeline(config, client=mock_client, license_info=_license_info())

    with patch("revue_core.core.pipeline.parse_diff_file", return_value=[_fc("app.py")]), \
         patch("revue_core.core.pipeline.track_usage"):
        results, _, _, _ = pipeline.run("fake.diff")

    assert mock_client.complete.called
    assert len(results) == 1


def test_pipeline_runs_included_files():
    """complete() called once per included file."""
    mock_client = MagicMock()
    mock_client.complete.return_value = _cr("ok")
    config = _config()
    pipeline = ReviewPipeline(config, client=mock_client, license_info=_license_info())

    with patch("revue_core.core.pipeline.parse_diff_file",
               return_value=[_fc("a.py"), _fc("b.py")]), \
         patch("revue_core.core.pipeline.track_usage"):
        results, excluded, _, _ = pipeline.run("fake.diff")

    assert mock_client.complete.call_count == 2
    assert len(results) == 2
    assert len(excluded) == 0


def test_pipeline_excludes_filtered_files():
    """Files matching ignore_patterns are excluded — complete() not called for them."""
    mock_client = MagicMock()
    mock_client.complete.return_value = _cr("ok")
    config = _config(ignore_patterns=["*.md"])
    pipeline = ReviewPipeline(config, client=mock_client, license_info=_license_info())

    with patch("revue_core.core.pipeline.parse_diff_file",
               return_value=[_fc("app.py"), _fc("README.md")]), \
         patch("revue_core.core.pipeline.track_usage"):
        results, excluded, _, _ = pipeline.run("fake.diff")

    assert mock_client.complete.call_count == 1
    assert len(results) == 1
    assert results[0].file_path == "app.py"


def test_pipeline_returns_excluded_list():
    """Excluded files returned as second element of tuple."""
    mock_client = MagicMock()
    mock_client.complete.return_value = _cr("ok")
    config = _config(ignore_patterns=["*.lock"])
    pipeline = ReviewPipeline(config, client=mock_client, license_info=_license_info())

    with patch("revue_core.core.pipeline.parse_diff_file",
               return_value=[_fc("main.py"), _fc("yarn.lock")]), \
         patch("revue_core.core.pipeline.track_usage"):
        results, excluded, _, _ = pipeline.run("fake.diff")

    assert len(excluded) == 1
    assert excluded[0].file_path == "yarn.lock"


def test_pipeline_handles_client_error():
    """Client error sets result.error — pipeline does not raise."""
    mock_client = MagicMock()
    mock_client.complete.side_effect = RuntimeError("API down")
    config = _config()
    pipeline = ReviewPipeline(config, client=mock_client, license_info=_license_info())

    with patch("revue_core.core.pipeline.parse_diff_file", return_value=[_fc("app.py")]), \
         patch("revue_core.core.pipeline.track_usage"):
        results, _, _, _ = pipeline.run("fake.diff")

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
    with patch("revue_core.core.pipeline.parse_diff_file", return_value=big_files):
        results, excluded, _, _ = pipeline.run("fake.diff")

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
        with patch("revue_core.core.pipeline.parse_diff_file", return_value=[_fc("a.py")]):
            pipeline.run("fake.diff")


def test_pipeline_proceeds_when_reviews_left_positive():
    """Pipeline runs normally when reviews_left > 0."""
    mock_client = MagicMock()
    mock_client.complete.return_value = _cr("ok")
    config = _config()
    pipeline = ReviewPipeline(
        config, client=mock_client,
        license_info=_license_info(reviews_left=5),
    )

    with patch("revue_core.core.pipeline.parse_diff_file", return_value=[_fc("a.py")]), \
         patch("revue_core.core.pipeline.track_usage"):
        results, _, _, _ = pipeline.run("fake.diff")

    assert len(results) == 1


def test_pipeline_calls_track_after_review():
    """track_usage is called after a successful review."""
    mock_client = MagicMock()
    mock_client.complete.return_value = _cr("ok")
    config = _config()
    pipeline = ReviewPipeline(config, client=mock_client, license_info=_license_info())

    with patch("revue_core.core.pipeline.parse_diff_file", return_value=[_fc("a.py")]), \
         patch("revue_core.core.pipeline.track_usage") as mock_track:
        pipeline.run("fake.diff")

    assert mock_track.called
    call_kwargs = mock_track.call_args[1]
    assert call_kwargs["key"] == "test-license-key"
    assert "duration_ms" in call_kwargs


def test_pipeline_calls_validate_license_when_none_injected(monkeypatch):
    """When no license_info injected, validate_license() is called."""
    monkeypatch.setenv("REVUE_LICENSE_KEY", "env-key")
    mock_client = MagicMock()
    mock_client.complete.return_value = _cr("ok")
    config = _config()
    pipeline = ReviewPipeline(config, client=mock_client)

    license_info = _license_info(key="env-key")
    with patch("revue_core.core.pipeline.validate_license", return_value=license_info) as mock_val, \
         patch("revue_core.core.pipeline.parse_diff_file", return_value=[_fc("a.py")]), \
         patch("revue_core.core.pipeline.track_usage"):
        pipeline.run("fake.diff")

    assert mock_val.called


# ---------------------------------------------------------------------------
# REVUE-81: agents_allowed enforcement
# ---------------------------------------------------------------------------

def test_pipeline_respects_free_tier_agents_allowed():
    """Free tier: only orchestrator, code-quality-expert, consolidator allowed."""
    mock_client = MagicMock()
    mock_client.complete.return_value = _cr('{"status": "clean", "summary": "ok", "confidence": 1.0}')
    config = _config()
    
    free_license = _license_info(
        tier="free",
        agents_allowed=["orchestrator", "code-quality-expert", "consolidator"],
    )
    pipeline = ReviewPipeline(config, client=mock_client, license_info=free_license)

    with patch("revue_core.core.pipeline.parse_diff_file", return_value=[_fc("app.py")]), \
         patch("revue_core.core.pipeline.track_usage") as mock_track:
        results, _, _, _ = pipeline.run("fake.diff")

    # Verify agents_used sent to track_usage includes only allowed agents
    call_kwargs = mock_track.call_args[1]
    agents_used = call_kwargs["agents_used"]
    
    assert "orchestrator" in agents_used
    assert "code-quality-expert" in agents_used
    # Ensure no premium agents tracked
    assert "security-expert" not in agents_used
    assert "performance-expert" not in agents_used


def test_pipeline_respects_pro_tier_agents_allowed():
    """Pro tier: full orchestration path runs (not simplified loop).

    Pro tier includes cleo/nova/sage in agents_allowed — _is_premium_tier()
    returns True, so the orchestration engine is invoked instead of the
    simplified single-pass loop. We verify:
    1. Orchestration engine ran (agents_used comes from real agent names)
    2. orchestrator always tracked as entry point
    3. track_usage was called
    """
    mock_client = MagicMock()
    _stub_both_paths(mock_client)
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

    with patch("revue_core.core.pipeline.parse_diff_file", return_value=[_fc("app.py")]), \
         patch("revue_core.core.pipeline.track_usage") as mock_track:
        results, _, _, _ = pipeline.run("fake.diff")

    # Orchestration path ran — track_usage called with agents_used
    assert mock_track.called
    call_kwargs = mock_track.call_args[1]
    agents_used = call_kwargs["agents_used"]

    # orchestrator always present (entry point sentinel)
    assert "orchestrator" in agents_used
    # At least one real orchestration agent ran
    assert len(agents_used) >= 1


def test_pipeline_uses_simplified_path_for_free_tier():
    """Free tier agents_allowed has no premium agents → simplified path runs.

    Verified by checking client.complete() is called (simplified loop calls it
    directly); orchestration path does NOT call client.complete() directly.
    """
    mock_client = MagicMock()
    mock_client.complete.return_value = _cr('{"status": "clean", "summary": "ok", "confidence": 1.0}')
    config = _config()

    free_license = _license_info(
        tier="free",
        agents_allowed=["orchestrator", "code-quality-expert", "consolidator"],
    )
    pipeline = ReviewPipeline(config, client=mock_client, license_info=free_license)

    with patch("revue_core.core.pipeline.parse_diff_file", return_value=[_fc("app.py")]), \
         patch("revue_core.core.pipeline.track_usage"):
        results, _, _, _ = pipeline.run("fake.diff")

    # Simplified path calls client.complete() directly
    assert mock_client.complete.called


def test_pipeline_uses_orchestration_path_for_pro_tier(caplog):
    """Pro tier triggers orchestration path — log says 'orchestrated'."""
    import logging
    mock_client = MagicMock()
    _stub_both_paths(mock_client)
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

    with caplog.at_level(logging.INFO, logger="revue_core.core.pipeline"), \
         patch("revue_core.core.pipeline.parse_diff_file", return_value=[_fc("app.py")]), \
         patch("revue_core.core.pipeline.track_usage"):
        pipeline.run("fake.diff")

    assert "orchestrated" in caplog.text


def test_pipeline_orchestration_falls_back_when_no_agents_match():
    """If no loaded agents match agents_allowed, falls back to simplified review."""
    mock_client = MagicMock()
    mock_client.complete.return_value = _cr('{"status": "clean", "summary": "ok", "confidence": 1.0}')
    config = _config()

    # Premium tier BUT with an agent name that doesn't exist in the agents dir
    exotic_license = _license_info(
        tier="pro",
        agents_allowed=["orchestrator", "nonexistent-agent-xyz", "another-fake"],
    )
    pipeline = ReviewPipeline(config, client=mock_client, license_info=exotic_license)

    with patch("revue_core.core.pipeline.parse_diff_file", return_value=[_fc("app.py")]), \
         patch("revue_core.core.pipeline.track_usage"):
        results, excluded, _, _ = pipeline.run("fake.diff")

    # Should complete without raising (graceful degradation)
    assert isinstance(results, list)
    assert isinstance(excluded, list)


def test_pipeline_logs_active_agents(caplog):
    """Pipeline logs active agents after license validation."""
    import logging
    mock_client = MagicMock()
    mock_client.complete.return_value = _cr('{"status": "clean", "summary": "ok", "confidence": 1.0}')
    config = _config()

    license_info = _license_info(
        agents_allowed=["orchestrator", "code-quality-expert"],
    )
    pipeline = ReviewPipeline(config, client=mock_client, license_info=license_info)

    with caplog.at_level(logging.INFO, logger="revue_core.core.pipeline"), \
         patch("revue_core.core.pipeline.parse_diff_file", return_value=[_fc("app.py")]), \
         patch("revue_core.core.pipeline.track_usage"):
        pipeline.run("fake.diff")

    # Verify log output contains active agents
    assert "Active agents:" in caplog.text
    assert "orchestrator" in caplog.text
    assert "code-quality-expert" in caplog.text


# ---------------------------------------------------------------------------
# REVUE-84: PR context injection tests
# ---------------------------------------------------------------------------

def test_pipeline_run_accepts_pr_description_param():
    """pipeline.run() accepts optional pr_description without error (AC4)."""
    from revue_core.core.pr_description_adapter import PRDescription

    mock_client = MagicMock()
    mock_client.complete.return_value = _cr('{"status": "clean", "summary": "ok", "confidence": 1.0}')
    pipeline = _pipeline(client=mock_client)

    pr = PRDescription(
        title="feat: add auth",
        raw_description="## Summary\nAdds JWT auth.",
        summary="Adds JWT auth.",
    )

    with patch("revue_core.core.pipeline.parse_diff_file", return_value=[_fc("auth.py")]), \
         patch("revue_core.core.pipeline.track_usage"):
        results, excluded, _, _ = pipeline.run("fake.diff", pr_description=pr)

    assert isinstance(results, list)


def test_pipeline_run_no_pr_description_unaffected():
    """pipeline.run() without pr_description behaves identically to before (AC4 backward compat)."""
    mock_client = MagicMock()
    mock_client.complete.return_value = _cr('{"status": "clean", "summary": "ok", "confidence": 1.0}')
    pipeline = _pipeline(client=mock_client)

    with patch("revue_core.core.pipeline.parse_diff_file", return_value=[_fc("app.py")]), \
         patch("revue_core.core.pipeline.track_usage"):
        results, excluded, _, _ = pipeline.run("fake.diff")  # no pr_description

    assert isinstance(results, list)


def test_inject_pr_context_prepends_to_system_prompt():
    """_inject_pr_context prepends filtered context to each agent's system_prompt (AC3)."""
    from revue_core.core.pipeline import _inject_pr_context
    from revue_core.core.pr_description_adapter import PRDescription
    from revue_core.core.pr_context import PRContextExtractor

    pr = PRDescription(
        title="Fix: SQL injection",
        raw_description="## Out of Scope\nRate limiting deferred.",
        out_of_scope="Rate limiting deferred.",
    )
    extractor = PRContextExtractor(pr)

    # Build a mock agent with a real-ish definition stub
    mock_agent = MagicMock()
    mock_agent.name = "security-expert"
    mock_agent._def = MagicMock()
    mock_agent._def.system_prompt = "You are a security expert."

    _inject_pr_context([mock_agent], extractor)

    updated = mock_agent._def.system_prompt
    assert "## PR Context" in updated
    assert "Fix: SQL injection" in updated
    assert "Rate limiting deferred" in updated
    assert "You are a security expert." in updated  # original preserved


def test_inject_pr_context_graceful_on_bad_agent():
    """_inject_pr_context never raises even if an agent is malformed (AC4)."""
    from revue_core.core.pipeline import _inject_pr_context
    from revue_core.core.pr_description_adapter import PRDescription
    from revue_core.core.pr_context import PRContextExtractor

    pr = PRDescription(title="Test", raw_description="")
    extractor = PRContextExtractor(pr)

    broken_agent = MagicMock()
    broken_agent.name = "security-expert"
    # _def.system_prompt will raise on assignment
    type(broken_agent._def).system_prompt = property(
        fget=lambda self: "original",
        fset=MagicMock(side_effect=AttributeError("read-only")),
    )

    # Must not raise
    _inject_pr_context([broken_agent], extractor)


def test_inject_pr_context_unknown_agent_gets_title_only():
    """Unknown agents receive just PR title — never noisy, never empty (AC3 fallback)."""
    from revue_core.core.pipeline import _inject_pr_context
    from revue_core.core.pr_description_adapter import PRDescription
    from revue_core.core.pr_context import PRContextExtractor

    pr = PRDescription(
        title="Refactor DB layer",
        raw_description="## Background\nLegacy code.",
        background="Legacy code.",
    )
    extractor = PRContextExtractor(pr)

    mock_agent = MagicMock()
    mock_agent.name = "unknown-future-agent"
    mock_agent._def = MagicMock()
    mock_agent._def.system_prompt = "Original prompt."

    _inject_pr_context([mock_agent], extractor)

    updated = mock_agent._def.system_prompt
    # Unknown agent → summary fallback from to_prompt_context() = just title
    assert "Refactor DB layer" in updated


def test_pipeline_free_tier_ignores_pr_description(capsys):
    """Free-tier path never calls _inject_pr_context — simplified review unaffected (AC4)."""
    from revue_core.core.pr_description_adapter import PRDescription

    mock_client = MagicMock()
    mock_client.complete.return_value = _cr('{"status": "clean", "summary": "ok", "confidence": 1.0}')
    free_license = _license_info(
        tier="free",
        agents_allowed=["orchestrator", "code-quality-expert", "consolidator"],
    )
    pipeline = _pipeline(client=mock_client, **{k: v for k, v in {
        "valid": True, "tier": "free",
        "agents_allowed": ["orchestrator", "code-quality-expert", "consolidator"],
        "reviews_left": None, "expires_at": "2027-01-01T00:00:00Z",
        "key": "test-key",
    }.items()})
    # Override license
    pipeline._license_info = free_license

    pr = PRDescription(title="Free tier PR", raw_description="## Summary\nTest")

    with patch("revue_core.core.pipeline.parse_diff_file", return_value=[_fc("app.py")]), \
         patch("revue_core.core.pipeline.track_usage"), \
         patch("revue_core.core.pipeline._inject_pr_context") as mock_inject:
        pipeline.run("fake.diff", pr_description=pr)

    # Free tier runs _run_simplified — _inject_pr_context never called
    mock_inject.assert_not_called()


# ---------------------------------------------------------------------------
# REVUE-103: All-agents-failed raises AllAgentsFailedError (not SystemExit)
# ---------------------------------------------------------------------------

def test_pipeline_aborts_when_all_agents_fail(capsys):
    """TC3 (AC3): If ALL reviewer agents fail, pipeline raises AllAgentsFailedError."""
    from unittest.mock import patch, MagicMock
    from revue_core.core.pipeline import AllAgentsFailedError

    mock_client = MagicMock()
    # REVUE-241: reviewer agents now route through complete_with_tools when a
    # ReadFileTool is wired in. The mock must fail consistently on both methods
    # so the test simulates an API outage regardless of which path the agent takes.
    api_error = RuntimeError("Error code: 400 - credit balance too low")
    mock_client.complete.side_effect = api_error
    mock_client.complete_with_tools.side_effect = api_error

    pro_license = _license_info(
        tier="pro",
        agents_allowed=[
            "orchestrator", "code-quality-expert", "security-expert",
            "performance-expert", "architecture-expert", "consolidator",
            "sage", "cleo", "nova",
        ],
    )
    config = _config()
    pipeline = ReviewPipeline(config, client=mock_client, license_info=pro_license)

    with patch("revue_core.core.pipeline.parse_diff_file", return_value=[_fc("app.py")]), \
         patch("revue_core.core.pipeline.track_usage"):
        with pytest.raises(AllAgentsFailedError) as exc_info:
            pipeline.run("fake.diff")

    # first_error attribute carries the raw agent error (not in __str__ to avoid re-exposure)
    assert "credit balance too low" in exc_info.value.first_error
    assert "All agents failed" in str(exc_info.value)


def test_partial_failure_does_not_abort_agent_runner():
    """TC4 (AC4): Only SOME agents fail — run_agents_parallel returns partial results."""
    from revue_core.core.agent_runner import run_agents_parallel
    from revue_core.core.models import AIReview

    class GoodAgent:
        name = "good"
        def analyse(self, changes, shared=None):
            return [AIReview(file_path="a.py", line_number=1, severity="low",
                             issue="issue", suggestion="fix", confidence=0.8)]

    class BadAgent:
        name = "bad"
        def analyse(self, changes, shared=None):
            raise RuntimeError("api error")

    result = run_agents_parallel([GoodAgent(), BadAgent()], [_fc("app.py")], shared=None)
    successes = [r for r in result.agent_results if r.success]
    failures  = [r for r in result.agent_results if not r.success]

    assert len(successes) == 1
    assert len(failures) == 1
    assert successes[0].agent_name == "good"
    assert len(successes[0].findings) == 1
    assert failures[0].agent_name == "bad"


# ---------------------------------------------------------------------------
# max_parallel_agents wired from config to run_agents_parallel
# ---------------------------------------------------------------------------

def test_pipeline_passes_max_parallel_agents_to_runner(capsys):
    """Pipeline passes config.max_parallel_agents as max_workers to run_agents_parallel."""
    mock_client = MagicMock()
    mock_client.complete.return_value = _cr('{"status": "clean", "summary": "ok", "confidence": 1.0}')
    config = _config(max_parallel_agents=2)

    pro_license = _license_info(
        tier="pro",
        agents_allowed=["orchestrator", "code-quality-expert", "security-expert",
                        "performance-expert", "architecture-expert", "consolidator",
                        "sage", "cleo", "nova"],
    )
    pipeline = ReviewPipeline(config, client=mock_client, license_info=pro_license)

    from revue_core.core.agent_runner import AgentRunResult, ParallelRunResult

    with patch("revue_core.core.pipeline.parse_diff_file", return_value=[_fc("app.py")]), \
         patch("revue_core.core.pipeline.track_usage"), \
         patch("revue_core.core.agent_runner.run_agents_parallel") as mock_run:
        mock_run.return_value = ParallelRunResult(
            agent_results=[AgentRunResult(agent_name="maya", findings=[], elapsed_seconds=0.0)],
            total_elapsed=0.0,
        )
        pipeline.run("fake.diff")

    # run_agents_parallel must have been called with max_workers=2 and the configured timeout
    assert mock_run.called, "run_agents_parallel was not called (pipeline may have taken simplified path)"
    call_kwargs = mock_run.call_args[1]
    assert call_kwargs.get("max_workers") == 2
    assert call_kwargs.get("timeout_seconds") == config.agent_timeout_seconds


def test_pipeline_sequential_mode_logged(caplog):
    """When max_parallel_agents=1, pipeline logs 'sequentially' not 'in parallel'."""
    import logging
    mock_client = MagicMock()
    _stub_both_paths(mock_client)
    config = _config(max_parallel_agents=1)

    pro_license = _license_info(
        tier="pro",
        agents_allowed=["orchestrator", "code-quality-expert", "security-expert",
                        "performance-expert", "architecture-expert", "consolidator",
                        "sage", "cleo", "nova"],
    )
    pipeline = ReviewPipeline(config, client=mock_client, license_info=pro_license)

    with caplog.at_level(logging.INFO, logger="revue_core.core.pipeline"), \
         patch("revue_core.core.pipeline.parse_diff_file", return_value=[_fc("app.py")]), \
         patch("revue_core.core.pipeline.track_usage"):
        pipeline.run("fake.diff")

    assert "sequentially" in caplog.text


# ---------------------------------------------------------------------------
# REVUE-117: rate-limit fallback cascade — helper function unit tests
# ---------------------------------------------------------------------------

def test_is_rate_limit_error_detects_common_variants():
    """_is_rate_limit_error returns True for 429/rate_limit/rate limit patterns."""
    from revue_core.core.pipeline import _is_rate_limit_error
    assert _is_rate_limit_error("Error 429 Too Many Requests")
    assert _is_rate_limit_error("rate_limit_exceeded: quota depleted")
    assert _is_rate_limit_error("rate limit exceeded for model")
    assert not _is_rate_limit_error("Error 500 Internal Server Error")
    assert not _is_rate_limit_error("connection timeout")
    assert not _is_rate_limit_error("")


def test_build_agent_changes_normal_returns_all_files():
    """Normal mode returns all files unchanged regardless of assignments."""
    from revue_core.core.pipeline import _build_agent_changes
    changes = [_fc("a.py"), _fc("b.py")]
    result = _build_agent_changes("maya", "normal", changes, {"maya": ["a.py"]})
    assert result == changes


def test_build_agent_changes_empty_assignments_returns_all():
    """Empty file_assignments dict returns all files (safe default)."""
    from revue_core.core.pipeline import _build_agent_changes
    changes = [_fc("a.py"), _fc("b.py")]
    result = _build_agent_changes("maya", "file_assigned", changes, {})
    assert result == changes


def test_build_agent_changes_file_assigned_filters_to_assigned():
    """file_assigned mode returns only the agent's assigned files."""
    from revue_core.core.pipeline import _build_agent_changes
    changes = [_fc("a.py"), _fc("b.py"), _fc("c.py")]
    result = _build_agent_changes("maya", "file_assigned", changes, {"maya": ["a.py", "c.py"]})
    assert len(result) == 2
    assert {fc.file_path for fc in result} == {"a.py", "c.py"}


def test_build_agent_changes_agent_missing_from_map_returns_all():
    """Agent not in file_assignments map receives all files (AC2 safe default)."""
    from revue_core.core.pipeline import _build_agent_changes
    changes = [_fc("a.py"), _fc("b.py")]
    result = _build_agent_changes("maya", "file_assigned", changes, {"zara": ["b.py"]})
    assert result == changes


def test_build_agent_changes_context_lite_assigned_full_others_summarized():
    """context_lite mode: assigned files get full diff, non-assigned get a one-liner."""
    from revue_core.core.pipeline import _build_agent_changes
    changes = [_fc("a.py"), _fc("b.py"), _fc("c.py")]
    result = _build_agent_changes("maya", "context_lite", changes, {"maya": ["a.py"]})
    by_path = {fc.file_path: fc for fc in result}
    assert len(result) == 3
    assert "[context-lite]" not in by_path["a.py"].diff  # assigned: full diff
    assert "[context-lite]" in by_path["b.py"].diff       # non-assigned: summary
    assert "[context-lite]" in by_path["c.py"].diff       # non-assigned: summary


# ---------------------------------------------------------------------------
# REVUE-117: TC3-TC13 — fallback cascade integration tests
#
# These tests call pipeline._run_orchestration() directly and inject all
# orchestration dependencies via patch("revue_core.core.pipeline._import_orchestration").
# The real assign_files_to_agents round-robin is used (deterministic pure fn).
# ---------------------------------------------------------------------------

# Agents allowed list used by cascade tests — includes all 4 reviewer agents.
_CASCADE_AGENTS_ALLOWED = [
    "orchestrator", "code-quality-expert", "security-expert",
    "performance-expert", "architecture-expert",
    "consolidator", "sage", "cleo", "nova",
]


def _cascade_mock_agent(name: str) -> MagicMock:
    """Minimal mock LoadedAgent for cascade tests."""
    agent = MagicMock()
    agent.name = name
    agent._def = MagicMock()
    agent._def.role = "reviewer"
    agent._def.display_name = name
    return agent


def _ok_run(agent_name: str):
    """Return a successful ParallelRunResult for agent_name."""
    from revue_core.core.agent_runner import AgentRunResult, ParallelRunResult
    return ParallelRunResult(
        agent_results=[AgentRunResult(agent_name=agent_name, findings=[], elapsed_seconds=0.0)],
        total_elapsed=0.0,
    )


def _rl_run(agent_name: str):
    """Return a 429 rate-limit failure ParallelRunResult for agent_name."""
    from revue_core.core.agent_runner import AgentRunResult, ParallelRunResult
    return ParallelRunResult(
        agent_results=[AgentRunResult(
            agent_name=agent_name, findings=[], elapsed_seconds=0.0,
            error="Error 429 rate_limit_exceeded",
        )],
        total_elapsed=0.0,
    )


def _cascade_orch(agents: list, run_side_effect=None):
    """Build the _import_orchestration return value for cascade integration tests.

    Returns (OrchestrationModules, mock_run) so callers can inspect/modify mock_run.
    """
    from revue_core.core.agent_runner import ParallelRunResult
    from revue_core.core.shared_analysis import SharedAnalysisResult
    from revue_core.core.pipeline import OrchestrationModules

    shared = SharedAnalysisResult.fallback(languages=["py"])
    mock_run = MagicMock()
    if run_side_effect is not None:
        mock_run.side_effect = run_side_effect
    else:
        mock_run.side_effect = lambda ag, ch, sh, **kw: _ok_run(ag[0].name)

    orch = OrchestrationModules(
        load_all_agents=MagicMock(return_value=agents),
        run_agents_parallel=mock_run,
        run_shared_analysis=MagicMock(return_value=shared),
        route=MagicMock(return_value=(MagicMock(), agents)),
        format_selection_message=MagicMock(return_value=""),
        assign_files_to_agents=MagicMock(return_value={}),
        ParallelRunResult=ParallelRunResult,
    )
    return orch, mock_run


def _cascade_pipeline_obj(max_parallel: int = 1) -> ReviewPipeline:
    """Return a pro-tier ReviewPipeline configured for cascade integration tests."""
    config = _config(max_parallel_agents=max_parallel)
    return ReviewPipeline(
        config, client=MagicMock(),
        license_info=_license_info(
            tier="pro",
            agents_allowed=_CASCADE_AGENTS_ALLOWED,
        ),
    )


# TC3 -----------------------------------------------------------------------

def test_normal_mode_full_diff_sent():
    """TC3: In sequential mode with no rate limits, all agents receive the complete diff."""
    from revue_core.core.pipeline import _FB_NORMAL

    changes = [_fc("a.py"), _fc("b.py")]
    agents = [_cascade_mock_agent("maya"), _cascade_mock_agent("zara")]
    received = []

    def run_side(ag, ch, sh, **kw):
        received.append((ag[0].name, list(ch)))
        return _ok_run(ag[0].name)

    orch, _ = _cascade_orch(agents, run_side)
    pl = _cascade_pipeline_obj(max_parallel=1)

    with patch("revue_core.core.pipeline._import_orchestration", return_value=orch):
        pl._run_orchestration(changes, _CASCADE_AGENTS_ALLOWED)

    for name, chgs in received:
        assert len(chgs) == len(changes), (
            f"{name} received {len(chgs)} file(s), expected {len(changes)} in normal mode"
        )
    assert pl.last_fallback_mode == _FB_NORMAL


# TC4 -----------------------------------------------------------------------

def test_fallback1_triggered_on_rate_limit():
    """TC4: A 429 error on any agent escalates fallback_mode from normal to file_assigned."""
    from revue_core.core.pipeline import _FB_FILE_ASSIGNED

    changes = [_fc("a.py"), _fc("b.py")]
    agents = [_cascade_mock_agent("maya"), _cascade_mock_agent("zara")]
    call_n = [0]

    def run_side(ag, ch, sh, **kw):
        call_n[0] += 1
        return _rl_run(ag[0].name) if call_n[0] == 1 else _ok_run(ag[0].name)

    orch, _ = _cascade_orch(agents, run_side)
    pl = _cascade_pipeline_obj(max_parallel=1)

    with patch("revue_core.core.pipeline._import_orchestration", return_value=orch):
        pl._run_orchestration(changes, _CASCADE_AGENTS_ALLOWED)

    assert pl.last_fallback_mode == _FB_FILE_ASSIGNED


# TC5 -----------------------------------------------------------------------

def test_fallback1_failed_agent_retried_with_smaller_diff():
    """TC5: After a rate limit, the failed agent is retried with only its assigned files."""
    changes = [_fc("a.py"), _fc("b.py")]
    agents = [_cascade_mock_agent("maya"), _cascade_mock_agent("zara")]
    maya_calls = []

    def run_side(ag, ch, sh, **kw):
        if ag[0].name == "maya":
            maya_calls.append(list(ch))
            return _rl_run("maya") if len(maya_calls) == 1 else _ok_run("maya")
        return _ok_run(ag[0].name)

    orch, _ = _cascade_orch(agents, run_side)
    pl = _cascade_pipeline_obj(max_parallel=1)

    with patch("revue_core.core.pipeline._import_orchestration", return_value=orch):
        pl._run_orchestration(changes, _CASCADE_AGENTS_ALLOWED)

    # maya called twice: initial (full diff) + retry (assigned file only)
    assert len(maya_calls) == 2
    assert len(maya_calls[0]) == len(changes)       # first call: full diff
    assert len(maya_calls[1]) == 1                   # retry: assigned file only
    assert maya_calls[1][0].file_path == "a.py"      # round-robin: maya → a.py


# TC6 -----------------------------------------------------------------------

def test_fallback1_succeeded_agents_not_rerun():
    """TC6: Agents that succeeded before a fallback escalation are not re-run."""
    changes = [_fc("a.py"), _fc("b.py")]
    agents = [_cascade_mock_agent("maya"), _cascade_mock_agent("zara")]
    counts = {"maya": 0, "zara": 0}

    def run_side(ag, ch, sh, **kw):
        name = ag[0].name
        counts[name] += 1
        if name == "zara" and counts["zara"] == 1:
            return _rl_run("zara")  # zara's first attempt rate-limits
        return _ok_run(name)

    orch, _ = _cascade_orch(agents, run_side)
    pl = _cascade_pipeline_obj(max_parallel=1)

    with patch("revue_core.core.pipeline._import_orchestration", return_value=orch):
        pl._run_orchestration(changes, _CASCADE_AGENTS_ALLOWED)

    assert counts["maya"] == 1, "maya ran once and must not be re-run after zara's rate limit"
    assert counts["zara"] == 2, "zara retried after rate-limiting"


# TC7 -----------------------------------------------------------------------

def test_fallback2_context_lite_triggered():
    """TC7: If file_assigned also rate-limits, the cascade escalates to context_lite.

    Scenario: maya rate-limits once (normal → file_assigned), zara rate-limits once
    (starts in sticky file_assigned → escalates to context_lite).
    """
    from revue_core.core.pipeline import _FB_CONTEXT_LITE

    changes = [_fc("a.py"), _fc("b.py")]
    agents = [_cascade_mock_agent("maya"), _cascade_mock_agent("zara")]
    maya_calls = [0]
    zara_calls = [0]

    def run_side(ag, ch, sh, **kw):
        name = ag[0].name
        if name == "maya":
            maya_calls[0] += 1
            return _rl_run("maya") if maya_calls[0] == 1 else _ok_run("maya")
        elif name == "zara":
            zara_calls[0] += 1
            return _rl_run("zara") if zara_calls[0] == 1 else _ok_run("zara")
        return _ok_run(name)

    orch, _ = _cascade_orch(agents, run_side)
    pl = _cascade_pipeline_obj(max_parallel=1)

    with patch("revue_core.core.pipeline._import_orchestration", return_value=orch):
        pl._run_orchestration(changes, _CASCADE_AGENTS_ALLOWED)

    assert pl.last_fallback_mode == _FB_CONTEXT_LITE


# TC8 -----------------------------------------------------------------------

def test_fallback2_nonassigned_files_one_liner():
    """TC8: In context_lite mode, non-assigned files receive a one-line summary diff."""
    changes = [_fc("a.py"), _fc("b.py")]
    agents = [_cascade_mock_agent("maya"), _cascade_mock_agent("zara")]
    maya_calls = [0]
    zara_attempts = [0]
    zara_calls_ch = []

    def run_side(ag, ch, sh, **kw):
        name = ag[0].name
        if name == "maya":
            maya_calls[0] += 1
            return _rl_run("maya") if maya_calls[0] == 1 else _ok_run("maya")
        elif name == "zara":
            zara_attempts[0] += 1
            zara_calls_ch.append(list(ch))
            return _rl_run("zara") if zara_attempts[0] == 1 else _ok_run("zara")
        return _ok_run(name)

    orch, _ = _cascade_orch(agents, run_side)
    pl = _cascade_pipeline_obj(max_parallel=1)

    with patch("revue_core.core.pipeline._import_orchestration", return_value=orch):
        pl._run_orchestration(changes, _CASCADE_AGENTS_ALLOWED)

    # zara called twice: file_assigned (rate-limited) + context_lite (success)
    assert len(zara_calls_ch) == 2
    # Second call is context_lite: zara's assigned file (b.py) full, a.py summarized
    ctx_chgs = zara_calls_ch[1]
    by_path = {fc.file_path: fc for fc in ctx_chgs}
    assert len(ctx_chgs) == 2
    assert "[context-lite]" not in by_path["b.py"].diff  # assigned: full diff
    assert "[context-lite]" in by_path["a.py"].diff       # non-assigned: one-liner


# TC9 -----------------------------------------------------------------------

def test_context_lite_failure_surfaces_error():
    """TC9: If the context_lite retry also rate-limits, the failure is kept (not retried further)."""
    from revue_core.core.pipeline import _FB_CONTEXT_LITE

    changes = [_fc("a.py"), _fc("b.py")]
    agents = [_cascade_mock_agent("maya"), _cascade_mock_agent("zara")]
    maya_calls = [0]

    def run_side(ag, ch, sh, **kw):
        name = ag[0].name
        if name == "maya":
            maya_calls[0] += 1
            return _rl_run("maya") if maya_calls[0] == 1 else _ok_run("maya")
        # zara always rate-limits — both file_assigned attempt and context_lite retry
        return _rl_run("zara")

    orch, _ = _cascade_orch(agents, run_side)
    pl = _cascade_pipeline_obj(max_parallel=1)

    with patch("revue_core.core.pipeline._import_orchestration", return_value=orch):
        _, _, failed_agents = pl._run_orchestration(changes, _CASCADE_AGENTS_ALLOWED)

    assert pl.last_fallback_mode == _FB_CONTEXT_LITE
    assert "zara" in failed_agents  # failure surfaced, not swallowed


# TC10 ----------------------------------------------------------------------

def test_fallback_sticky():
    """TC10: Once fallback escalates to file_assigned, subsequent agents start there too."""
    changes = [_fc("a.py"), _fc("b.py"), _fc("c.py")]
    agents = [
        _cascade_mock_agent("maya"),
        _cascade_mock_agent("zara"),
        _cascade_mock_agent("leo"),
    ]
    first_call_sizes: dict[str, int] = {}
    maya_calls = [0]

    def run_side(ag, ch, sh, **kw):
        name = ag[0].name
        if name not in first_call_sizes:
            first_call_sizes[name] = len(list(ch))
        if name == "maya":
            maya_calls[0] += 1
            return _rl_run("maya") if maya_calls[0] == 1 else _ok_run("maya")
        return _ok_run(name)

    orch, _ = _cascade_orch(agents, run_side)
    pl = _cascade_pipeline_obj(max_parallel=1)

    with patch("revue_core.core.pipeline._import_orchestration", return_value=orch):
        pl._run_orchestration(changes, _CASCADE_AGENTS_ALLOWED)

    # maya's first call was in normal mode — received the full diff
    assert first_call_sizes["maya"] == len(changes)
    # zara and leo started in sticky file_assigned mode — received one file each
    assert first_call_sizes["zara"] < len(changes), "zara must start in file_assigned (sticky)"
    assert first_call_sizes["leo"] < len(changes), "leo must start in file_assigned (sticky)"


# TC11 ----------------------------------------------------------------------

def test_fallback_log_warning_emitted(caplog):
    """TC11: A warning is logged when the rate-limit cascade escalates."""
    import logging
    changes = [_fc("a.py"), _fc("b.py")]
    agents = [_cascade_mock_agent("maya"), _cascade_mock_agent("zara")]
    call_n = [0]

    def run_side(ag, ch, sh, **kw):
        call_n[0] += 1
        return _rl_run(ag[0].name) if call_n[0] == 1 else _ok_run(ag[0].name)

    orch, _ = _cascade_orch(agents, run_side)
    pl = _cascade_pipeline_obj(max_parallel=1)

    with caplog.at_level(logging.WARNING, logger="revue_core.core.pipeline"), \
         patch("revue_core.core.pipeline._import_orchestration", return_value=orch):
        pl._run_orchestration(changes, _CASCADE_AGENTS_ALLOWED)

    assert "⚠" in caplog.text
    assert "file-assigned" in caplog.text


# TC12 ----------------------------------------------------------------------

def test_summary_comment_includes_fallback_notice():
    """TC12: _build_enhanced_summary includes a degradation notice when fallback is active."""
    from revue_ci.cli import _build_enhanced_summary

    body = _build_enhanced_summary(
        review_results=[],
        total_findings={},
        revision=1,
        last_updated_at="just now",
        fallback_mode="file_assigned",
    )

    assert "Reduced context mode active" in body
    assert "file-assigned" in body


# TC13 ----------------------------------------------------------------------

def test_parallel_mode_bypasses_cascade():
    """TC13: With max_parallel > 1, run_agents_parallel is called once for all agents."""
    from revue_core.core.agent_runner import AgentRunResult, ParallelRunResult

    changes = [_fc("a.py"), _fc("b.py")]
    agents = [_cascade_mock_agent("maya"), _cascade_mock_agent("zara")]

    orch, mock_run = _cascade_orch(agents)
    mock_run.side_effect = None
    mock_run.return_value = ParallelRunResult(
        agent_results=[
            AgentRunResult(agent_name="maya", findings=[], elapsed_seconds=0.0),
            AgentRunResult(agent_name="zara", findings=[], elapsed_seconds=0.0),
        ],
        total_elapsed=0.0,
    )

    pl = _cascade_pipeline_obj(max_parallel=2)

    with patch("revue_core.core.pipeline._import_orchestration", return_value=orch):
        pl._run_orchestration(changes, _CASCADE_AGENTS_ALLOWED)

    assert mock_run.call_count == 1, "Parallel mode should call run_agents_parallel once"
    call_agents = mock_run.call_args[0][0]
    assert len(call_agents) == len(agents), "All agents passed in a single parallel call"
    assert mock_run.call_args[1].get("max_workers") == 2


# ---------------------------------------------------------------------------
# REVUE-112 Phase 2: classify/respond pipeline ordering (TC23–TC26)
# ---------------------------------------------------------------------------

def _bitbucket_pr_context():
    """Minimal PRContext fixture for Bitbucket reply-tracking tests."""
    from revue_core.core.models import PRContext
    return PRContext(
        platform="bitbucket",
        pr_number=42,
        repo_owner="ws",
        repo_name="repo",
        repo_path="/tmp",
    )


def _empty_classification():
    """Empty ClassificationResult — no patterns, no state updates."""
    from revue_core.core.models import ClassificationResult
    return ClassificationResult([], [], [], [])


def _mock_strategy(svc):
    """Return a mock ReplyTrackingStrategy that always returns *svc*."""
    strategy = MagicMock()
    strategy.build_wont_fix_svc.return_value = svc
    return strategy


def test_pipeline_classify_runs_before_agents(tmp_path):
    """TC23: classify() is called before _run_orchestration / _run_simplified."""
    call_order: list[str] = []

    mock_svc = MagicMock()
    mock_svc.classify.return_value = _empty_classification()
    mock_svc.respond.return_value = None
    mock_svc.classify.side_effect = lambda n: (call_order.append("classify"), _empty_classification())[1]

    pipeline = _pipeline()

    with patch("revue_core.core.pipeline.parse_diff_file", return_value=[_fc("a.py")]), \
         patch("revue_core.core.pipeline.track_usage"), \
         patch("revue_core.core.reply_tracking.get_strategy", return_value=_mock_strategy(mock_svc)), \
         patch.object(pipeline, "_run_simplified",
                      side_effect=lambda *a, **kw: (call_order.append("agents"), ([], [], []))[1]):
        pipeline.run("fake.diff", pr_context=_bitbucket_pr_context())

    assert "classify" in call_order
    assert "agents" in call_order
    assert call_order.index("classify") < call_order.index("agents"), (
        "classify must be called before agents"
    )


def test_pipeline_config_patched_after_classify(tmp_path):
    """TC24: pipeline.config.allowed_patterns includes classify() output before agents run."""
    from revue_core.core.models import ClassificationResult

    captured_config_state: list[list] = []

    new_pattern = {"pattern": "legacy bypass", "rationale": "intentional"}
    classification = ClassificationResult(
        patterns_to_allow=[new_pattern],
        patterns_to_disallow=[],
        state_updates=[],
        decisions=[],
    )

    mock_svc = MagicMock()
    mock_svc.classify.return_value = classification
    mock_svc.respond.return_value = None

    pipeline = _pipeline()

    def capture_and_run(*args, **kwargs):
        # Capture config.allowed_patterns at the moment agents run
        captured_config_state.append(
            list(getattr(pipeline.config, "allowed_patterns", None) or [])
        )
        return [], [], []

    with patch("revue_core.core.pipeline.parse_diff_file", return_value=[_fc("a.py")]), \
         patch("revue_core.core.pipeline.track_usage"), \
         patch("revue_core.core.reply_tracking.get_strategy", return_value=_mock_strategy(mock_svc)), \
         patch.object(pipeline, "_run_simplified", side_effect=capture_and_run):
        pipeline.run("fake.diff", pr_context=_bitbucket_pr_context())

    assert len(captured_config_state) == 1
    assert new_pattern in captured_config_state[0], (
        "allowed_patterns must include classify() output before agents run"
    )


def test_pipeline_state_updates_applied_before_diff_parse(tmp_path):
    """TC25: apply_state_updates called before parse_diff_file for state_updates."""
    from revue_core.core.models import ClassificationResult

    call_order: list[str] = []

    classification = ClassificationResult(
        patterns_to_allow=[],
        patterns_to_disallow=[],
        state_updates=[{"fingerprint": "fp001", "file_path": "a.py", "decision": "allowed_pattern"}],
        decisions=[],
    )

    mock_svc = MagicMock()
    mock_svc.classify.return_value = classification
    mock_svc.respond.return_value = None
    mock_svc.apply_state_updates.side_effect = lambda *a, **kw: call_order.append("mark_resolved")

    pipeline = _pipeline()

    with patch("revue_core.core.pipeline.parse_diff_file",
               side_effect=lambda *a, **kw: (call_order.append("parse_diff"), [_fc("a.py")])[1]), \
         patch("revue_core.core.pipeline.track_usage"), \
         patch("revue_core.core.reply_tracking.get_strategy", return_value=_mock_strategy(mock_svc)):
        pipeline.run("fake.diff", pr_context=_bitbucket_pr_context())

    assert "mark_resolved" in call_order, "mark_resolved must be called for state_updates"
    assert "parse_diff" in call_order
    assert call_order.index("mark_resolved") < call_order.index("parse_diff"), (
        "state_updates must be applied before diff parsing"
    )


def test_pipeline_respond_runs_after_agents(tmp_path):
    """TC26: respond() is called after agents run when threads with replies exist."""
    from revue_core.core.models import ClassificationResult

    call_order: list[str] = []

    # Non-empty classification so the respond branch is taken
    classification_with_threads = ClassificationResult(
        patterns_to_allow=[],
        patterns_to_disallow=[],
        state_updates=[],
        decisions=[{"fingerprint": "fp1", "decision": "reason_missing", "reply_draft": "Why?"}],
    )

    mock_svc = MagicMock()
    mock_svc.classify.return_value = classification_with_threads
    mock_svc.respond.side_effect = lambda *a, **kw: call_order.append("respond")

    pipeline = _pipeline()

    with patch("revue_core.core.pipeline.parse_diff_file", return_value=[_fc("a.py")]), \
         patch("revue_core.core.pipeline.track_usage"), \
         patch("revue_core.core.reply_tracking.get_strategy", return_value=_mock_strategy(mock_svc)), \
         patch.object(pipeline, "_run_simplified",
                      side_effect=lambda *a, **kw: (call_order.append("agents"), ([], [], []))[1]):
        pipeline.run("fake.diff", pr_context=_bitbucket_pr_context())

    assert "respond" in call_order, "respond() must be called"
    assert "agents" in call_order
    assert call_order.index("agents") < call_order.index("respond"), (
        "respond must run after agents"
    )


# ---------------------------------------------------------------------------
# REVUE-134: reviewed-files dedup + show_reviewed_files flag (TC14–TC16)
# ---------------------------------------------------------------------------

def test_build_enhanced_summary_deduplicates_reviewed_files():
    """TC14: files reviewed section deduplicates by file_path."""
    from revue_ci.cli import _build_enhanced_summary

    rr_cls = ReviewResult
    review_results = [
        rr_cls(file_path="service.py", response="ok", error=None),
        rr_cls(file_path="service.py", response="ok", error=None),
        rr_cls(file_path="service.py", response="ok", error=None),
        rr_cls(file_path="pipeline.py", response="ok", error=None),
    ]
    body = _build_enhanced_summary(
        review_results=review_results,
        total_findings={},
        revision=1,
        last_updated_at="just now",
    )
    assert "Files Reviewed (2)" in body, "Count should reflect 2 unique files"
    assert body.count("service.py") == 1, "service.py should appear exactly once"


def test_build_enhanced_summary_flag_disabled_hides_section():
    """TC15: show_reviewed_files=False suppresses the Files Reviewed section even with results."""
    from revue_ci.cli import _build_enhanced_summary

    rr = ReviewResult(file_path="auth.py", response="ok", error=None)
    body = _build_enhanced_summary(
        review_results=[rr],
        total_findings={},
        revision=1,
        last_updated_at="just now",
        show_reviewed_files=False,
    )
    assert "### Files Reviewed" not in body
    assert "auth.py" not in body


def test_build_enhanced_summary_flag_default_shows_section():
    """TC16: show_reviewed_files defaults to True — section is present."""
    from revue_ci.cli import _build_enhanced_summary

    body = _build_enhanced_summary(
        review_results=[],
        total_findings={},
        revision=1,
        last_updated_at="just now",
    )
    assert "### Files Reviewed" in body


def test_build_enhanced_summary_unknown_category_falls_back_to_code_quality():
    """B4 regression: findings with unrecognised category must appear under Code Quality."""
    import json
    from revue_ci.cli import _build_enhanced_summary

    response_json = json.dumps({
        "findings": [
            {
                "category": "unexpected-garbage",
                "severity": "low",
                "issue": "Some unexpected issue",
                "suggestion": "Fix it",
                "line": 1,
            }
        ],
        "summary": "one finding",
    })
    rr = ReviewResult(file_path="app.py", response=response_json, error=None)
    body = _build_enhanced_summary(
        review_results=[rr],
        total_findings={"low": 1},
        revision=1,
        last_updated_at="just now",
    )
    # The finding must appear under Code Quality, not be silently dropped
    assert "Code Quality" in body
    # Code Quality row must show a finding (warning icon), not the clean-slate label
    lines = body.splitlines()
    cq_line = next((l for l in lines if "Code Quality" in l), None)
    assert cq_line is not None
    assert "⚠️" in cq_line, (
        "Code Quality row must show a warning when the unknown-category finding is present"
    )


def test_build_enhanced_summary_previously_tracked_adjusts_findings_section():
    """When previously_tracked>0 the Findings section shows active count and a
    '(N previously tracked)' note, rather than claiming all issues require attention.

    Regression: resolved won't-fix threads were still counted in 'requires attention'
    even though they were already decided.
    """
    import json
    from revue_ci.cli import _build_enhanced_summary

    body = _build_enhanced_summary(
        review_results=[],
        total_findings={"medium": 6, "low": 0, "high": 0, "info": 0},
        revision=3,
        last_updated_at="just now",
        previously_tracked=3,
    )
    # Should show the active count (6), not inflated total (9)
    assert "6 issue" in body
    # Must mention the previously tracked count so the user understands the discrepancy
    assert "previously tracked" in body


def test_build_enhanced_summary_zero_previously_tracked_unchanged():
    """When previously_tracked=0 the output is identical to not passing the argument."""
    import json
    from revue_ci.cli import _build_enhanced_summary

    body_default = _build_enhanced_summary(
        review_results=[],
        total_findings={"medium": 3},
        revision=1,
        last_updated_at="just now",
    )
    body_zero = _build_enhanced_summary(
        review_results=[],
        total_findings={"medium": 3},
        revision=1,
        last_updated_at="just now",
        previously_tracked=0,
    )
    assert body_default == body_zero


def test_apply_sentinel_strategy_stores_resolved_flag():
    """_apply_sentinel_strategy should store resolved state from the comment dict.

    Regression: fingerprint map dropped resolved state, so resolved-thread
    findings were counted as 'requiring attention' in the summary.
    """
    from revue_ci.cli import _apply_sentinel_strategy

    body = "[//]: # (revue:fp:abc123) some finding text"
    result: dict = {}
    _apply_sentinel_strategy(body, "42", result, resolved=True)

    assert "abc123" in result
    assert result["abc123"]["resolved"] is True


def test_apply_sentinel_strategy_resolved_false_by_default():
    """When resolved is False (default), the stored entry has resolved=False."""
    from revue_ci.cli import _apply_sentinel_strategy

    body = "[//]: # (revue:fp:def456) another finding"
    result: dict = {}
    _apply_sentinel_strategy(body, "99", result, resolved=False)

    assert "def456" in result
    assert result["def456"]["resolved"] is False


def test_apply_location_strategy_uses_gitlab_position() -> None:
    """_apply_location_strategy must recognise GitLab 'position' as well as
    Bitbucket 'inline', so pre-sentinel comments on GitLab are fingerprinted
    and AC5 can auto-resolve them.

    Regression: location strategy only read c.get('inline'), which is
    Bitbucket-specific. GitLab notes use c.get('position') with new_path /
    new_line. Ghost threads from pre-sentinel runs were invisible to merged_prior
    → AC5 never fired → open threads persisted unresolved.
    """
    from revue_ci.cli import _apply_location_strategy
    from revue_core.comments.fingerprint import fingerprint as gen_fp

    gitlab_note = {
        "id": 99,
        "body": "**🔴 [HIGH] Missing validation**\ndetails here",
        "_discussion_resolved": False,
        # GitLab inline comment has 'position', not 'inline'
        "position": {
            "new_path": "src/app.py",
            "new_line": 42,
        },
    }
    result: dict = {}
    _apply_location_strategy(gitlab_note, gitlab_note["body"], "disc-99", result, gen_fp)

    expected_fp = gen_fp("src/app.py", 42, "")
    assert expected_fp in result, "GitLab position must produce a location fingerprint"
    assert result[expected_fp]["file_path"] == "src/app.py"
    assert result[expected_fp]["platform_comment_id"] == "disc-99"
    assert result[expected_fp]["severity"] == "high"


def test_apply_location_strategy_bitbucket_inline_still_works() -> None:
    """Ensure the GitLab position fix does not break Bitbucket 'inline' support."""
    from revue_ci.cli import _apply_location_strategy
    from revue_core.comments.fingerprint import fingerprint as gen_fp

    bitbucket_note = {
        "id": 55,
        "body": "**🟡 [MEDIUM] Unused import**\ndetails",
        "_discussion_resolved": False,
        "inline": {"path": "lib/util.py", "to": 7},
    }
    result: dict = {}
    _apply_location_strategy(bitbucket_note, bitbucket_note["body"], "55", result, gen_fp)

    expected_fp = gen_fp("lib/util.py", 7, "")
    assert expected_fp in result
    assert result[expected_fp]["file_path"] == "lib/util.py"
    assert result[expected_fp]["severity"] == "medium"


def test_build_api_fingerprint_map_uses_discussion_id_as_platform_id() -> None:
    """_build_api_fingerprint_map must store _discussion_id as platform_comment_id.

    GitLab's resolve_inline_comment endpoint requires the discussion ID, not the
    note ID. If the fingerprint map stores note IDs, AC5 fails silently with 404.
    """
    from revue_ci.cli import _build_api_fingerprint_map
    from revue_core.comments.fingerprint import fingerprint as gen_fp

    fp_hash = gen_fp("app.py", 10, "")
    mock_adapter = MagicMock()
    mock_adapter.get_existing_comments.return_value = [
        {
            "id": 9999,               # note ID — must NOT be stored
            "_discussion_id": "disc-aaa",  # discussion ID — MUST be stored
            "_discussion_resolved": False,
            "body": f"**🔴 [HIGH] Some issue**\n\n[//]: # (revue:fp:{fp_hash})",
        }
    ]

    result = _build_api_fingerprint_map(mock_adapter, 1)

    assert fp_hash in result
    assert result[fp_hash]["platform_comment_id"] == "disc-aaa", (
        "platform_comment_id must be discussion ID for AC5 resolve_inline_comment to work"
    )


def test_apply_location_strategy_github_flat_structure() -> None:
    """_apply_location_strategy must handle GitHub's flat comment structure.

    GitHub review comments have top-level 'path' and 'line' fields.
    They also have 'position' as an INTEGER (diff position), not a dict —
    calling .get() on it would raise AttributeError.

    Regression guard: the GitLab 'position' dict fix must not break GitHub
    by treating integer position as a dict.
    """
    from revue_ci.cli import _apply_location_strategy
    from revue_core.comments.fingerprint import fingerprint as gen_fp

    github_comment = {
        "id": 777,
        "body": "**🟡 [MEDIUM] Missing type hints**\ndetails",
        "_discussion_resolved": False,
        # GitHub: integer diff position (NOT a dict), plus flat path/line
        "position": 5,
        "path": "src/utils.py",
        "line": 20,
    }
    result: dict = {}
    _apply_location_strategy(github_comment, github_comment["body"], "777", result, gen_fp)

    expected_fp = gen_fp("src/utils.py", 20, "")
    assert expected_fp in result, "GitHub flat path/line must produce a location fingerprint"
    assert result[expected_fp]["file_path"] == "src/utils.py"
    assert result[expected_fp]["severity"] == "medium"


def test_apply_location_strategy_github_integer_position_no_crash() -> None:
    """GitHub's integer 'position' field must not raise AttributeError.

    Verifies the isinstance(position, dict) guard prevents .get() being called
    on an integer — even for a comment that doesn't pass the FINDING_HEADER_RE
    guard, the position extraction code path must not crash.
    """
    from revue_ci.cli import _apply_location_strategy
    from revue_core.comments.fingerprint import fingerprint as gen_fp

    github_non_revue_comment = {
        "id": 888,
        "body": "Some regular review comment (not a Revue finding)",
        "position": 3,   # integer — must NOT be called with .get()
        "path": "file.py",
        "line": 10,
    }
    result: dict = {}
    # Must not raise AttributeError even though position is an int
    _apply_location_strategy(
        github_non_revue_comment, github_non_revue_comment["body"], "888", result, gen_fp
    )
    # Non-Revue comment filtered by FINDING_HEADER_RE → no entry
    assert result == {}


# ---------------------------------------------------------------------------
# REVUE-154: ReviewPipeline metrics integration
# ---------------------------------------------------------------------------


def test_pipeline_metrics_disabled_by_default() -> None:
    """Without REVUE_METRICS_ENABLED, pipeline uses NullMetricsCollector."""
    import os
    from unittest.mock import patch

    # Ensure env var not set
    with patch.dict(os.environ, {}, clear=False):
        if "REVUE_METRICS_ENABLED" in os.environ:
            del os.environ["REVUE_METRICS_ENABLED"]

        config = _config()
        pipeline = ReviewPipeline(config, license_info=_license_info())

        # Verify null collector is used
        from revue_core.core.metrics import NullMetricsCollector
        assert isinstance(pipeline._metrics, NullMetricsCollector)


def test_pipeline_metrics_enabled_creates_jsonl_collector() -> None:
    """With REVUE_METRICS_ENABLED, pipeline uses JsonlMetricsCollector."""
    import os
    from unittest.mock import patch

    with patch.dict(os.environ, {"REVUE_METRICS_ENABLED": "1"}):
        config = _config()
        pipeline = ReviewPipeline(config, license_info=_license_info())

        # Verify JSON collector is used
        from revue_core.infrastructure.metrics_writer import JsonlMetricsCollector
        assert isinstance(pipeline._metrics, JsonlMetricsCollector)


def test_infrastructure_only_routing_guarantee_rule_prevents_zero_reviewers():
    """AC1 (REVUE-166): route() guarantees ≥1 non-infra agent for YAML/docs diffs.

    Previously, infrastructure-only routing was handled by a pipeline fallback.
    Now, the guarantee rule in route() ensures ≥1 non-infrastructure reviewer is
    always present in the routed agents, eliminating the need for the fallback.
    """
    from revue_core.core.cleo_router import route, _INFRASTRUCTURE_AGENTS
    from tests.core.test_cleo_router import _FakeAgent, _fc

    # Set up agents: YAML file triggers cleo and nova only (infrastructure-only)
    agents = [
        _FakeAgent("cleo", ["**"]),
        _FakeAgent("maya", ["**"]),  # generalist with broad triggers
        _FakeAgent("leo", ["**/*.js"]),  # language-specific, doesn't match YAML
        _FakeAgent("nova", ["**"]),
    ]

    # YAML file: would trigger only cleo and nova without guarantee rule
    files = [_fc("bitbucket-pipelines.yml")]

    # Route through the real route() function (exercises AC1 guarantee rule)
    selection, filtered = route(files, agents)

    filtered_names = {a.name for a in filtered}
    non_infra = {n for n in filtered_names if n not in _INFRASTRUCTURE_AGENTS}

    # AC1: guarantee rule ensures ≥1 non-infrastructure agent
    assert len(non_infra) >= 1, (
        f"AC1 guarantee rule must prevent zero reviewers for YAML diffs. "
        f"Got filtered={filtered_names}"
    )


def test_pipeline_failure_produces_no_metrics_artefact() -> None:
    """AC6: AllAgentsFailedError raised before flush() — no metrics.jsonl created."""
    import tempfile
    from unittest.mock import MagicMock, patch
    from pathlib import Path
    from revue_core.core.pipeline import AllAgentsFailedError
    from revue_core.infrastructure.metrics_writer import JsonlMetricsCollector

    mock_client = MagicMock()
    # REVUE-241: reviewer agents may route through complete_with_tools — mock both.
    api_error = RuntimeError("api error")
    mock_client.complete.side_effect = api_error
    mock_client.complete_with_tools.side_effect = api_error

    pro_license = _license_info(
        tier="pro",
        agents_allowed=[
            "orchestrator", "code-quality-expert", "security-expert",
            "performance-expert", "architecture-expert", "consolidator",
            "sage", "cleo", "nova",
        ],
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        collector = JsonlMetricsCollector(base_dir=tmpdir)
        config = _config()
        pipeline = ReviewPipeline(
            config,
            client=mock_client,
            license_info=pro_license,
            metrics=collector,
        )

        with patch("revue_core.core.pipeline.parse_diff_file", return_value=[_fc("app.py")]), \
             patch("revue_core.core.pipeline.track_usage"):
            with pytest.raises(AllAgentsFailedError):
                pipeline.run("fake.diff")

        # flush() was never reached — no artefact
        assert not (Path(tmpdir) / ".revue" / "metrics.jsonl").exists()


# ---------------------------------------------------------------------------
# REVUE-170 AC5 — pipeline→metrics end-to-end wiring
# ---------------------------------------------------------------------------

def test_p4_pipeline_calls_record_routing_after_orchestration():
    """AC5 wiring: pipeline.py calls self._metrics.record_routing() after route()
    succeeds. CapturingMetricsCollector.routing_events must be non-empty.
    CLAUDE.md requires caller-wiring tests, not just writer-unit tests."""
    from revue_core.core.metrics import CapturingMetricsCollector

    mock_client = MagicMock()
    _stub_both_paths(mock_client)
    capturing = CapturingMetricsCollector()

    pro_license = _license_info(
        tier="pro",
        agents_allowed=[
            "orchestrator", "code-quality-expert", "security-expert",
            "performance-expert", "architecture-expert", "consolidator",
            "sage", "cleo", "nova",
        ],
    )
    pipeline = ReviewPipeline(
        _config(), client=mock_client, license_info=pro_license, metrics=capturing,
    )

    with patch("revue_core.core.pipeline.parse_diff_file", return_value=[_fc("app.py")]), \
         patch("revue_core.core.pipeline.track_usage"):
        pipeline.run("fake.diff")

    assert len(capturing.routing_events) == 1, (
        f"Expected 1 routing event recorded by pipeline; got {len(capturing.routing_events)}"
    )
    event = capturing.routing_events[0]
    assert isinstance(event.ai_suggested_agents, list)
    assert isinstance(event.algorithm_selected_agents, list)
    assert isinstance(event.final_agents, list)
    assert event.routing_source in ("ai_assisted", "algorithm_fallback")
    assert event.model_used


# ---------------------------------------------------------------------------
# REVUE-179 AC4 — pipeline→metrics synthesis wiring
# ---------------------------------------------------------------------------

def test_p4_pipeline_calls_record_synthesis_after_consolidation():
    """AC4 wiring: pipeline.py calls self._metrics.record_synthesis() after consolidate().
    CapturingMetricsCollector.synthesis_events must be non-empty.
    CLAUDE.md requires caller-wiring tests, not just writer-unit tests."""
    from revue_core.core.metrics import CapturingMetricsCollector

    mock_client = MagicMock()
    _stub_both_paths(mock_client)
    capturing = CapturingMetricsCollector()

    pro_license = _license_info(
        tier="pro",
        agents_allowed=[
            "orchestrator", "code-quality-expert", "security-expert",
            "performance-expert", "architecture-expert", "consolidator",
            "sage", "cleo", "nova",
        ],
    )
    pipeline = ReviewPipeline(
        _config(), client=mock_client, license_info=pro_license, metrics=capturing,
    )

    with patch("revue_core.core.pipeline.parse_diff_file", return_value=[_fc("app.py")]), \
         patch("revue_core.core.pipeline.track_usage"):
        pipeline.run("fake.diff")

    assert len(capturing.synthesis_events) == 1, (
        f"Expected 1 synthesis event recorded by pipeline; got {len(capturing.synthesis_events)}"
    )
    event = capturing.synthesis_events[0]
    assert isinstance(event.total_findings, int)
    assert isinstance(event.synthesised_count, int)
    assert isinstance(event.synthesis_events, list)
    assert event.synthesised_count >= 0
    assert event.total_findings >= 0


# ---------------------------------------------------------------------------
# Synthesis-client resolution — REVUE-236 follow-up (Option 3)
# ---------------------------------------------------------------------------


def test_resolve_synthesis_client_returns_main_client_when_synthesis_model_empty():
    """Empty synthesis_model means Nova reuses the reviewer client — no separate client built."""
    # Arrange
    from revue_core.core.pipeline import resolve_synthesis_client

    main_client = MagicMock(name="main_client")
    config = _config(model="claude-haiku-4-5-20251001", synthesis_model="")

    # Act
    result = resolve_synthesis_client(config, main_client=main_client)

    # Assert
    assert result is main_client


def test_resolve_synthesis_client_returns_main_client_when_synthesis_model_equals_main_model():
    """If synthesis_model matches main model, building a separate client is wasteful — reuse."""
    # Arrange
    from revue_core.core.pipeline import resolve_synthesis_client

    main_client = MagicMock(name="main_client")
    same_model = "claude-sonnet-4-6"
    config = _config(model=same_model, synthesis_model=same_model)

    # Act
    result = resolve_synthesis_client(config, main_client=main_client)

    # Assert
    assert result is main_client


def test_resolve_synthesis_client_builds_separate_client_with_synthesis_model_when_set():
    """When synthesis_model differs from main model, a separate client is built using the override."""
    # Arrange
    from revue_core.core.pipeline import resolve_synthesis_client

    main_client = MagicMock(name="main_client")
    synthesis_client = MagicMock(name="synthesis_client_built_for_sonnet")
    captured_configs: list[AIConfig] = []

    def fake_create_ai_client(cfg: AIConfig, metrics=None):
        captured_configs.append(cfg)
        return synthesis_client

    config = _config(
        model="claude-haiku-4-5-20251001",
        synthesis_model="claude-sonnet-4-6",
    )

    # Act
    with patch("revue_core.core.pipeline.create_ai_client", side_effect=fake_create_ai_client):
        result = resolve_synthesis_client(config, main_client=main_client)

    # Assert — separate client was built, with the synthesis model in its config
    assert result is synthesis_client
    assert result is not main_client
    assert len(captured_configs) == 1
    assert captured_configs[0].model == "claude-sonnet-4-6"
    # Other config fields preserved (provider, api_key, etc.)
    assert captured_configs[0].provider == "anthropic"


def test_pipeline_constructor_accepts_injected_synthesis_client_for_testability():
    """Tests need to inject a synthesis client without going through create_ai_client."""
    # Arrange
    main_client = MagicMock(name="main_client")
    main_client.complete.return_value = _cr("ok")
    synthesis_client = MagicMock(name="synthesis_client")
    config = _config(synthesis_model="claude-sonnet-4-6")

    # Act
    pipeline = ReviewPipeline(
        config,
        client=main_client,
        synthesis_client=synthesis_client,
        license_info=_license_info(),
    )

    # Assert — exposed via a public attribute named for the synthesis client
    assert pipeline.synthesis_client is synthesis_client
    assert pipeline.synthesis_client is not main_client


# ---------------------------------------------------------------------------
# Reasoning-tier invariant — REVUE-240
# ---------------------------------------------------------------------------


def test_vex_and_nova_share_the_reasoning_tier_client():
    """Vex (verification) and Nova (synthesis) MUST share the same AI client.

    Both are reasoning-tier agents; divergence breaks coherence (REVUE-240).
    Identity check, not equality — guards against future refactors that
    accidentally split the wire into two separately-built clients.
    """
    # Arrange
    from pathlib import Path

    main_client = MagicMock(name="main_client")
    main_client.complete.return_value = _cr("ok")
    synthesis_client = MagicMock(name="synthesis_client")
    pipeline = ReviewPipeline(
        _config(),
        client=main_client,
        synthesis_client=synthesis_client,
        license_info=_license_info(),
    )

    # Act
    consolidator, vex_post_processor, _orphan_guard = pipeline._build_consolidator(
        nova_system_prompt=None,
        vex_system_prompt=None,
        diff_by_file={},
        repo_root=Path("."),
        summary_sink=[],
    )

    # Assert — identity, not equality
    nova_client = consolidator._synthesis._client
    vex_client = vex_post_processor._verifier._client
    assert vex_client is nova_client
    assert vex_client is synthesis_client


def test_orphan_line_guard_runs_after_vex_in_post_processor_chain():
    """REVUE-249 AC11 — OrphanLineGuardPostProcessor must be wired AFTER
    VexVerifyPostProcessor in the consolidator's post-processor chain.

    Order matters: the guard inspects the *result* of Vex's verdict (only
    findings that survived Vex's ``apply`` verdict still carry a multi-line
    ``code_replacement``). Wiring it before Vex would gate the wrong verdict.
    """
    # Arrange
    from pathlib import Path

    from revue_core.comments._orphan_line_guard import OrphanLineGuardPostProcessor
    from revue_core.comments._verifier import VexVerifyPostProcessor

    main_client = MagicMock(name="main_client")
    main_client.complete.return_value = _cr("ok")
    synthesis_client = MagicMock(name="synthesis_client")
    pipeline = ReviewPipeline(
        _config(),
        client=main_client,
        synthesis_client=synthesis_client,
        license_info=_license_info(),
    )

    # Act
    consolidator, vex_post_processor, orphan_guard = pipeline._build_consolidator(
        nova_system_prompt=None,
        vex_system_prompt=None,
        diff_by_file={"src/example.py": "@@ -0,0 +1,1 @@\n+x = 1\n"},
        repo_root=Path("."),
        summary_sink=[],
    )

    # Assert — both processors are present, in the right relative order.
    chain = consolidator._post_processors
    vex_index = next(
        (i for i, p in enumerate(chain) if isinstance(p, VexVerifyPostProcessor)),
        None,
    )
    guard_index = next(
        (i for i, p in enumerate(chain) if isinstance(p, OrphanLineGuardPostProcessor)),
        None,
    )
    assert vex_index is not None, "VexVerifyPostProcessor missing from chain"
    assert guard_index is not None, "OrphanLineGuardPostProcessor missing from chain"
    assert guard_index > vex_index, (
        f"OrphanLineGuardPostProcessor must run AFTER VexVerifyPostProcessor; "
        f"got guard_index={guard_index} vex_index={vex_index}"
    )
    # Returned reference matches the one installed in the chain.
    assert chain[guard_index] is orphan_guard


# ---------------------------------------------------------------------------
# REVUE-249 — summary log + metrics persistence (AC12, AC13, AC25)
# ---------------------------------------------------------------------------


class _StubVex:
    """Minimal stand-in for VexVerifyPostProcessor — only the counters
    properties accessed by _log_vex_summary need to exist.
    """

    def __init__(
        self,
        *,
        verdict_counts: dict[str, int] | None = None,
        failure_counts: dict[str, int] | None = None,
    ) -> None:
        self._verdict = verdict_counts or {}
        self._failure = failure_counts or {}

    @property
    def verdict_counts(self) -> dict[str, int]:
        return dict(self._verdict)

    @property
    def failure_counts(self) -> dict[str, int]:
        return dict(self._failure)


class _StubGuard:
    """Minimal stand-in for OrphanLineGuardPostProcessor."""

    def __init__(self, *, guard_downgrade: int = 0) -> None:
        self._guard_downgrade = guard_downgrade

    @property
    def guard_downgrade(self) -> int:
        return self._guard_downgrade


def _pipeline_for_summary_log() -> ReviewPipeline:
    main_client = MagicMock(name="main_client")
    main_client.complete.return_value = _cr("ok")
    synthesis_client = MagicMock(name="synthesis_client")
    return ReviewPipeline(
        _config(),
        client=main_client,
        synthesis_client=synthesis_client,
        license_info=_license_info(),
    )


def test_vex_summary_log_includes_orphan_guard_field(caplog) -> None:
    """AC12 — the [revue] Vex: ... summary log line includes ``orphan_guard=N``
    so dogfood log greps see how often the deterministic guard fired.
    """
    # Arrange
    pipeline = _pipeline_for_summary_log()
    vex = _StubVex(
        verdict_counts={"apply": 3, "drop_cr_keep_prose": 1, "reject_finding": 0},
        failure_counts={},
    )
    guard = _StubGuard(guard_downgrade=2)
    caplog.set_level("INFO", logger="revue_core.core.pipeline")

    # Act
    pipeline._log_vex_summary(vex, guard)

    # Assert
    summary_lines = [r.getMessage() for r in caplog.records if "Vex:" in r.getMessage()]
    assert len(summary_lines) == 1, summary_lines
    assert "orphan_guard=2" in summary_lines[0]
    assert "apply=3" in summary_lines[0]


def test_vex_summary_log_fires_when_only_orphan_guard_fired(caplog) -> None:
    """AC25 — the summary log fires even when Vex itself is idle, provided the
    deterministic guard registered a downgrade. The pre-REVUE-249 gate only
    looked at Vex's counters; the wider gate is required so a guard-only run
    is still visible.
    """
    # Arrange
    pipeline = _pipeline_for_summary_log()
    vex = _StubVex(verdict_counts={}, failure_counts={})  # all zero
    guard = _StubGuard(guard_downgrade=1)
    caplog.set_level("INFO", logger="revue_core.core.pipeline")

    # Act
    pipeline._log_vex_summary(vex, guard)

    # Assert
    summary_lines = [r.getMessage() for r in caplog.records if "Vex:" in r.getMessage()]
    assert len(summary_lines) == 1, summary_lines
    assert "orphan_guard=1" in summary_lines[0]


def test_vex_summary_log_silent_when_every_counter_is_zero(caplog) -> None:
    """The summary log is conditional — when nothing fired, nothing is logged.
    Guards against a noisy log line on reviews that emit no findings.
    """
    # Arrange
    pipeline = _pipeline_for_summary_log()
    vex = _StubVex(verdict_counts={}, failure_counts={})
    guard = _StubGuard(guard_downgrade=0)
    caplog.set_level("INFO", logger="revue_core.core.pipeline")

    # Act
    pipeline._log_vex_summary(vex, guard)

    # Assert
    summary_lines = [r.getMessage() for r in caplog.records if "Vex:" in r.getMessage()]
    assert summary_lines == []


def test_vex_summary_log_persists_guard_downgrade_into_metrics(monkeypatch) -> None:
    """AC13 — the per-run ``record_vex`` call propagates the guard counter
    into ``VexMetricsData.guard_downgrade`` so metrics.jsonl carries it.
    """
    # Arrange
    pipeline = _pipeline_for_summary_log()
    vex = _StubVex(
        verdict_counts={"apply": 2},
        failure_counts={},
    )
    guard = _StubGuard(guard_downgrade=5)
    captured: list = []
    monkeypatch.setattr(
        pipeline._metrics, "record_vex", lambda data: captured.append(data)
    )

    # Act
    pipeline._log_vex_summary(vex, guard)

    # Assert
    assert len(captured) == 1
    assert captured[0].guard_downgrade == 5
    assert captured[0].verdict_counts == {"apply": 2}


def test_infrastructure_agents_survive_license_filter_when_absent_from_agents_allowed():
    """Cleo/Nova/Vex must survive even when the license server omits them.

    The license API (previously on the legacy Fly host, now on revue.sh)
    returned ``agents_allowed`` lists that predate Vex. Without this guarantee, Vex was filtered out at the license
    stage, the warning ``Vex system prompt not found in routed_agents``
    fired in CI, and Vex ran on the degraded built-in default prompt.
    """
    from revue_core.core.cleo_router import _INFRASTRUCTURE_AGENTS, TeamSelection
    from revue_core.core.pipeline import OrchestrationModules

    captured: dict = {}

    def _capture_route(included, allowed_agents, shared=None, config=None):
        captured["allowed_names"] = {a.name for a in allowed_agents}
        return (
            TeamSelection(team="team-full-review", agents=[a.name for a in allowed_agents]),
            list(allowed_agents),
        )

    def _fake_agent(name: str):
        m = MagicMock()
        m.name = name
        m.analyse.return_value = MagicMock(success=True, findings=[], file_path="app.py")
        return m

    fake_agents = [_fake_agent(n) for n in ("maya", "cleo", "nova", "vex")]

    main_client = MagicMock()
    main_client.complete.return_value = _cr('{"status": "clean", "summary": "ok", "confidence": 1.0}')
    pipeline = ReviewPipeline(
        _config(),
        client=main_client,
        license_info=_license_info(
            agents_allowed=[
                "orchestrator", "code-quality-expert", "security-expert",
                "performance-expert", "architecture-expert", "consolidator",
                "sage", "cleo",
            ],  # paid-tier list pre-Vex — vex is deliberately absent
        ),
    )

    from revue_core.core.shared_analysis import run_shared_analysis
    from revue_core.core.formatting import format_selection_message
    from revue_core.core.cleo_router import assign_files_to_agents
    from revue_core.core.agent_runner import run_agents_parallel, ParallelRunResult

    fake_mods = OrchestrationModules(
        load_all_agents=lambda config, client, read_file_tool=None, read_lines_tool=None, find_code_tool=None: fake_agents,
        run_agents_parallel=run_agents_parallel,
        run_shared_analysis=run_shared_analysis,
        route=_capture_route,
        format_selection_message=format_selection_message,
        assign_files_to_agents=assign_files_to_agents,
        ParallelRunResult=ParallelRunResult,
    )

    with patch("revue_core.core.pipeline.parse_diff_file", return_value=[_fc("app.py")]), \
         patch("revue_core.core.pipeline.track_usage"), \
         patch("revue_core.core.pipeline._import_orchestration", return_value=fake_mods):
        pipeline.run("fake.diff")

    assert _INFRASTRUCTURE_AGENTS.issubset(captured["allowed_names"]), (
        f"Infrastructure agents must survive license filter even when omitted from "
        f"agents_allowed. Got allowed_agents={captured['allowed_names']}"
    )
