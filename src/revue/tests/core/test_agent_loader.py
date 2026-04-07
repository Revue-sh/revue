"""Tests for agent definition loader."""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from revue.core.agent_loader import (
    AgentDefinition, LoadedAgent, YAMLAgentParser, MarkdownAgentParser,
    load_agent_definition, load_agents_from_dir,
    load_custom_agents, load_all_agents,
)
from revue.core.ai_config import AIConfig
from revue.core.models import FileChange, AIReview


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

YAML_CONTENT = """\
name: test-agent
display_name: Test Agent
role: Test role description
system_prompt: You are a test agent. Find issues.
focus_areas:
  - security
  - performance
severity_default: minor
enabled: true
version: "1.0"
"""

MD_CONTENT = """\
---
name: md-agent
display_name: Markdown Agent
role: Markdown-defined agent
severity_default: major
enabled: true
---
You are a markdown agent. Review for quality issues.
Find bugs and suggest improvements.
"""

DISABLED_YAML = """\
name: disabled-agent
display_name: Disabled Agent
role: Should not load
system_prompt: ignored
enabled: false
"""


def _fc(path: str = "app.py") -> FileChange:
    return FileChange(file_path=path, change_type="modified",
                      additions=5, deletions=2, diff="@@ -1 +1 @@\n-old\n+new")


def _mock_client(response=None) -> MagicMock:
    c = MagicMock()
    c.complete.return_value = response or json.dumps([{
        "file_path": "app.py", "line_number": 5,
        "severity": "minor", "issue": "test finding",
        "suggestion": "fix it", "confidence": 0.8,
    }])
    return c


# ---------------------------------------------------------------------------
# AgentDefinition tests
# ---------------------------------------------------------------------------

def test_yaml_parser_loads_definition(tmp_path):
    p = tmp_path / "agent.yaml"
    p.write_text(YAML_CONTENT)
    defn = YAMLAgentParser().parse(p)
    assert defn.name == "test-agent"
    assert defn.role == "Test role description"
    assert "security" in defn.focus_areas
    assert defn.enabled is True


def test_yaml_parser_can_parse_yml(tmp_path):
    p = tmp_path / "agent.yml"
    p.write_text(YAML_CONTENT)
    assert YAMLAgentParser().can_parse(p)


def test_markdown_parser_loads_definition(tmp_path):
    p = tmp_path / "agent.md"
    p.write_text(MD_CONTENT)
    defn = MarkdownAgentParser().parse(p)
    assert defn.name == "md-agent"
    assert defn.severity_default == "major"
    assert "markdown agent" in defn.system_prompt.lower()


def test_markdown_parser_body_becomes_system_prompt(tmp_path):
    p = tmp_path / "agent.md"
    p.write_text(MD_CONTENT)
    defn = MarkdownAgentParser().parse(p)
    assert "quality issues" in defn.system_prompt


def test_load_agent_definition_yaml(tmp_path):
    p = tmp_path / "agent.yaml"
    p.write_text(YAML_CONTENT)
    defn = load_agent_definition(p)
    assert defn.name == "test-agent"


def test_load_agent_definition_markdown(tmp_path):
    p = tmp_path / "agent.md"
    p.write_text(MD_CONTENT)
    defn = load_agent_definition(p)
    assert defn.name == "md-agent"


def test_load_agent_definition_unknown_format(tmp_path):
    p = tmp_path / "agent.toml"
    p.write_text("name = 'x'")
    with pytest.raises(ValueError, match="No parser"):
        load_agent_definition(p)


def test_missing_name_raises(tmp_path):
    p = tmp_path / "agent.yaml"
    p.write_text("role: test\nsystem_prompt: test\n")
    with pytest.raises(ValueError, match="name"):
        load_agent_definition(p)


# ---------------------------------------------------------------------------
# LoadedAgent tests
# ---------------------------------------------------------------------------

def test_loaded_agent_name():
    defn = AgentDefinition(name="zara", display_name="Zara", role="security",
                           system_prompt="Find security issues.")
    agent = LoadedAgent(defn, _mock_client())
    assert agent.name == "zara"


