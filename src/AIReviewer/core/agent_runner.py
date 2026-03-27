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
    """Result for a single agent's execution."""
    agent_name: str
    findings: list[AIReview]
    elapsed_seconds: float
    timed_out: bool = False
    error: str = ""

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


def run_agents_parallel(
    agents: list[AgentProtocol],
    changes: list[FileChange],
    shared: "SharedAnalysisResult | None" = None,
    timeout_seconds: float = 120.0,
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
            findings = agent.analyse(changes, shared)
            return AgentRunResult(
                agent_name=agent.name,
                findings=findings,
                elapsed_seconds=time.monotonic() - t0,
            )
        except Exception as exc:
            return AgentRunResult(
                agent_name=agent.name,
                findings=[],
                elapsed_seconds=time.monotonic() - t0,
                error=str(exc),
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

