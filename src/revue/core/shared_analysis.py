"""
Shared analysis — single upfront AI classification call (SRP).

Runs before specialist agents to classify the diff and provide context.
Follows OCP: SharedAnalysisResult is immutable; agents read, never write.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .ai_client import AIClient

from .ai_client import _CACHE_TIER, _JSON_FORMAT_PROVIDERS
from .models import FileChange

_log = logging.getLogger(__name__)

_EXT_TO_LANG: dict[str, str] = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".tsx": "typescript", ".jsx": "javascript", ".rb": "ruby",
    ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin",
    ".swift": "swift", ".cs": "csharp", ".cpp": "cpp", ".c": "c",
    ".h": "c", ".hpp": "cpp", ".php": "php", ".scala": "scala",
    ".sh": "shell", ".bash": "shell",
}

SHARED_ANALYSIS_PROMPT_INSTRUCTIONS = """\
You are a code review orchestrator. Analyse this diff and respond with valid JSON only.

You MUST respond using ONLY this exact JSON schema — no other format is accepted:

TEMPLATE:
{{
  "detected_areas": [
    {{"emoji": "<relevant emoji>", "description": "<area detected in the diff>"}}
  ],
  "selected_agents": [
    {{"emoji": "<agent emoji>", "name": "<Agent Name>", "reason": "<high-level reason e.g. for auth review>", "files": ["<file path 1>", "<file path 2>"]}}
  ],
  "languages": ["<detected programming languages>"],
  "risk_areas": ["<risk labels e.g. authentication, database, api-boundary, concurrency>"],
  "summary": "<1-2 sentence plain English summary of what this diff does>"
}}

EXAMPLE:
{{
  "detected_areas": [
    {{"emoji": "🔐", "description": "Authentication middleware (login flow updated)"}},
    {{"emoji": "🗄️", "description": "Database migrations (users table schema change)"}},
    {{"emoji": "⚡", "description": "API endpoints (new rate limiting logic)"}}
  ],
  "selected_agents": [
    {{"emoji": "🛡️", "name": "Security Agent", "reason": "for auth review", "files": ["app/auth.py", "app/middleware.py"]}},
    {{"emoji": "🗄️", "name": "Data Agent", "reason": "for schema validation", "files": ["migrations/001_users.sql"]}},
    {{"emoji": "⚡", "name": "Performance Agent", "reason": "for API optimization", "files": ["app/api.py", "app/routes.py"]}}
  ],
  "languages": ["python"],
  "risk_areas": ["authentication", "database", "api-boundary"],
  "summary": "This diff updates the login flow, migrates the users table, and adds rate limiting to API endpoints."
}}"""

# Legacy prompt template (for reference; prefer building system as list in run_shared_analysis)
SHARED_ANALYSIS_PROMPT = f"""\
You are a code review orchestrator. Analyse this diff and respond with valid JSON only.

Diff summary:
{{diff_summary}}