def test_loaded_agent_analyse_returns_reviews():
    defn = AgentDefinition(name="zara", display_name="Zara", role="security",
                           system_prompt="Find security issues.")
    agent = LoadedAgent(defn, _mock_client())
    results = agent.analyse([_fc()])
    assert len(results) == 1
    assert isinstance(results[0], AIReview)
    assert results[0].category == "zara"


def test_loaded_agent_analyse_propagates_client_error():
    """REVUE-103: Fatal client errors (RuntimeError) now propagate — not swallowed."""
    defn = AgentDefinition(name="zara", display_name="Zara", role="security",
                           system_prompt="Find security issues.")
    c = MagicMock()
    c.complete.side_effect = RuntimeError("API down")
    agent = LoadedAgent(defn, c)
    with pytest.raises(RuntimeError, match="API down"):
        agent.analyse([_fc()])


def test_loaded_agent_analyse_graceful_on_bad_json():
    defn = AgentDefinition(name="zara", display_name="Zara", role="security",
                           system_prompt="Find security issues.")
    agent = LoadedAgent(defn, _mock_client("not json"))
    results = agent.analyse([_fc()])
    assert results == []


def test_loaded_agent_analyse_strips_markdown_fences():
    """analyse() must parse findings even when LLM wraps response in ```json fences."""
    fenced = '```json\n[{"file_path": "a.py", "line_number": 1, "severity": "high", "issue": "XSS", "suggestion": "escape", "confidence": 0.9}]\n```'
    defn = AgentDefinition(name="zara", display_name="Zara", role="security",
                           system_prompt="Find security issues.")
    agent = LoadedAgent(defn, _mock_client(fenced))
    results = agent.analyse([_fc()])
    assert len(results) == 1
    assert results[0].issue == "XSS"


def test_loaded_agent_analyse_strips_plain_fences():
    """analyse() strips plain ``` fences too (no language tag)."""
    fenced = '```\n[{"file_path": "a.py", "line_number": 1, "severity": "medium", "issue": "issue", "suggestion": "fix", "confidence": 0.8}]\n```'
    defn = AgentDefinition(name="maya", display_name="Maya", role="code-quality",
                           system_prompt="Review code quality.")
    agent = LoadedAgent(defn, _mock_client(fenced))
    results = agent.analyse([_fc()])
    assert len(results) == 1


def test_loaded_agent_analyse_maps_severity_minor_to_low():
    """analyse() maps legacy 'minor' severity to 'low' for cli.py compatibility."""
    payload = json.dumps([{"file_path": "a.py", "line_number": 1, "severity": "minor",
                           "issue": "style issue", "suggestion": "fix", "confidence": 0.5}])
    defn = AgentDefinition(name="maya", display_name="Maya", role="code-quality",
                           system_prompt="Review.")
    agent = LoadedAgent(defn, _mock_client(payload))
    results = agent.analyse([_fc()])
    assert results[0].severity == "low"


def test_loaded_agent_analyse_maps_severity_critical_to_high():
    """analyse() maps 'critical' → 'high'."""
    payload = json.dumps([{"file_path": "a.py", "line_number": 1, "severity": "critical",
                           "issue": "SQL injection", "suggestion": "use params", "confidence": 0.95}])
    defn = AgentDefinition(name="zara", display_name="Zara", role="security",
                           system_prompt="Find security issues.")
    agent = LoadedAgent(defn, _mock_client(payload))
    results = agent.analyse([_fc()])
    assert results[0].severity == "high"


def test_loaded_agent_analyse_maps_severity_major_to_medium():
    """analyse() maps 'major' → 'medium'."""
    payload = json.dumps([{"file_path": "a.py", "line_number": 1, "severity": "major",
                           "issue": "perf issue", "suggestion": "cache", "confidence": 0.8}])
    defn = AgentDefinition(name="kai", display_name="Kai", role="performance",
                           system_prompt="Find performance issues.")
    agent = LoadedAgent(defn, _mock_client(payload))
    results = agent.analyse([_fc()])
    assert results[0].severity == "medium"


