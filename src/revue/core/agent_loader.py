"""
Agent definition loader — parse YAML/Markdown agent definition files (Story [016]).

SRP: loading/parsing only. Agent execution is in agent_runner.py.
OCP: new agent definition formats can be added by implementing AgentDefinitionParser Protocol.
DIP: AgentRunner depends on AgentProtocol, not concrete loaded agent classes.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from .ai_client import AIClient
    from .ai_config import AIConfig
    from .shared_analysis import SharedAnalysisResult

from .models import FileChange, AIReview

logger = logging.getLogger(__name__)

# Four canonical category values that cli._CATEGORY_MAP recognises.
_KNOWN_CATEGORIES: frozenset[str] = frozenset({
    "architecture", "security", "performance", "code-quality"
})

# Fallback canonical category keyed by agent definition name.
# Used when the AI omits the category field or returns an unrecognised string,
# preventing agent names from leaking into the summary Quality Breakdown.
_AGENT_CANONICAL_CATEGORY: dict[str, str] = {
    "leo": "architecture",
    "zara": "security",
    "kai": "performance",
    "maya": "code-quality",
}


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


def _normalise_category(raw: str, agent_name: str) -> str:
    """Return a canonical category string safe for cli._CATEGORY_MAP lookup.

    If *raw* (what the AI returned) is already a known canonical value, use it.
    Otherwise fall back to the agent's own canonical from _AGENT_CANONICAL_CATEGORY,
    defaulting to 'code-quality' for unknown agents.
    """
    normalised = raw.lower().strip()
    if normalised in _KNOWN_CATEGORIES:
        return normalised
    return _AGENT_CANONICAL_CATEGORY.get(agent_name, "code-quality")


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
        import hashlib
        import json

        diff_text = _build_diff_text(changes)
        shared_context = _build_shared_context(shared) if shared else ""
        _INSTRUCTIONS = (
            "Respond with a JSON array of findings (no markdown fences, raw JSON only):\n"
            '[{"file_path": "...", "line_number": 1, "severity": "high|medium|low|info", '
            '"issue": "...", "suggestion": "...", "confidence": 0.0-1.0, "category": "architecture|security|performance|code-quality"}]'
        )

        # Severity vocabulary used by revue agents → cli.py display names
        _SEV_MAP = {
            "critical": "high",
            "major": "medium",
            "minor": "low",
            "suggestion": "info",
            # Pass-through for agents already using the display vocab
            "high": "high",
            "medium": "medium",
            "low": "low",
            "info": "info",
        }
        # Stable 16-char routing key for this diff — passed as prompt_cache_key
        # to OpenAI-compatible clients so re-reviews of the same PR land on the
        # same cache server and hit the cached prefix. Anthropic ignores it.
        diff_hash = hashlib.sha256(diff_text.encode()).hexdigest()[:16]
        try:
            user_prompt = (
                f"{shared_context}"
                f"Review the following diff:\n\n{diff_text}\n\n"
                f"{_INSTRUCTIONS}"
            )
            raw = self._client.complete(
                [{"role": "user", "content": user_prompt}],
                system=self._def.system_prompt,
                cache_key=diff_hash,
            )
            print(
                f"[revue]     [{self._def.name}] raw response "
                f"({len(raw)} chars, starts: {raw[:80]!r})",
                flush=True,
            )
            # Strip markdown code fences that LLMs often wrap responses in
            clean = raw.strip()
            if clean.startswith("```"):
                clean = "\n".join(clean.split("\n")[1:])
            if clean.endswith("```"):
                clean = "\n".join(clean.split("\n")[:-1])
            clean = clean.strip()
            data = json.loads(clean)
            if not isinstance(data, list):
                data = data.get("findings", []) if isinstance(data, dict) else []
            reviews = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                raw_sev = item.get("severity", self._def.severity_default).lower()
                severity = _SEV_MAP.get(raw_sev, "low")
                reviews.append(AIReview(
                    file_path=item.get("file_path", "unknown"),
                    line_number=int(item.get("line_number", 0)),
                    severity=severity,
                    issue=item.get("issue", ""),
                    suggestion=item.get("suggestion", ""),
                    confidence=float(item.get("confidence", 0.7)),
                    category=_normalise_category(
                        item.get("category", ""), self._def.name
                    ),
                    agent_name=self._def.name,
                ))
            print(
                f"[revue]     [{self._def.name}] parsed {len(reviews)} finding(s)",
                flush=True,
            )
            return reviews
        except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
            # Non-fatal: bad response shape/content — degrade gracefully.
            # Raw response is NOT logged — it may contain credentials or sensitive data.
            # The exception type and message are sufficient for diagnosis.
            print(
                f"[revue]     [{self._def.name}] response parse error "
                f"({type(exc).__name__}): {exc}",
                flush=True,
            )
            return []
        # All other exceptions (HTTP errors, network failures, auth errors) propagate
        # so agent_runner can correctly mark this agent as failed (success=False)


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
# Custom agent loading (Story [030])
# ---------------------------------------------------------------------------

def _is_safe_path(file_path: Path, base_dir: Path) -> bool:
    """Return True if *file_path* resolves inside *base_dir* (no symlink escape)."""
    try:
        resolved = file_path.resolve(strict=True)
    except OSError:
        return False
    return resolved == base_dir or str(resolved).startswith(str(base_dir) + "/")


def load_custom_agents(
    custom_agents_dir: str,
    parsers: list[AgentDefinitionParser] | None = None,
) -> list[AgentDefinition]:
    """
    Load project-specific agent definitions from *custom_agents_dir*.

    - If *custom_agents_dir* is empty or None → return []
    - If directory does not exist → log warning, return []
    - Scan for *.yaml, *.yml, *.md files
    - Parse each using the standard parsers
    - Skip files that fail validation (log warning, continue)
    - Reject paths that resolve outside *custom_agents_dir* (symlink escape)
    - Return list of AgentDefinition objects
    """
    if not custom_agents_dir:
        return []

    dir_path = Path(custom_agents_dir)
    if not dir_path.is_dir():
        logger.warning("Custom agents directory does not exist: %s", custom_agents_dir)
        return []

    resolved_base = dir_path.resolve(strict=True)
    active_parsers = parsers or _DEFAULT_PARSERS
    definitions: list[AgentDefinition] = []

    for file_path in sorted(dir_path.iterdir()):
        if not _is_safe_path(file_path, resolved_base):
            logger.warning("Skipping path outside custom agents dir: %s", file_path)
            continue
        for parser in active_parsers:
            if parser.can_parse(file_path):
                try:
                    definition = parser.parse(file_path)
                    definitions.append(definition)
                except Exception as exc:
                    logger.warning("Skipping invalid custom agent %s: %s", file_path, exc)
                break

    return definitions


def load_all_agents(
    config: "AIConfig",
    client: "AIClient",
    builtin_agents_dir: str | None = None,
) -> list[LoadedAgent]:
    """
    Load built-in agents + custom agents, with custom overriding built-ins by name.

    1. Load built-in agents from *builtin_agents_dir* (or default ``agents/`` dir).
    2. Load custom agents from ``config.custom_agents_dir``.
    3. Custom agents with the same name as a built-in replace the built-in (logged at INFO).
    4. Disabled agents (``enabled: false``) are excluded.
    """
    if builtin_agents_dir is None:
        builtin_agents_dir = str(Path(__file__).resolve().parent.parent / "agents")

    builtin = load_agents_from_dir(builtin_agents_dir, client)
    agents_by_name: dict[str, LoadedAgent] = {a.name: a for a in builtin}

    custom_defs = load_custom_agents(config.custom_agents_dir)
    for defn in custom_defs:
        if not defn.enabled:
            continue
        if defn.name in agents_by_name:
            logger.info("Custom agent '%s' overrides built-in agent", defn.name)
        agents_by_name[defn.name] = LoadedAgent(defn, client)

    return list(agents_by_name.values())


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
