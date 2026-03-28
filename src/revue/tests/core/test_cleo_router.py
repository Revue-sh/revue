"""Tests for Cleo routing — team auto-selection and trigger evaluation."""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from revue.core.cleo_router import (
    DEFAULT_TEAM,
    LARGE_CHANGE_THRESHOLD,
    QUICK_THRESHOLD_LINES,
    FULL_REVIEW_THRESHOLD_LINES,
    SECURITY_AGENT_NAME,
    SECURITY_FILE_PATTERNS,
    TEAM_PRESETS,
    TeamSelection,
    evaluate_triggers,
    route,
    select_team,
)
from revue.core.models import FileChange
from revue.core.shared_analysis import SharedAnalysisResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fc(path: str = "app.py", change_type: str = "modified",
        additions: int = 5, deletions: int = 2) -> FileChange:
    return FileChange(
        file_path=path, change_type=change_type,
        additions=additions, deletions=deletions, diff="",
    )


def _many_files(n: int, ext: str = ".py",
                additions: int = 5, deletions: int = 2) -> list[FileChange]:
    return [_fc(f"src/file_{i}{ext}", additions=additions, deletions=deletions)
            for i in range(n)]


def _files_with_lines(total_lines: int, ext: str = ".py") -> list[FileChange]:
    """Create file changes totalling exactly total_lines changed lines."""
    return [_fc(f"src/file.{ext}", additions=total_lines, deletions=0)]


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

    def test_default_full_team_medium_changeset(self):
        """Diff between 50-500 lines with no language match → full review."""
        files = _files_with_lines(100)
        result = select_team(files)
        assert result.team_name == DEFAULT_TEAM
        assert set(result.agents) == set(TEAM_PRESETS[DEFAULT_TEAM])
        assert result.security_override is False

    def test_quick_team_small_diff(self):
        """Diff < 50 lines → team-quick (Maya only)."""
        files = _files_with_lines(QUICK_THRESHOLD_LINES - 1)
        result = select_team(files)
        assert result.team_name == "team-quick"
        assert "maya" in result.agents

    def test_quick_team_boundary_exactly_49_lines(self):
        files = _files_with_lines(49)
        result = select_team(files)
        assert result.team_name == "team-quick"

    def test_quick_team_not_triggered_at_50_lines(self):
        """Exactly 50 lines is NOT < 50 — should NOT be team-quick."""
        files = _files_with_lines(QUICK_THRESHOLD_LINES)
        result = select_team(files)
        assert result.team_name != "team-quick"

    def test_full_review_large_diff(self):
        """Diff > 500 lines → team-full-review."""
        files = _files_with_lines(FULL_REVIEW_THRESHOLD_LINES + 1)
        result = select_team(files)
        assert result.team_name == "team-full-review"
        assert f"{FULL_REVIEW_THRESHOLD_LINES}" in result.reason

    def test_full_review_boundary_exactly_501_lines(self):
        files = _files_with_lines(501)
        result = select_team(files)
        assert result.team_name == "team-full-review"

    def test_lean_team_large_changeset(self):
        """Legacy: large file count still works (team-lean not removed)."""
        files = _many_files(LARGE_CHANGE_THRESHOLD + 1, additions=2, deletions=2)
        # each file = 4 lines, 21 files = 84 lines → between thresholds
        result = select_team(files)
        # Should NOT be team-lean (size heuristic now line-based)
        assert result.team_name != "team-lean"

    def test_lean_team_boundary_not_triggered(self):
        """Legacy compat — file count boundary no longer triggers lean."""
        files = _many_files(LARGE_CHANGE_THRESHOLD, additions=2, deletions=2)
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
        # Use enough lines to skip team-quick threshold
        result = select_team([_fc(additions=60)], config=config)  # type: ignore[arg-type]
        assert result.team_name == DEFAULT_TEAM

    def test_swift_files_select_ios_team(self):
        # Use enough lines to skip team-quick (< 50 would route to team-quick)
        files = [_fc("Sources/App/ViewController.swift", additions=60)]
        result = select_team(files)
        assert result.team_name == "team-swift-ios"

    def test_kotlin_files_select_ios_team(self):
        files = [_fc("app/src/main/Activity.kt", additions=60)]
        result = select_team(files)
        assert result.team_name == "team-swift-ios"

    def test_security_override_bypasses_quick_team(self):
        """Security override prevents team-quick even on tiny diffs."""
        files = [_fc("Sources/App.swift", additions=5), _fc("config/.env", additions=3)]
        result = select_team(files)
        assert result.security_override is True
        assert result.team_name != "team-quick"
        assert SECURITY_AGENT_NAME in result.agents

    def test_security_override_added_to_lang_team(self):
        """Language team + security file → Zara added if not already present."""
        files = [_fc("Sources/App.swift", additions=60), _fc("config/.env", additions=5)]
        result = select_team(files)
        assert result.security_override is True
        assert SECURITY_AGENT_NAME in result.agents

    def test_large_diff_overrides_language_team(self):
        """Line-count size heuristic (>500) takes precedence over language detection."""
        files = [_fc("Sources/App.swift", additions=600)]
        result = select_team(files)
        assert result.team_name == "team-full-review"
        assert "600" in result.reason or "500" in result.reason

    def test_empty_file_list(self):
        """Empty file list → 0 lines → team-quick (below 50 threshold)."""
        result = select_team([])
        # 0 lines < 50 → team-quick, no security override
        assert result.team_name == "team-quick"
        assert result.security_override is False


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------


