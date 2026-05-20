"""Tests for language-context injection into reviewer agent prompts (REVUE-267).

The reviewer agents (Kai, Leo, Maya, Zara) are language-agnostic by design but
historically carried hard-coded ``trigger_patterns`` that gated them on a
fixed set of file extensions. The language inferred from the diff was passed
as ambient metadata in the user message, never as role priming in the system
prompt. This module replaces both with an explicit injection step: prepend a
short language-expertise priming sentence to each agent's system prompt.

Source of truth resolution:
  1. ``primary_language`` from .revue.yml (operator pin)
  2. First detected language (lexicographically sorted) from the diff (fallback)
  3. None → no injection (graceful no-op)
"""
from __future__ import annotations

from unittest.mock import MagicMock

from revue_core.core.agent_loader import AgentDefinition, LoadedAgent
from revue_core.core.language_injection import (
    build_language_prompt_section,
    inject_language_context,
    resolve_primary_language,
)


def _agent(
    name: str = "kai",
    prompt: str = "You are Kai, a performance specialist.",
    expertise: str = "",
) -> LoadedAgent:
    defn = AgentDefinition(
        name=name, display_name=name.title(), role="reviewer",
        system_prompt=prompt, expertise=expertise,
    )
    return LoadedAgent(defn, MagicMock(), 4096)


# ---------------------------------------------------------------------------
# build_language_prompt_section
#
# Contract: callers must pass a non-blank language string. The caller-side
# guard lives in ``inject_language_context`` (via ``resolve_primary_language``);
# the section builder itself trusts its input. That keeps the function narrow
# and avoids a dead second guard.
# ---------------------------------------------------------------------------


def test_build_section_includes_language_name_for_priming():
    """The priming text must name the resolved language explicitly."""
    section = build_language_prompt_section("python")
    assert "python" in section.lower()


def test_build_section_instructs_review_every_file_regardless_of_extension():
    """Priming must explicitly tell the agent to review every file in the diff —
    not gate on extension. This is the load-bearing instruction that replaces
    the removed ``trigger_patterns`` gate."""
    section = build_language_prompt_section("python")
    text = section.lower()
    assert "every file" in text or "regardless of extension" in text


# ---------------------------------------------------------------------------
# resolve_primary_language
# ---------------------------------------------------------------------------


def test_resolve_prefers_configured_primary_language_over_detected():
    """When .revue.yml pins ``primary_language``, that wins — even if the
    detected list disagrees. Operator intent overrides inference."""
    assert resolve_primary_language(
        configured="swift",
        detected=["python", "yaml"],
    ) == "swift"


def test_resolve_falls_back_to_first_detected_when_no_configured():
    """No operator pin → use the first detected language (lexicographic
    order from _detect_languages keeps this deterministic)."""
    assert resolve_primary_language(
        configured="",
        detected=["python", "yaml"],
    ) == "python"


def test_resolve_returns_none_when_neither_configured_nor_detected():
    """No signal at all → return None; caller must skip injection."""
    assert resolve_primary_language(configured="", detected=[]) is None


def test_resolve_treats_whitespace_only_configured_as_unset():
    """A blank configured value must not shadow the detected fallback."""
    assert resolve_primary_language(
        configured="   ",
        detected=["go"],
    ) == "go"


# ---------------------------------------------------------------------------
# inject_language_context
# ---------------------------------------------------------------------------


def test_inject_prepends_priming_to_each_agent_system_prompt():
    """Injection is in-place, prepended (so agent identity follows priming)."""
    kai = _agent("kai", "You are Kai.")
    zara = _agent("zara", "You are Zara.")
    inject_language_context([kai, zara], primary_language="python", detected_languages=[])
    assert "python" in kai._def.system_prompt.lower()
    assert "python" in zara._def.system_prompt.lower()
    # Original agent identity must still be present and AFTER the priming block.
    assert kai._def.system_prompt.endswith("You are Kai.")
    assert zara._def.system_prompt.endswith("You are Zara.")


def test_inject_uses_detected_when_no_primary_configured():
    """When primary_language is empty, the first detected language is used."""
    leo = _agent("leo", "You are Leo.")
    inject_language_context([leo], primary_language="", detected_languages=["ruby", "yaml"])
    assert "ruby" in leo._def.system_prompt.lower()


def test_inject_is_noop_when_no_language_signal_available():
    """No configured + no detected → no mutation, and ``None`` is returned
    so callers (pipeline.py) can skip the "language priming injected" log."""
    maya = _agent("maya", "You are Maya.")
    original = maya._def.system_prompt
    result = inject_language_context([maya], primary_language="", detected_languages=[])
    assert maya._def.system_prompt == original
    assert result is None


def test_inject_handles_empty_agent_list_without_error():
    """No agents → silent no-op, never raise."""
    inject_language_context([], primary_language="python", detected_languages=[])


def test_inject_primary_language_overrides_detected():
    """Operator pin (.revue.yml) takes precedence over detection."""
    kai = _agent("kai", "You are Kai.")
    inject_language_context(
        [kai], primary_language="swift", detected_languages=["python", "yaml"],
    )
    assert "swift" in kai._def.system_prompt.lower()
    assert "python" not in kai._def.system_prompt.lower()


# ---------------------------------------------------------------------------
# Per-agent expertise priming
# ---------------------------------------------------------------------------


def test_inject_includes_agent_expertise_when_declared():
    """Each agent's ``expertise`` field appears in its own priming text.

    Language is uniform across agents; expertise differs (Kai = performance,
    Zara = security, etc.). Both axes must be present so the model gets a
    sharp role + language combo, not just one or the other.
    """
    kai = _agent("kai", "You are Kai.", expertise="performance engineering")
    zara = _agent("zara", "You are Zara.", expertise="application security")
    inject_language_context(
        [kai, zara], primary_language="python", detected_languages=[],
    )
    assert "performance engineering" in kai._def.system_prompt.lower()
    assert "application security" in zara._def.system_prompt.lower()
    # Cross-contamination check: Kai must not see Zara's expertise text.
    assert "application security" not in kai._def.system_prompt.lower()
    assert "performance engineering" not in zara._def.system_prompt.lower()


def test_inject_falls_back_to_language_only_when_expertise_blank():
    """Custom agents that omit ``expertise`` still get a valid priming —
    just without the domain phrase. This keeps the field optional for
    user-defined agents in custom_agents_dir."""
    generic = _agent("custom", "You are a custom agent.", expertise="")
    inject_language_context(
        [generic], primary_language="python", detected_languages=[],
    )
    # Language must still appear; the prompt should be non-empty mutated.
    assert "python" in generic._def.system_prompt.lower()
    assert generic._def.system_prompt != "You are a custom agent."


def test_build_section_includes_expertise_when_provided():
    """Unit-level: ``build_language_prompt_section`` accepts an optional
    expertise argument and renders it into the priming text."""
    section = build_language_prompt_section("python", expertise="application security")
    assert "application security" in section.lower()
    assert "python" in section.lower()


def test_build_section_renders_without_expertise():
    """No expertise argument → fallback wording, language-only."""
    section = build_language_prompt_section("python")
    assert "python" in section.lower()
    # Sanity check — no leftover placeholder leaking through.
    assert "{expertise}" not in section
    assert "{language}" not in section
