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
# License name → agent file name mapping (REVUE-99)
#
# TIER_ALL_AGENTS in license_validator.py uses display-style names (e.g.
# "code-quality-expert") for readability and backward compat with the license
# server API. The actual AIReviewer agent files use short codenames (e.g.
# "maya"). This map resolves the mismatch without touching either side.
# ---------------------------------------------------------------------------
_LICENCE_NAME_TO_AGENT: dict[str, str] = {
    # Licence display name → AIReviewer agent file name (name: field inside file)
    "code-quality-expert": "maya",
    "security-expert": "zara",
    "performance-expert": "kai",
    "architecture-expert": "leo",
    "consolidator": "nova",
    # Pass-throughs — already match agent file names
    "cleo": "cleo",
    "nova": "nova",
}

# Virtual agents implemented in pipeline code, not as agent files.
# These are valid licence names but will never appear in load_all_agents() output.
_VIRTUAL_AGENTS = frozenset({"orchestrator", "sage"})


class AllAgentsFailedError(RuntimeError):
    """Raised when all reviewer agents failed due to a fatal infrastructure error.

    Separates business logic (pipeline) from process control (CLI entrypoint).
    The caller (cmd_review in cli.py) decides whether to sys.exit or handle differently.

    Attributes:
        first_error: The error string from the first failed agent.
    """
    def __init__(self, first_error: str) -> None:
        self.first_error = first_error
        # first_error is kept as an attribute for the caller to log to stderr.
        # It is NOT embedded in the exception message to avoid credential
        # exposure if this exception is caught and re-logged by third parties.
        super().__init__(
            "All agents failed — review aborted. "
            "Check API credentials, credit balance, and network connectivity."
        )

