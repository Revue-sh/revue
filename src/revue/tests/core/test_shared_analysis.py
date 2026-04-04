"""Tests for shared analysis."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from revue.core.shared_analysis import (
    run_shared_analysis,
    SharedAnalysisResult,
    DetectedArea,
    SelectedAgent,
    OrchestratorResponse,
    _parse_orchestrator_response,
    _detect_provider,
)
from revue.core.formatting import format_selection_message
from revue.core.models import FileChange


def _fc(path: str) -> FileChange:
    return FileChange(
        file_path=path, change_type="modified",
        additions=5, deletions=2,
        diff="@@ -1 +1 @@\n-old\n+new",
    )


def _mock_client(response: str) -> MagicMock:
    c = MagicMock()
    c.complete.return_value = response
    return c


_VALID_JSON = json.dumps({
    "languages": ["python"],
    "risk_areas": ["database"],
    "suggested_agents": ["maya", "zara"],
    "summary": "Adds a new database query.",
})

_VALID_ORCHESTRATOR_JSON = json.dumps({
    "detected_areas": [
        {"emoji": "🔐", "description": "Authentication middleware (login flow updated)"},
        {"emoji": "🗄️", "description": "Database migrations (users table schema change)"},
    ],
    "selected_agents": [
        {"emoji": "🛡️", "name": "Security Agent", "reason": "for auth review"},
        {"emoji": "🗄️", "name": "Data Agent", "reason": "for schema validation"},
    ],
    "languages": ["python"],
    "risk_areas": ["authentication", "database"],
    "summary": "Updates login flow and migrates user table.",
})


# ---------------------------------------------------------------------------
# Existing tests (must still pass — no regression)
# ---------------------------------------------------------------------------

def test_run_shared_analysis_success():
    result = run_shared_analysis([_fc("app.py")], _mock_client(_VALID_JSON))
    assert result.success
    assert "python" in result.languages


def test_run_shared_analysis_extracts_all_fields():
    result = run_shared_analysis([_fc("app.py")], _mock_client(_VALID_JSON))
    assert result.risk_areas == ["database"]
    assert result.suggested_agents == ["maya", "zara"]
    assert "database query" in result.summary


def test_run_shared_analysis_fallback_on_invalid_json():
    result = run_shared_analysis([_fc("app.py")], _mock_client("not json"))
    assert not result.success
    assert result.error == "fallback"


def test_run_shared_analysis_fallback_on_api_error():
    c = MagicMock()
    c.complete.side_effect = OSError("API down")
    result = run_shared_analysis([_fc("app.py")], c)
    assert not result.success


def test_run_shared_analysis_propagates_unexpected_exceptions():
    """Exceptions not in the catch list (e.g. RuntimeError) propagate instead of
    being silently swallowed — this makes unexpected failures visible."""
    c = MagicMock()
    c.complete.side_effect = RuntimeError("unexpected")
    with pytest.raises(RuntimeError, match="unexpected"):
        run_shared_analysis([_fc("app.py")], c)


def test_run_shared_analysis_fallback_on_os_error():
    """OSError (network/filesystem failures) is caught and returns fallback."""
    c = MagicMock()
    c.complete.side_effect = OSError("connection refused")
    result = run_shared_analysis([_fc("app.py")], c)
    assert isinstance(result, SharedAnalysisResult)
    assert not result.success


def test_shared_analysis_result_success_true():
    r = SharedAnalysisResult(
        languages=[], risk_areas=[], suggested_agents=[], summary="ok"
    )
    assert r.success is True


def test_shared_analysis_result_success_false():
    r = SharedAnalysisResult(
        languages=[], risk_areas=[], suggested_agents=[], summary="", error="fail"
    )
    assert r.success is False


def test_fallback_result_has_all_agents():
    r = SharedAnalysisResult.fallback(["python"])
    assert set(r.suggested_agents) == {"zara", "kai", "maya", "leo"}


def test_diff_summary_truncated():
    big_diff = "\n".join(["+line"] * 200)
    fc = FileChange(
        file_path="big.py", change_type="modified",
        additions=200, deletions=0, diff=big_diff,
    )
    c = _mock_client(_VALID_JSON)
    run_shared_analysis([fc], c, max_diff_summary_lines=100)
    prompt_sent = c.complete.call_args[0][0][0]["content"]
    assert prompt_sent.count("+line") <= 100


def test_run_shared_analysis_with_multiple_files():
    files = [_fc("a.py"), _fc("b.py"), _fc("c.py")]
    c = _mock_client(_VALID_JSON)
    run_shared_analysis(files, c)
    assert c.complete.call_count == 1


# ---------------------------------------------------------------------------
# REVUE-95: format_selection_message tests
# ---------------------------------------------------------------------------

def test_format_selection_message_full():
    resp = OrchestratorResponse(
        detected_areas=[
            DetectedArea(emoji="🔐", description="Authentication middleware (login flow updated)"),
            DetectedArea(emoji="🗄️", description="Database migrations (users table schema change)"),
        ],
        selected_agents=[
            SelectedAgent(emoji="🛡️", name="Security Agent", reason="for auth review"),
            SelectedAgent(emoji="🗄️", name="Data Agent", reason="for schema validation"),
        ],
        languages=["python"],
        risk_areas=["authentication", "database"],
        summary="Updates login flow and migrates user table.",
    )
    msg = format_selection_message(resp)
    assert "🔍 Analyzing your changes..." in msg
    assert "I've detected modifications in:" in msg
    assert "🔐 Authentication middleware (login flow updated)" in msg
    assert "🗄️ Database migrations (users table schema change)" in msg
    assert "To ensure quality, I'm bringing in:" in msg
    assert "→ 🛡️ Security Agent for auth review" in msg
    assert "→ 🗄️ Data Agent for schema validation" in msg
    assert "Starting review..." in msg


def test_format_selection_message_empty_areas():
    resp = OrchestratorResponse(
        detected_areas=[],
        selected_agents=[
            SelectedAgent(emoji="🛡️", name="Security Agent", reason="for auth review"),
        ],
        languages=["python"],
        risk_areas=[],
        summary="Simple change.",
    )
    msg = format_selection_message(resp)
    assert "🔍 Analyzing your changes..." in msg
    assert "I've detected modifications in:" not in msg
    assert "To ensure quality, I'm bringing in:" in msg
    assert "Starting review..." in msg


def test_format_selection_message_empty_agents():
    resp = OrchestratorResponse(
        detected_areas=[
            DetectedArea(emoji="📝", description="Docs update"),
        ],
        selected_agents=[],
        languages=["markdown"],
        risk_areas=[],
        summary="Documentation only.",
    )
    msg = format_selection_message(resp)
    assert "I've detected modifications in:" in msg
    assert "To ensure quality, I'm bringing in:" not in msg
    assert "Starting review..." in msg


def test_format_selection_message_both_empty():
    """REVUE-95: when both lists are empty, show explanation with summary."""
    resp = OrchestratorResponse(
        detected_areas=[],
        selected_agents=[],
        languages=["yaml"],
        risk_areas=[],
        summary="Configuration file update.",
    )
    msg = format_selection_message(resp)
    assert "🔍 Analyzing your changes..." in msg
    assert "No specialist agents required" in msg
    assert "Summary: Configuration file update." in msg
    assert "Starting review..." in msg


def test_format_selection_message_both_empty_no_summary():
    """REVUE-95: empty lists with no summary still shows explanation."""
    resp = OrchestratorResponse(
        detected_areas=[],
        selected_agents=[],
        languages=[],
        risk_areas=[],
        summary="",
    )
    msg = format_selection_message(resp)
    assert "No specialist agents required" in msg
    assert "Summary:" not in msg
    assert "Starting review..." in msg


# ---------------------------------------------------------------------------
# REVUE-95: OrchestratorResponse validation tests
# ---------------------------------------------------------------------------

def test_parse_orchestrator_response_valid():
    resp = _parse_orchestrator_response(_VALID_ORCHESTRATOR_JSON)
    assert len(resp.detected_areas) == 2
    assert len(resp.selected_agents) == 2
    assert resp.detected_areas[0].emoji == "🔐"
    assert resp.selected_agents[0].name == "Security Agent"
    assert resp.languages == ["python"]
    assert resp.risk_areas == ["authentication", "database"]
    assert "login flow" in resp.summary


def test_parse_orchestrator_response_missing_field():
    incomplete = json.dumps({
        "languages": ["python"],
        "summary": "Missing fields.",
    })
    with pytest.raises(ValueError, match="Missing required field"):
        _parse_orchestrator_response(incomplete)


def test_parse_orchestrator_response_not_json():
    with pytest.raises(json.JSONDecodeError):
        _parse_orchestrator_response("not json at all")


def test_parse_orchestrator_response_not_dict():
    with pytest.raises(ValueError, match="Expected JSON object"):
        _parse_orchestrator_response('"just a string"')


def test_parse_orchestrator_response_non_dict_items_skipped():
    """Non-dict items in detected_areas/selected_agents are silently skipped."""
    data = json.dumps({
        "detected_areas": [
            {"emoji": "🔐", "description": "Auth"},
            "not a dict",
            42,
        ],
        "selected_agents": [
            {"emoji": "🛡️", "name": "Security Agent", "reason": "for auth"},
        ],
        "languages": ["python"],
        "risk_areas": [],
        "summary": "Test.",
    })
    resp = _parse_orchestrator_response(data)
    assert len(resp.detected_areas) == 1
    assert resp.detected_areas[0].description == "Auth"


# ---------------------------------------------------------------------------
# REVUE-95: Fallback when AI returns unexpected format
# ---------------------------------------------------------------------------

def test_run_shared_analysis_legacy_format_no_orchestrator_response():
    """Old-format JSON (with suggested_agents) still works; orchestrator_response is None."""
    result = run_shared_analysis([_fc("app.py")], _mock_client(_VALID_JSON))
    assert result.success
    assert result.orchestrator_response is None
    assert result.suggested_agents == ["maya", "zara"]


def test_run_shared_analysis_new_format_has_orchestrator_response():
    """New-format JSON produces an OrchestratorResponse."""
    result = run_shared_analysis([_fc("app.py")], _mock_client(_VALID_ORCHESTRATOR_JSON))
    assert result.success
    assert result.orchestrator_response is not None
    assert len(result.orchestrator_response.detected_areas) == 2
    assert len(result.orchestrator_response.selected_agents) == 2


def test_run_shared_analysis_new_format_defaults_suggested_agents():
    """New format without suggested_agents defaults to all agents."""
    result = run_shared_analysis([_fc("app.py")], _mock_client(_VALID_ORCHESTRATOR_JSON))
    assert result.suggested_agents == ["zara", "kai", "maya", "leo"]


# ---------------------------------------------------------------------------
# REVUE-95: Provider-specific handling
# ---------------------------------------------------------------------------

def test_detect_provider_anthropic():
    client = MagicMock()
    client.__class__.__name__ = "AnthropicClient"
    assert _detect_provider(client) == "anthropic"


def test_detect_provider_openai():
    client = MagicMock()
    client.__class__.__name__ = "OpenAIClient"
    assert _detect_provider(client) == "openai"


def test_detect_provider_azure():
    client = MagicMock()
    client.__class__.__name__ = "AzureOpenAIClient"
    assert _detect_provider(client) == "azure"


def test_detect_provider_openrouter():
    client = MagicMock()
    client.__class__.__name__ = "OpenRouterClient"
    assert _detect_provider(client) == "openrouter"


def test_detect_provider_unknown():
    client = MagicMock()
    client.__class__.__name__ = "SomethingElse"
    assert _detect_provider(client) == ""


def test_anthropic_provider_appends_json_suffix():
    """Anthropic provider appends JSON instruction to prompt."""
    c = _mock_client(_VALID_ORCHESTRATOR_JSON)
    run_shared_analysis([_fc("app.py")], c, provider="anthropic")
    prompt_sent = c.complete.call_args[0][0][0]["content"]
    assert "Respond with raw JSON only" in prompt_sent


def test_openai_provider_no_json_suffix():
    """OpenAI provider does NOT append the Anthropic JSON suffix."""
    c = _mock_client(_VALID_ORCHESTRATOR_JSON)
    run_shared_analysis([_fc("app.py")], c, provider="openai")
    prompt_sent = c.complete.call_args[0][0][0]["content"]
    assert "Respond with raw JSON only" not in prompt_sent


def test_google_provider_no_json_suffix():
    """Google provider does NOT append the Anthropic JSON suffix."""
    c = _mock_client(_VALID_ORCHESTRATOR_JSON)
    run_shared_analysis([_fc("app.py")], c, provider="google")
    prompt_sent = c.complete.call_args[0][0][0]["content"]
    assert "Respond with raw JSON only" not in prompt_sent


def test_groq_provider_no_json_suffix():
    """Groq provider does NOT append the Anthropic JSON suffix."""
    c = _mock_client(_VALID_ORCHESTRATOR_JSON)
    run_shared_analysis([_fc("app.py")], c, provider="groq")
    prompt_sent = c.complete.call_args[0][0][0]["content"]
    assert "Respond with raw JSON only" not in prompt_sent


# ---------------------------------------------------------------------------
# REVUE-95: Fence-stripping in run_shared_analysis
# ---------------------------------------------------------------------------

def test_run_shared_analysis_strips_markdown_fences():
    """run_shared_analysis() must parse JSON and build OrchestratorResponse even
    when the LLM wraps its response in ```json fences."""
    fenced = '```json\n' + _VALID_ORCHESTRATOR_JSON + '\n```'
    result = run_shared_analysis([_fc("app.py")], _mock_client(fenced))
    assert result.success
    assert "python" in result.languages
    assert result.orchestrator_response is not None
    assert len(result.orchestrator_response.detected_areas) == 2
    assert len(result.orchestrator_response.selected_agents) == 2


def test_run_shared_analysis_strips_plain_fences():
    """run_shared_analysis() strips plain ``` fences too (no language tag)."""
    fenced = '```\n' + _VALID_ORCHESTRATOR_JSON + '\n```'
    result = run_shared_analysis([_fc("app.py")], _mock_client(fenced))
    assert result.success
    assert result.orchestrator_response is not None
    assert len(result.orchestrator_response.selected_agents) == 2


def test_unknown_provider_appends_json_suffix():
    """Unknown providers get the prompt engineering fallback."""
    c = _mock_client(_VALID_ORCHESTRATOR_JSON)
    run_shared_analysis([_fc("app.py")], c, provider="")
    prompt_sent = c.complete.call_args[0][0][0]["content"]
    # MagicMock class name isn't in _JSON_FORMAT_PROVIDERS → suffix appended
    assert "Respond with raw JSON only" in prompt_sent


# ---------------------------------------------------------------------------
# REVUE-95: Warning log on fallback
# ---------------------------------------------------------------------------

def test_run_shared_analysis_empty_fenced_response_returns_fallback():
    """Empty fenced response (```json\\n```) must not raise JSONDecodeError.

    It should hit the ValueError("LLM returned empty response after fence
    stripping") path and return SharedAnalysisResult.fallback().
    """
    result = run_shared_analysis([_fc("app.py")], _mock_client("```json\n```"))
    assert not result.success
    assert result.error == "fallback"
    assert set(result.suggested_agents) == {"zara", "kai", "maya", "leo"}


def test_run_shared_analysis_logs_warning_on_fallback(caplog):
    """Fallback must emit a WARNING log so CI can surface the real failure reason."""
    import logging
    with caplog.at_level(logging.WARNING, logger="revue.core.shared_analysis"):
        result = run_shared_analysis([_fc("app.py")], _mock_client("not json"))
    assert not result.success
    assert "Shared analysis failed" in caplog.text


# ---------------------------------------------------------------------------
# REVUE-95: Realistic Anthropic response patterns (production regression)
# ---------------------------------------------------------------------------

_WRONG_SCHEMA_WITH_SUMMARY_JSON = json.dumps({
    "classification": "FEATURE",
    "risk_level": "LOW",
    "review_priority": "STANDARD",
    "requires_security_review": False,
    "summary": "test",
})

_WRONG_SCHEMA_JSON = json.dumps({
    "classification": "FEATURE",
    "risk_level": "LOW",
    "review_priority": "STANDARD",
    "requires_security_review": False,
})

_AUTH_ORCHESTRATOR_JSON = json.dumps({
    "detected_areas": [{"emoji": "🔐", "description": "Auth changes"}],
    "selected_agents": [{"emoji": "🛡️", "name": "Security Agent", "reason": "for auth review"}],
    "languages": ["python"],
    "risk_areas": ["authentication"],
    "summary": "Auth changes detected.",
})


def test_wrong_schema_old_classification_format_returns_fallback(caplog):
    """Claude returning a completely different JSON schema (classification/risk_level)
    must return fallback — the root cause of the production regression."""
    import logging
    with caplog.at_level(logging.WARNING, logger="revue.core.shared_analysis"):
        result = run_shared_analysis([_fc("app.py")], _mock_client(_WRONG_SCHEMA_JSON))
    # Wrong schema still parses as valid JSON with get() defaults, so it
    # succeeds but with no orchestrator_response and default agent list.
    # The key guarantee: it does NOT raise an exception.
    assert isinstance(result, SharedAnalysisResult)
    assert result.orchestrator_response is None


def test_empty_fenced_response_returns_fallback_no_exception(caplog):
    """```json\\n``` (empty fences) — realistic Anthropic edge case."""
    import logging
    with caplog.at_level(logging.WARNING, logger="revue.core.shared_analysis"):
        result = run_shared_analysis([_fc("app.py")], _mock_client("```json\n```"))
    assert not result.success
    assert result.error == "fallback"
    assert "Shared analysis failed" in caplog.text


def test_plain_empty_string_returns_fallback_no_exception():
    """Plain empty string must return fallback, never raise."""
    result = run_shared_analysis([_fc("app.py")], _mock_client(""))
    assert not result.success
    assert result.error == "fallback"


def test_valid_new_schema_fires_transparency_message():
    """Valid OrchestratorResponse schema produces transparency message."""
    result = run_shared_analysis([_fc("app.py")], _mock_client(_AUTH_ORCHESTRATOR_JSON))
    assert result.success is True
    assert result.orchestrator_response is not None
    assert result.orchestrator_response.detected_areas[0].description == "Auth changes"
    msg = format_selection_message(result.orchestrator_response)
    assert "🔍 Analyzing" in msg


def test_valid_schema_wrapped_in_fences_parses_correctly():
    """Anthropic often wraps JSON in ```json fences — must still parse and
    produce a full OrchestratorResponse with transparency message."""
    fenced = "```json\n" + _AUTH_ORCHESTRATOR_JSON + "\n```"
    result = run_shared_analysis([_fc("app.py")], _mock_client(fenced))
    assert result.success is True
    assert result.orchestrator_response is not None
    assert result.orchestrator_response.detected_areas[0].description == "Auth changes"
    msg = format_selection_message(result.orchestrator_response)
    assert "🔍 Analyzing" in msg


# ---------------------------------------------------------------------------
# REVUE-95: Mandatory schema language & production Anthropic response patterns
# ---------------------------------------------------------------------------

def test_prompt_contains_mandatory_schema_language():
    """SHARED_ANALYSIS_PROMPT must use mandatory language to force the schema.
    Root cause of REVUE-95: conditional language let Claude return a different schema."""
    from revue.core.shared_analysis import SHARED_ANALYSIS_PROMPT
    assert "MUST" in SHARED_ANALYSIS_PROMPT or "ONLY" in SHARED_ANALYSIS_PROMPT
    assert "TEMPLATE:" in SHARED_ANALYSIS_PROMPT
    assert "EXAMPLE:" in SHARED_ANALYSIS_PROMPT
    # The old conditional phrase that caused the regression must be gone
    assert "When announcing" not in SHARED_ANALYSIS_PROMPT


def test_wrong_schema_with_summary_triggers_fallback_gracefully():
    """Production Anthropic response: has 'summary' (legacy parsing finds it)
    but wrong schema (no suggested_agents, no detected_areas/selected_agents).
    Must not crash — returns success with default fallback agents and no
    orchestrator_response."""
    result = run_shared_analysis(
        [_fc("app.py")], _mock_client(_WRONG_SCHEMA_WITH_SUMMARY_JSON)
    )
    assert result.success is True
    assert result.orchestrator_response is None
    assert result.suggested_agents == ["zara", "kai", "maya", "leo"]


def test_valid_mandatory_schema_returns_orchestrator_response():
    """Raw JSON (no fences) matching the mandatory schema must produce a full
    OrchestratorResponse with correct fields and transparency message."""
    raw = json.dumps({
        "detected_areas": [{"emoji": "🔧", "description": "Core changes"}],
        "selected_agents": [
            {"emoji": "🏗️", "name": "Architecture Agent", "reason": "for structure review"}
        ],
        "languages": ["python"],
        "risk_areas": ["architecture"],
        "summary": "Core changes detected.",
    })
    result = run_shared_analysis([_fc("app.py")], _mock_client(raw))
    assert result.orchestrator_response is not None
    assert result.orchestrator_response.detected_areas[0].emoji == "🔧"
    msg = format_selection_message(result.orchestrator_response)
    assert "🔍 Analyzing" in msg