def test_loaded_agent_analyse_preserves_category_from_finding():
    """analyse() uses category from finding JSON, not agent name, when present."""
    payload = json.dumps([{"file_path": "a.py", "line_number": 1, "severity": "high",
                           "issue": "XSS", "suggestion": "escape", "confidence": 0.9,
                           "category": "security"}])
    defn = AgentDefinition(name="zara", display_name="Zara", role="security",
                           system_prompt="Find security issues.")
    agent = LoadedAgent(defn, _mock_client(payload))
    results = agent.analyse([_fc()])
    assert results[0].category == "security"


# ---------------------------------------------------------------------------
# Directory loading tests
# ---------------------------------------------------------------------------

def test_load_agents_from_dir(tmp_path):
    (tmp_path / "zara.yaml").write_text(YAML_CONTENT)
    (tmp_path / "md.md").write_text(MD_CONTENT)
    agents = load_agents_from_dir(tmp_path, _mock_client())
    assert len(agents) == 2


def test_load_agents_skips_disabled(tmp_path):
    (tmp_path / "enabled.yaml").write_text(YAML_CONTENT)
    (tmp_path / "disabled.yaml").write_text(DISABLED_YAML)
    agents = load_agents_from_dir(tmp_path, _mock_client())
    names = [a.name for a in agents]
    assert "disabled-agent" not in names
    assert "test-agent" in names


def test_load_agents_skips_unparseable(tmp_path):
    (tmp_path / "valid.yaml").write_text(YAML_CONTENT)
    (tmp_path / "bad.yaml").write_text("role: missing name field\n")
    agents = load_agents_from_dir(tmp_path, _mock_client())
    assert len(agents) == 1


# ---------------------------------------------------------------------------
# Custom agent loading tests (Story 030)
# ---------------------------------------------------------------------------

CUSTOM_YAML = """\
name: custom-lint
display_name: Custom Lint Agent
role: Project-specific linting rules
system_prompt: You enforce our project linting rules.
focus_areas:
  - linting
severity_default: suggestion
enabled: true
"""


def _config(**overrides) -> AIConfig:
    """Create an AIConfig with sensible test defaults."""
    defaults = dict(
        gitlab_url="", gitlab_token="", gitlab_project_id="",
        gitlab_project_path="", gitlab_project_url="",
        genai_gateway_url="", openai_api_key="", gen_ai_gateway_model="",
        ai_temp=0.3, ai_confidence=70, ai_max_tokens=4096,
    )
    defaults.update(overrides)
    return AIConfig(**defaults)


def test_load_custom_agents_empty_string():
    """Empty custom_agents_dir returns empty list."""
    assert load_custom_agents("") == []


def test_load_custom_agents_none():
    """None custom_agents_dir returns empty list."""
    assert load_custom_agents(None) == []


def test_load_custom_agents_nonexistent_path(caplog):
    """Non-existent directory returns [] and logs warning."""
    result = load_custom_agents("/nonexistent/path/to/agents")
    assert result == []
    assert "does not exist" in caplog.text


def test_load_custom_agents_valid_yaml(tmp_path):
    """Loads a valid custom agent YAML from a temp dir."""
    (tmp_path / "custom-lint.yaml").write_text(CUSTOM_YAML)
    defs = load_custom_agents(str(tmp_path))
    assert len(defs) == 1
    assert defs[0].name == "custom-lint"
    assert defs[0].role == "Project-specific linting rules"


def test_load_custom_agents_valid_markdown(tmp_path):
    """Loads a valid custom agent Markdown from a temp dir."""
    (tmp_path / "custom-md.md").write_text(MD_CONTENT)
    defs = load_custom_agents(str(tmp_path))
    assert len(defs) == 1
    assert defs[0].name == "md-agent"


