"""
Agent definition loader — parse YAML/Markdown agent definition files (Story [016]).

SRP: loading/parsing only. Agent execution is in agent_runner.py.
OCP: new agent definition formats can be added by implementing AgentDefinitionParser Protocol.
DIP: AgentRunner depends on AgentProtocol, not concrete loaded agent classes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from .ai_client import AIClient
    from .shared_analysis import SharedAnalysisResult

from .models import FileChange, AIReview


# ---------------------------------------------------------------------------
# Agent definition dataclass
# ---------------------------------------------------------------------------

@dataclass
class AgentDefinition:
    """Parsed agent definition from YAML or Markdown front-matter."""
    name: str                           # e.g. "zara"
    display_name: str                   # e.g. "Zara (Security Analyst)"
    role: str                           # one-liner role description
    system_prompt: str                  # full system prompt for the AI call
    focus_areas: list[str] = field(default_factory=list)
    trigger_patterns: list[str] = field(default_factory=list)  # fnmatch patterns to trigger
    severity_default: str = "minor"
    enabled: bool = True
    version: str = "1.0"


# ---------------------------------------------------------------------------
# Loaded agent — wraps definition + AI client (implements AgentProtocol)
# ---------------------------------------------------------------------------

class LoadedAgent:
    """
    A runnable agent loaded from a definition file.
    Implements AgentProtocol from agent_runner.py.
    Depends on AIClient Protocol (DIP).
    """

    def __init__(self, definition: AgentDefinition, client: "AIClient") -> None:
        self._def = definition
        self._client = client

    @property
    def name(self) -> str:
        return self._def.name

    @property
    def definition(self) -> AgentDefinition:
        return self._def

    def analyse(
        self,
        changes: list[FileChange],
        shared: "SharedAnalysisResult | None" = None,
    ) -> list[AIReview]:
        """
        Run this agent's analysis on the provided changes.

        Builds a prompt from the system_prompt + diff content,
        calls the AI client, parses the JSON response.
        Returns empty list on any failure (graceful degradation).
        """
        import json

        diff_text = _build_diff_text(changes)
        shared_context = _build_shared_context(shared) if shared else ""
        prompt = (
            f"{self._def.system_prompt}\n\n"
            f"{shared_context}"
            f"Review the following diff:\n\n{diff_text}\n\n"
            f"Respond with a JSON array of findings:\n"
            f'[{{"file_path": "...", "line_number": 1, "severity": "minor|major|critical|suggestion", '
            f'"issue": "...", "suggestion": "...", "confidence": 0.0-1.0}}]'
        )
        try:
            raw = self._client.complete([{"role": "user", "content": prompt}])
            data = json.loads(raw)
            if not isinstance(data, list):
                data = data.get("findings", []) if isinstance(data, dict) else []
            reviews = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                reviews.append(AIReview(
                    file_path=item.get("file_path", "unknown"),
                    line_number=int(item.get("line_number", 0)),
                    severity=item.get("severity", self._def.severity_default),
                    issue=item.get("issue", ""),
                    suggestion=item.get("suggestion", ""),
                    confidence=float(item.get("confidence", 0.7)),
                    category=self._def.name,
                ))
            return reviews
        except Exception:
            return []


# ---------------------------------------------------------------------------
# Parser Protocol (OCP — new formats implement this)
# ---------------------------------------------------------------------------

class AgentDefinitionParser(Protocol):
    """Protocol for agent definition file parsers."""
    def can_parse(self, path: Path) -> bool: ...
    def parse(self, path: Path) -> AgentDefinition: ...


# ---------------------------------------------------------------------------
# YAML parser
# ---------------------------------------------------------------------------

class YAMLAgentParser:
    """Parse agent definitions from .yaml / .yml files."""

    def can_parse(self, path: Path) -> bool:
        return path.suffix in {".yaml", ".yml"}

    def parse(self, path: Path) -> AgentDefinition:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return _dict_to_definition(data, source=str(path))


# ---------------------------------------------------------------------------
# Markdown front-matter parser
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)", re.DOTALL)


class MarkdownAgentParser:
    """Parse agent definitions from .md files with YAML front-matter."""

    def can_parse(self, path: Path) -> bool:
        return path.suffix == ".md"

    def parse(self, path: Path) -> AgentDefinition:
        text = path.read_text()
        match = _FRONTMATTER_RE.match(text)
        if not match:
            raise ValueError(f"No YAML front-matter found in {path}")
        front_matter = yaml.safe_load(match.group(1)) or {}
        body = match.group(2).strip()
        # Body becomes system_prompt if not specified in front-matter
        if "system_prompt" not in front_matter and body:
            front_matter["system_prompt"] = body
        return _dict_to_definition(front_matter, source=str(path))


# ---------------------------------------------------------------------------
# Agent loader
# ---------------------------------------------------------------------------

_DEFAULT_PARSERS: list[AgentDefinitionParser] = [
    YAMLAgentParser(),
    MarkdownAgentParser(),
]


def load_agent_definition(path: str | Path) -> AgentDefinition:
    """
    Load a single agent definition from a YAML or Markdown file.
    Raises ValueError if no parser can handle the file.
    """
    p = Path(path)
    for parser in _DEFAULT_PARSERS:
        if parser.can_parse(p):
            return parser.parse(p)
    raise ValueError(f"No parser for agent definition file: {path}")


def load_agents_from_dir(
    directory: str | Path,
    client: "AIClient",
    parsers: list[AgentDefinitionParser] | None = None,
) -> list[LoadedAgent]:
    """
    Load all agent definitions from a directory.

    - Scans for .yaml, .yml, .md files
    - Skips disabled agents
    - Returns list of LoadedAgent instances ready to run
    """
    active_parsers = parsers or _DEFAULT_PARSERS
    dir_path = Path(directory)
    agents: list[LoadedAgent] = []

    for file_path in sorted(dir_path.iterdir()):
        for parser in active_parsers:
            if parser.can_parse(file_path):
                try:
                    definition = parser.parse(file_path)
                    if definition.enabled:
                        agents.append(LoadedAgent(definition, client))
                except Exception:
                    pass  # skip unparseable files silently
                break

    return agents


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dict_to_definition(data: dict, source: str = "") -> AgentDefinition:
    name = data.get("name", "")
    if not name:
        raise ValueError(f"Agent definition missing required 'name' field in {source}")
    return AgentDefinition(
        name=name,
        display_name=data.get("display_name", name.title()),
        role=data.get("role", ""),
        system_prompt=data.get("system_prompt", ""),
        focus_areas=list(data.get("focus_areas", [])),
        trigger_patterns=list(data.get("trigger_patterns", [])),
        severity_default=data.get("severity_default", "minor"),
        enabled=bool(data.get("enabled", True)),
        version=str(data.get("version", "1.0")),
    )


def _build_diff_text(changes: list[FileChange]) -> str:
    return "\n\n".join(
        f"File: {fc.file_path}\n{fc.diff}" for fc in changes
    )


def _build_shared_context(shared: "SharedAnalysisResult") -> str:
    return (
        f"Context from shared analysis:\n"
        f"Languages: {', '.join(shared.languages)}\n"
        f"Risk areas: {', '.join(shared.risk_areas)}\n"
        f"Summary: {shared.summary}\n\n"
    )
