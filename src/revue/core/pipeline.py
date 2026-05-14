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
import logging
import os
import re
import sys
import dataclasses
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple, Optional
from uuid import uuid4

from .agent_names import NOVA, ORCHESTRATOR, VEX
from ..comments.summary_builder import _AGENT_EMOJIS
from .ai_config import AIConfig
from .ai_client import AIClient, create_ai_client


def resolve_synthesis_client(
    config: AIConfig,
    main_client: AIClient,
    metrics: "MetricsCollector | None" = None,
) -> AIClient:
    """Return the AI client the reasoning tier should use (Nova + Vex).

    Both Nova (synthesis) and Vex (verification) run on this client — they
    are reasoning-tier agents and MUST share a model (REVUE-240). When
    ``config.synthesis_model`` is set and differs from ``config.model``, a
    separate client is built with the override. Otherwise the reviewer
    client is reused (no extra API initialisation).
    """
    override = config.synthesis_model
    if override and override != config.model:
        synthesis_config = dataclasses.replace(config, model=override)
        return create_ai_client(synthesis_config, metrics=metrics)
    return main_client
from .cleo_router import _INFRASTRUCTURE_AGENTS
from .metrics import (
    MetricsCollector,
    NullMetricsCollector,
    RoutingMetricsData,
    SynthesisMetricsData,
)
from .diff_parser import parse_diff_file, filter_changes
from .diff_limit import check_diff_limit, DiffLimitResult
from .license_validator import LicenseInfo, validate as validate_license
from .models import FileChange, PRContext
from .pr_description_adapter import PRDescription
from .pr_context import PRContextExtractor
from .pattern_injection import inject_patterns
from .usage_tracker import check_reviews_left, track as track_usage
from .reviewer_tools import build_reviewer_read_file_tool, build_reviewer_toolset
from .run_verdict import AgentStatus, RunVerdict, compute_run_verdict

_log = logging.getLogger(__name__)

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
    "vex": "vex",
    # Server-side alias mismatch — API returns these names instead of the canonical
    # "security-expert" / "architecture-expert". Remove once REVUE-204 is resolved.
    "security-analyst": "zara",
    "architecture-reviewer": "leo",
}

# Virtual agents implemented in pipeline code, not as agent files.
# These are valid licence names but will never appear in load_all_agents() output.
_VIRTUAL_AGENTS = frozenset({"orchestrator", "sage"})


_HTTP_CODE_RE = re.compile(r"\b([45]\d{2})\b")


def _extract_http_code(error: str) -> str:
    """Return the first 4xx/5xx HTTP code in the error string, or empty.

    Operators triaging an agent failure need the code to decide whether
    to retry (transient 5xx), back off (429), check credentials (401/403),
    or escalate (500). The classifier message strips the rest of the
    payload — without preserving the code, we'd lose the routing signal.
    """
    m = _HTTP_CODE_RE.search(error)
    return m.group(1) if m else ""


def _classify_error_body(error: str) -> str:
    """Condense a raw error string to a single human-readable line.

    Rate-limit JSON blobs and other verbose SDK errors collapse to a known
    classifier ("rate limit exceeded", "authentication error", …) with the
    originating HTTP code preserved in parentheses. Unknown errors fall back
    to the first line, capped at 120 chars.

    Pure single-responsibility helper: no prefix/suffix decoration, no
    knowledge of ``error_type`` or ``call_site`` — those belong to the
    caller (``_short_error``) so this function stays trivially testable
    against just the raw string input.
    """
    if not error:
        return "unknown error"
    low = error.lower()
    code = _extract_http_code(error)
    code_suffix = f" ({code})" if code else ""
    if "rate_limit" in low or "rate limit" in low or "429" in low:
        return f"rate limit exceeded{code_suffix} (see ❌ RATE LIMIT ERROR above)"
    if "timeout" in low or "timed out" in low:
        return "timed out"
    if "401" in low or "403" in low or "authentication" in low or "unauthorized" in low:
        return f"authentication error{code_suffix}"
    if "500" in low or "502" in low or "503" in low:
        return f"server error{code_suffix}"
    first_line = error.split("\n")[0]
    return first_line[:120] + ("…" if len(first_line) > 120 else "")