def test_load_custom_agents_skips_invalid(tmp_path, caplog):
    """Invalid files are skipped with a warning, valid files still load."""
    (tmp_path / "good.yaml").write_text(CUSTOM_YAML)
    (tmp_path / "bad.yaml").write_text("role: missing name\n")
    defs = load_custom_agents(str(tmp_path))
    assert len(defs) == 1
    assert defs[0].name == "custom-lint"
    assert "Skipping invalid custom agent" in caplog.text


def test_load_custom_agents_rejects_symlink_escape(tmp_path):
    """Symlinks pointing outside custom_agents_dir are rejected."""
    custom_dir = tmp_path / "custom"
    custom_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "evil.yaml"
    target.write_text(CUSTOM_YAML)
    link = custom_dir / "evil.yaml"
    link.symlink_to(target)
    defs = load_custom_agents(str(custom_dir))
    assert len(defs) == 0


def test_load_custom_agents_rejects_dotdot_symlink(tmp_path):
    """Symlink using parent traversal is rejected."""
    custom_dir = tmp_path / "project" / "agents"
    custom_dir.mkdir(parents=True)
    secret = tmp_path / "secret.yaml"
    secret.write_text(CUSTOM_YAML)
    link = custom_dir / "sneaky.yaml"
    link.symlink_to(secret)
    defs = load_custom_agents(str(custom_dir))
    assert len(defs) == 0


# ---------------------------------------------------------------------------
# load_all_agents tests (Story 030)
# ---------------------------------------------------------------------------

def test_load_all_agents_builtin_only(tmp_path):
    """When custom_agents_dir is empty, returns only built-in agents."""
    (tmp_path / "zara.yaml").write_text(YAML_CONTENT)
    config = _config(custom_agents_dir="")
    agents = load_all_agents(config, _mock_client(), builtin_agents_dir=str(tmp_path))
    assert len(agents) == 1
    assert agents[0].name == "test-agent"


def test_load_all_agents_custom_override(tmp_path):
    """Custom agent with same name as built-in overrides it."""
    builtin_dir = tmp_path / "builtin"
    builtin_dir.mkdir()
    custom_dir = tmp_path / "custom"
    custom_dir.mkdir()

    (builtin_dir / "agent.yaml").write_text(YAML_CONTENT)  # name: test-agent

    override_yaml = YAML_CONTENT.replace(
        "role: Test role description", "role: Custom override role"
    )
    (custom_dir / "agent.yaml").write_text(override_yaml)

    config = _config(custom_agents_dir=str(custom_dir))
    client = _mock_client()
    agents = load_all_agents(config, client, builtin_agents_dir=str(builtin_dir))

    assert len(agents) == 1
    assert agents[0].name == "test-agent"
    assert agents[0].definition.role == "Custom override role"


def test_load_all_agents_merges_both(tmp_path):
    """Built-in and custom agents with different names are both returned."""
    builtin_dir = tmp_path / "builtin"
    builtin_dir.mkdir()
    custom_dir = tmp_path / "custom"
    custom_dir.mkdir()

    (builtin_dir / "builtin.yaml").write_text(YAML_CONTENT)  # name: test-agent
    (custom_dir / "custom.yaml").write_text(CUSTOM_YAML)  # name: custom-lint

    config = _config(custom_agents_dir=str(custom_dir))
    agents = load_all_agents(config, _mock_client(), builtin_agents_dir=str(builtin_dir))

    names = {a.name for a in agents}
    assert names == {"test-agent", "custom-lint"}


def test_load_all_agents_skips_disabled_custom(tmp_path):
    """Disabled custom agents are not loaded."""
    builtin_dir = tmp_path / "builtin"
    builtin_dir.mkdir()
    custom_dir = tmp_path / "custom"
    custom_dir.mkdir()

    (builtin_dir / "a.yaml").write_text(YAML_CONTENT)
    (custom_dir / "b.yaml").write_text(DISABLED_YAML)

    config = _config(custom_agents_dir=str(custom_dir))
    agents = load_all_agents(config, _mock_client(), builtin_agents_dir=str(builtin_dir))

    names = [a.name for a in agents]
    assert "disabled-agent" not in names


