"""Canonical display data for Revue — accessible from any module.

Single source of truth for:
  * Agent → display-name + emoji (used by body/summary builders, CLI,
    pipeline log lines).
  * Severity → emoji + ordering (used by body/summary builders, the
    poster's header-parsing regexes, the CLI's parsing regexes, and the
    won't-fix service's severity sniffing).

Lives in ``core/`` rather than ``comments/`` so any layer can import it
without crossing the comments-package boundary backwards.

For backward compatibility, ``comments/_agent_display.py`` re-exports
the agent maps from here, so older imports keep working.

Keys are canonical agent names (matching ``agent_names.py`` constants
and the ``name:`` field of each agent YAML/MD definition).
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Agent display
# ---------------------------------------------------------------------------

AGENT_DISPLAY_NAMES: dict[str, str] = {
    "leo": "Leo",
    "zara": "Zara",
    "kai": "Kai",
    "maya": "Maya",
    "nova": "Nova",
    "vex": "Vex",
}

AGENT_EMOJIS: dict[str, str] = {
    "leo": "🏗️",
    "zara": "🔒",
    "kai": "⏱️",
    "maya": "✨",
    "nova": "🌟",
    "vex": "🚦",
}

# ---------------------------------------------------------------------------
# Severity display
# ---------------------------------------------------------------------------

SEVERITY_EMOJIS: dict[str, str] = {
    "high": "🔴",
    "medium": "🟡",
    "low": "🔵",
    "info": "ℹ️",
}

# Order used everywhere severity is grouped/sorted (highest first).
SEVERITY_ORDER: list[str] = ["high", "medium", "low", "info"]

# Pre-built regex alternation for severity emojis, used by the poster,
# CLI, and won't-fix service to detect Revue-authored finding headers
# (e.g. "**🟡 [MEDIUM] ..."). Built from ``SEVERITY_EMOJIS`` so re-skinning
# a severity emoji propagates everywhere.
SEVERITY_EMOJI_ALT: str = "|".join(re.escape(e) for e in SEVERITY_EMOJIS.values())
