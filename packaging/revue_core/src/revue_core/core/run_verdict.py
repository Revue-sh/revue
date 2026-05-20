"""Run-level verdict aggregation across reviewer agents (REVUE-246 AC5+AC6).

After every agent has produced an :class:`AgentVerdict`, the pipeline
composes a single ``RunVerdict`` summarising the run. The verdict is a
closed-set discriminator (``clean`` / ``findings`` / ``degraded`` / ``failed``)
so consumers — CLI display, metrics writer, downstream automation — can
branch on a stable enum rather than re-deriving meaning from severity counts.

Threshold semantics (per REVUE-246 spec, "Contentious points #2"):

* ≥ 50% of agents errored → ``degraded``. The threshold is somewhat
  arbitrary; pinned here so a future change is explicit.
* 100% errored → ``failed`` (no agent produced a real review).
* Any error below the 50% threshold leaves the run as either ``findings``
  or ``clean``, depending on the non-errored agents.

State assignment is registry-driven (see ``_VERDICT_RULES``) — closed-set
discriminator dispatch, not an if/elif chain. See the
``feedback_no_platform_elif`` rule in auto-memory.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Final, Sequence


# ---------------------------------------------------------------------------
# AgentStatus — one entry per agent in the run
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentStatus:
    """The minimum a verdict needs to know about one agent's outcome."""
    agent_name: str
    status: str  # "findings" | "clean" | "error"
    finding_count: int = 0
    error_code: "str | None" = None
    confidence: "float | None" = None
    summary: "str | None" = None


# ---------------------------------------------------------------------------
# RunVerdict — what the pipeline / CLI reports at end of run
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunVerdict:
    """Closed-set run outcome plus per-agent breakdown (AC5 visibility)."""
    verdict: str  # "clean" | "findings" | "degraded" | "failed"
    clean_count: int
    finding_count: int   # number of agents whose status was "findings"
    error_count: int
    errors_by_code: dict[str, int]
    breakdown: list[AgentStatus] = field(default_factory=list)

    @property
    def total_agents(self) -> int:
        return self.clean_count + self.finding_count + self.error_count


# ---------------------------------------------------------------------------
# Internal verdict-rule registry — closed-set discriminator
# ---------------------------------------------------------------------------


def _rule_failed(total: int, errors: int, findings: int, cleans: int) -> bool:
    # Every agent errored — or the run had no agents at all.
    return total == 0 or errors == total


def _rule_degraded(total: int, errors: int, findings: int, cleans: int) -> bool:
    # ≥ 50% errors — but not all (failed catches the all-errored case first).
    return total > 0 and errors * 2 >= total


def _rule_findings(total: int, errors: int, findings: int, cleans: int) -> bool:
    return findings > 0


def _rule_clean(total: int, errors: int, findings: int, cleans: int) -> bool:
    return cleans == total and total > 0


# Order matters. The first rule whose predicate returns True wins. ``failed``
# precedes ``degraded`` so a 100%-errored run isn't classified as merely
# degraded. ``findings`` precedes ``clean`` so a mixed run is "findings".
_VERDICT_RULES: Final[list[tuple[str, Callable[[int, int, int, int], bool]]]] = [
    ("failed", _rule_failed),
    ("degraded", _rule_degraded),
    ("findings", _rule_findings),
    ("clean", _rule_clean),
]


def compute_run_verdict(statuses: Sequence[AgentStatus]) -> RunVerdict:
    """Aggregate per-agent statuses into a run-level verdict.

    The breakdown preserves the agent order so the CLI can render them in
    the order the pipeline ran them (rather than re-sorting them by status,
    which would hide which agent contributed which outcome).
    """
    clean_count = sum(1 for s in statuses if s.status == "clean")
    finding_count = sum(1 for s in statuses if s.status == "findings")
    error_count = sum(1 for s in statuses if s.status == "error")
    total = len(statuses)

    errors_by_code: dict[str, int] = {}
    for s in statuses:
        if s.status == "error" and s.error_code:
            errors_by_code[s.error_code] = errors_by_code.get(s.error_code, 0) + 1

    verdict_name = "failed"  # safe default; the rule loop will overwrite
    for name, predicate in _VERDICT_RULES:
        if predicate(total, error_count, finding_count, clean_count):
            verdict_name = name
            break

    return RunVerdict(
        verdict=verdict_name,
        clean_count=clean_count,
        finding_count=finding_count,
        error_count=error_count,
        errors_by_code=errors_by_code,
        breakdown=list(statuses),
    )