# ---------------------------------------------------------------------------
# REVUE-103: Fatal infrastructure errors must propagate (not be swallowed)
# ---------------------------------------------------------------------------

class _FakeHTTPError(Exception):
    """Simulates an HTTP 400 error from the AI client (e.g. credit exhausted)."""
    def __init__(self, code: int, msg: str):
        self.code = code
        super().__init__(f"Error code: {code} - {msg}")


def test_analyse_propagates_http_error():
    """TC1 (AC1): HTTP error from client propagates out of analyse() — not swallowed."""
    defn = AgentDefinition(name="zara", display_name="Zara", role="security",
                           system_prompt="Find issues.")
    client = MagicMock()
    client.complete.side_effect = _FakeHTTPError(400, "credit balance too low")
    agent = LoadedAgent(defn, client)

    with pytest.raises(_FakeHTTPError):
        agent.analyse([_fc()])


def test_analyse_propagates_runtime_error():
    """AC1: Generic RuntimeError (e.g. network down) propagates."""
    defn = AgentDefinition(name="kai", display_name="Kai", role="performance",
                           system_prompt="Find issues.")
    client = MagicMock()
    client.complete.side_effect = RuntimeError("Connection refused")
    agent = LoadedAgent(defn, client)

    with pytest.raises(RuntimeError):
        agent.analyse([_fc()])


def test_analyse_graceful_on_json_parse_error():
    """TC2 (AC2): JSONDecodeError returns [] — graceful degradation preserved."""
    defn = AgentDefinition(name="maya", display_name="Maya", role="quality",
                           system_prompt="Find issues.")
    agent = LoadedAgent(defn, _mock_client("not valid json at all"))
    results = agent.analyse([_fc()])
    assert results == []


def test_analyse_graceful_on_empty_response():
    """AC2: Empty string response returns [] gracefully (JSONDecodeError caught)."""
    defn = AgentDefinition(name="leo", display_name="Leo", role="arch",
                           system_prompt="Find issues.")
    c = MagicMock()
    c.complete.return_value = ""  # Explicitly set empty string directly — _mock_client("") is falsy and would use the default non-empty response
    agent = LoadedAgent(defn, c)
    results = agent.analyse([_fc()])
    assert results == []


def test_agent_runner_marks_failed_on_http_error():
    """TC1 (AC1): agent_runner correctly sets success=False when analyse() raises."""
    from revue.core.agent_runner import run_agents_parallel, AgentProtocol
    from revue.core.models import AIReview

    class FailingAgent:
        name = "failing"
        def analyse(self, changes, shared=None):
            raise _FakeHTTPError(400, "credit exhausted")

    result = run_agents_parallel([FailingAgent()], [_fc()], shared=None)
    assert len(result.agent_results) == 1
    r = result.agent_results[0]
    assert r.success is False
    assert any(s in r.error.lower() for s in ["400", "credit"])
    assert r.findings == []


def test_agent_runner_partial_failure_continues():
    """TC4 (AC4): one agent fails, others continue — partial results returned."""
    from revue.core.agent_runner import run_agents_parallel
    from revue.core.models import AIReview

    class GoodAgent:
        name = "good"
        def analyse(self, changes, shared=None):
            return [AIReview(file_path="a.py", line_number=1, severity="low",
                             issue="issue", suggestion="fix", confidence=0.8)]

    class BadAgent:
        name = "bad"
        def analyse(self, changes, shared=None):
            raise RuntimeError("network down")

    result = run_agents_parallel([GoodAgent(), BadAgent()], [_fc()], shared=None)
    successes = [r for r in result.agent_results if r.success]
    failures = [r for r in result.agent_results if not r.success]
    assert len(successes) == 1
    assert len(failures) == 1
    assert successes[0].agent_name == "good"
    assert len(successes[0].findings) == 1


