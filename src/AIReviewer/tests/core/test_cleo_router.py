"""Tests for Cleo routing — team auto-selection and trigger evaluation."""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from AIReviewer.core.cleo_router import (
    DEFAULT_TEAM,
    LARGE_CHANGE_THRESHOLD,
    SECURITY_AGENT_NAME,
    SECURITY_FILE_PATTERNS,
    TEAM_PRESETS,
    TeamSelection,
    evaluate_triggers,
    route,
    select_team,
)
from AIReviewer.core.models import FileChange
from AIReviewer.core.shared_analysis import SharedAnalysisResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fc(path: str = "app.py", change_type: str = "modified") -> FileChange:
    return FileChange(
        file_path=path, change_type=change_type,
        additions=5, deletions=2, diff="",
    )


def _many_files(n: int, ext: str = ".py") -> list[FileChange]:
    return [_fc(f"src/file_{i}{ext}") for i in range(n)]


def _shared(
    risk_areas: list[str] | None = None,
    languages: list[str] | None = None,
) -> SharedAnalysisResult:
    return SharedAnalysisResult(
        languages=languages or ["python"],
        risk_areas=risk_areas or [],
        suggested_agents=["zara", "kai", "maya", "leo"],
        summary="test",
    )


@dataclass
class _FakeConfig:
    """Minimal stand-in for AIConfig — only the field we need."""
    agents_team: str = ""


class _FakeAgent:
    """Minimal agent satisfying AgentProtocol."""

    def __init__(self, name: str, trigger_patterns: list[str] | None = None):
        self.name = name
        self._trigger_patterns = trigger_patterns or []

    @property
    def definition(self) -> _FakeDefinition:
        return _FakeDefinition(trigger_patterns=self._trigger_patterns)

    def analyse(self, changes, shared=None):
        return []


@dataclass
class _FakeDefinition:
    trigger_patterns: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Story 17 — Team auto-selection
# ---------------------------------------------------------------------------


class TestSelectTeam:
    """select_team() tests."""

    def test_default_full_team_small_changeset(self):
        result = select_team([_fc("app.py"), _fc("utils.py")])
        assert result.team_name == DEFAULT_TEAM
        assert set(result.agents) == set(TEAM_PRESETS[DEFAULT_TEAM])
        assert result.security_override is False

    def test_lean_team_large_changeset(self):
        files = _many_files(LARGE_CHANGE_THRESHOLD + 1)
        result = select_team(files)
        assert result.team_name == "team-lean"
        assert "cleo" in result.agents
        assert "nova" in result.agents
        assert "zara" in result.agents
        assert f"{LARGE_CHANGE_THRESHOLD}" in result.reason

    def test_lean_team_boundary_not_triggered(self):
        """Exactly LARGE_CHANGE_THRESHOLD files should NOT trigger lean team."""
        files = _many_files(LARGE_CHANGE_THRESHOLD)
        result = select_team(files)
        assert result.team_name != "team-lean"

    def test_security_override_from_risk_areas(self):
        shared = _shared(risk_areas=["authentication", "database"])
        result = select_team([_fc()], shared=shared)
        assert result.security_override is True
        assert SECURITY_AGENT_NAME in result.agents

    def test_security_override_from_sql_file(self):
        result = select_team([_fc("migrations/001_create_users.sql")])
        assert result.security_override is True
        assert SECURITY_AGENT_NAME in result.agents

    def test_security_override_from_env_file(self):
        result = select_team([_fc(".env")])
        assert result.security_override is True

    def test_security_override_from_secret_in_filename(self):
        result = select_team([_fc("config/app_secrets.json")])
        assert result.security_override is True

    def test_security_override_from_password_in_filename(self):
        result = select_team([_fc("utils/password_hash.py")])
        assert result.security_override is True

    def test_security_override_from_token_in_filename(self):
        result = select_team([_fc("auth/token_manager.py")])
        assert result.security_override is True

    def test_security_override_from_auth_in_filename(self):
        result = select_team([_fc("middleware/auth_handler.py")])
        assert result.security_override is True

    def test_no_security_override_normal_files(self):
        result = select_team([_fc("app.py"), _fc("utils.py")])
        assert result.security_override is False

    def test_config_overrides_team(self):
        config = _FakeConfig(agents_team="team-security-focus")
        result = select_team([_fc()], config=config)  # type: ignore[arg-type]
        assert result.team_name == "team-security-focus"
        assert "team set by config" in result.reason

    def test_config_default_team_does_not_short_circuit(self):
        """Setting config.agents_team to DEFAULT_TEAM should not short-circuit."""
        config = _FakeConfig(agents_team=DEFAULT_TEAM)
        result = select_team([_fc()], config=config)  # type: ignore[arg-type]
        assert result.team_name == DEFAULT_TEAM

    def test_swift_files_select_ios_team(self):
        files = [_fc("Sources/App/ViewController.swift")]
        result = select_team(files)
        assert result.team_name == "team-swift-ios"

    def test_kotlin_files_select_ios_team(self):
        files = [_fc("app/src/main/Activity.kt")]
        result = select_team(files)
        assert result.team_name == "team-swift-ios"

    def test_security_override_added_to_lang_team(self):
        """Language team + security file → Zara added if not already present."""
        files = [_fc("Sources/App.swift"), _fc("config/.env")]
        result = select_team(files)
        assert result.security_override is True
        assert SECURITY_AGENT_NAME in result.agents

    def test_large_changeset_overrides_language_team(self):
        """Size heuristic takes precedence over language detection."""
        files = _many_files(LARGE_CHANGE_THRESHOLD + 1, ext=".swift")
        result = select_team(files)
        assert result.team_name == "team-lean"

    def test_empty_file_list(self):
        result = select_team([])
        assert result.team_name == DEFAULT_TEAM
        assert result.security_override is False


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------