def _short_error(error: str, *, error_type: str = "", call_site: str = "") -> str:
    """Decorate a classified error body with ``[Type]`` prefix and
    ``(at Client.method)`` suffix so the '⚠ Agent X failed:' log line tells
    operators *what* failed and *where* in one glance.

    REVUE-241: classification is delegated to ``_classify_error_body``;
    this function only owns the prefix/suffix formatting (SRP).
    """
    body = _classify_error_body(error)
    prefix = f"[{error_type}] " if error_type else ""
    suffix = f" (at {call_site})" if call_site else ""
    return f"{prefix}{body}{suffix}"


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
        from revue.core.shared_analysis import run_shared_analysis
        from revue.core.formatting import format_selection_message
        from revue.core.cleo_router import route, assign_files_to_agents
        return OrchestrationModules(
            load_all_agents=load_all_agents,
            run_agents_parallel=run_agents_parallel,
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


# ---------------------------------------------------------------------------
# Consolidation helpers (REVUE-210)
# ---------------------------------------------------------------------------

_SEVERITY_NORMALISE: dict[str, str] = {
    "critical": "high",  # AgentFinding Literal has no "critical"
}


def _air_to_agent_finding(air: object) -> object:
    """Convert an AIReview to an AgentFinding for the new Consolidator pipeline.

    Handles severity normalisation (AIReview may carry "critical" which is
    absent from AgentFinding's Literal union).
    """
    from revue.comments.models import AgentFinding
    sev = getattr(air, "severity", "medium")
    sev = _SEVERITY_NORMALISE.get(sev, sev)
    if sev not in ("high", "medium", "low", "info"):
        sev = "medium"
    return AgentFinding(
        file_path=getattr(air, "file_path", ""),
        line_number=max(1, getattr(air, "line_number", 1)),
        severity=sev,
        issue=getattr(air, "issue", ""),
        suggestion=getattr(air, "suggestion", ""),
        confidence=float(getattr(air, "confidence", 0.5)),
        category=getattr(air, "category", "general"),
        agent_name=getattr(air, "agent_name", ""),
        code_replacement=getattr(air, "code_replacement", None),
        replacement_line_count=getattr(air, "replacement_line_count", 1),
        snippet=getattr(air, "snippet", ""),
        language=getattr(air, "language", "unknown"),
    )


def _consolidated_to_dict(finding: object) -> dict:
    """Serialise a ConsolidatedFinding to a JSON-compatible dict for ReviewResult.

    Produces fields compatible with cli.py _extract_finding_fields() and the
    existing posting pipeline.
    """
    attribution = getattr(finding, "attribution", [])
    attribution_list = [
        {"agent_name": a.agent_name, "category": a.category}
        for a in attribution
    ]
    first_agent = attribution[0].agent_name if attribution else ""
    return {
        "file_path": getattr(finding, "file_path", ""),
        "line_number": getattr(finding, "line_number", 1),
        "severity": getattr(finding, "severity", "medium"),
        "issue": getattr(finding, "issue", ""),
        "suggestion": getattr(finding, "suggestion", ""),
        "confidence": getattr(finding, "confidence", 0.5),
        "category": getattr(finding, "category", "general"),
        "agent_name": first_agent,
        "code_replacement": getattr(finding, "code_replacement", None),
        "replacement_line_count": getattr(finding, "replacement_line_count", 1),
        "snippet": getattr(finding, "snippet", ""),
        "attribution": attribution_list,
        "group_type": getattr(finding, "group_type", "singleton"),
    }


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
        synthesis_client: AIClient | None = None,
    ) -> None:
        self.config = config
        self._metrics = metrics or self._build_metrics_collector()
        self._client: AIClient = (
            client if client is not None
            else create_ai_client(config, metrics=self._metrics)
        )
        self.synthesis_client: AIClient = (
            synthesis_client if synthesis_client is not None
            else resolve_synthesis_client(config, self._client, metrics=self._metrics)
        )
        self._license_info: LicenseInfo | None = license_info
        # REVUE-117: set by _run_orchestration(); readable by CLI after run()
        self.last_fallback_mode: str = _FB_NORMAL
        # REVUE-241: per-agent failure details (error_type, call_site, reason)
        # captured from the latest run so the CLI summary can surface them
        # without re-parsing log lines. List of dicts with keys: name,
        # error_type, call_site, reason, timed_out.
        self.last_failed_agent_details: list[dict[str, str]] = []
        # REVUE-246: per-agent statuses + run-level four-state verdict captured
        # from the latest run so Step 4/4 can render the breakdown and the CLI
        # / metrics can read it after run() returns. Empty until orchestration
        # populates it; the simplified free-tier loop populates a synthetic
        # findings status per file.
        self.last_agent_statuses: list[Any] = []
        self.last_run_verdict: "Any | None" = None

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
        license_info = self._license_info or validate_license(
            repo_id=self.config.gitlab_project_id,
        )
        check_reviews_left(license_info.reviews_left)

        agents_allowed = license_info.agents_allowed or ["orchestrator"]
        _log.info(
            "[revue] Licence validated — %s plan, %s",
            license_info.tier,
            license_info.reviews_left_display,
        )
        _log.info("[revue] Active agents: %s", ', '.join(agents_allowed))

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
                _log.warning(
                    "[revue]   💬 Won't-fix reply tracking: platform '%s' "
                    "not yet supported — skipping classify/respond.",
                    pr_context.platform,
                )

        if wont_fix_svc is not None:
            _log.info(
                "[revue]   💬 Won't-fix classify: PR #%d (%s/%s)",
                pr_context.pr_number,  # type: ignore[union-attr]
                pr_context.repo_owner,  # type: ignore[union-attr]
                pr_context.repo_name,  # type: ignore[union-attr]
            )
            classification = wont_fix_svc.classify(pr_context.pr_number)  # type: ignore[union-attr]
            _log.info(
                "[revue]   💬 Won't-fix classify result: %d decision(s), %d state update(s), %d pattern(s) to allow.",
                len(classification.decisions),
                len(classification.state_updates),
                len(classification.patterns_to_allow),
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
        _log.info("[revue] ── Step 1/4: Parsing diff")
        changes = parse_diff_file(diff_path)
        _log.info("[revue]   %d file(s) found in diff", len(changes))

        limit_result = check_diff_limit(changes, self.config.max_diff_lines)
        if limit_result.exceeded:
            _log.warning("[revue]   ⚠ Diff too large — %s", limit_result.suggestion)
            return [ReviewResult(file_path="[diff-limit]", response=limit_result.suggestion)], [], 0, []

        included, excluded = filter_changes(
            changes, self.config.ignore_patterns, self.config.max_diff_lines
        )
        if excluded:
            _log.info("[revue]   %d file(s) skipped (ignored patterns)", len(excluded))

        if not included:
            _log.info("[revue]   No files to review after filtering.")
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
        _log.info(
            "[revue] ── Step 3/4: Consolidation — %d finding(s) across %d file(s)",
            total_findings,
            len(results),
        )

        # ── Step 4: Verdict ───────────────────────────────────────────────────
        _log.info("[revue] ── Step 4/4: Verdict")
        # REVUE-246 AC10: render the four-state run verdict and the per-agent
        # breakdown so a silent bail-out can no longer hide behind a 0-finding
        # severity count. ``last_agent_statuses`` is populated by the
        # orchestration path; the free-tier simplified path leaves it empty
        # and the legacy severity-based verdict logs below as a fallback.
        run_verdict: "RunVerdict | None" = None
        if self.last_agent_statuses:
            run_verdict = compute_run_verdict(self.last_agent_statuses)
            self.last_run_verdict = run_verdict
            # REVUE-246 AC7: feed the verdict into the metrics collector so
            # .revue/metrics.jsonl carries clean_count / finding_count /
            # error_count + errors_by_code for the run.
            from .metrics import RunVerdictMetricsData
            self._metrics.record_run_verdict(RunVerdictMetricsData(
                verdict=run_verdict.verdict,
                clean_count=run_verdict.clean_count,
                finding_count=run_verdict.finding_count,
                error_count=run_verdict.error_count,
                errors_by_code=dict(run_verdict.errors_by_code),
            ))
            _log.info(
                "[revue]   Verdict: %s  (%d clean, %d findings, %d error)",
                run_verdict.verdict.upper(),
                run_verdict.clean_count,
                run_verdict.finding_count,
                run_verdict.error_count,
            )
            for agent_status in run_verdict.breakdown:
                if agent_status.status == "error":
                    _log.warning(
                        "[revue]     • %s → error(%s)",
                        agent_status.agent_name, agent_status.error_code or "?",
                    )
                elif agent_status.status == "clean":
                    _log.info(
                        "[revue]     • %s → clean (confidence %s)",
                        agent_status.agent_name,
                        f"{agent_status.confidence:.2f}" if agent_status.confidence is not None else "?",
                    )
                else:
                    _log.info(
                        "[revue]     • %s → %d finding(s)",
                        agent_status.agent_name, agent_status.finding_count,
                    )

        high_med = _count_severity(results, ("high", "medium"))
        low = _count_severity(results, ("low", "info"))
        if high_med > 0:
            _log.warning(
                "[revue]   ⚠️ Escalate to human — %d high/medium finding(s) require attention",
                high_med,
            )
        elif low > 0:
            _log.info("[revue]   ✓ Accept with notes — %d low-severity finding(s) only", low)
        else:
            _log.info("[revue]   ✓ Looks good — no findings")

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
                _log.info("[revue]   💬 Won't-fix reply tracking: no developer replies found.")
            else:
                _log.info(
                    "[revue]   💬 Won't-fix reply tracking: %d thread(s) with replies — responding...",
                    total_threads,
                )
                try:
                    wont_fix_svc.respond(classification, pr_context.pr_number)
                except Exception as exc:
                    _log.warning(
                        "[revue]   ⚠ Won't-fix reply tracking failed: %s",
                        exc,
                    )
                    _log.exception(
                        "respond() failed for PR #%d",
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
                _log.info("[revue]   💬 Won't-fix reply tracking complete — %s.", summary)

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
        _log.info("[revue] ── Step 2/4: AI review (simplified) — %d file(s)", len(included))

        agents_used: list[str] = ["orchestrator"] if "orchestrator" in agents_allowed else []
        results: list[ReviewResult] = []

        for i, fc in enumerate(included, 1):
            _log.info("[revue]   [%d/%d] %s...", i, len(included), fc.file_path)
            try:
                prompt = (
                    f"Review this code diff for {fc.file_path}:\n\n"
                    f"{fc.diff}\n\nProvide findings as JSON."
                )
                response = self._client.complete(
                    [{"role": "user", "content": prompt}],
                    max_tokens=self.config.ai_max_tokens,
                    temperature=self.config.ai_temp,
                    agent_name=ORCHESTRATOR,
                ).text
                results.append(ReviewResult(file_path=fc.file_path, response=response))
                _log.info("[revue]   ✓ %s", fc.file_path)

                if "code-quality-expert" in agents_allowed and "code-quality-expert" not in agents_used:
                    agents_used.append("code-quality-expert")
            except Exception as exc:
                _log.warning("[revue]   ✗ %s: %s", fc.file_path, exc)
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
        _log.info(
            "[revue] ── Step 2/4: AI review (orchestrated) — %d file(s), %d agent(s) available",
            len(included),
            len(agents_allowed),
        )

        mods = _import_orchestration()
        load_all_agents = mods.load_all_agents
        run_agents_parallel = mods.run_agents_parallel
        run_shared_analysis = mods.run_shared_analysis
        route = mods.route
        format_selection_message = mods.format_selection_message
        assign_files_to_agents = mods.assign_files_to_agents
        ParallelRunResult = mods.ParallelRunResult

        # 1. Shared analysis — one AI call to classify the diff for all agents
        _log.info("[revue]   Running shared diff analysis...")
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
                        _log.info("[revue]   %s", line)
                else:
                    _log.info("[revue]   Shared analysis: %s...", shared.summary[:80])
            else:
                # Surface the actual error so it's diagnosable in CI logs
                _log.warning(
                    "[revue]   Shared analysis unavailable (%s) — running all agents as fallback.",
                    shared.error,
                )
        except Exception as exc:
            _log.warning("[revue]   Shared analysis failed (%s) — using fallback.", exc)
            from revue.core.shared_analysis import SharedAnalysisResult
            shared = SharedAnalysisResult.fallback(
                languages=[fc.file_path.rsplit(".", 1)[-1] for fc in included]
            )

        # 1b. Build ReadFileTool for reviewer agents (REVUE-241 — lazy full-file reads).
        # The decision (flag check), construction, and error policy live in
        # reviewer_tools.py — pipeline.py only asks for a tool. Resolve repo_root
        # once and reuse it for Nova/Vex below so all tool-using agents share
        # exactly the same sandbox root.
        repo_root = Path.cwd()
        # REVUE-243: build the targeted-retrieval toolset (read_file +
        # read_lines + find_code). The single ``reviewer_tool_use`` flag still
        # gates the whole set. read_file alone stays available as a fallback
        # for the Nova consolidator path which calls build_reviewer_read_file_tool.
        reviewer_toolset = build_reviewer_toolset(self.config, included, repo_root=repo_root)
        read_file_tool = reviewer_toolset.read_file

        # 2. Load and filter agents by license (REVUE-99: resolve licence names → agent names)
        _log.info("[revue]   Loading agents...")
        all_agents = load_all_agents(
            self.config, self._client,
            read_file_tool=reviewer_toolset.read_file,
            read_lines_tool=reviewer_toolset.read_lines,
            find_code_tool=reviewer_toolset.find_code,
        )
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
                _log.warning(
                    "[revue]   ⚠ Unknown agent '%s' in licence — skipping.",
                    licence_name,
                )

        # Infrastructure agents (cleo, nova, vex) are pipeline components, not
        # user-license-gated reviewers. They must survive even when the license
        # server returns an `agents_allowed` list that predates their existence.
        for infra_name in _INFRASTRUCTURE_AGENTS:
            if infra_name in agents_by_name:
                resolved_agent_names.add(infra_name)

        allowed_agents = [agents_by_name[n] for n in resolved_agent_names if n in agents_by_name]

        if not allowed_agents:
            _log.warning(
                "[revue]   ⚠ No matching agents found for allowed list — "
                "falling back to simplified review.",
            )
            return self._run_simplified(included, agents_allowed)

        # AC4: log with display_name + role for customer-readable output
        def _agent_label(a) -> str:
            role = getattr(getattr(a, "_def", a), "role", "") or ""
            display = getattr(getattr(a, "_def", a), "display_name", "") or a.name
            # Trim role to the first clause (before " — ") for brevity
            short_role = role.split(" — ")[0].split(" — ")[0].strip()
            return f"{display} [{short_role}]" if short_role else display

        _log.info(
            "[revue]   Agents loaded (%d): %s",
            len(allowed_agents),
            ", ".join(_agent_label(a) for a in allowed_agents),
        )

        # 2b. PR context injection (REVUE-84) — prepend filtered PR description
        #     sections to each agent's system prompt before dispatch.
        if pr_description is not None:
            extractor = PRContextExtractor(pr_description)
            _inject_pr_context(allowed_agents, extractor)
            _log.info("[revue]   PR context injected — smart filtering active.")

        # 2c. Pattern injection (REVUE-94) — inject allowed/disallowed patterns
        #     from .revue.yml into each agent's system prompt.
        if self.config.allowed_patterns or self.config.disallowed_patterns:
            inject_patterns(
                allowed_agents,
                self.config.allowed_patterns,
                self.config.disallowed_patterns,
            )
            _log.info(
                "[revue]   Pattern guidance injected — %d allowed, %d disallowed.",
                len(self.config.allowed_patterns),
                len(self.config.disallowed_patterns),
            )

        # 3. Cleo routing — select agents relevant to this diff
        _log.info("[revue]   Routing files to agents (Cleo)...")
        try:
            _team_selection, routed_agents = route(included, allowed_agents, shared, self.config)
            if not routed_agents:
                routed_agents = allowed_agents  # fallback: run all allowed agents
            # AC5: role-aware routing log
            _log.info(
                "[revue]   Routed to: %s",
                ', '.join(_agent_label(a) for a in routed_agents),
            )
            # REVUE-170 AC5: record routing observability metrics
            ai_suggested = (
                [a.name for a in shared.orchestrator_response.selected_agents]
                if shared and shared.orchestrator_response
                else []
            )
            self._metrics.record_routing(RoutingMetricsData(
                ai_suggested_agents=ai_suggested,
                algorithm_selected_agents=_team_selection.algorithm_filtered_agents,
                final_agents=[a.name for a in routed_agents],
                routing_source="ai_assisted" if ai_suggested else "algorithm_fallback",
                model_used=self.config.model,
            ))
        except Exception as exc:
            _log.warning("[revue]   Cleo routing failed (%s) — running all allowed agents.", exc)
            routed_agents = allowed_agents

        # 4. Parallel agent execution — strip infrastructure agents (cleo/nova)
        #    they are called separately as router/consolidator, not reviewers.
        reviewer_agents = [a for a in routed_agents if a.name not in _INFRASTRUCTURE_AGENTS]
        if len(reviewer_agents) < len(routed_agents):
            stripped = [a.name for a in routed_agents if a.name in _INFRASTRUCTURE_AGENTS]
            _log.info(
                "[revue]   (Infrastructure agents excluded from review pool: %s)",
                ', '.join(stripped),
            )
        max_parallel = self.config.max_parallel_agents
        mode_label = "sequentially" if max_parallel == 1 else f"in parallel (max {max_parallel})"
        _log.info("[revue]   Running %d reviewer(s) %s...", len(reviewer_agents), mode_label)

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
                        _log.warning(
                            "[revue]   \u26a0 Rate limit hit on %s — switching to %s mode (reduced context)",
                            agent.name,
                            fallback_mode.replace('_', '-'),
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
            details: list[dict[str, str]] = []
            for f in failed:
                if f.timed_out:
                    reason = "timed out"
                else:
                    reason = _short_error(
                        f.error, error_type=f.error_type, call_site=f.call_site,
                    )
                _log.warning("[revue]   ⚠ Agent %s failed: %s", f.agent_name, reason)
                details.append({
                    "name": f.agent_name,
                    "error_type": f.error_type,
                    "call_site": f.call_site,
                    "reason": reason,
                    "timed_out": "true" if f.timed_out else "false",
                })
            self.last_failed_agent_details = details

        # REVUE-246: per-agent status snapshot. Built from AgentRunResult.status
        # so the run-level verdict can distinguish clean / findings / error —
        # the legacy "any agent with findings == 0 was either clean OR error"
        # ambiguity is exactly what the three-state contract eliminates.
        agent_statuses: list[AgentStatus] = []
        for r in parallel_result.agent_results:
            if r.timed_out:
                agent_statuses.append(AgentStatus(
                    agent_name=r.agent_name, status="error",
                    error_code="internal_error",
                ))
                continue
            agent_statuses.append(AgentStatus(
                agent_name=r.agent_name,
                status=r.status or "findings",
                finding_count=len(r.findings),
                error_code=r.error_code,
                summary=r.summary,
                confidence=r.confidence,
            ))
        self.last_agent_statuses = agent_statuses

        # Per-agent finding count — makes 0-finding runs diagnosable
        for r in parallel_result.agent_results:
            if r.success:
                _log.info("[revue]   [%s] → %d finding(s)", r.agent_name, len(r.findings))

        _log.info("[revue]   %d agent(s) succeeded, %d failed.", len(agents_used), len(failed))

        # AC3: if ALL reviewer agents failed, raise AllAgentsFailedError.
        # The caller (CLI) decides whether to sys.exit or handle differently.
        # This keeps process control out of the pipeline (SRP/OCP).
        if reviewer_agents and not agents_used:
            first_error = failed[0].error if failed else "unknown"
            raise AllAgentsFailedError(first_error)

        # 5. Consolidation — group, synthesise, and post-process findings (REVUE-210)
        _log.info("[revue]   %s Nova is consolidating findings...", _AGENT_EMOJIS[NOVA])
        raw_findings = [
            finding
            for r in parallel_result.agent_results
            if r.success
            for finding in r.findings
        ]
        original_count = len(raw_findings)

        # Infrastructure agents (Nova, Vex) live in routed_agents but are
        # stripped from reviewer_agents. Look up their system prompts here.
        nova_system_prompt: str | None = None
        vex_system_prompt: str | None = None
        for agent in routed_agents:
            if not (hasattr(agent, "definition") and agent.definition):
                continue
            if agent.name == "nova":
                nova_system_prompt = getattr(agent.definition, "system_prompt", None)
            elif agent.name == "vex":
                vex_system_prompt = getattr(agent.definition, "system_prompt", None)

        # P4: surface silent prompt fallback. A missing/unparseable vex.yaml
        # would otherwise leave Vex running on a weaker built-in prompt with
        # no examples — degraded mode that's invisible in metrics.
        if vex_system_prompt is None:
            _log.warning(
                "[revue]   Vex system prompt not found in routed_agents (vex.yaml may be missing "
                "or unparseable). Falling back to the built-in default prompt — verification "
                "quality will be degraded."
            )

        diff_by_file = {fc.file_path: fc.diff for fc in included}
        # repo_root was resolved at step 1b so all tool-using agents (reviewers,
        # Nova, Vex) share exactly the same sandbox anchor.

        # summary_sink accumulates unanchored findings for the PR-level summary comment.
        # BodyBuilder reads it when building the summary block (wired in REVUE-poster, next story).
        summary_sink: list = []
        consolidator, vex_post_processor = self._build_consolidator(
            nova_system_prompt=nova_system_prompt,
            vex_system_prompt=vex_system_prompt,
            diff_by_file=diff_by_file,
            repo_root=repo_root,
            summary_sink=summary_sink,
        )
        agent_findings = [_air_to_agent_finding(f) for f in raw_findings]
        consolidated = consolidator.consolidate(agent_findings)

        _log.info(
            "[revue]   %d findings → %d after consolidation",
            original_count,
            len(consolidated),
        )

        v_counts = vex_post_processor.verdict_counts
        f_counts = vex_post_processor.failure_counts
        if any(v_counts.values()) or any(f_counts.values()):
            # REVUE-248 — verifier_exception was split into five error_type buckets.
            # Surface them individually so dogfood can see *why* Vex failed without
            # scraping log files.
            _log.info(
                "[revue]   %s Vex: apply=%d drop_cr=%d reject=%d | "
                "no_cr=%d read_err=%d "
                "timeout=%d bad_json=%d 5xx=%d 4xx=%d other=%d",
                _AGENT_EMOJIS[VEX],
                v_counts.get("apply", 0),
                v_counts.get("drop_cr_keep_prose", 0),
                v_counts.get("reject_finding", 0),
                f_counts.get("no_code_replacement", 0),
                f_counts.get("read_error", 0),
                f_counts.get("timeout", 0),
                f_counts.get("malformed_json", 0),
                f_counts.get("http_5xx", 0),
                f_counts.get("http_4xx", 0),
                f_counts.get("other", 0),
            )
            # Persist Vex tallies to metrics.jsonl so a later audit can see
            # rejection rate / failure mix without re-parsing terminal output.
            from revue.core.metrics import VexMetricsData
            self._metrics.record_vex(VexMetricsData(
                verdict_counts=dict(v_counts),
                failure_counts=dict(f_counts),
            ))

        # Record synthesis metrics (REVUE-179 AC4)
        synthesised_count = sum(1 for f in consolidated if f.group_type != "singleton")
        self._metrics.record_synthesis(SynthesisMetricsData(
            total_findings=len(consolidated),
            synthesised_count=synthesised_count,
            synthesis_events=[],
        ))

        # 6. Convert ConsolidatedFinding → ReviewResult for the shared result format
        results: list[ReviewResult] = []
        for finding in consolidated:
            payload = json.dumps({"findings": [_consolidated_to_dict(finding)]})
            results.append(ReviewResult(file_path=finding.file_path, response=payload))

        # Ensure orchestrator tracked
        if "orchestrator" not in agents_used:
            agents_used.insert(0, "orchestrator")

        failed_agent_names = [f.agent_name for f in failed]
        return results, agents_used, failed_agent_names

    # ------------------------------------------------------------------
    # Consolidator construction (REVUE-240 — reasoning-tier invariant)
    # ------------------------------------------------------------------

    def _build_consolidator(
        self,
        *,
        nova_system_prompt: str | None,
        vex_system_prompt: str | None,
        diff_by_file: dict[str, str],
        repo_root: Path,
        summary_sink: list,
    ):
        """Wire Nova (synthesis) and Vex (verification) onto the reasoning-tier client.

        REVUE-240 invariant: Vex and Nova MUST share `self.synthesis_client`.
        They are both reasoning-tier agents — running them on divergent models
        breaks coherence. The identity is locked by a runtime test (see
        test_vex_and_nova_share_the_reasoning_tier_client). Future refactors
        that split this wire will fail that test.
        """
        from revue.comments.consolidator import (
            Consolidator,
            NoOpSuggestionDropper,
            NovaSingleShotStrategy,
            ProximityAndCountGroupingStrategy,
            UnanchoredFindingExtractor,
        )
        from revue.comments._verifier import VexVerifier, VexVerifyPostProcessor

        reasoning_client = self.synthesis_client  # shared by Nova + Vex (REVUE-240)

        # P10: Vex runs its verification calls concurrently up to the same
        # parallelism budget already configured for reviewer agents.
        vex_post_processor = VexVerifyPostProcessor(
            verifier=VexVerifier(
                ai_client=reasoning_client,
                system_prompt=vex_system_prompt,
            ),
            repo_root=repo_root,
            diff_by_file=diff_by_file,
            max_workers=self.config.max_parallel_agents,
        )

        # P3: NoOpSuggestionDropper runs FIRST so trivial no-ops are filtered
        # before they burn Vex LLM calls. Vex then verifies the remaining
        # real candidates. UnanchoredFindingExtractor runs last because it
        # observes the post-verification outcome.
        consolidator = Consolidator(
            grouping=ProximityAndCountGroupingStrategy(
                n=self.config.consolidation_proximity_lines,
                k=self.config.consolidation_max_group_size,
            ),
            synthesis=NovaSingleShotStrategy(
                ai_client=reasoning_client,
                system_prompt=nova_system_prompt,
                diff_by_file=diff_by_file,
                repo_root=repo_root,
            ),
            post_processors=[
                NoOpSuggestionDropper(),
                vex_post_processor,
                UnanchoredFindingExtractor(summary_sink),
            ],
        )

        # REVUE-240 invariant — defensive runtime check. The test
        # test_vex_and_nova_share_the_reasoning_tier_client locks identity at
        # CI time; this assertion catches the same divergence in production if
        # a future refactor accidentally splits the wire between Nova's
        # synthesis client and Vex's verifier client.
        nova_client = consolidator._synthesis._client
        vex_client = vex_post_processor._verifier._client
        assert vex_client is nova_client, (
            "REVUE-240 invariant violated: Vex and Nova must share the same "
            "reasoning-tier client. Got nova_client=%r, vex_client=%r"
            % (nova_client, vex_client)
        )

        return consolidator, vex_post_processor
