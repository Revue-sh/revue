"""Tests for agent definition loader."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from AIReviewer.core.agent_loader import (
    AgentDefinition, LoadedAgent, YAMLAgentParser, MarkdownAgentParser,
    load_agent_definition, load_agents_from_dir,
)
from AIReviewer.core.models import FileChange, AIReview


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


def test_loaded_agent_analyse_graceful_on_client_error():
    defn = AgentDefinition(name="zara", display_name="Zara", role="security",
                           system_prompt="Find security issues.")
    c = MagicMock()
    c.complete.side_effect = RuntimeError("API down")
    agent = LoadedAgent(defn, c)
    results = agent.analyse([_fc()])
    assert results == []


def test_loaded_agent_analyse_graceful_on_bad_json():
    defn = AgentDefinition(name="zara", display_name="Zara", role="security",
                           system_prompt="Find security issues.")
    agent = LoadedAgent(defn, _mock_client("not json"))
    results = agent.analyse([_fc()])
    assert results == []


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