class TestLanguageDetection:
    """Language detection from file extensions."""

    def test_python_detected(self):
        result = select_team([_fc("app.py")])
        # Python doesn't have a special team, so falls back to default
        assert result.team_name == DEFAULT_TEAM

    def test_mixed_extensions_primary_wins(self):
        files = [_fc("a.swift"), _fc("b.swift"), _fc("c.py")]
        result = select_team(files)
        assert result.team_name == "team-swift-ios"

    def test_no_recognised_extension(self):
        result = select_team([_fc("Makefile"), _fc("Dockerfile")])
        assert result.team_name == DEFAULT_TEAM


# ---------------------------------------------------------------------------
# Story 18 — Trigger evaluation
# ---------------------------------------------------------------------------


class TestEvaluateTriggers:
    """evaluate_triggers() tests."""

    def test_no_triggers_always_runs(self):
        agent_def: dict = {}
        assert evaluate_triggers("cleo", [_fc()], agent_def) is True

    def test_file_pattern_match(self):
        agent_def = {"trigger_patterns": ["**/*.py"]}
        assert evaluate_triggers("kai", [_fc("src/app.py")], agent_def) is True

    def test_file_pattern_no_match(self):
        agent_def = {"trigger_patterns": ["**/*.js"]}
        assert evaluate_triggers("kai", [_fc("src/app.py")], agent_def) is False

    def test_nested_triggers_file_patterns(self):
        agent_def = {"triggers": {"file_patterns": ["**/*.ts", "**/*.tsx"]}}
        assert evaluate_triggers("maya", [_fc("src/App.tsx")], agent_def) is True

    def test_nested_triggers_file_patterns_no_match(self):
        agent_def = {"triggers": {"file_patterns": ["**/*.ts"]}}
        assert evaluate_triggers("maya", [_fc("app.py")], agent_def) is False

    def test_language_trigger_match(self):
        agent_def = {"triggers": {"languages": ["python"]}}
        assert evaluate_triggers("kai", [_fc("app.py")], agent_def) is True

    def test_language_trigger_no_match(self):
        agent_def = {"triggers": {"languages": ["rust"]}}
        assert evaluate_triggers("kai", [_fc("app.py")], agent_def) is False

    def test_language_trigger_case_insensitive(self):
        agent_def = {"triggers": {"languages": ["Python"]}}
        assert evaluate_triggers("kai", [_fc("app.py")], agent_def) is True

    def test_multiple_file_patterns_any_match(self):
        agent_def = {"trigger_patterns": ["**/*.py", "**/*.js"]}
        assert evaluate_triggers("maya", [_fc("app.js")], agent_def) is True

    def test_wildcard_pattern_matches_all(self):
        agent_def = {"trigger_patterns": ["**"]}
        assert evaluate_triggers("cleo", [_fc("anything.xyz")], agent_def) is True

    def test_empty_file_changes_with_patterns(self):
        agent_def = {"trigger_patterns": ["**/*.py"]}
        assert evaluate_triggers("kai", [], agent_def) is False

    def test_empty_file_changes_no_triggers(self):
        assert evaluate_triggers("cleo", [], {}) is True


# ---------------------------------------------------------------------------
# Story 18 — Full route() integration
# ---------------------------------------------------------------------------


