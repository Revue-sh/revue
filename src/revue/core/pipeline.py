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
import os
import sys
import time
from dataclasses import dataclass
from typing import NamedTuple, Optional
from uuid import uuid4

from .ai_config import AIConfig
from .ai_client import AIClient, create_ai_client
from .cleo_router import _INFRASTRUCTURE_AGENTS
from .metrics import MetricsCollector, NullMetricsCollector
from .diff_parser import parse_diff_file, filter_changes
from .diff_limit import check_diff_limit, DiffLimitResult
from .license_validator import LicenseInfo, validate as validate_license
from .models import FileChange, PRContext
from .pr_description_adapter import PRDescription
from .pr_context import PRContextExtractor
from .pattern_injection import inject_patterns
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
# server API. The actual revue agent files use short codenames (e.g.
# "maya"). This map resolves the mismatch without touching either side.
# ---------------------------------------------------------------------------
_LICENCE_NAME_TO_AGENT: dict[str, str] = {
    # Licence display name → revue agent file name (name: field inside file)
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


def _short_error(error: str) -> str:
    """Return a brief, human-readable summary of an agent error string.

    Rate-limit JSON blobs and other verbose SDK errors are condensed to a
    single line so the '⚠ Agent X failed:' log stays readable. The full
    reason has already been printed by _with_retry's ❌ RATE LIMIT ERROR block.
    """
    if not error:
        return "unknown error"
    low = error.lower()
    if "rate_limit" in low or "rate limit" in low or "429" in low:
        return "rate limit exceeded (see ❌ RATE LIMIT ERROR above)"
    if "timeout" in low or "timed out" in low:
        return "timed out"
    if "401" in low or "403" in low or "authentication" in low or "unauthorized" in low:
        return "authentication error"
    if "500" in low or "502" in low or "503" in low:
        return "server error"
    # Fallback: first line only, capped at 120 chars
    first_line = error.split("\n")[0]
    return first_line[:120] + ("…" if len(first_line) > 120 else "")


# ---------------------------------------------------------------------------
# Rate-limit fallback cascade constants and helpers (REVUE-117)
# ---------------------------------------------------------------------------

_FB_NORMAL = "normal"
_FB_FILE_ASSIGNED = "file_assigned"
_FB_CONTEXT_LITE = "context_lite"

_FALLBACK_SEQUENCE = [_FB_NORMAL, _FB_FILE_ASSIGNED, _FB_CONTEXT_LITE]


def _is_rate_limit_error(error: str) -> bool:
    """Return True if the error string indicates a rate-limit failure (HTTP 429)."""
    low = error.lower()
    return "rate_limit" in low or "rate limit" in low or "429" in low


def _next_fallback(mode: str) -> str | None:
    """Return the next fallback mode in the cascade, or None if already at the deepest level."""
    try:
        idx = _FALLBACK_SEQUENCE.index(mode)
        if idx + 1 < len(_FALLBACK_SEQUENCE):
            return _FALLBACK_SEQUENCE[idx + 1]
    except ValueError:
        pass
    return None


def _build_agent_changes(
    agent_name: str,
    mode: str,
    all_changes: list[FileChange],
    file_assignments: dict[str, list[str]],
) -> list[FileChange]:
    """Build the FileChange list for an agent based on the current fallback mode.

    - normal: all files with full diffs (no change)
    - file_assigned: only the agent's assigned files with full diffs
    - context_lite: assigned files in full + one-line summary for all other files
    """
    if mode == _FB_NORMAL or not file_assignments:
        return all_changes

    assigned_paths = set(file_assignments.get(agent_name, []))
    if not assigned_paths:
        return all_changes  # agent not in map — send everything (safe default)

    assigned = [fc for fc in all_changes if fc.file_path in assigned_paths]

    if mode == _FB_FILE_ASSIGNED:
        return assigned

    # context_lite: assigned files in full + one-line summary for unassigned files
    summarized = [
        FileChange(
            file_path=fc.file_path,
            change_type=fc.change_type,
            additions=fc.additions,
            deletions=fc.deletions,
            diff=f"[context-lite] {fc.additions} additions, {fc.deletions} deletions",
            language=fc.language,
        )
        for fc in all_changes
        if fc.file_path not in assigned_paths
    ]
    return assigned + summarized


def _extract_cleo_file_assignments(
    orchestrator_response,
    reviewer_agents: list,
    all_changes: list[FileChange],
) -> dict[str, list[str]]:
    """Build per-agent file assignments for the fallback cascade (REVUE-117).

    Priority order:
    1. Cleo AI file assignments from orchestrator_response.selected_agents[n].files
       — matched to reviewer agents by name substring (case-insensitive).
    2. Round-robin distribution if Cleo provided no file assignments at all.

    Agents not matched by Cleo get no entry in the returned dict; callers treat
    a missing entry as "all files" (AC2 safe default).
    """
    from .cleo_router import assign_files_to_agents

    if orchestrator_response is not None:
        cleo_assignments: dict[str, list[str]] = {}
        any_files_provided = False

        for entry in orchestrator_response.selected_agents:
            if not entry.files:
                continue
            any_files_provided = True
            entry_name_lower = entry.name.lower()
            for agent in reviewer_agents:
                if (agent.name.lower() == entry_name_lower
                        or agent.name.lower() in entry_name_lower
                        or entry_name_lower in agent.name.lower()):
                    cleo_assignments[agent.name] = entry.files
                    break

        if any_files_provided:
            # Return Cleo assignments; missing agents fall back to all files
            return cleo_assignments

    # Cleo provided no file assignments — use round-robin so fallback provides
    # real token reduction even without Cleo guidance
    return assign_files_to_agents(
        [a.name for a in reviewer_agents], all_changes
    )


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


# ---------------------------------------------------------------------------
# Lazy imports for orchestration (only loaded on paid tiers)
# ---------------------------------------------------------------------------

class OrchestrationModules(NamedTuple):
    """Named container for lazily-imported orchestration dependencies.

    Using a NamedTuple instead of a positional tuple means call sites access
    members by name, so adding a new dependency never silently shifts existing
    positional assignments.
    """
    load_all_agents: object
    run_agents_parallel: object
    consolidate: object
    run_shared_analysis: object
    route: object
    format_selection_message: object
    assign_files_to_agents: object
    ParallelRunResult: object


def _import_orchestration() -> "OrchestrationModules":
    """Lazy-import revue orchestration modules.

    Kept lazy so free-tier pipeline starts faster and import errors
    are surfaced only when orchestration is actually needed.
    """
    try:
        from revue.core.agent_loader import load_all_agents
        from revue.core.agent_runner import run_agents_parallel, ParallelRunResult
        from revue.core.dedup_consolidator import consolidate
        from revue.core.shared_analysis import run_shared_analysis
        from revue.core.formatting import format_selection_message
        from revue.core.cleo_router import route, assign_files_to_agents
        return OrchestrationModules(
            load_all_agents=load_all_agents,
            run_agents_parallel=run_agents_parallel,
            consolidate=consolidate,
            run_shared_analysis=run_shared_analysis,
            route=route,
            format_selection_message=format_selection_message,
            assign_files_to_agents=assign_files_to_agents,
            ParallelRunResult=ParallelRunResult,
        )
    except ImportError as exc:
        raise RuntimeError(
            f"Orchestration engine unavailable: {exc}. "
            "Ensure revue is on PYTHONPATH."
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

    @staticmethod
    def _build_metrics_collector() -> MetricsCollector:
        """Build metrics collector based on REVUE_METRICS_ENABLED env var."""
        if os.getenv("REVUE_METRICS_ENABLED"):
            from revue.infrastructure.metrics_writer import JsonlMetricsCollector
            return JsonlMetricsCollector()
        return NullMetricsCollector()

    def __init__(
        self,
        config: AIConfig,
        client: AIClient | None = None,
        license_info: LicenseInfo | None = None,
        metrics: MetricsCollector | None = None,
    ) -> None:
        self.config = config
        self._metrics = metrics or self._build_metrics_collector()
        self._client: AIClient = (
            client if client is not None
            else create_ai_client(config, metrics=self._metrics)
        )
        self._license_info: LicenseInfo | None = license_info
        # REVUE-117: set by _run_orchestration(); readable by CLI after run()
        self.last_fallback_mode: str = _FB_NORMAL

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(
        self,
        diff_path: str,
        pr_description: Optional[PRDescription] = None,
        pr_context: Optional[PRContext] = None,
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
            pr_context: Optional VCS context (platform, pr_number, repo_owner,
                repo_name, repo_path). When provided for Bitbucket, won't-fix
                reply tracking is invoked after the findings phase (REVUE-112).
                Use PRContext to extend with new metadata without changing this
                signature (OCP).
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

        # ── Won't-fix classify phase (REVUE-112 Phase 2, AC16) ───────────────
        # classify() is zero side-effects and must run BEFORE diff parsing so
        # that new allowed/disallowed patterns are in memory when agents execute.
        # OCP/DIP: platform strategy looked up from registry — no if/elif chain.
        wont_fix_svc = None
        classification = None
        if pr_context is not None:
            from revue.core.reply_tracking import get_strategy
            strategy = get_strategy(pr_context.platform)
            if strategy is not None:
                wont_fix_svc = strategy.build_wont_fix_svc(pr_context, self._client)
            else:
                print(
                    f"[revue]   💬 Won't-fix reply tracking: platform '{pr_context.platform}' "
                    "not yet supported — skipping classify/respond.",
                    flush=True,
                )

        if wont_fix_svc is not None:
            print(
                f"[revue]   💬 Won't-fix classify: PR #{pr_context.pr_number} "  # type: ignore[union-attr]
                f"({pr_context.repo_owner}/{pr_context.repo_name})",  # type: ignore[union-attr]
                flush=True,
            )
            classification = wont_fix_svc.classify(pr_context.pr_number)  # type: ignore[union-attr]
            print(
                f"[revue]   💬 Won't-fix classify result: "
                f"{len(classification.decisions)} decision(s), "
                f"{len(classification.state_updates)} state update(s), "
                f"{len(classification.patterns_to_allow)} pattern(s) to allow.",
                flush=True,
            )
            # Patch config in-memory — no file write (AC17)
            if classification.patterns_to_allow:
                self.config.allowed_patterns = list(
                    getattr(self.config, "allowed_patterns", None) or []
                ) + classification.patterns_to_allow
            if classification.patterns_to_disallow:
                self.config.disallowed_patterns = list(
                    getattr(self.config, "disallowed_patterns", None) or []
                ) + classification.patterns_to_disallow
            # Apply state updates before diff parse (AC18).
            # Delegates to wont_fix_svc — pipeline.py must not import
            # CommentState (comments layer belongs to comments, not core).
            if classification.state_updates:
                wont_fix_svc.apply_state_updates(classification, pr_context.pr_number)

        # ── Step 1: Diff parsing ──────────────────────────────────────────────
        print("[revue] ── Step 1/4: Parsing diff", flush=True)
        changes = parse_diff_file(diff_path)
        print(f"[revue]   {len(changes)} file(s) found in diff", flush=True)

        limit_result = check_diff_limit(changes, self.config.max_diff_lines)
        if limit_result.exceeded:
            print(f"[revue]   ⚠ Diff too large — {limit_result.suggestion}", flush=True)
            return [ReviewResult(file_path="[diff-limit]", response=limit_result.suggestion)], [], 0, []

        included, excluded = filter_changes(
            changes, self.config.ignore_patterns, self.config.max_diff_lines
        )
        if excluded:
            print(f"[revue]   {len(excluded)} file(s) skipped (ignored patterns)", flush=True)

        if not included:
            print("[revue]   No files to review after filtering.", flush=True)
            return [], excluded, 0, []

        # ── Step 2: AI review ────────────────────────────────────────────────
        start_ms = int(time.time() * 1000)

        if _is_premium_tier(agents_allowed):
            results, agents_used, failed_agents = self._run_orchestration(
                included, agents_allowed, pr_description=pr_description
            )
        else:
            results, agents_used, failed_agents = self._run_simplified(included, agents_allowed)

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

        # ── Won't-fix respond phase (REVUE-112 Phase 2, AC16) ────────────────
        # respond() contains all I/O: lessons PR, thread replies, store updates.
        # Runs after verdict — never before agents (RFC step 6, AC11).
        if wont_fix_svc is not None and classification is not None:
            total_threads = len(classification.decisions)
            if total_threads == 0:
                print("[revue]   💬 Won't-fix reply tracking: no developer replies found.", flush=True)
            else:
                print(
                    f"[revue]   💬 Won't-fix reply tracking: {total_threads} thread(s) with replies — responding...",
                    flush=True,
                )
                try:
                    wont_fix_svc.respond(classification, pr_context.pr_number)
                except Exception as exc:
                    print(
                        f"[revue]   ⚠ Won't-fix reply tracking failed: {exc}",
                        flush=True,
                    )
                    _log.exception(
                        "[REVUE-112] respond() failed for PR #%d",
                        pr_context.pr_number,
                    )
                    # Degrade gracefully — won't-fix posting failed but review is complete.
                resolved = len(classification.state_updates)
                reason_missing = sum(
                    1 for d in classification.decisions if d.get("decision") == "reason_missing"
                )
                not_acked = sum(
                    1 for d in classification.decisions if d.get("decision") == "not_acknowledged"
                )
                parts = []
                if resolved:
                    parts.append(f"{resolved} resolved (won't-fix)")
                if reason_missing:
                    parts.append(f"{reason_missing} awaiting reason")
                if not_acked:
                    parts.append(f"{not_acked} reaffirmed")
                summary = ", ".join(parts) if parts else "no state changes"
                print(f"[revue]   💬 Won't-fix reply tracking complete — {summary}.", flush=True)

        # Flush metrics at end of run
        run_id = str(uuid4())
        self._metrics.flush(run_id)

        return results, excluded, len(included), failed_agents

    # ------------------------------------------------------------------
    # Review paths
    # ------------------------------------------------------------------

    def _run_simplified(
        self,
        included: list[FileChange],
        agents_allowed: list[str],
    ) -> tuple[list[ReviewResult], list[str], list[str]]:
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
                ).text
                results.append(ReviewResult(file_path=fc.file_path, response=response))
                print(f"[revue]   ✓ {fc.file_path}", flush=True)

                if "code-quality-expert" in agents_allowed and "code-quality-expert" not in agents_used:
                    agents_used.append("code-quality-expert")
            except Exception as exc:
                print(f"[revue]   ✗ {fc.file_path}: {exc}", flush=True)
                results.append(ReviewResult(file_path=fc.file_path, response="", error=str(exc)))

        return results, agents_used, []

    def _run_orchestration(
        self,
        included: list[FileChange],
        agents_allowed: list[str],
        pr_description: Optional[PRDescription] = None,
    ) -> tuple[list[ReviewResult], list[str], list[str]]:
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

        mods = _import_orchestration()
        load_all_agents = mods.load_all_agents
        run_agents_parallel = mods.run_agents_parallel
        consolidate = mods.consolidate
        run_shared_analysis = mods.run_shared_analysis
        route = mods.route
        format_selection_message = mods.format_selection_message
        assign_files_to_agents = mods.assign_files_to_agents
        ParallelRunResult = mods.ParallelRunResult

        # 1. Shared analysis — one AI call to classify the diff for all agents
        print("[revue]   Running shared diff analysis...", flush=True)
        try:
            shared = run_shared_analysis(
                included, self._client, provider=self.config.provider,
            )
            if shared.success:
                # REVUE-95: log human-readable selection transparency message
                if shared.orchestrator_response and (
                    shared.orchestrator_response.detected_areas or
                    shared.orchestrator_response.selected_agents
                ):
                    msg = format_selection_message(shared.orchestrator_response)
                    for line in msg.splitlines():
                        print(f"[revue]   {line}", flush=True)
                else:
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
            from revue.core.shared_analysis import SharedAnalysisResult
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

        # 2c. Pattern injection (REVUE-94) — inject allowed/disallowed patterns
        #     from .revue.yml into each agent's system prompt.
        if self.config.allowed_patterns or self.config.disallowed_patterns:
            inject_patterns(
                allowed_agents,
                self.config.allowed_patterns,
                self.config.disallowed_patterns,
            )
            print(
                f"[revue]   Pattern guidance injected — "
                f"{len(self.config.allowed_patterns)} allowed, "
                f"{len(self.config.disallowed_patterns)} disallowed.",
                flush=True,
            )

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
        max_parallel = self.config.max_parallel_agents
        mode_label = "sequentially" if max_parallel == 1 else f"in parallel (max {max_parallel})"
        print(f"[revue]   Running {len(reviewer_agents)} reviewer(s) {mode_label}...", flush=True)

        if max_parallel > 1:
            # Parallel mode: no fallback cascade (AC10 — cascade undefined for parallel)
            parallel_result = run_agents_parallel(
                reviewer_agents, included, shared,
                timeout_seconds=self.config.agent_timeout_seconds,
                max_workers=max_parallel,
            )
        else:
            # Sequential mode: per-agent execution with rate-limit fallback cascade (REVUE-117)
            fallback_mode = _FB_NORMAL
            file_assignments = _extract_cleo_file_assignments(
                shared.orchestrator_response if shared else None,
                reviewer_agents,
                included,
            )
            seq_results = []
            seq_start = time.monotonic()

            for agent in reviewer_agents:
                agent_changes = _build_agent_changes(
                    agent.name, fallback_mode, included, file_assignments
                )
                run_result = run_agents_parallel(
                    [agent], agent_changes, shared,
                    timeout_seconds=self.config.agent_timeout_seconds,
                    max_workers=1,
                )
                agent_result = run_result.agent_results[0] if run_result.agent_results else None

                if agent_result is not None and _is_rate_limit_error(agent_result.error):
                    next_mode = _next_fallback(fallback_mode)
                    if next_mode is not None:
                        fallback_mode = next_mode
                        print(
                            f"[revue]   \u26a0 Rate limit hit on {agent.name} — "
                            f"switching to {fallback_mode.replace('_', '-')} mode "
                            f"(reduced context)",
                            flush=True,
                        )
                        agent_changes = _build_agent_changes(
                            agent.name, fallback_mode, included, file_assignments
                        )
                        run_result = run_agents_parallel(
                            [agent], agent_changes, shared,
                            timeout_seconds=self.config.agent_timeout_seconds,
                            max_workers=1,
                        )
                        agent_result = run_result.agent_results[0] if run_result.agent_results else None
                    # If still failing at context_lite or next_mode is None: keep the failure

                if agent_result is not None:
                    seq_results.append(agent_result)

            self.last_fallback_mode = fallback_mode
            parallel_result = ParallelRunResult(
                agent_results=seq_results,
                total_elapsed=time.monotonic() - seq_start,
            )

        agents_used = [r.agent_name for r in parallel_result.agent_results if r.success]
        failed = [r for r in parallel_result.agent_results if not r.success]
        if failed:
            for f in failed:
                reason = "timed out" if f.timed_out else _short_error(f.error)
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

        failed_agent_names = [f.agent_name for f in failed]
        return results, agents_used, failed_agent_names
