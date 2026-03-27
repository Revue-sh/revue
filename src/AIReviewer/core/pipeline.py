"""
ReviewPipeline — orchestrates parse → filter → AI review (SRP).
Accepts injected AIClient for testability (DIP).
"""
from __future__ import annotations

from dataclasses import dataclass
from .ai_config import AIConfig
from .ai_client import AIClient, create_ai_client
from .diff_parser import parse_diff_file, filter_changes
from .models import FileChange


@dataclass
class ReviewResult:
    file_path: str
    response: str
    error: str = ""

    @property
    def success(self) -> bool:
        return not self.error


class ReviewPipeline:
    """Orchestrates a single diff review run.

    Pass client= to inject a mock for testing (DIP).
    """

    def __init__(self, config: AIConfig, client: AIClient | None = None) -> None:
        self.config = config
        self._client: AIClient = client if client is not None else create_ai_client(config)

    def run(self, diff_path: str) -> tuple[list[ReviewResult], list[FileChange]]:
        """Returns (results, excluded_files)."""
        changes = parse_diff_file(diff_path)
        included, excluded = filter_changes(
            changes, self.config.ignore_patterns, self.config.max_diff_lines
        )
        results: list[ReviewResult] = []
        for fc in included:
            try:
                prompt = (
                    f"Review this code diff for {fc.file_path}:\n\n"
                    f"{fc.diff}\n\nProvide findings as JSON."
                )
                response = self._client.complete(
                    [{"role": "user", "content": prompt}],
                    max_tokens=self.config.ai_max_tokens,
                    temperature=self.config.ai_temp,
                )
                results.append(ReviewResult(file_path=fc.file_path, response=response))
            except Exception as exc:
                results.append(ReviewResult(file_path=fc.file_path, response="", error=str(exc)))
        return results, excluded