class TestRoute:
    """route() integration tests."""

    def test_route_filters_agents_by_team(self):
        agents = [
            _FakeAgent("cleo", ["**"]),
            _FakeAgent("zara", ["**/*.py"]),
            _FakeAgent("kai", ["**/*.py"]),
            _FakeAgent("maya", ["**/*.py"]),
            _FakeAgent("leo", ["**/*.py"]),
            _FakeAgent("nova", ["**"]),
        ]
        # Large changeset → lean team (cleo, zara, nova)
        files = _many_files(LARGE_CHANGE_THRESHOLD + 5)
        selection, filtered = route(files, agents)

        assert selection.team_name == "team-lean"
        filtered_names = {a.name for a in filtered}
        assert "cleo" in filtered_names
        assert "zara" in filtered_names
        assert "nova" in filtered_names
        assert "kai" not in filtered_names
        assert "leo" not in filtered_names

    def test_route_full_team_all_agents_run(self):
        agents = [
            _FakeAgent("cleo", ["**"]),
            _FakeAgent("zara", ["**/*.py"]),
            _FakeAgent("kai", ["**/*.py"]),
            _FakeAgent("maya", ["**/*.py"]),
            _FakeAgent("leo", ["**/*.py"]),
            _FakeAgent("nova", ["**"]),
        ]
        files = [_fc("app.py")]
        selection, filtered = route(files, agents)

        assert selection.team_name == DEFAULT_TEAM
        assert len(filtered) == 6

    def test_route_security_override_forces_zara(self):
        agents = [
            _FakeAgent("cleo", ["**"]),
            _FakeAgent("zara", ["**/*.py"]),
            _FakeAgent("nova", ["**"]),
        ]
        files = [_fc("config/.env")]
        selection, filtered = route(files, agents)

        assert selection.security_override is True
        filtered_names = {a.name for a in filtered}
        assert "zara" in filtered_names

    def test_route_trigger_filters_non_matching_agent(self):
        agents = [
            _FakeAgent("cleo", ["**"]),
            _FakeAgent("zara", ["**/*.js"]),  # only JS
            _FakeAgent("nova", ["**"]),
        ]
        files = [_fc("app.py")]  # Python only
        selection, filtered = route(files, agents)

        filtered_names = {a.name for a in filtered}
        assert "cleo" in filtered_names
        assert "nova" in filtered_names
        # Zara's triggers don't match .py, and no security override
        assert "zara" not in filtered_names

    def test_route_security_override_bypasses_trigger_check(self):
        """Zara runs even if trigger patterns don't match, when security_override is True."""
        agents = [
            _FakeAgent("cleo", ["**"]),
            _FakeAgent("zara", ["**/*.js"]),  # triggers only for JS
            _FakeAgent("nova", ["**"]),
        ]
        files = [_fc("migrations/001.sql")]  # SQL triggers security override
        selection, filtered = route(files, agents)

        assert selection.security_override is True
        filtered_names = {a.name for a in filtered}
        assert "zara" in filtered_names

    def test_route_with_config_team(self):
        agents = [
            _FakeAgent("cleo", ["**"]),
            _FakeAgent("zara", ["**/*.py"]),
            _FakeAgent("nova", ["**"]),
        ]
        config = _FakeConfig(agents_team="team-security-focus")
        files = [_fc("app.py")]
        selection, filtered = route(files, agents, config=config)  # type: ignore[arg-type]

        assert selection.team_name == "team-security-focus"
        filtered_names = {a.name for a in filtered}
        assert "cleo" in filtered_names
        assert "zara" in filtered_names
        assert "nova" in filtered_names

    def test_route_with_shared_analysis(self):
        agents = [
            _FakeAgent("cleo", ["**"]),
            _FakeAgent("zara", ["**/*.py"]),
            _FakeAgent("nova", ["**"]),
        ]
        shared = _shared(risk_areas=["injection", "database"])
        files = [_fc("app.py")]
        selection, filtered = route(files, agents, shared=shared)

        assert selection.security_override is True
        assert "zara" in {a.name for a in filtered}

    def test_route_empty_agents_list(self):
        selection, filtered = route([_fc()], [])
        assert filtered == []
        assert selection.team_name == DEFAULT_TEAM

    def test_route_agent_not_in_team_excluded(self):
        """Agent present in available_agents but not in team → excluded."""
        agents = [
            _FakeAgent("cleo", ["**"]),
            _FakeAgent("custom-agent", ["**"]),
        ]
        files = [_fc("app.py")]
        selection, filtered = route(files, agents)

        filtered_names = {a.name for a in filtered}
        assert "cleo" in filtered_names
        assert "custom-agent" not in filtered_names

    def test_route_agent_without_definition(self):
        """Agent without .definition attr should still be evaluated."""

        class _BareAgent:
            name = "cleo"
            def analyse(self, changes, shared=None):
                return []

        agents = [_BareAgent()]
        files = [_fc("app.py")]
        selection, filtered = route(files, agents)

        # No definition → no triggers → always runs (if in team)
        assert len(filtered) == 1


# ---------------------------------------------------------------------------
# TeamSelection dataclass
# ---------------------------------------------------------------------------


class TestTeamSelection:
    """TeamSelection dataclass tests."""

    def test_fields(self):
        ts = TeamSelection(
            team_name="test",
            agents=["a", "b"],
            security_override=False,
            reason="test reason",
        )
        assert ts.team_name == "test"
        assert ts.agents == ["a", "b"]
        assert ts.security_override is False
        assert ts.reason == "test reason"

    def test_equality(self):
        a = TeamSelection("t", ["a"], False, "r")
        b = TeamSelection("t", ["a"], False, "r")
        assert a == b
