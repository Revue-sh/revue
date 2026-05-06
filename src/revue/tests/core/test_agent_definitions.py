"""Tests for built-in agent definitions (Story [021], [019], [046], [047], [048])."""
from __future__ import annotations

from pathlib import Path

import pytest

from revue.core.agent_loader import load_agent_definition, AgentDefinition

AGENTS_DIR = Path(__file__).parent.parent.parent / "agents"

EXPECTED_AGENTS = ["cleo", "nova", "zara", "kai", "maya", "leo"]


def _load(name: str) -> AgentDefinition:
    for ext in [".yaml", ".yml", ".md"]:
        p = AGENTS_DIR / f"{name}{ext}"
        if p.exists():
            return load_agent_definition(p)
    raise FileNotFoundError(f"No agent definition file for '{name}' in {AGENTS_DIR}")


@pytest.mark.parametrize("agent_name", EXPECTED_AGENTS)
def test_agent_file_exists(agent_name):
    defn = _load(agent_name)
    assert defn.name == agent_name


@pytest.mark.parametrize("agent_name", EXPECTED_AGENTS)
def test_agent_has_required_fields(agent_name):
    defn = _load(agent_name)
    assert defn.name, f"{agent_name}: name is empty"
    assert defn.display_name, f"{agent_name}: display_name is empty"
    assert defn.role, f"{agent_name}: role is empty"
    assert defn.system_prompt, f"{agent_name}: system_prompt is empty"


@pytest.mark.parametrize("agent_name", EXPECTED_AGENTS)
def test_agent_is_enabled(agent_name):
    defn = _load(agent_name)
    assert defn.enabled, f"{agent_name}: should be enabled"


@pytest.mark.parametrize("agent_name", EXPECTED_AGENTS)
def test_agent_valid_severity_default(agent_name):
    defn = _load(agent_name)
    assert defn.severity_default in {"critical", "major", "minor", "suggestion"}, \
        f"{agent_name}: invalid severity_default '{defn.severity_default}'"


def test_all_six_agents_load_from_dir():
    from unittest.mock import MagicMock
    from revue.core.agent_loader import load_agents_from_dir
    agents = load_agents_from_dir(AGENTS_DIR, MagicMock(), 4096)
    names = {a.name for a in agents}
    for expected in EXPECTED_AGENTS:
        assert expected in names, f"Agent '{expected}' not loaded from agents dir"


def test_specialist_agents_have_focus_areas():
    """Specialist agents (zara, kai, maya, leo) must declare focus areas."""
    for name in ["zara", "kai", "maya", "leo"]:
        defn = _load(name)
        assert defn.focus_areas, f"{name}: focus_areas should not be empty"


def test_zara_system_prompt_mentions_security():
    defn = _load("zara")
    assert any(word in defn.system_prompt.lower()
               for word in ["security", "vulnerabilit", "injection"])


def test_kai_system_prompt_mentions_performance():
    defn = _load("kai")
    assert any(word in defn.system_prompt.lower()
               for word in ["performance", "optimis", "bottleneck", "algorith"])


def test_maya_system_prompt_mentions_quality():
    defn = _load("maya")
    assert any(word in defn.system_prompt.lower()
               for word in ["quality", "maintainab", "correctness", "bug"])


def test_leo_system_prompt_mentions_architecture():
    defn = _load("leo")
    assert any(word in defn.system_prompt.lower()
               for word in ["architect", "solid", "design", "coupling"])
