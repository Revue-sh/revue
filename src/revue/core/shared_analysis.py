"""
Shared analysis — single upfront AI classification call (SRP).

Runs before specialist agents to classify the diff and provide context.
Follows OCP: SharedAnalysisResult is immutable; agents read, never write.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .ai_client import AIClient

from .models import FileChange

_EXT_TO_LANG: dict[str, str] = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".tsx": "typescript", ".jsx": "javascript", ".rb": "ruby",
    ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin",
    ".swift": "swift", ".cs": "csharp", ".cpp": "cpp", ".c": "c",
    ".h": "c", ".hpp": "cpp", ".php": "php", ".scala": "scala",
    ".sh": "shell", ".bash": "shell",
}

SHARED_ANALYSIS_PROMPT = """\
You are a code review orchestrator. Analyse this diff and respond with valid JSON only.

Diff summary:
{diff_summary}

Respond with this exact JSON structure:
{{
  "languages": ["list of programming languages detected"],
  "risk_areas": ["areas of concern e.g. authentication, database, concurrency, file-io, api-boundary"],
  "suggested_agents": ["which specialist agents to activate: zara=security, kai=performance, maya=quality, leo=architecture"],
  "summary": "1-2 sentence plain English summary of what this diff does"
}}"""


@dataclass
class SharedAnalysisResult:
    """Immutable context shared with all specialist agents."""

    languages: list[str]
    risk_areas: list[str]
    suggested_agents: list[str]
    summary: str
    raw_response: str = ""
    error: str = ""

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


def run_shared_analysis(
    changes: list[FileChange],
    client: "AIClient",
    max_diff_summary_lines: int = 100,
) -> SharedAnalysisResult:
    """
    Run the shared analysis AI call.

    - Builds a compact diff summary (first max_diff_summary_lines lines per file)
    - Calls client.complete() with SHARED_ANALYSIS_PROMPT
    - Parses JSON response into SharedAnalysisResult
    - On any failure: returns SharedAnalysisResult.fallback() — never raises
    """
    languages = _detect_languages(changes)
    try:
        diff_summary = _build_diff_summary(changes, max_diff_summary_lines)
        prompt = SHARED_ANALYSIS_PROMPT.format(diff_summary=diff_summary)
        raw = client.complete([{"role": "user", "content": prompt}])
        data = json.loads(raw)
        return SharedAnalysisResult(
            languages=data.get("languages", languages),
            risk_areas=data.get("risk_areas", []),
            suggested_agents=data.get("suggested_agents", ["zara", "kai", "maya", "leo"]),
            summary=data.get("summary", ""),
            raw_response=raw,
        )
    except Exception:
        return SharedAnalysisResult.fallback(languages)
