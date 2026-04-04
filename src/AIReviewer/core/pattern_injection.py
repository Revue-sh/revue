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


def inject_patterns(
    agents: list["LoadedAgent"],
    allowed_patterns: list[dict[str, str]],
    disallowed_patterns: list[dict[str, str]],
) -> None:
    """Inject pattern sections into each agent's system prompt (in-place).

    Skips injection when both pattern lists are empty.
    """
    section = build_pattern_prompt_sections(allowed_patterns, disallowed_patterns)
    if not section:
        return

    for agent in agents:
        agent._def.system_prompt = (
            f"{section}\n\n{agent._def.system_prompt}"
        )
