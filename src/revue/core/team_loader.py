"""
Team configuration loader — parse declarative YAML team definition files.

Each team is defined as a YAML file in the src/revue/teams/ directory.
This module provides:
  - TeamConfig: dataclass holding all team metadata
  - load_team(team_id): load a single team by ID
  - load_all_teams(): load every team file in the teams directory
  - get_team_agents(team_id): convenience — returns agent list for a team

Schema (team YAML):
  team:
    name: str
    id:   str          (must match filename stem, e.g. team-swift-ios)
    icon: str          (emoji)
    description: str
    when_to_use: str
    timeout_seconds: int  (default 90)

  agents:
    primary:   list[str]
    secondary: list[str]   (optional)

  triggers:
    languages:     list[str]   (detected file languages)
    file_patterns: list[str]   (glob patterns)
    keywords:      list[str]   (keywords in diff text)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from revue.core.logging_channels import Log

# Default directory — relative to this file's location
_TEAMS_DIR = Path(__file__).parent.parent / "teams"

REQUIRED_TEAM_FIELDS = ("name", "id")


@dataclass
class TeamConfig:
    """Parsed representation of a team YAML definition."""
    id: str
    name: str
    icon: str = "🔍"
    description: str = ""
    when_to_use: str = ""
    timeout_seconds: int = 90
    primary_agents: list[str] = field(default_factory=list)
    secondary_agents: list[str] = field(default_factory=list)
    trigger_languages: list[str] = field(default_factory=list)
    trigger_file_patterns: list[str] = field(default_factory=list)
    trigger_keywords: list[str] = field(default_factory=list)

    @property
    def agents(self) -> list[str]:
        """All agents: primary + secondary (deduplicated, order preserved)."""
        seen: set[str] = set()
        result: list[str] = []
        for a in self.primary_agents + self.secondary_agents:
            if a not in seen:
                seen.add(a)
                result.append(a)
        return result


def _parse_team_yaml(raw: dict, source_path: Path) -> TeamConfig:
    """Parse a raw YAML dict into a TeamConfig. Raises ValueError on bad schema."""
    team_section: dict = raw.get("team", {})
    if not team_section:
        raise ValueError(f"Missing 'team:' section in {source_path}")

    for required in REQUIRED_TEAM_FIELDS:
        if not team_section.get(required):
            raise ValueError(
                f"Team file {source_path} missing required field 'team.{required}'"
            )

    agents_section: dict = raw.get("agents", {})
    triggers_section: dict = raw.get("triggers", {})

    return TeamConfig(
        id=team_section["id"],
        name=team_section["name"],
        icon=team_section.get("icon", "🔍"),
        description=(team_section.get("description") or "").strip(),
        when_to_use=(team_section.get("when_to_use") or "").strip(),
        timeout_seconds=int(team_section.get("timeout_seconds", 90)),
        primary_agents=list(agents_section.get("primary", [])),
        secondary_agents=list(agents_section.get("secondary", [])),
        trigger_languages=list(triggers_section.get("languages", [])),
        trigger_file_patterns=list(triggers_section.get("file_patterns", [])),
        trigger_keywords=list(triggers_section.get("keywords", [])),
    )


def load_team(team_id: str, teams_dir: Path | None = None) -> TeamConfig:
    """Load a single team definition by ID.

    Args:
        team_id:   Team identifier, e.g. ``"team-swift-ios"``.
        teams_dir: Override directory (default: src/revue/teams/).

    Returns:
        TeamConfig instance.

    Raises:
        FileNotFoundError: If ``{team_id}.yml`` does not exist.
        ValueError: If the YAML schema is invalid.
    """
    directory = teams_dir or _TEAMS_DIR
    path = directory / f"{team_id}.yml"
    if not path.exists():
        raise FileNotFoundError(f"Team definition not found: {path}")

    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    config = _parse_team_yaml(raw, path)

    # Validate id matches filename
    if config.id != team_id:
        raise ValueError(
            f"Team file {path}: team.id '{config.id}' does not match filename '{team_id}'"
        )

    return config


def load_all_teams(teams_dir: Path | None = None) -> dict[str, TeamConfig]:
    """Load all team YAML files from the teams directory.

    Returns:
        Dict mapping team_id → TeamConfig. Skips files that fail to parse
        (logs a warning) so a single bad file doesn't break everything.
    """
    directory = teams_dir or _TEAMS_DIR
    teams: dict[str, TeamConfig] = {}

    if not directory.exists():
        Log.agent.warning("Teams directory does not exist: %s", directory)
        return teams

    for path in sorted(directory.glob("team-*.yml")):
        team_id = path.stem
        try:
            config = load_team(team_id, teams_dir=directory)
            teams[team_id] = config
        except Exception as exc:
            Log.agent.warning("Skipping invalid team file %s: %s", path, exc)

    return teams


def get_team_agents(team_id: str, teams_dir: Path | None = None) -> list[str]:
    """Convenience: return agent list for a team ID.

    Falls back to an empty list if the team file is not found, so callers
    can degrade gracefully to the hardcoded TEAM_PRESETS fallback in
    cleo_router.py.
    """
    try:
        config = load_team(team_id, teams_dir=teams_dir)
        return config.agents
    except (FileNotFoundError, ValueError) as exc:
        Log.agent.warning("get_team_agents(%r) failed: %s", team_id, exc)
        return []