class TestLanguageDetection:
    """Language detection from file extensions."""

    def test_python_detected(self):
        # Use enough lines to avoid team-quick threshold
        result = select_team([_fc("app.py", additions=60)])
        # Python doesn't have a special team, so falls back to default
        assert result.team_name == DEFAULT_TEAM

    def test_mixed_extensions_primary_wins(self):
        files = [_fc("a.swift", additions=30), _fc("b.swift", additions=30),
                 _fc("c.py", additions=5)]
        result = select_team(files)
        assert result.team_name == "team-swift-ios"

    def test_no_recognised_extension(self):
        result = select_team([_fc("Makefile", additions=60),
                               _fc("Dockerfile", additions=10)])
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
        # Large diff (>500 lines) → full review team
        files = [_fc("app.py", additions=600)]
        selection, filtered = route(files, agents)

        assert selection.team_name == "team-full-review"
        filtered_names = {a.name for a in filtered}
        assert "cleo" in filtered_names
        assert "nova" in filtered_names

    def test_route_full_team_all_agents_run(self):
        agents = [
            _FakeAgent("cleo", ["**"]),
            _FakeAgent("zara", ["**/*.py"]),
            _FakeAgent("kai", ["**/*.py"]),
            _FakeAgent("maya", ["**/*.py"]),
            _FakeAgent("leo", ["**/*.py"]),
            _FakeAgent("nova", ["**"]),
        ]
        # Medium diff (between thresholds, no special language) → full review
        files = [_fc("app.py", additions=100)]
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
        # Medium diff (not quick) — Python only, no security
        files = [_fc("app.py", additions=60)]
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
        files = [_fc("app.py", additions=60)]
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
        files = [_fc("app.py", additions=60)]
        selection, filtered = route(files, agents, shared=shared)

        assert selection.security_override is True
        assert "zara" in {a.name for a in filtered}

    def test_route_empty_agents_list(self):
        selection, filtered = route([_fc(additions=60)], [])
        assert filtered == []
        assert selection.team_name == DEFAULT_TEAM

    def test_route_agent_not_in_team_excluded(self):
        """Agent present in available_agents but not in team → excluded."""
        agents = [
            _FakeAgent("cleo", ["**"]),
            _FakeAgent("custom-agent", ["**"]),
        ]
        # medium diff → full team (cleo is in it, custom-agent is not)
        files = [_fc("app.py", additions=60)]
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
        # medium diff → full review (cleo is in full review team)
        files = [_fc("app.py", additions=60)]
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


# ---------------------------------------------------------------------------
# [76] AC: size heuristic thresholds + team-quick (new tests)
# ---------------------------------------------------------------------------

class TestSizeHeuristicAC:
    """Explicit AC tests for Story 23 size thresholds."""

    def test_49_lines_routes_to_team_quick(self):
        result = select_team(_files_with_lines(49))
        assert result.team_name == "team-quick"
        assert "maya" in result.agents

    def test_50_lines_does_not_route_to_team_quick(self):
        result = select_team(_files_with_lines(50))
        assert result.team_name != "team-quick"

    def test_501_lines_routes_to_team_full_review(self):
        result = select_team(_files_with_lines(501))
        assert result.team_name == "team-full-review"
        assert "500" in result.reason or "501" in result.reason

    def test_500_lines_does_not_route_to_full_review_by_size(self):
        """Exactly 500 lines is NOT > 500 — size heuristic should not trigger."""
        result = select_team(_files_with_lines(500))
        assert result.team_name != "team-full-review" or "language" in result.reason or "default" in result.reason

    def test_security_override_trumps_size_heuristic(self):
        """Security override always wins — even tiny diffs get Zara."""
        result = select_team([_fc(".env", additions=3)])
        assert result.security_override is True
        assert result.team_name != "team-quick"
        assert SECURITY_AGENT_NAME in result.agents

    def test_team_quick_has_maya(self):
        """PRD specifies team-quick is Maya only (+ nova for consolidation)."""
        from revue.core.cleo_router import TEAM_PRESETS
        assert "maya" in TEAM_PRESETS["team-quick"]

    def test_team_quick_excludes_expensive_agents(self):
        """team-quick should not include heavy agents like leo, kai, zara."""
        from revue.core.cleo_router import TEAM_PRESETS
        quick = TEAM_PRESETS["team-quick"]
        assert "leo" not in quick
        assert "kai" not in quick
        assert "zara" not in quick

    def test_constants_match_spec(self):
        """QUICK_THRESHOLD_LINES=50 and FULL_REVIEW_THRESHOLD_LINES=500 per AC."""
        assert QUICK_THRESHOLD_LINES == 50
        assert FULL_REVIEW_THRESHOLD_LINES == 500
