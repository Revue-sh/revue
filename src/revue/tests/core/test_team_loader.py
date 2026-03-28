"""Tests for team_loader — YAML-backed team configuration (Story [77])."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from revue.core.team_loader import (
    TeamConfig,
    get_team_agents,
    load_all_teams,
    load_team,
)

# Path to the actual teams directory
_TEAMS_DIR = Path(__file__).parent.parent.parent / "teams"


# ---------------------------------------------------------------------------
# TeamConfig dataclass
# ---------------------------------------------------------------------------

class TestTeamConfigDataclass:
    def test_agents_merges_primary_and_secondary(self) -> None:
        config = TeamConfig(
            id="team-test",
            name="Test",
            primary_agents=["cleo", "maya"],
            secondary_agents=["zara"],
        )
        assert config.agents == ["cleo", "maya", "zara"]

    def test_agents_deduplicates(self) -> None:
        config = TeamConfig(
            id="team-test",
            name="Test",
            primary_agents=["cleo", "nova"],
            secondary_agents=["cleo"],  # duplicate
        )
        assert config.agents.count("cleo") == 1

    def test_agents_preserves_order(self) -> None:
        config = TeamConfig(
            id="team-test",
            name="Test",
            primary_agents=["cleo", "maya", "nova"],
            secondary_agents=["zara", "kai"],
        )
        assert config.agents == ["cleo", "maya", "nova", "zara", "kai"]

    def test_default_timeout(self) -> None:
        config = TeamConfig(id="t", name="T")
        assert config.timeout_seconds == 90


# ---------------------------------------------------------------------------
# load_team — from real teams directory
# ---------------------------------------------------------------------------

class TestLoadTeam:
    def test_load_team_full_review(self) -> None:
        config = load_team("team-full-review", teams_dir=_TEAMS_DIR)
        assert config.id == "team-full-review"
        assert config.name
        assert "maya" in config.agents
        assert "nova" in config.agents
        assert config.timeout_seconds > 0

    def test_load_team_quick(self) -> None:
        config = load_team("team-quick", teams_dir=_TEAMS_DIR)
        assert config.id == "team-quick"
        assert "maya" in config.primary_agents
        assert "nova" in config.primary_agents
        # team-quick must NOT have heavy agents
        assert "leo" not in config.agents
        assert "kai" not in config.agents
        assert "zara" not in config.agents

    def test_load_team_swift_ios(self) -> None:
        config = load_team("team-swift-ios", teams_dir=_TEAMS_DIR)
        assert config.id == "team-swift-ios"
        assert "swift" in config.trigger_languages
        assert any("*.swift" in p for p in config.trigger_file_patterns)
        assert config.timeout_seconds > 0

    def test_load_team_security_focus(self) -> None:
        config = load_team("team-security-focus", teams_dir=_TEAMS_DIR)
        assert config.id == "team-security-focus"
        assert "zara" in config.primary_agents
        assert len(config.trigger_keywords) > 0

    def test_load_team_kotlin_android(self) -> None:
        config = load_team("team-kotlin-android", teams_dir=_TEAMS_DIR)
        assert "kotlin" in config.trigger_languages

    def test_load_team_python(self) -> None:
        config = load_team("team-python", teams_dir=_TEAMS_DIR)
        assert "python" in config.trigger_languages

    def test_load_team_typescript(self) -> None:
        config = load_team("team-typescript", teams_dir=_TEAMS_DIR)
        assert "typescript" in config.trigger_languages

    def test_load_team_not_found_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_team("team-does-not-exist", teams_dir=_TEAMS_DIR)


# ---------------------------------------------------------------------------
# load_team — schema validation (using tmp files)
# ---------------------------------------------------------------------------

class TestLoadTeamSchemaValidation:
    def test_missing_team_section_raises(self, tmp_path) -> None:
        f = tmp_path / "team-bad.yml"
        f.write_text("agents:\n  primary: [cleo]\n")
        with pytest.raises(ValueError, match="Missing 'team:'"):
            load_team("team-bad", teams_dir=tmp_path)

    def test_missing_required_id_raises(self, tmp_path) -> None:
        f = tmp_path / "team-bad.yml"
        f.write_text("team:\n  name: Bad Team\nagents:\n  primary: [cleo]\n")
        with pytest.raises(ValueError, match="team.id"):
            load_team("team-bad", teams_dir=tmp_path)

    def test_id_mismatch_raises(self, tmp_path) -> None:
        f = tmp_path / "team-foo.yml"
        f.write_text("team:\n  id: team-bar\n  name: Bar\n")
        with pytest.raises(ValueError, match="does not match filename"):
            load_team("team-foo", teams_dir=tmp_path)

    def test_valid_minimal_team_loads(self, tmp_path) -> None:
        f = tmp_path / "team-minimal.yml"
        f.write_text(
            "team:\n  id: team-minimal\n  name: Minimal\n"
            "agents:\n  primary: [cleo]\n"
        )
        config = load_team("team-minimal", teams_dir=tmp_path)
        assert config.id == "team-minimal"
        assert "cleo" in config.agents

    def test_optional_fields_default_gracefully(self, tmp_path) -> None:
        f = tmp_path / "team-sparse.yml"
        f.write_text("team:\n  id: team-sparse\n  name: Sparse\n")
        config = load_team("team-sparse", teams_dir=tmp_path)
        assert config.primary_agents == []
        assert config.trigger_languages == []
        assert config.timeout_seconds == 90


# ---------------------------------------------------------------------------
# load_all_teams
# ---------------------------------------------------------------------------

class TestLoadAllTeams:
    def test_loads_all_real_team_files(self) -> None:
        teams = load_all_teams(teams_dir=_TEAMS_DIR)
        assert len(teams) >= 7  # 7 YAML files
        expected_ids = {
            "team-full-review", "team-quick", "team-swift-ios",
            "team-security-focus", "team-kotlin-android",
            "team-python", "team-typescript",
        }
        assert expected_ids.issubset(set(teams.keys()))

    def test_skips_invalid_files_without_crashing(self, tmp_path) -> None:
        # Write one valid and one invalid
        (tmp_path / "team-good.yml").write_text(
            "team:\n  id: team-good\n  name: Good\nagents:\n  primary: [maya]\n"
        )
        (tmp_path / "team-bad.yml").write_text("not: valid: yaml: at: all: {{")
        teams = load_all_teams(teams_dir=tmp_path)
        assert "team-good" in teams
        assert "team-bad" not in teams

    def test_empty_directory_returns_empty_dict(self, tmp_path) -> None:
        teams = load_all_teams(teams_dir=tmp_path)
        assert teams == {}

    def test_nonexistent_directory_returns_empty_dict(self) -> None:
        teams = load_all_teams(teams_dir=Path("/nonexistent/path/teams"))
        assert teams == {}


# ---------------------------------------------------------------------------
# get_team_agents — convenience function
# ---------------------------------------------------------------------------

class TestGetTeamAgents:
    def test_returns_agent_list_for_known_team(self) -> None:
        agents = get_team_agents("team-quick", teams_dir=_TEAMS_DIR)
        assert "maya" in agents
        assert "nova" in agents

    def test_returns_empty_list_for_unknown_team(self) -> None:
        agents = get_team_agents("team-does-not-exist", teams_dir=_TEAMS_DIR)
        assert agents == []


# ---------------------------------------------------------------------------
# cleo_router integration — _TEAM_REGISTRY uses YAML
# ---------------------------------------------------------------------------

class TestCleoRouterUsesYamlTeams:
    def test_registry_contains_yaml_teams(self) -> None:
        from revue.core.cleo_router import _TEAM_REGISTRY
        assert "team-full-review" in _TEAM_REGISTRY
        assert "team-quick" in _TEAM_REGISTRY
        assert "team-swift-ios" in _TEAM_REGISTRY

    def test_registry_agents_match_yaml(self) -> None:
        from revue.core.cleo_router import _TEAM_REGISTRY
        quick = _TEAM_REGISTRY.get("team-quick", [])
        assert "maya" in quick
        assert "leo" not in quick

    def test_yaml_team_wins_over_preset(self, tmp_path, monkeypatch) -> None:
        """When YAML defines a team, it overrides the hardcoded TEAM_PRESETS."""
        (tmp_path / "team-custom.yml").write_text(
            "team:\n  id: team-custom\n  name: Custom\n"
            "agents:\n  primary: [maya]\n  secondary: [nova]\n"
        )
        from revue.core import cleo_router, team_loader
        monkeypatch.setattr(team_loader, "_TEAMS_DIR", tmp_path)
        registry = cleo_router._build_team_registry()
        assert "team-custom" in registry
        assert registry["team-custom"] == ["maya", "nova"]
