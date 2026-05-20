"""
Parallel agent execution with timeout and graceful degradation (Story [004]).

Follows SRP: this module only handles parallel dispatch and result collection.
Follows OCP: new agents are registered, not added here.
Follows DIP: depends on AgentProtocol, not concrete agent classes.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from dataclasses import dataclass, field
from typing import Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from .ai_client import AIClient
    from .shared_analysis import SharedAnalysisResult

from .models import FileChange, AIReview


class AgentProtocol(Protocol):
    """Interface all specialist agents must implement (ISP)."""

    name: str

    def analyse(
        self,
        changes: list[FileChange],
        shared: "SharedAnalysisResult | None" = None,
    ) -> list[AIReview]: ...


@dataclass
class AgentRunResult:
    """Result for a single agent's execution.

    REVUE-246: the ``status`` field carries the three-state contract verdict
    (``findings`` / ``clean`` / ``error``) so the pipeline-level verdict
    composition can read it directly. Legacy callers using ``success`` /
    ``findings`` / ``error`` continue to work — the new fields are additive.
    """
    agent_name: str
    findings: list[AIReview]
    elapsed_seconds: float
    timed_out: bool = False
    error: str = ""
    error_type: str = ""  # unqualified exception class (e.g. "BadRequestError")
    call_site: str = ""   # client.method that raised (e.g. "AnthropicClient.complete_with_tools")
    status: str = "findings"  # "findings" | "clean" | "error" — REVUE-246
    error_code: "str | None" = None
    summary: "str | None" = None
    confidence: "float | None" = None
    iterations_used: "int | None" = None

    @property
    def success(self) -> bool:
        return not self.timed_out and not self.error


@dataclass
class ParallelRunResult:
    """Aggregated results from all agents."""
    agent_results: list[AgentRunResult]
    total_elapsed: float

    @property
    def all_findings(self) -> list[AIReview]:
        findings: list[AIReview] = []
        for r in self.agent_results:
            findings.extend(r.findings)
        return findings

    @property
    def failed_agents(self) -> list[str]:
        return [r.agent_name for r in self.agent_results if not r.success]

    @property
    def succeeded_agents(self) -> list[str]:
        return [r.agent_name for r in self.agent_results if r.success]


DEFAULT_AGENT_TIMEOUT_SECONDS: float = 90.0
"""Per-agent wall-clock timeout in seconds.

PRD specifies 90s. Configurable via .revue.yml (review.agent_timeout_seconds).
Raise to 120 for slow VPN/corporate networks.
Pass AIConfig.agent_timeout_seconds when calling run_agents_parallel().
"""


def run_agents_parallel(
    agents: list[AgentProtocol],
    changes: list[FileChange],
    shared: "SharedAnalysisResult | None" = None,
    timeout_seconds: float = DEFAULT_AGENT_TIMEOUT_SECONDS,
    max_workers: int | None = None,
) -> ParallelRunResult:
    """
    Run all agents concurrently using ThreadPoolExecutor.

    - Each agent runs in its own thread
    - If an agent exceeds timeout_seconds: marked as timed_out, findings=[]
    - If an agent raises: marked as error, findings=[] (graceful degradation)
    - Always returns ParallelRunResult even if all agents fail
    - Never raises
    """
    start_total = time.monotonic()
    results: list[AgentRunResult] = []

    if not agents:
        return ParallelRunResult(agent_results=[], total_elapsed=0.0)

    workers = max_workers or min(len(agents), 8)

    def _run_one(agent: AgentProtocol) -> AgentRunResult:
        t0 = time.monotonic()
        try:
            verdict = agent.analyse(changes, shared)
            # REVUE-246: ``analyse`` returns an ``AgentVerdict`` (or, for legacy
            # in-test stubs, a bare list). Treat a bare list as a findings
            # verdict so test doubles that pre-date the typed return don't
            # require a sweep of every fixture.
            status = getattr(verdict, "status", "findings")
            findings_list = list(getattr(verdict, "findings", verdict) or [])
            return AgentRunResult(
                agent_name=agent.name,
                findings=findings_list,
                elapsed_seconds=time.monotonic() - t0,
                status=status,
                error_code=getattr(verdict, "error_code", None),
                summary=getattr(verdict, "summary", None),
                confidence=getattr(verdict, "confidence", None),
                iterations_used=getattr(verdict, "iterations_used", None),
                error=(getattr(verdict, "error_message", None) or "") if status == "error" else "",
                error_type="AgentVerdictError" if status == "error" else "",
            )
        except Exception as exc:
            return AgentRunResult(
                agent_name=agent.name,
                findings=[],
                elapsed_seconds=time.monotonic() - t0,
                error=str(exc),
                error_type=type(exc).__name__,
                call_site=getattr(exc, "call_site", ""),
                status="error",
                error_code="internal_error",
            )

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_run_one, agent): agent for agent in agents}
        try:
            for future in as_completed(futures, timeout=timeout_seconds):
                try:
                    results.append(future.result())
                except Exception as exc:
                    agent = futures[future]
                    results.append(AgentRunResult(
                        agent_name=agent.name,
                        findings=[],
                        elapsed_seconds=0.0,
                        error=str(exc),
                        error_type=type(exc).__name__,
                        call_site=getattr(exc, "call_site", ""),
                    ))
        except FuturesTimeoutError:
            # Overall timeout — mark any agents that haven't completed yet
            completed_names = {r.agent_name for r in results}
            for agent in agents:
                if agent.name not in completed_names:
                    results.append(AgentRunResult(
                        agent_name=agent.name,
                        findings=[],
                        elapsed_seconds=timeout_seconds,
                        timed_out=True,
                    ))

    return ParallelRunResult(
        agent_results=results,
        total_elapsed=time.monotonic() - start_total,
    )

