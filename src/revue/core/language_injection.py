"""Language-context injection into reviewer agent system prompts.

Reviewer agents (Kai, Leo, Maya, Zara) are language-agnostic by design. Their
expertise lens is set at pipeline runtime by prepending a short priming
section to each agent's system prompt. The priming names the repository's
primary language and explicitly instructs the agent to review every file in
the diff regardless of extension — replacing the previous hard-coded
``trigger_patterns`` gate that excluded non-code files (YAML, shell, SQL,
Dockerfile, CI configs).

Resolution order (highest first):
  1. ``primary_language`` from .revue.yml (operator pin)
  2. First entry of ``detected_languages`` (inferred from diff)
  3. None → no injection
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .agent_loader import LoadedAgent


_PRIMING_HEADER = "## Repository Language Context"

_PRIMING_OPENER_WITH_EXPERTISE = (
    "This repository's primary coding language is {language}. You are a "
    "senior expert in {expertise} with deep {language} fluency — you know "
    "{language}'s idioms, common bugs, ecosystem pitfalls, best practices, "
    "anti-patterns, and tooling conventions cold. You are also a competent "
    "generalist across other languages and configuration formats."
)

_PRIMING_OPENER_LANGUAGE_ONLY = (
    "This repository's primary coding language is {language}. You are a "
    "senior expert in {language} — you know its idioms, common bugs, "
    "ecosystem pitfalls, best practices, anti-patterns, and tooling "
    "conventions cold. You are also a competent generalist across other "
    "languages and configuration formats."
)

_PRIMING_REVIEW_INSTRUCTION = (
    "Review every file in the diff regardless of extension. Performance, "
    "security, architecture, and quality issues exist in configuration "
    "files, shell scripts, SQL migrations, Dockerfiles, and CI definitions "
    "too — not just source-code files."
)


def resolve_primary_language(
    configured: str | None,
    detected: list[str] | None,
) -> str | None:
    """Pick the language to prime agents with.

    - Operator pin (``configured``) wins outright when non-blank.
    - Otherwise the first detected language is used (the detector returns a
      lexicographically sorted set, which keeps the choice deterministic
      across runs for the same diff).
    - When neither signal is available, return ``None`` — callers must
      treat that as a no-op for injection.
    """
    if configured and configured.strip():
        return configured.strip()
    if detected:
        return detected[0]
    return None


def build_language_prompt_section(
    language: str,
    expertise: str | None = None,
) -> str:
    """Render the priming section for *language* (and optional *expertise*).

    Callers must guarantee *language* is a non-blank string —
    ``inject_language_context`` does the None/blank resolution upstream via
    ``resolve_primary_language``. When *expertise* is provided the priming
    names both axes (e.g. "senior expert in application security with deep
    python fluency"); when absent it falls back to language-only wording.
    """
    lang = language.strip()
    exp = (expertise or "").strip()
    opener = (
        _PRIMING_OPENER_WITH_EXPERTISE.format(language=lang, expertise=exp)
        if exp
        else _PRIMING_OPENER_LANGUAGE_ONLY.format(language=lang)
    )
    return f"{_PRIMING_HEADER}\n{opener}\n\n{_PRIMING_REVIEW_INSTRUCTION}"


def inject_language_context(
    agents: list["LoadedAgent"],
    primary_language: str | None,
    detected_languages: list[str] | None,
) -> str | None:
    """Prepend a language-priming section to each agent's system prompt
    in-place, and return the resolved language string for logging.

    Per-agent: each agent's own ``expertise`` field is included in its
    priming so the model gets both axes (language + domain). Same in-place
    mutation pattern as ``inject_patterns``. Returns ``None`` when no
    language could be resolved — in which case no agents are touched.
    """
    language = resolve_primary_language(primary_language, detected_languages)
    if not language:
        return None
    for agent in agents:
        section = build_language_prompt_section(
            language, expertise=agent._def.expertise,
        )
        agent._def.system_prompt = f"{section}\n\n{agent._def.system_prompt}"
    return language
