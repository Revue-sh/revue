"""
ReviewPipeline — orchestrates parse → filter → AI review (SRP).
Accepts injected AIClient for testability (DIP).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Optional


def _count_findings(response: str) -> list:
    """Parse findings list from a JSON review response. Returns [] on error."""
    try:
        clean = response.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        data = json.loads(clean)
        return data.get("findings", [])
    except Exception:
        return []


def _count_severity(results: list, severities: tuple) -> int:
    """Count total findings matching any of the given severities across all results."""
    total = 0
    for r in results:
        if r.error:
            continue
        for f in _count_findings(r.response):
            if f.get("severity", "").lower() in severities:
                total += 1
    return total

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
        
        # Extract allowed agents from license
        agents_allowed = license_info.agents_allowed or ["orchestrator"]
        print(f"[revue] License valid — tier={license_info.tier}, reviews_left={license_info.reviews_left}", flush=True)
        print(f"[revue] Active agents: {', '.join(agents_allowed)}", flush=True)

        # ── Step 1: Diff parsing ──────────────────────────────────────────────
        print("[revue] ── Step 1/4: Parsing diff", flush=True)
        changes = parse_diff_file(diff_path)
        print(f"[revue]   {len(changes)} file(s) found in diff", flush=True)

        # Hard diff limit check — runs before any AI call
        limit_result = check_diff_limit(changes, self.config.max_diff_lines)
        if limit_result.exceeded:
            print(f"[revue]   ⚠ Diff too large — {limit_result.suggestion}", flush=True)
            return [ReviewResult(file_path="[diff-limit]", response=limit_result.suggestion)], []

        included, excluded = filter_changes(
            changes, self.config.ignore_patterns, self.config.max_diff_lines
        )
        if excluded:
            print(f"[revue]   {len(excluded)} file(s) skipped (ignored patterns)", flush=True)

        if not included:
            print("[revue]   No files to review after filtering.", flush=True)
            return [], excluded

        # ── Step 2: AI review (per file) ──────────────────────────────────────
        print(f"[revue] ── Step 2/4: AI review — {len(included)} file(s)", flush=True)
        start_ms = int(time.time() * 1000)
        
        # Track agents used — start with orchestrator if allowed
        agents_used: list[str] = ["orchestrator"] if "orchestrator" in agents_allowed else []

        results: list[ReviewResult] = []
        for i, fc in enumerate(included, 1):
            print(f"[revue]   [{i}/{len(included)}] {fc.file_path}...", flush=True)
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
                print(f"[revue]   ✓ {fc.file_path}", flush=True)
                
                # Track code-quality-expert if allowed and not already tracked
                if "code-quality-expert" in agents_allowed and "code-quality-expert" not in agents_used:
                    agents_used.append("code-quality-expert")
            except Exception as exc:
                print(f"[revue]   ✗ {fc.file_path}: {exc}", flush=True)
                results.append(ReviewResult(file_path=fc.file_path, response="", error=str(exc)))

        # ── Step 3: Consolidation ─────────────────────────────────────────────
        # (Nova consolidation & contradiction resolution are handled by the full
        #  multi-agent pipeline in revue/core — this simplified pipeline skips
        #  those steps for the web app context)
        total_findings = sum(
            len(_count_findings(r.response)) for r in results if not r.error
        )
        print(f"[revue] ── Step 3/4: Consolidation — {total_findings} finding(s) across {len(results)} file(s)", flush=True)

        # ── Step 4: Verdict ───────────────────────────────────────────────────
        print(f"[revue] ── Step 4/4: Verdict", flush=True)
        high_med = _count_severity(results, ("high", "medium"))
        low = _count_severity(results, ("low", "info"))
        if high_med > 0:
            print(f"[revue]   ⚠ Escalate to human — {high_med} high/medium finding(s) require attention", flush=True)
        elif low > 0:
            print(f"[revue]   ✓ Accept with notes — {low} low-severity finding(s) only", flush=True)
        else:
            print(f"[revue]   ✓ Looks good — no findings", flush=True)

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
