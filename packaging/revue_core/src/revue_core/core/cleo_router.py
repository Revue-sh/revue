"""
Cleo routing — team auto-selection and agent trigger evaluation (Stories 017–018).

SRP: routing decisions only. Execution is in agent_runner.py.
OCP: new team configs or trigger rules are data-driven, not coded here.
DIP: depends on AgentProtocol, not concrete agent classes.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from .ai_config import AIConfig
    from .shared_analysis import SharedAnalysisResult

from .models import FileChange
from .team_loader import load_all_teams, TeamConfig
from revue_core.core.logging_channels import Log

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Agents that are infrastructure (routing/consolidation) not reviewers
_INFRASTRUCTURE_AGENTS = frozenset({"cleo", "nova", "vex"})

SECURITY_FILE_PATTERNS: frozenset[str] = frozenset({
    "*.sql",
    "*.env",
    "*secret*",
    "*password*",
    "*token*",
    "*auth*",
    "*.pem",
    "*.key",
    "*.cert",
})

# Line-count thresholds for size heuristic (AC Story 23)
# diff < QUICK_THRESHOLD_LINES  → team-quick  (Maya only — fast, low cost)
# diff > FULL_REVIEW_THRESHOLD_LINES → team-full-review
# between the two → language/default selection
QUICK_THRESHOLD_LINES: int = 50
FULL_REVIEW_THRESHOLD_LINES: int = 500

# Kept for backward compat — was file-count based; superseded by line-count above
LARGE_CHANGE_THRESHOLD: int = 20

# Extension → language mapping (mirrors shared_analysis._EXT_TO_LANG)
_EXT_TO_LANG: dict[str, str] = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".tsx": "typescript", ".jsx": "javascript", ".rb": "ruby",
    ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin",
    ".swift": "swift", ".cs": "csharp", ".cpp": "cpp", ".c": "c",
    ".h": "c", ".hpp": "cpp", ".php": "php", ".scala": "scala",
    ".sh": "shell", ".bash": "shell",
}

# Language → preferred team config key
_LANG_TO_TEAM: dict[str, str] = {
    "swift": "team-swift-ios",
    "kotlin": "team-swift-ios",
}

# Team presets: team_name → list of agent names
TEAM_PRESETS: dict[str, list[str]] = {
    "team-full-review": ["cleo", "zara", "kai", "maya", "leo", "nova", "vex"],
    "team-quick": ["maya", "nova", "vex"],          # trivial/small diffs — Maya only
    "team-lean": ["cleo", "zara", "nova", "vex"],   # legacy — kept for compat
    "team-swift-ios": ["cleo", "zara", "maya", "nova", "vex"],
    "team-security-focus": ["cleo", "zara", "nova", "vex"],
}

DEFAULT_TEAM: str = "team-full-review"

SECURITY_AGENT_NAME: str = "zara"

# ---------------------------------------------------------------------------
# YAML-backed team registry — loaded at import time, falls back to TEAM_PRESETS
# ---------------------------------------------------------------------------

def _build_team_registry() -> dict[str, list[str]]:
    """Load team agent lists from YAML files, merged with TEAM_PRESETS fallback.

    YAML definitions take precedence over hardcoded TEAM_PRESETS when both
    exist for the same team ID.
    """
    registry = dict(TEAM_PRESETS)  # start from hardcoded fallback
    try:
        yaml_teams = load_all_teams()
        for team_id, config in yaml_teams.items():
            registry[team_id] = config.agents
    except Exception as exc:  # pragma: no cover
        import logging
        Log.agent.warning("Failed to load team YAMLs: %s", exc)
    return registry


# Populated once at import. Tests can monkey-patch _TEAM_REGISTRY directly.
_TEAM_REGISTRY: dict[str, list[str]] = _build_team_registry()

SECURITY_RISK_AREAS: frozenset[str] = frozenset({
    "authentication", "authorisation", "authorization",
    "injection", "cryptographic", "secrets", "credentials",
})

# ---------------------------------------------------------------------------
# AgentProtocol — mirrors agent_runner.AgentProtocol
# ---------------------------------------------------------------------------


class AgentProtocol(Protocol):
    """Interface all specialist agents must implement."""

    name: str

    def analyse(
        self,
        changes: list[FileChange],
        shared: "SharedAnalysisResult | None" = None,
    ) -> list: ...


# ---------------------------------------------------------------------------
# TeamSelection dataclass
# ---------------------------------------------------------------------------


@dataclass
class TeamSelection:
    """Result of the team auto-selection algorithm."""

    team_name: str
    agents: list[str]
    security_override: bool
    reason: str
    skip_review: bool = False
    algorithm_filtered_agents: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def select_team(
    file_changes: list[FileChange],
    shared: "SharedAnalysisResult | None" = None,
    config: "AIConfig | None" = None,
) -> TeamSelection:
    """
    Select the review team based on file changes and shared analysis.

    Algorithm (two-step):
    1. Security override — if shared analysis flags critical risk areas OR any
       file matches security patterns, always include Zara.
    2. Size heuristic — >LARGE_CHANGE_THRESHOLD files → lean team; otherwise full.
    3. Language detection — map primary language to a specialised team config.
    """
    security_override = _needs_security_override(file_changes, shared)

    # If config explicitly sets a team, honour it
    if config is not None and config.agents_team and config.agents_team != DEFAULT_TEAM:
        preset = _TEAM_REGISTRY.get(config.agents_team, _TEAM_REGISTRY.get(DEFAULT_TEAM, []))
        agents = list(preset)
        if security_override and SECURITY_AGENT_NAME not in agents:
            agents.append(SECURITY_AGENT_NAME)
        return TeamSelection(
            team_name=config.agents_team,
            agents=agents,
            security_override=security_override,
            reason=f"team set by config: {config.agents_team}",
        )

    # Size heuristic (line-count based — AC Story 23)
    # Security override already applied above — size check is subordinate
    total_lines = sum(fc.additions + fc.deletions for fc in file_changes)

    if total_lines < QUICK_THRESHOLD_LINES and not security_override:
        agents = list(_TEAM_REGISTRY.get("team-quick", TEAM_PRESETS.get("team-quick", [])))
        return TeamSelection(
            team_name="team-quick",
            agents=agents,
            security_override=False,
            reason=f"small diff ({total_lines} lines < {QUICK_THRESHOLD_LINES} — quick review)",
        )

    if total_lines > FULL_REVIEW_THRESHOLD_LINES:
        agents = list(_TEAM_REGISTRY.get("team-full-review", TEAM_PRESETS.get("team-full-review", [])))
        if security_override and SECURITY_AGENT_NAME not in agents:
            agents.append(SECURITY_AGENT_NAME)
        return TeamSelection(
            team_name="team-full-review",
            agents=agents,
            security_override=security_override,
            reason=f"large diff ({total_lines} lines > {FULL_REVIEW_THRESHOLD_LINES} — full review)",
        )

    # Language-based team selection
    primary_lang = _detect_primary_language(file_changes)
    lang_team = _LANG_TO_TEAM.get(primary_lang, "") if primary_lang else ""
    if lang_team and lang_team in TEAM_PRESETS:
        agents = list(_TEAM_REGISTRY.get(lang_team, TEAM_PRESETS.get(lang_team, [])))
        if security_override and SECURITY_AGENT_NAME not in agents:
            agents.append(SECURITY_AGENT_NAME)
        return TeamSelection(
            team_name=lang_team,
            agents=agents,
            security_override=security_override,
            reason=f"language-based team for {primary_lang}",
        )

    # Default: full review
    agents = list(_TEAM_REGISTRY.get(DEFAULT_TEAM, TEAM_PRESETS.get(DEFAULT_TEAM, [])))
    if security_override and SECURITY_AGENT_NAME not in agents:
        agents.append(SECURITY_AGENT_NAME)
    return TeamSelection(
        team_name=DEFAULT_TEAM,
        agents=agents,
        security_override=security_override,
        reason="default full review team",
    )


def evaluate_triggers(
    agent_name: str,
    file_changes: list[FileChange],
    agent_def: dict,
) -> bool:
    """
    Return True if the agent should run given the file changes.

    Trigger matching rules:
    - Check triggers.file_patterns (glob-style) against changed file paths.
    - Check triggers.languages against detected languages.
    - If agent has no triggers defined → always runs.
    - Security agents always run if security_override context applies (caller checks).
    """
    triggers = agent_def.get("triggers", {})
    file_patterns: list[str] = triggers.get("file_patterns", [])
    trigger_languages: list[str] = triggers.get("languages", [])

    # Also check top-level trigger_patterns (used by AgentDefinition)
    if not file_patterns:
        file_patterns = agent_def.get("trigger_patterns", [])

    # No triggers defined → always run
    if not file_patterns and not trigger_languages:
        return True

    # Check file pattern triggers
    if file_patterns:
        for fc in file_changes:
            for pattern in file_patterns:
                if _match_glob(fc.file_path, pattern):
                    return True

    # Check language triggers
    if trigger_languages:
        detected = _detect_languages(file_changes)
        for lang in trigger_languages:
            if lang.lower() in detected:
                return True

    return False


def assign_files_to_agents(
    agent_names: list[str],
    file_changes: list[FileChange],
) -> dict[str, list[str]]:
    """Distribute file paths evenly across agents using round-robin.

    Returns a dict mapping agent_name -> list of file paths.
    Used by the pipeline fallback cascade (REVUE-117) to build per-agent contexts
    when rate limits prevent sending the full diff to every agent.

    If *agent_names* is empty, returns an empty dict.
    If *file_changes* is empty, all agents map to an empty list.
    """
    if not agent_names:
        return {}
    assignment: dict[str, list[str]] = {name: [] for name in agent_names}
    for i, fc in enumerate(file_changes):
        agent = agent_names[i % len(agent_names)]
        assignment[agent].append(fc.file_path)
    return assignment


def route(
    file_changes: list[FileChange],
    available_agents: list[AgentProtocol],
    shared: "SharedAnalysisResult | None" = None,
    config: "AIConfig | None" = None,
) -> tuple[TeamSelection, list[AgentProtocol]]:
    """
    Full routing: combine team selection with trigger evaluation.

    Returns (team_selection, filtered_agents_to_run).

    AC1 (REVUE-166): guarantees ≥1 non-infrastructure agent in filtered list
    for any diff, ensuring YAML/Markdown-only diffs are not routed to zero reviewers.
    """
    selection = select_team(file_changes, shared, config)
    security_override = selection.security_override

    filtered: list[AgentProtocol] = []
    for agent in available_agents:
        # Agent must be in the selected team
        if agent.name not in selection.agents:
            continue

        # Security agent always runs under security override
        if security_override and agent.name == SECURITY_AGENT_NAME:
            filtered.append(agent)
            continue

        # Build agent_def dict for trigger evaluation
        agent_def = _extract_agent_def(agent)
        if evaluate_triggers(agent.name, file_changes, agent_def):
            filtered.append(agent)

    # AC1 (REVUE-166): Guarantee rule — ensure ≥1 non-infrastructure reviewer
    # If only infrastructure agents remain, inject non-infra agents from available_agents
    # that pass trigger evaluation
    non_infra_in_filtered = [a for a in filtered if a.name not in _INFRASTRUCTURE_AGENTS]
    if not non_infra_in_filtered:
        # Find non-infra agents from available_agents that are in the selected team
        # AND pass trigger evaluation for the given file changes
        for agent in available_agents:
            if agent.name not in _INFRASTRUCTURE_AGENTS and agent.name in selection.agents:
                agent_def = _extract_agent_def(agent)
                if evaluate_triggers(agent.name, file_changes, agent_def):
                    filtered.append(agent)

    # REVUE-170: Apply AI-signal refinement (AC2–AC4) after floor guarantee
    selection.algorithm_filtered_agents = [a.name for a in filtered]
    filtered = _apply_ai_routing_signal(filtered, available_agents, shared, file_changes)

    return selection, filtered


def _agent_matches_ai_suggestion(agent_name: str, ai_suggestions: list[str]) -> bool:
    """Return True if agent_name matches any AI-suggested name via 3-way substring match.

    Mirrors _extract_cleo_file_assignments() in pipeline.py to handle LLM name variation:
    exact match, canonical-in-display ("zara" in "zara security"), or display-in-canonical.
    """
    canonical = agent_name.lower()
    for suggestion in ai_suggestions:
        if not isinstance(suggestion, str):
            continue
        ai = suggestion.lower()
        if canonical == ai or canonical in ai or ai in canonical:
            return True
    return False


def _apply_ai_routing_signal(
    filtered: "list[AgentProtocol]",
    available_agents: "list[AgentProtocol]",
    shared: "SharedAnalysisResult | None",
    file_changes: "list[FileChange]",
) -> "list[AgentProtocol]":
    """Refine the algorithm's agent list using the AI's routing signal (REVUE-170).

    AC4 bail-out: returns filtered unchanged when shared is None, has an error,
    has no orchestrator_response, or has empty selected_agents.
    AC3: if the AI signal would produce no non-infra reviewers, the original
    algorithm result is kept (floor guarantee preserved).
    """
    if (
        shared is None
        or shared.error
        or shared.orchestrator_response is None
        or not shared.orchestrator_response.selected_agents
    ):
        return filtered  # AC4: algorithm result unchanged

    ai_suggestions: list[str] = [
        a.name for a in shared.orchestrator_response.selected_agents
    ]

    # Always keep infrastructure agents from the algorithm's filtered set
    infra = [a for a in filtered if a.name in _INFRASTRUCTURE_AGENTS]

    # Non-infra from filtered that the AI also suggested (3-way substring match)
    ai_non_infra = [
        a for a in filtered
        if a.name not in _INFRASTRUCTURE_AGENTS
        and _agent_matches_ai_suggestion(a.name, ai_suggestions)
    ]

    # Admit non-infra agents from available_agents that the AI suggested but the
    # algorithm excluded — only if they also pass trigger evaluation (consistent
    # with the floor guarantee block which calls evaluate_triggers before injecting)
    filtered_names = frozenset(a.name for a in filtered)
    for agent in available_agents:
        if (
            agent.name not in _INFRASTRUCTURE_AGENTS
            and _agent_matches_ai_suggestion(agent.name, ai_suggestions)
            and agent.name not in filtered_names
        ):
            agent_def = _extract_agent_def(agent)
            if evaluate_triggers(agent.name, file_changes, agent_def):
                ai_non_infra.append(agent)

    # AC3: if AI signal yields no non-infra reviewers, keep the algorithm set
    if not ai_non_infra:
        return filtered

    return infra + ai_non_infra


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _needs_security_override(
    file_changes: list[FileChange],
    shared: "SharedAnalysisResult | None",
) -> bool:
    """Determine if the security override should be activated."""
    # Check shared analysis risk areas for critical security concerns
    if shared is not None:
        for area in shared.risk_areas:
            if area.lower() in SECURITY_RISK_AREAS:
                return True

    # Check file paths against security patterns
    for fc in file_changes:
        filename = fc.file_path.rsplit("/", 1)[-1] if "/" in fc.file_path else fc.file_path
        for pattern in SECURITY_FILE_PATTERNS:
            if fnmatch.fnmatch(filename.lower(), pattern):
                return True

    return False


def _detect_primary_language(file_changes: list[FileChange]) -> str:
    """Detect the most common language from file extensions."""
    counts: dict[str, int] = {}
    for fc in file_changes:
        ext = _get_extension(fc.file_path)
        lang = _EXT_TO_LANG.get(ext)
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    if not counts:
        return ""
    return max(counts, key=counts.get)  # type: ignore[arg-type]


def _detect_languages(file_changes: list[FileChange]) -> set[str]:
    """Detect all languages present in the file changes."""
    langs: set[str] = set()
    for fc in file_changes:
        ext = _get_extension(fc.file_path)
        lang = _EXT_TO_LANG.get(ext)
        if lang:
            langs.add(lang)
    return langs


def _get_extension(file_path: str) -> str:
    """Extract file extension including the dot."""
    if "." not in file_path:
        return ""
    return "." + file_path.rsplit(".", 1)[-1]


def _match_glob(file_path: str, pattern: str) -> bool:
    """
    Match a file path against a glob pattern, handling ``**/`` recursion.

    Python's fnmatch does not treat ``**`` as a recursive directory wildcard.
    This helper strips leading ``**/`` and matches against both the full path
    and the filename alone, so ``**/*.py`` matches ``app.py`` and ``src/app.py``.
    """
    if fnmatch.fnmatch(file_path, pattern):
        return True
    if "**" in pattern:
        suffix = pattern.replace("**/", "")
        basename = file_path.rsplit("/", 1)[-1]
        if fnmatch.fnmatch(basename, suffix) or fnmatch.fnmatch(file_path, suffix):
            return True
    return False


def _extract_agent_def(agent: AgentProtocol) -> dict:
    """
    Extract trigger information from an agent.

    Supports LoadedAgent (with .definition) and plain dicts/objects.
    """
    defn = getattr(agent, "definition", None)
    if defn is not None:
        return {
            "trigger_patterns": getattr(defn, "trigger_patterns", []),
            "triggers": {},
        }
    return {}