# Agents that must NOT be passed to run_agents_parallel — they are called
# separately by the pipeline as infrastructure (router, consolidator).
_INFRASTRUCTURE_AGENTS = frozenset({"cleo", "nova"})

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

    Mutates each LoadedAgent's _def.system_prompt in-place so that the
    standard analyse() path in agent_runner.py picks it up without any
    changes to the orchestration machinery (OCP).

    Design note: direct mutation of _def.system_prompt is intentional here.
    LoadedAgent (agent_loader.py) exposes no public setter for system_prompt;
    adding one would widen the agent_loader API solely for this use-case.
    The mutation is safe because each pipeline.run() creates a fresh agent
    list via load_all_agents(), so there is no state leak across reviews.
    A future refactor could add AgentDefinition.prepend_context() if more
    callers need this pattern.

    Agents not in AGENT_SECTION_MAP receive just the PR title — never
    empty-handed, never noisy (unknown-agent fallback in PRContextExtractor).
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
            # Never let context injection break a review — skip silently.
            # Bare except is intentional: any failure (AttributeError,
            # TypeError, etc.) must not abort the review loop.
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
    ) -> tuple[list[ReviewResult], list[FileChange], int]:
        """Returns (results, excluded_files, files_reviewed_count).

        files_reviewed_count is the number of files that entered the review
        (included, after filtering). Exposed so callers don't need to re-parse
        the diff — review_results in orchestration mode has one entry per
        finding, not per file, so len(results) is not a reliable file count.

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
            return [ReviewResult(file_path="[diff-limit]", response=limit_result.suggestion)], [], 0

        included, excluded = filter_changes(
            changes, self.config.ignore_patterns, self.config.max_diff_lines
        )
        if excluded:
            print(f"[revue]   {len(excluded)} file(s) skipped (ignored patterns)", flush=True)

        if not included:
            print("[revue]   No files to review after filtering.", flush=True)
            return [], excluded, 0

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

        return results, excluded, len(included)

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
                # Surface the actual error so it's diagnosable in CI logs
                print(
                    f"[revue]   Shared analysis unavailable ({shared.error}) — "
                    "running all agents as fallback.",
                    flush=True,
                )
        except Exception as exc:
            print(f"[revue]   Shared analysis failed ({exc}) — using fallback.", flush=True)
            from AIReviewer.core.shared_analysis import SharedAnalysisResult
            shared = SharedAnalysisResult.fallback(
                languages=[fc.file_path.rsplit(".", 1)[-1] for fc in included]
            )

        # 2. Load and filter agents by license (REVUE-99: resolve licence names → agent names)
        print("[revue]   Loading agents...", flush=True)
        all_agents = load_all_agents(self.config, self._client)
        agents_by_name = {a.name: a for a in all_agents}

        # Expand licence names through the mapping, warn on unknowns (AC6)
        resolved_agent_names: set[str] = set()
        for licence_name in agents_allowed:
            if licence_name in _VIRTUAL_AGENTS:
                continue  # orchestrator/sage are pipeline-level, not file-based agents
            agent_name = _LICENCE_NAME_TO_AGENT.get(licence_name, licence_name)
            if agent_name in agents_by_name:
                resolved_agent_names.add(agent_name)
            else:
                print(
                    f"[revue]   ⚠ Unknown agent '{licence_name}' in licence — skipping.",
                    flush=True,
                )

        allowed_agents = [agents_by_name[n] for n in resolved_agent_names if n in agents_by_name]

        if not allowed_agents:
            print(
                "[revue]   ⚠ No matching agents found for allowed list — "
                "falling back to simplified review.",
                flush=True,
            )
            return self._run_simplified(included, agents_allowed)

        # AC4: log with display_name + role for customer-readable output
        def _agent_label(a) -> str:
            role = getattr(getattr(a, "_def", a), "role", "") or ""
            display = getattr(getattr(a, "_def", a), "display_name", "") or a.name
            # Trim role to the first clause (before " — ") for brevity
            short_role = role.split(" — ")[0].split(" — ")[0].strip()
            return f"{display} [{short_role}]" if short_role else display

        print(
            f"[revue]   Agents loaded ({len(allowed_agents)}): "
            + ", ".join(_agent_label(a) for a in allowed_agents),
            flush=True,
        )

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
            # AC5: role-aware routing log
            print(
                f"[revue]   Routed to: {', '.join(_agent_label(a) for a in routed_agents)}",
                flush=True,
            )
        except Exception as exc:
            print(f"[revue]   Cleo routing failed ({exc}) — running all allowed agents.", flush=True)
            routed_agents = allowed_agents

        # 4. Parallel agent execution — strip infrastructure agents (cleo/nova)
        #    they are called separately as router/consolidator, not reviewers.
        reviewer_agents = [a for a in routed_agents if a.name not in _INFRASTRUCTURE_AGENTS]
        if len(reviewer_agents) < len(routed_agents):
            stripped = [a.name for a in routed_agents if a.name in _INFRASTRUCTURE_AGENTS]
            print(
                f"[revue]   (Infrastructure agents excluded from review pool: "
                f"{', '.join(stripped)})",
                flush=True,
            )
        print(f"[revue]   Running {len(reviewer_agents)} reviewer(s) in parallel...", flush=True)
        parallel_result = run_agents_parallel(reviewer_agents, included, shared)

        agents_used = [r.agent_name for r in parallel_result.agent_results if r.success]
        failed = [r for r in parallel_result.agent_results if not r.success]
        if failed:
            for f in failed:
                reason = "timed out" if f.timed_out else f.error
                print(f"[revue]   ⚠ Agent {f.agent_name} failed: {reason}", flush=True)

        # Per-agent finding count — makes 0-finding runs diagnosable
        for r in parallel_result.agent_results:
            if r.success:
                print(
                    f"[revue]   [{r.agent_name}] → {len(r.findings)} finding(s)",
                    flush=True,
                )

        print(
            f"[revue]   {len(agents_used)} agent(s) succeeded, "
            f"{len(failed)} failed.",
            flush=True,
        )

        # AC3: if ALL reviewer agents failed, raise AllAgentsFailedError.
        # The caller (CLI) decides whether to sys.exit or handle differently.
        # This keeps process control out of the pipeline (SRP/OCP).
        if reviewer_agents and not agents_used:
            first_error = failed[0].error if failed else "unknown"
            print(
                f"[revue] ✗ All agents failed — review aborted.\n"
                f"[revue]   Check API credentials, credit balance, and network connectivity.",
                flush=True,
            )
            raise AllAgentsFailedError(first_error)

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
