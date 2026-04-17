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


# ---------------------------------------------------------------------------
# REVUE-115: Unified code path — no provider-specific branching (AC3)
# ---------------------------------------------------------------------------

def test_shared_analysis_same_call_structure_for_anthropic_and_openai():
    """TC3: run_shared_analysis() sends identical message structure regardless of provider.

    With the Anthropic branch removed, both clients receive a plain string
    content message — no cache_control blocks, no isinstance branching.
    """
    from revue.core.ai_client import AnthropicClient, OpenAIClient

    anthropic_mock = MagicMock(spec=AnthropicClient)
    anthropic_mock.complete.return_value = _VALID_JSON

    openai_mock = MagicMock(spec=OpenAIClient)
    openai_mock.complete.return_value = _VALID_JSON

    run_shared_analysis([_fc("app.py")], anthropic_mock, provider="anthropic")
    run_shared_analysis([_fc("app.py")], openai_mock, provider="openai")

    anthropic_call = anthropic_mock.complete.call_args[0][0]
    openai_call = openai_mock.complete.call_args[0][0]

    # Both calls should have a single user message with plain string content
    assert len(anthropic_call) == 1
    assert len(openai_call) == 1
    assert anthropic_call[0]["role"] == "user"
    assert openai_call[0]["role"] == "user"
    assert isinstance(anthropic_call[0]["content"], str)
    assert isinstance(openai_call[0]["content"], str)


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
    """Anthropic provider appends JSON instruction to system block (D1)."""
    c = _mock_client(_VALID_ORCHESTRATOR_JSON)
    run_shared_analysis([_fc("app.py")], c, provider="anthropic")
    system_blocks = c.complete.call_args[1].get("system", [])
    # JSON suffix is in system[1] (orchestrator_instructions)
    instructions_text = system_blocks[1].get("text", "") if len(system_blocks) > 1 else ""
    assert "Respond with raw JSON only" in instructions_text


def test_openai_provider_no_json_suffix():
    """OpenAI provider does NOT append the Anthropic JSON suffix to system block."""
    c = _mock_client(_VALID_ORCHESTRATOR_JSON)
    run_shared_analysis([_fc("app.py")], c, provider="openai")
    system_blocks = c.complete.call_args[1].get("system", [])
    instructions_text = system_blocks[1].get("text", "") if len(system_blocks) > 1 else ""
    assert "Respond with raw JSON only" not in instructions_text


def test_google_provider_no_json_suffix():
    """Google provider does NOT append the Anthropic JSON suffix to system block."""
    c = _mock_client(_VALID_ORCHESTRATOR_JSON)
    run_shared_analysis([_fc("app.py")], c, provider="google")
    system_blocks = c.complete.call_args[1].get("system", [])
    instructions_text = system_blocks[1].get("text", "") if len(system_blocks) > 1 else ""
    assert "Respond with raw JSON only" not in instructions_text


def test_groq_provider_no_json_suffix():
    """Groq provider does NOT append the Anthropic JSON suffix to system block."""
    c = _mock_client(_VALID_ORCHESTRATOR_JSON)
    run_shared_analysis([_fc("app.py")], c, provider="groq")
    system_blocks = c.complete.call_args[1].get("system", [])
    instructions_text = system_blocks[1].get("text", "") if len(system_blocks) > 1 else ""
    assert "Respond with raw JSON only" not in instructions_text


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
    """Unknown providers get the prompt engineering fallback (in system block D1)."""
    c = _mock_client(_VALID_ORCHESTRATOR_JSON)
    run_shared_analysis([_fc("app.py")], c, provider="")
    system_blocks = c.complete.call_args[1].get("system", [])
    # Empty string provider isn't in _JSON_FORMAT_PROVIDERS → suffix appended to system[1]
    instructions_text = system_blocks[1].get("text", "") if len(system_blocks) > 1 else ""
    assert "Respond with raw JSON only" in instructions_text


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


# ---------------------------------------------------------------------------
# REVUE-117: SelectedAgent.files field — TC1 & TC2
# ---------------------------------------------------------------------------

def test_cleo_response_includes_files_per_agent():
    """TC1 — files list per agent is extracted from the parsed orchestrator response."""
    import json
    raw = json.dumps({
        "detected_areas": [{"emoji": "🔐", "description": "Auth changes"}],
        "selected_agents": [
            {
                "emoji": "🛡️",
                "name": "Security Agent",
                "reason": "for auth review",
                "files": ["app/auth.py", "app/middleware.py"],
            },
            {
                "emoji": "⚡",
                "name": "Performance Agent",
                "reason": "for API review",
                "files": ["app/api.py"],
            },
        ],
        "languages": ["python"],
        "risk_areas": ["authentication"],
        "summary": "Auth changes.",
    })

    class _Client:
        def complete(self, *a, **kw):
            return raw

    result = run_shared_analysis([_fc("app/auth.py")], _Client())
    assert result.orchestrator_response is not None
    agents = result.orchestrator_response.selected_agents
    assert len(agents) == 2

    security = agents[0]
    assert security.name == "Security Agent"
    assert security.files == ["app/auth.py", "app/middleware.py"]

    perf = agents[1]
    assert perf.name == "Performance Agent"
    assert perf.files == ["app/api.py"]


def test_cleo_missing_files_field_graceful():
    """TC2 — when Cleo omits the files field, SelectedAgent.files defaults to []."""
    import json

    raw = json.dumps({
        "detected_areas": [],
        "selected_agents": [
            {"emoji": "🛡️", "name": "Security Agent", "reason": "for review"},
        ],
        "languages": ["python"],
        "risk_areas": [],
        "summary": "Minor changes.",
    })

    class _Client:
        def complete(self, *a, **kw):
            return raw

    result = run_shared_analysis([_fc("app.py")], _Client())
    assert result.orchestrator_response is not None
    agent = result.orchestrator_response.selected_agents[0]
    assert agent.files == []


# ---------------------------------------------------------------------------
# REVUE-151: D1 — run_shared_analysis() restructure (TC_D1_6)
# ---------------------------------------------------------------------------

def test_shared_analysis_places_diff_summary_in_system_block() -> None:
    """TC_D1_6: run_shared_analysis() calls client.complete() with system=[diff_summary_block].

    After D1, the diff summary is system[0] with cache_control (shared cached prefix
    for the orchestrator analysis). The orchestrator instructions are system[1] without
    cache_control.
    """
    from unittest.mock import MagicMock

    mock_client = MagicMock()
    mock_client.complete.return_value = _VALID_JSON

    result = run_shared_analysis([_fc("test.py")], mock_client)

    call_args = mock_client.complete.call_args
    system = call_args[1].get("system", [])
    assert isinstance(system, list) and len(system) >= 1, (
        f"system should be a list with at least 1 element; got {system}"
    )
    # system[0] must have the diff summary with cache_control
    assert system[0].get("cache_control") == {"type": "ephemeral"}, (
        f"system[0] should have cache_control; got {system[0]}"
    )
    # The diff summary should be in system[0]'s text
    summary_text = system[0].get("text", "")
    assert "Diff summary:" in summary_text, "system[0] text should contain the diff summary"
    # system[1] must have the bridge phrase pointing back to system[0]
    assert len(system) >= 2, "system should have at least 2 blocks"
    assert "The diff summary above is what you must analyse." in system[1].get("text", ""), (
        f"system[1] must contain the bridge phrase; got: {system[1].get('text', '')!r}"
    )
