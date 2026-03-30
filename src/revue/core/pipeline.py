"""
ReviewPipeline — orchestrates parse → filter → AI review (SRP).
Accepts injected AIClient for testability (DIP).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from .ai_config import AIConfig
from .ai_client import AIClient, create_ai_client
from .diff_parser import parse_diff_file, filter_changes
from .diff_limit import check_diff_limit, DiffLimitResult
from .license_validator import LicenseInfo, validate as validate_license
from .models import FileChange
from .usage_tracker import check_reviews_left, track as track_usage


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
    Pass license_info= to inject a pre-validated LicenseInfo (avoids API call in tests).
    """

    def __init__(
        self,
        config: AIConfig,
        client: AIClient | None = None,
        license_info: LicenseInfo | None = None,
    ) -> None:
        self.config = config
        self._client: AIClient = client if client is not None else create_ai_client(config)
        self._license_info: LicenseInfo | None = license_info

    def run(self, diff_path: str) -> tuple[list[ReviewResult], list[FileChange]]:
        """Returns (results, excluded_files).

        Validates license and checks review limits before running agents.
        Tracks usage after a successful review (fire-and-forget).
        """
        # --- License validation ---
        print("[revue] Validating license key...", flush=True)
        license_info = self._license_info or validate_license(
            repo_id=self.config.gitlab_project_id,
        )
        check_reviews_left(license_info.reviews_left)
        print(f"[revue] License valid — tier={license_info.tier}, reviews_left={license_info.reviews_left}", flush=True)

        # --- Diff parsing ---
        changes = parse_diff_file(diff_path)
        print(f"[revue] Parsed {len(changes)} file(s) from diff", flush=True)

        # Hard diff limit check — runs before any AI call (non-blocking warning per PRD)
        limit_result = check_diff_limit(changes, self.config.max_diff_lines)
        if limit_result.exceeded:
            print(f"[revue] Diff limit exceeded — {limit_result.suggestion}", flush=True)
            warning = ReviewResult(
                file_path="[diff-limit]",
                response=limit_result.suggestion,
                error="",
            )
            return [warning], []

        included, excluded = filter_changes(
            changes, self.config.ignore_patterns, self.config.max_diff_lines
        )
        print(f"[revue] Reviewing {len(included)} file(s), skipping {len(excluded)} (ignored patterns)", flush=True)

        if not included:
            print("[revue] No files to review after filtering.", flush=True)
            return [], excluded

        start_ms = int(time.time() * 1000)
        agents_used: list[str] = ["orchestrator"]

        results: list[ReviewResult] = []
        for fc in included:
            print(f"[revue] Reviewing {fc.file_path}...", flush=True)
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
                print(f"[revue] ✓ {fc.file_path}", flush=True)
                if "code-quality-expert" not in agents_used:
                    agents_used.append("code-quality-expert")
            except Exception as exc:
                print(f"[revue] ✗ {fc.file_path}: {exc}", flush=True)
                results.append(ReviewResult(file_path=fc.file_path, response="", error=str(exc)))

        duration_ms = int(time.time() * 1000) - start_ms

        # --- Usage tracking (fire-and-forget) ---
        if license_info.key:
            track_usage(
                key=license_info.key,
                repo_id=self.config.gitlab_project_id,
                agents_used=agents_used,
                duration_ms=duration_ms,
            )

        return results, excluded
