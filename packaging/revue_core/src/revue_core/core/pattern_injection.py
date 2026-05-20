"""
REVUE-94: Pattern injection into agent system prompts.

Builds prompt sections from allowed/disallowed patterns and injects them
into agent system prompts before the first LLM call.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .agent_loader import LoadedAgent


def build_pattern_prompt_sections(
    allowed_patterns: list[dict[str, str]],
    disallowed_patterns: list[dict[str, str]],
) -> str:
    """Build prompt text for allowed and disallowed patterns.

    Returns empty string when both lists are empty (no headers injected).
    """
    sections: list[str] = []

    if allowed_patterns:
        lines = [
            "## Allowed Patterns \u2014 Do Not Flag",
            "The following patterns represent intentional design decisions. "
            "Do NOT report findings for these:",
        ]
        for p in allowed_patterns:
            lines.append(f"- {p['pattern']} \u2014 {p['rationale']}")
        sections.append("\n".join(lines))

    if disallowed_patterns:
        lines = [
            "## Disallowed Patterns \u2014 Always Flag",
            "The following patterns should always be reported, regardless of confidence:",
        ]
        for p in disallowed_patterns:
            lines.append(f"- {p['pattern']} \u2014 {p['rationale']}")
        sections.append("\n".join(lines))

    return "\n\n".join(sections)


def _pattern_matches_agent(pattern: dict, agent_name: str) -> bool:
    """Return True if *pattern* applies to *agent_name*.

    A pattern with no ``applies_to`` key (or an empty list) is global —
    it applies to every agent (backward-compatible behaviour).
    ``applies_to`` matching is case-insensitive so YAML authors don't need
    to worry about capitalisation.
    """
    applies_to = pattern.get("applies_to", [])
    if not applies_to:
        return True
    return agent_name.lower() in [s.lower() for s in applies_to]


def inject_patterns(
    agents: list["LoadedAgent"],
    allowed_patterns: list[dict],
    disallowed_patterns: list[dict],
) -> None:
    """Inject pattern sections into each agent's system prompt (in-place).

    Patterns with an ``applies_to`` list are only injected into agents
    whose name appears in that list.  Patterns without ``applies_to``
    are injected into every agent (backward-compatible).

    Skips injection entirely for agents that have no matching patterns.
    """
    for agent in agents:
        agent_allowed = [p for p in allowed_patterns if _pattern_matches_agent(p, agent.name)]
        agent_disallowed = [p for p in disallowed_patterns if _pattern_matches_agent(p, agent.name)]
        section = build_pattern_prompt_sections(agent_allowed, agent_disallowed)
        if section:
            agent._def.system_prompt = (
                f"{section}\n\n{agent._def.system_prompt}"
            )