{SHARED_ANALYSIS_PROMPT_INSTRUCTIONS}"""

_ANTHROPIC_JSON_SUFFIX = "\n\nRespond with raw JSON only. No markdown formatting. No code fences."


# ---------------------------------------------------------------------------
# Orchestrator response models (dataclasses — pydantic not in dependencies)
# ---------------------------------------------------------------------------

@dataclass
class DetectedArea:
    emoji: str
    description: str


@dataclass
class SelectedAgent:
    emoji: str
    name: str
    reason: str
    files: list[str] = field(default_factory=list)


@dataclass
class OrchestratorResponse:
    detected_areas: list[DetectedArea]
    selected_agents: list[SelectedAgent]
    languages: list[str]
    risk_areas: list[str]
    summary: str


def _parse_orchestrator_response(raw: str) -> OrchestratorResponse:
    """Parse raw JSON string into OrchestratorResponse.

    Raises ValueError on malformed or incomplete data so callers can
    fall back gracefully.
    """
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Expected JSON object")

    for key in ("detected_areas", "selected_agents", "languages", "risk_areas", "summary"):
        if key not in data:
            raise ValueError(f"Missing required field: {key}")

    detected_areas = [
        DetectedArea(emoji=str(a.get("emoji", "")), description=str(a.get("description", "")))
        for a in data["detected_areas"]
        if isinstance(a, dict)
    ]
    selected_agents = [
        SelectedAgent(
            emoji=str(a.get("emoji", "")),
            name=str(a.get("name", "")),
            reason=str(a.get("reason", "")),
            files=[f for f in a.get("files", []) if isinstance(f, str)],
        )
        for a in data["selected_agents"]
        if isinstance(a, dict)
    ]

    return OrchestratorResponse(
        detected_areas=detected_areas,
        selected_agents=selected_agents,
        languages=[str(lang) for lang in data["languages"]],
        risk_areas=[str(r) for r in data["risk_areas"]],
        summary=str(data["summary"]),
    )


# ---------------------------------------------------------------------------
# Internal result (passed to agents — unchanged contract)
# ---------------------------------------------------------------------------

@dataclass
class SharedAnalysisResult:
    """Immutable context shared with all specialist agents."""

    languages: list[str]
    risk_areas: list[str]
    suggested_agents: list[str]
    summary: str
    raw_response: str = ""
    error: str = ""
    orchestrator_response: OrchestratorResponse | None = field(default=None, repr=False)

    @property
    def success(self) -> bool:
        return not self.error

    @classmethod
    def fallback(cls, languages: list[str]) -> "SharedAnalysisResult":
        """Safe fallback when AI call fails — all agents run, no risk areas."""
        return cls(
            languages=languages,
            risk_areas=[],
            suggested_agents=["zara", "kai", "maya", "leo"],
            summary="Shared analysis unavailable — all agents will run.",
            error="fallback",
        )


def _detect_languages(changes: list[FileChange]) -> list[str]:
    langs: set[str] = set()
    for fc in changes:
        suffix = "." + fc.file_path.rsplit(".", 1)[-1] if "." in fc.file_path else ""
        lang = _EXT_TO_LANG.get(suffix)
        if lang:
            langs.add(lang)
    return sorted(langs)


def _build_diff_summary(changes: list[FileChange], max_lines_per_file: int) -> str:
    parts: list[str] = []
    for fc in changes:
        lines = fc.diff.splitlines()[:max_lines_per_file]
        parts.append(f"File: {fc.file_path}\n" + "\n".join(lines))
    return "\n\n".join(parts)


def _detect_provider(client: "AIClient") -> str:
    """Best-effort provider detection from client instance type name."""
    type_name = type(client).__name__.lower()
    if "anthropic" in type_name:
        return "anthropic"
    if "azure" in type_name:
        return "azure"
    if "openrouter" in type_name:
        return "openrouter"
    if "openai" in type_name:
        return "openai"
    return ""


def run_shared_analysis(
    changes: list[FileChange],
    client: "AIClient",
    max_diff_summary_lines: int = 100,
    provider: str = "",
) -> SharedAnalysisResult:
    """
    Run the shared analysis AI call.

    - Builds a compact diff summary (first max_diff_summary_lines lines per file)
    - Calls client.complete() with SHARED_ANALYSIS_PROMPT
    - Parses JSON response into OrchestratorResponse for transparency logging
    - Converts to SharedAnalysisResult for internal agent consumption
    - Provider-aware: Anthropic gets explicit JSON suffix; OpenAI-compatible
      providers (openai, azure, openrouter, google, groq, custom) omit the
      suffix.  NOTE: response_format=json_object is not yet forwarded via
      AIClient.complete() — see REVUE-107.
    - On any failure: returns SharedAnalysisResult.fallback() — never raises
    """
    languages = _detect_languages(changes)
    try:
        import hashlib

        diff_summary = _build_diff_summary(changes, max_diff_summary_lines)
        diff_hash = hashlib.sha256(diff_summary.encode()).hexdigest()[:16]

        # AC5: Provider-specific JSON handling
        resolved_provider = provider or _detect_provider(client)

        # D1: diff_summary in system[0] with cache_control (shared cached prefix)
        # orchestrator_instructions in system[1] without cache_control
        orchestrator_instructions = SHARED_ANALYSIS_PROMPT_INSTRUCTIONS
        if resolved_provider not in _JSON_FORMAT_PROVIDERS:
            orchestrator_instructions += _ANTHROPIC_JSON_SUFFIX

        system_blocks = [
            {"type": "text", "text": f"Diff summary:\n{diff_summary}", "cache_control": {"type": _CACHE_TIER}},
            {"type": "text", "text": f"The diff summary above is what you must analyse. {orchestrator_instructions}"},
        ]
        raw = client.complete(
            [{"role": "user", "content": "Analyse the diff above and respond with valid JSON."}],
            system=system_blocks,
            cache_key=diff_hash,
        )
        _log.debug("Shared analysis raw response (%d chars): %.300r", len(raw), raw)
        # Strip markdown code fences that LLMs often wrap responses in
        clean = raw.strip()
        clean = re.sub(r"^```(?:json)?\s*\n?", "", clean)
        clean = re.sub(r"\n?```\s*$", "", clean)
        clean = clean.strip()
        if not clean:
            raise ValueError("LLM returned empty response after fence stripping")
        data = json.loads(clean)

        # Try to parse the structured OrchestratorResponse (new format)
        orch_response: OrchestratorResponse | None = None
        try:
            orch_response = _parse_orchestrator_response(clean)
        except (ValueError, KeyError, TypeError):
            pass  # Graceful degradation — legacy format still works

        return SharedAnalysisResult(
            languages=data.get("languages", languages),
            risk_areas=data.get("risk_areas", []),
            suggested_agents=data.get("suggested_agents", ["zara", "kai", "maya", "leo"]),
            summary=data.get("summary", ""),
            raw_response=raw,
            orchestrator_response=orch_response,
        )
    except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
        _log.warning("Shared analysis failed: %s", exc)
        return SharedAnalysisResult.fallback(languages)
    except (OSError, AttributeError) as exc:
        _log.error("Shared analysis failed (system error): %s", exc, exc_info=True)
        return SharedAnalysisResult.fallback(languages)
