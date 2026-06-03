"""Tests for REVUE-99: agent name mapping from licence names to agent file names."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from revue_core.core.pipeline import _INFRASTRUCTURE_AGENTS, _LICENCE_NAME_TO_AGENT, _VIRTUAL_AGENTS, _is_premium_tier


class TestLicenceNameMapping:
    """_LICENCE_NAME_TO_AGENT maps display names → agent file names."""

    def test_code_quality_expert_maps_to_maya(self):
        assert _LICENCE_NAME_TO_AGENT["code-quality-expert"] == "maya"

    def test_security_expert_maps_to_zara(self):
        assert _LICENCE_NAME_TO_AGENT["security-expert"] == "zara"

    def test_performance_expert_maps_to_kai(self):
        assert _LICENCE_NAME_TO_AGENT["performance-expert"] == "kai"

    def test_architecture_expert_maps_to_leo(self):
        assert _LICENCE_NAME_TO_AGENT["architecture-expert"] == "leo"

    def test_consolidator_maps_to_nova(self):
        assert _LICENCE_NAME_TO_AGENT["consolidator"] == "nova"

    def test_pass_throughs_unchanged(self):
        """Agents that already match file names should pass through unchanged."""
        assert _LICENCE_NAME_TO_AGENT["cleo"] == "cleo"
        assert _LICENCE_NAME_TO_AGENT["nova"] == "nova"

    def test_virtual_agents_not_in_mapping(self):
        """orchestrator and sage are pipeline-level — not file-based, not in mapping."""
        assert "orchestrator" not in _LICENCE_NAME_TO_AGENT
        assert "sage" not in _LICENCE_NAME_TO_AGENT

    def test_virtual_agents_set(self):
        assert "orchestrator" in _VIRTUAL_AGENTS
        assert "sage" in _VIRTUAL_AGENTS

    def test_infrastructure_agents_excluded_from_parallel_pool(self):
        """cleo (router) and nova (consolidator) must not run as reviewers."""
        assert "cleo" in _INFRASTRUCTURE_AGENTS
        assert "nova" in _INFRASTRUCTURE_AGENTS

    def test_reviewer_agents_not_in_infrastructure(self):
        """Specialist reviewers must NOT be infrastructure agents."""
        for reviewer in ("maya", "zara", "kai", "leo"):
            assert reviewer not in _INFRASTRUCTURE_AGENTS, \
                f"{reviewer} should be a reviewer, not infrastructure"


class TestPremiumTierDetection:
    """_is_premium_tier correctly identifies paid vs free tier agent lists."""

    def test_free_tier_is_not_premium(self):
        assert not _is_premium_tier(["orchestrator", "code-quality-expert", "consolidator"])

    def test_pro_tier_is_premium(self):
        assert _is_premium_tier([
            "orchestrator", "code-quality-expert", "security-expert",
            "performance-expert", "architecture-expert", "consolidator",
            "sage", "cleo", "nova",
        ])


class TestAgentResolution:
    """All four specialist agents resolve correctly from licence names."""

    def test_paid_tier_resolves_all_specialists(self):
        """Simulate what pipeline does: resolve licence names → agent names."""
        agents_allowed = [
            "orchestrator", "code-quality-expert", "security-expert",
            "performance-expert", "architecture-expert", "consolidator",
            "sage", "cleo", "nova",
        ]
        # Fake agents_by_name as if load_all_agents returned kai/zara/maya/leo/cleo/nova
        agents_by_name = {
            name: MagicMock(name=name)
            for name in ["orchestrator", "maya", "zara", "kai", "leo", "cleo", "nova", "sage"]
        }

        resolved: set[str] = set()
        for licence_name in agents_allowed:
            agent_name = _LICENCE_NAME_TO_AGENT.get(licence_name, licence_name)
            if agent_name in agents_by_name:
                resolved.add(agent_name)

        # All four specialists must be present
        assert "maya" in resolved, "Code quality agent (maya) not resolved"
        assert "zara" in resolved, "Security agent (zara) not resolved"
        assert "kai" in resolved, "Performance agent (kai) not resolved"
        assert "leo" in resolved, "Architecture agent (leo) not resolved"
        assert "cleo" in resolved
        assert "nova" in resolved

    def test_no_agents_silently_dropped(self):
        """The old bug: code-quality-expert/security-expert etc. were silently dropped."""
        agents_allowed = ["code-quality-expert", "security-expert",
                          "performance-expert", "architecture-expert"]

        # Old (broken) logic: direct name match
        agents_by_name_old = {"maya": MagicMock(), "zara": MagicMock(),
                               "kai": MagicMock(), "leo": MagicMock()}
        old_result = [a for name, a in agents_by_name_old.items() if name in agents_allowed]
        assert len(old_result) == 0, "Confirms the old bug: all specialists dropped"

        # New logic: resolve through mapping
        agents_by_name_new = {"maya": MagicMock(), "zara": MagicMock(),
                               "kai": MagicMock(), "leo": MagicMock()}
        resolved: set[str] = set()
        for licence_name in agents_allowed:
            agent_name = _LICENCE_NAME_TO_AGENT.get(licence_name, licence_name)
            if agent_name in agents_by_name_new:
                resolved.add(agent_name)
        assert len(resolved) == 4, f"All 4 specialists should resolve, got: {resolved}"
