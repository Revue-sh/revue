"""
Presentation / UX formatting for orchestrator output.

Separated from shared_analysis.py (SRP): analysis logic stays there,
human-readable rendering lives here.
"""
from __future__ import annotations

from .shared_analysis import OrchestratorResponse


def format_selection_message(response: OrchestratorResponse) -> str:
    """Format an OrchestratorResponse into a human-readable emoji log message.

    Output follows AC4:
      🔍 Analyzing your changes...
      <blank>
      I've detected modifications in:
        <emoji> <description>
      <blank>
      To ensure quality, I'm bringing in:
        → <emoji> <Agent Name> <reason>
      <blank>
      Starting review...
    """
    lines: list[str] = ["🔍 Analyzing your changes...", ""]

    if response.detected_areas:
        lines.append("I've detected modifications in:")
        for area in response.detected_areas:
            lines.append(f"  {area.emoji} {area.description}")
        lines.append("")

    if response.selected_agents:
        lines.append("To ensure quality, I'm bringing in:")
        for agent in response.selected_agents:
            lines.append(f"  → {agent.emoji} {agent.name} {agent.reason}")
        lines.append("")

    if not response.detected_areas and not response.selected_agents:
        lines.append(
            "No specialist agents required for this change type"
            " (configuration/documentation)."
        )
        if response.summary:
            lines.append(f"Summary: {response.summary}")
        lines.append("")

    lines.append("Starting review...")
    return "\n".join(lines)
