"""
ReviewPipeline — orchestrates parse → filter → AI review (SRP).
Accepts injected AIClient for testability (DIP).

Tier routing:
  - Free tier  → simplified single-pass review loop
  - Paid tiers → full orchestration engine (Cleo routing, parallel agents,
                 Nova consolidation). PR context filtering wired in REVUE-84.
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from typing import Optional

from .ai_config import AIConfig
from .ai_client import AIClient, create_ai_client
from .diff_parser import parse_diff_file, filter_changes
from .diff_limit import check_diff_limit, DiffLimitResult
from .license_validator import LicenseInfo, validate as validate_license
from .models import FileChange
from .pr_description_adapter import PRDescription
from .pr_context import PRContextExtractor
from .usage_tracker import check_reviews_left, track as track_usage

# ---------------------------------------------------------------------------
# Premium-agent sentinel — these agents trigger full orchestration path
# ---------------------------------------------------------------------------

_FREE_TIER_AGENTS = frozenset({"orchestrator", "code-quality-expert", "consolidator"})

# ---------------------------------------------------------------------------
# Lazy imports for orchestration (only loaded on paid tiers)
# ---------------------------------------------------------------------------

def _import_orchestration():
    """Lazy-import AIReviewer orchestration modules.

    Kept lazy so free-tier pipeline starts faster and import errors
    are surfaced only when orchestration is actually needed.
    """
    try:
        from AIReviewer.core.agent_loader import load_all_agents
        from AIReviewer.core.agent_runner import run_agents_parallel
        from AIReviewer.core.nova_consolidator import consolidate
        from AIReviewer.core.shared_analysis import run_shared_analysis
        from AIReviewer.core.cleo_router import route
        return load_all_agents, run_agents_parallel, consolidate, run_shared_analysis, route
    except ImportError as exc:
        raise RuntimeError(
            f"Orchestration engine unavailable: {exc}. "
            "Ensure AIReviewer is on PYTHONPATH."
        ) from exc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _is_premium_tier(agents_allowed: list[str]) -> bool:
    """Return True when license permits agents beyond the free-tier set."""
    return bool(set(agents_allowed) - _FREE_TIER_AGENTS)


def _inject_pr_context(agents: list, extractor: "PRContextExtractor") -> None:
    """Prepend filtered PR description context to each agent's system prompt.

    Mutates each agent's underlying AgentDefinition.system_prompt in-place
    so that the standard analyse() path in agent_runner.py picks it up
    without any changes to the orchestration machinery (OCP).

    Agents not in AGENT_SECTION_MAP (unknown agents) receive just the PR
    title as a fallback — never empty-handed, never noisy.
    """
    for agent in agents:
        try:
            context = extractor.get_context_for_agent(agent.name)
            snippet = context.to_prompt_context()
            if snippet:
                agent._def.system_prompt = (
                    f"## PR Context\n{snippet}\n\n"
                    f"{agent._def.system_prompt}"
                )
        except Exception:
            # Never let context injection break a review — skip silently
            pass


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ReviewResult:
    file_path: str
    response: str
    error: str = ""

    @property
    def success(self) -> bool:
        return not self.error


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(
        self,
        diff_path: str,
        pr_description: Optional[PRDescription] = None,
    ) -> tuple[list[ReviewResult], list[FileChange]]:
        """Returns (results, excluded_files).

        Validates license, selects review path (simplified vs orchestration),
        and tracks usage after a successful review (fire-and-forget).

        Args:
            diff_path: Path to the diff file to review.
            pr_description: Optional parsed PR description. When provided on
                paid tiers, each agent receives a filtered context snippet
                containing only the sections relevant to its domain
                (REVUE-84 — ~40-60% token savings vs. naive full-description).
        """
        # --- License validation ---
        print("[revue] Validating license key...", flush=True)
        license_info = self._license_info or validate_license(
            repo_id=self.config.gitlab_project_id,
        )
        check_reviews_left(license_info.reviews_left)

        agents_allowed = license_info.agents_allowed or ["orchestrator"]
        print(
            f"[revue] License valid — tier={license_info.tier}, "
            f"reviews_left={license_info.reviews_left}",
            flush=True,
        )
        print(f"[revue] Active agents: {', '.join(agents_allowed)}", flush=True)

        # ── Step 1: Diff parsing ──────────────────────────────────────────────
        print("[revue] ── Step 1/4: Parsing diff", flush=True)
        changes = parse_diff_file(diff_path)
        print(f"[revue]   {len(changes)} file(s) found in diff", flush=True)

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

        # ── Step 2: AI review ────────────────────────────────────────────────
        start_ms = int(time.time() * 1000)

        if _is_premium_tier(agents_allowed):
            results, agents_used = self._run_orchestration(
                included, agents_allowed, pr_description=pr_description
            )
        else:
            results, agents_used = self._run_simplified(included, agents_allowed)

        # ── Step 3: Consolidation ─────────────────────────────────────────────
        total_findings = sum(
            len(_count_findings(r.response)) for r in results if not r.error
        )
        print(
            f"[revue] ── Step 3/4: Consolidation — "
            f"{total_findings} finding(s) across {len(results)} file(s)",
            flush=True,
        )

        # ── Step 4: Verdict ───────────────────────────────────────────────────
        print("[revue] ── Step 4/4: Verdict", flush=True)
        high_med = _count_severity(results, ("high", "medium"))
        low = _count_severity(results, ("low", "info"))
        if high_med > 0:
            print(
                f"[revue]   ⚠ Escalate to human — "
                f"{high_med} high/medium finding(s) require attention",
                flush=True,
            )
        elif low > 0:
            print(f"[revue]   ✓ Accept with notes — {low} low-severity finding(s) only", flush=True)
        else:
            print("[revue]   ✓ Looks good — no findings", flush=True)

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

    # ------------------------------------------------------------------
    # Review paths
    # ------------------------------------------------------------------

    def _run_simplified(
        self,
        included: list[FileChange],
        agents_allowed: list[str],
    ) -> tuple[list[ReviewResult], list[str]]:
        """Free-tier single-pass review loop.

        One client.complete() call per file. No agent routing or consolidation.
        """
        print(f"[revue] ── Step 2/4: AI review (simplified) — {len(included)} file(s)", flush=True)

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

                if "code-quality-expert" in agents_allowed and "code-quality-expert" not in agents_used:
                    agents_used.append("code-quality-expert")
            except Exception as exc:
                print(f"[revue]   ✗ {fc.file_path}: {exc}", flush=True)
                results.append(ReviewResult(file_path=fc.file_path, response="", error=str(exc)))

        return results, agents_used

    def _run_orchestration(
        self,
        included: list[FileChange],
        agents_allowed: list[str],
        pr_description: Optional[PRDescription] = None,
    ) -> tuple[list[ReviewResult], list[str]]:
        """Paid-tier full orchestration: shared analysis → Cleo routing →
        parallel agents → Nova consolidation.

        When pr_description is provided, each agent receives a context snippet
        filtered to the sections relevant to its domain (REVUE-84).
        """
        print(
            f"[revue] ── Step 2/4: AI review (orchestrated) — "
            f"{len(included)} file(s), {len(agents_allowed)} agent(s) available",
            flush=True,
        )

        load_all_agents, run_agents_parallel, consolidate, run_shared_analysis, route = (
            _import_orchestration()
        )

        # 1. Shared analysis — one AI call to classify the diff for all agents
        print("[revue]   Running shared diff analysis...", flush=True)
        try:
            shared = run_shared_analysis(included, self._client)
            if shared.success:
                print(f"[revue]   Shared analysis: {shared.summary[:80]}...", flush=True)
            else:
                print("[revue]   Shared analysis unavailable — using fallback.", flush=True)
        except Exception as exc:
            print(f"[revue]   Shared analysis failed ({exc}) — using fallback.", flush=True)
            from AIReviewer.core.shared_analysis import SharedAnalysisResult
            from AIReviewer.core.diff_parser import _detect_languages_from_changes  # noqa: F401
            shared = SharedAnalysisResult.fallback(
                languages=[fc.file_path.rsplit(".", 1)[-1] for fc in included]
            )

        # 2. Load and filter agents by license
        print("[revue]   Loading agents...", flush=True)
        all_agents = load_all_agents(self.config, self._client)
        allowed_agents = [a for a in all_agents if a.name in agents_allowed]

        if not allowed_agents:
            print(
                "[revue]   ⚠ No matching agents found for allowed list — "
                "falling back to simplified review.",
                flush=True,
            )
            return self._run_simplified(included, agents_allowed)

        print(f"[revue]   Agents loaded: {', '.join(a.name for a in allowed_agents)}", flush=True)

        # 2b. PR context injection (REVUE-84) — prepend filtered PR description
        #     sections to each agent's system prompt before dispatch.
        if pr_description is not None:
            extractor = PRContextExtractor(pr_description)
            _inject_pr_context(allowed_agents, extractor)
            print("[revue]   PR context injected — smart filtering active.", flush=True)

        # 3. Cleo routing — select agents relevant to this diff
        print("[revue]   Routing files to agents (Cleo)...", flush=True)
        try:
            _team_selection, routed_agents = route(included, allowed_agents, shared, self.config)
            if not routed_agents:
                routed_agents = allowed_agents  # fallback: run all allowed agents
            print(f"[revue]   Routed to: {', '.join(a.name for a in routed_agents)}", flush=True)
        except Exception as exc:
            print(f"[revue]   Cleo routing failed ({exc}) — running all allowed agents.", flush=True)
            routed_agents = allowed_agents

        # 4. Parallel agent execution
        print(f"[revue]   Running {len(routed_agents)} agent(s) in parallel...", flush=True)
        parallel_result = run_agents_parallel(routed_agents, included, shared)

        agents_used = [r.agent_name for r in parallel_result.agent_results if r.success]
        failed = [r for r in parallel_result.agent_results if not r.success]
        if failed:
            for f in failed:
                reason = "timed out" if f.timed_out else f.error
                print(f"[revue]   ⚠ Agent {f.agent_name} failed: {reason}", flush=True)

        print(
            f"[revue]   {len(agents_used)} agent(s) succeeded, "
            f"{len(failed)} failed.",
            flush=True,
        )

        # 5. Nova consolidation — deduplicate across agents
        print("[revue]   Consolidating findings (Nova)...", flush=True)
        all_findings = [
            finding
            for r in parallel_result.agent_results
            if r.success
            for finding in r.findings
        ]

        consolidation = consolidate(all_findings)
        print(
            f"[revue]   {consolidation.original_count} findings → "
            f"{len(consolidation.findings)} after deduplication "
            f"({consolidation.duplicates_removed} removed)",
            flush=True,
        )

        # 6. Convert AIReview findings → ReviewResult for the shared result format
        results: list[ReviewResult] = []
        for finding in consolidation.findings:
            payload = json.dumps({"findings": [finding.__dict__
                                               if hasattr(finding, "__dict__")
                                               else vars(finding)]})
            results.append(ReviewResult(file_path=finding.file_path, response=payload))

        # Ensure orchestrator tracked
        if "orchestrator" not in agents_used:
            agents_used.insert(0, "orchestrator")

        return results, agents_used