# ---------------------------------------------------------------------------
# REVUE-94: Pattern injection into system prompts (AC2)
# ---------------------------------------------------------------------------

from revue.core.pattern_injection import build_pattern_prompt_sections, inject_patterns


def test_allowed_patterns_injected_into_system_prompt():
    """AC2: Allowed patterns appear in system prompt under correct header."""
    patterns = [
        {"pattern": "_def attribute access on LoadedAgent", "rationale": "Internal implementation detail, no public API"},
        {"pattern": "Inline lazy httpx import", "rationale": "Intentional lazy loading pattern"},
    ]
    defn = AgentDefinition(name="zara", display_name="Zara", role="security",
                           system_prompt="Find security issues.")
    agent = LoadedAgent(defn, _mock_client())
    inject_patterns([agent], allowed_patterns=patterns, disallowed_patterns=[])
    prompt = agent._def.system_prompt
    assert "## Allowed Patterns \u2014 Do Not Flag" in prompt
    assert "_def attribute access on LoadedAgent" in prompt
    assert "Internal implementation detail, no public API" in prompt
    assert "Inline lazy httpx import" in prompt


def test_disallowed_patterns_injected_into_system_prompt():
    """AC2: Disallowed patterns appear in system prompt under correct header."""
    patterns = [
        {"pattern": "TODO comments in production code", "rationale": "TODOs should be tracked as Jira tickets"},
    ]
    defn = AgentDefinition(name="zara", display_name="Zara", role="security",
                           system_prompt="Find security issues.")
    agent = LoadedAgent(defn, _mock_client())
    inject_patterns([agent], allowed_patterns=[], disallowed_patterns=patterns)
    prompt = agent._def.system_prompt
    assert "## Disallowed Patterns \u2014 Always Flag" in prompt
    assert "TODO comments in production code" in prompt
    assert "TODOs should be tracked as Jira tickets" in prompt


# ---------------------------------------------------------------------------
# REVUE-115: No per-block cache_control in caller (AC2)
# ---------------------------------------------------------------------------

def test_loaded_agent_analyse_no_cache_control_in_content():
    """TC2 (REVUE-115): analyse() passes plain content — no cache_control keys at any level."""
    defn = AgentDefinition(name="zara", display_name="Zara", role="security",
                           system_prompt="Find security issues.")
    client = _mock_client()
    agent = LoadedAgent(defn, client)
    agent.analyse([_fc()])

    call_args = client.complete.call_args
    messages = call_args[0][0]  # positional arg: list of messages
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            for block in content:
                assert "cache_control" not in block, f"cache_control found in block: {block}"
    # system kwarg should not be passed (no Anthropic-specific split)
    kwargs = call_args[1] if call_args[1] else {}
    assert "system" not in kwargs


def test_loaded_agent_analyse_passes_diff_hash_as_cache_key():
    """TC5 (REVUE-116): analyse() passes a 16-char hex cache_key derived from the diff."""
    defn = AgentDefinition(name="zara", display_name="Zara", role="security",
                           system_prompt="Find security issues.")
    client = _mock_client()
    agent = LoadedAgent(defn, client)
    agent.analyse([_fc()])

    call_kwargs = client.complete.call_args[1]
    cache_key = call_kwargs.get("cache_key")
    assert cache_key is not None, "cache_key should be passed to complete()"
    assert len(cache_key) == 16
    assert all(c in "0123456789abcdef" for c in cache_key), f"Not a hex string: {cache_key!r}"


def test_empty_patterns_no_injection():
    """AC2: When both lists are empty, no pattern section headers appear."""
    defn = AgentDefinition(name="zara", display_name="Zara", role="security",
                           system_prompt="Find security issues.")
    agent = LoadedAgent(defn, _mock_client())
    original_prompt = agent._def.system_prompt
    inject_patterns([agent], allowed_patterns=[], disallowed_patterns=[])
    assert agent._def.system_prompt == original_prompt
    assert "Allowed Patterns" not in agent._def.system_prompt
    assert "Disallowed Patterns" not in agent._def.system_prompt
