"""Consolidator pipeline stage (REVUE-210).

Orchestrates Pass A (GroupingStrategy) + Pass B (SynthesisStrategy) + post-processor chain.
Architecture spec: docs/architecture/comment-posting.md
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from ..core.agent_loader import filter_code_replacement
from ..core.tools import ReadFileTool
from .models import (
    AgentFinding,
    Attribution,
    ConsolidatedFinding,
    FindingPostProcessor,
    GroupingStrategy,
    SynthesisGroup,
    SynthesisStrategy,
)

from revue_core.core.logging_channels import Log  # logging_channels

_NOVA = "nova"

_SEVERITY_ORDER: dict[str, int] = {"high": 0, "medium": 1, "low": 2, "info": 3}

# Diff sigil characters that prefix lines in code_replacement blocks
_DIFF_SIGILS = frozenset({"+", "-", " "})


def _strip_sigils(line: str) -> str:
    """Remove a single leading diff sigil character from a line."""
    if line and line[0] in _DIFF_SIGILS:
        return line[1:]
    return line


def _coerce_positive_int(value: Any, *, default: int) -> int:
    """Return *value* as a positive int, or *default* when it can't be coerced.

    Accepts native ints (≥ 1) and integer-valued floats (LLMs frequently emit
    6.0 instead of 6 when JSON-encoding numbers). Negative, zero, fractional,
    string, and missing values all fall through to *default*.
    """
    if isinstance(value, bool):
        # bools subclass int — exclude them explicitly so True/False don't sneak in.
        return default
    if isinstance(value, int) and value >= 1:
        return value
    if isinstance(value, float) and value >= 1 and value.is_integer():
        return int(value)
    return default


# ---------------------------------------------------------------------------
# Consolidator
# ---------------------------------------------------------------------------


class Consolidator:
    """Orchestrates Pass A (GroupingStrategy) + Pass B (SynthesisStrategy) + post-processor chain."""

    def __init__(
        self,
        grouping: GroupingStrategy,
        synthesis: SynthesisStrategy,
        post_processors: tuple[FindingPostProcessor, ...] | list[FindingPostProcessor] = (),
        max_synthesis_workers: int = 8,
    ) -> None:
        self._grouping = grouping
        self._synthesis = synthesis
        self._post_processors = list(post_processors)
        self._max_synthesis_workers = max(1, max_synthesis_workers)

    def consolidate(self, findings: list[AgentFinding]) -> list[ConsolidatedFinding]:
        """Run Pass A → Pass B → post-processor chain, then sort output.

        Returns findings sorted by severity (high → medium → low → info) then
        confidence descending within each severity tier.
        """
        if not findings:
            return []

        # Pass A — deterministic grouping
        groups: list[SynthesisGroup] = self._grouping.group(findings)

        # Pass B — synthesis (one call per group, parallelised).
        # Each group → one Nova call which may chain up to max_iterations tool
        # rounds via read_file. Serially this is O(groups × iterations) API
        # roundtrips and stalls CI for many minutes on typical PRs. Threads
        # are safe here: NovaSingleShotStrategy.synthesise is stateless across
        # groups and the underlying SDK clients are thread-safe (httpx).
        results_by_index: dict[int, ConsolidatedFinding] = {}
        worker_count = min(self._max_synthesis_workers, max(1, len(groups)))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_to_index = {
                executor.submit(self._synthesis.synthesise, group): idx
                for idx, group in enumerate(groups)
            }
            for future in future_to_index:
                idx = future_to_index[future]
                try:
                    results_by_index[idx] = future.result()
                except Exception:
                    Log.nova.exception(
                        "Consolidator: synthesis failed for group at %s:%s — skipping",
                        groups[idx].file_path,
                        groups[idx].line_range,
                    )

        consolidated: list[ConsolidatedFinding] = [
            results_by_index[i] for i in range(len(groups)) if i in results_by_index
        ]

        # Post-processor chain. A processor may opt into batch processing
        # (parallel execution, batched LLM calls, etc.) by defining
        # ``process_all(findings) -> list[finding | None]``; otherwise the
        # default per-finding sequential loop runs.
        for processor in self._post_processors:
            batch_fn = getattr(processor, "process_all", None)
            if callable(batch_fn):
                try:
                    consolidated = [f for f in batch_fn(consolidated) if f is not None]
                except Exception:
                    Log.nova.exception(
                        "Consolidator: batch post-processor %s raised — keeping findings as-is",
                        type(processor).__name__,
                    )
                continue

            next_batch: list[ConsolidatedFinding] = []
            for finding in consolidated:
                try:
                    out = processor.process(finding)
                except Exception:
                    Log.nova.exception(
                        "Consolidator: post-processor %s raised on %s:%s — keeping finding",
                        type(processor).__name__,
                        finding.file_path,
                        finding.line_number,
                    )
                    out = finding
                if out is not None:
                    next_batch.append(out)
            consolidated = next_batch

        # Sort by severity then confidence desc
        return sorted(
            consolidated,
            key=lambda f: (_SEVERITY_ORDER.get(f.severity, 99), -f.confidence),
        )


# ---------------------------------------------------------------------------
# REVUE-248 §D3 — Majority-vote line reconciler
# ---------------------------------------------------------------------------


def _majority_vote_line(group: SynthesisGroup) -> int | None:
    """Return the line that ≥ N-1 of N agents in *group* report — else None.

    Thresholds:
      • N = 1 (singleton): the only line trivially wins.
      • N = 2:             both agents must agree (intentional deviation from
                           the literal "≥ N-1" rule — a 1-1 split is not a
                           majority, so D3 falls back to the consolidator's
                           tentative line. The system prompt is aligned with
                           this stricter rule.).
      • N ≥ 3:             ≥ N-1 agents must agree on the same line.

    Returns the majority line for the caller to use as the consolidated
    anchor. Returns None when no majority emerges (caller falls back to
    today's behaviour — Nova's chosen line, or ``group.line_range[0]``).
    """
    n = len(group.findings)
    if n == 0:
        return None
    if n == 1:
        return group.findings[0].line_number

    counts = Counter(f.line_number for f in group.findings)
    line, votes = counts.most_common(1)[0]
    # N=2 is intentionally stricter than the general N-1 formula: a 1-1 split
    # is not a majority, so both agents must agree. Using `n` (=2) rather than
    # `n - 1` (=1) enforces unanimity and avoids promoting either agent's line
    # over the other when there is genuine disagreement.
    threshold = n if n == 2 else n - 1
    return line if votes >= threshold else None


def _reconcile_anchor_line(
    group: SynthesisGroup, tentative_line: int, file_path: str
) -> int:
    """Apply the majority-vote rule and log the reconciliation.

    Returns the line to use as the consolidated anchor. When the majority
    differs from *tentative_line*, emits an INFO log on the nova channel so
    dogfood greps surface the reconciliation event.
    """
    majority = _majority_vote_line(group)
    if majority is None or majority == tentative_line:
        return tentative_line

    minority = sorted({f.line_number for f in group.findings if f.line_number != majority})
    Log.nova.info(
        "[nova-reconcile] %s:%d minority=%s",
        file_path,
        majority,
        ",".join(str(ln) for ln in minority),
    )
    return majority


# ---------------------------------------------------------------------------
# Pass A — ProximityAndCountGroupingStrategy
# ---------------------------------------------------------------------------


class ProximityAndCountGroupingStrategy:
    """Pass A: cluster findings where line_distance ≤ N AND group_size ≤ K (Decision 2).

    Both thresholds are constructor-injectable and configurable via .revue.yml
    consolidation stanza (read by pipeline.py at wiring time).
    """

    def __init__(self, n: int = 3, k: int = 3) -> None:
        self._n = n
        self._k = k

    def group(self, findings: list[AgentFinding]) -> list[SynthesisGroup]:
        """Cluster findings into SynthesisGroups by file, then by proximity."""
        if not findings:
            return []

        # Sort by (file_path, line_number) for deterministic grouping
        sorted_findings = sorted(findings, key=lambda f: (f.file_path, f.line_number))

        groups: list[SynthesisGroup] = []
        current_group: list[AgentFinding] = []

        for finding in sorted_findings:
            if not current_group:
                current_group = [finding]
                continue

            last = current_group[-1]
            same_file = finding.file_path == last.file_path
            within_distance = (finding.line_number - last.line_number) <= self._n
            within_count = len(current_group) < self._k

            if same_file and within_distance and within_count:
                current_group.append(finding)
            else:
                groups.append(self._make_group(current_group))
                current_group = [finding]

        if current_group:
            groups.append(self._make_group(current_group))

        return groups

    def _make_group(self, findings: list[AgentFinding]) -> SynthesisGroup:
        first = findings[0]
        last = findings[-1]
        min_line = min(f.line_number for f in findings)
        max_line = max(f.line_number for f in findings)

        if len(findings) == 1:
            group_type = "singleton"
        elif first.line_number == last.line_number:
            group_type = "same_line"
        else:
            group_type = "proximity"

        return SynthesisGroup(
            findings=findings,
            file_path=first.file_path,
            line_range=(min_line, max_line),
            group_type=group_type,
        )


# ---------------------------------------------------------------------------
# Pass B — NovaSingleShotStrategy
# ---------------------------------------------------------------------------

_SYNTHESIS_SYSTEM_PROMPT = """\
You are Nova, the Revue code-review consolidator.

For each group below, produce ONE unified finding that synthesises all agent perspectives.
Groups may contain findings on the same line (conflict groups) or on nearby lines (proximity groups).

For same-line conflicts: synthesise into a single coherent finding that captures the highest-severity concern.
For proximity groups: produce a unified finding summarising all concerns.

Line selection rule (REVUE-248 §D3):
  • For N=1 (singleton): use that agent's line.
  • For N=2: use the line only when BOTH agents agree; otherwise use the first
    line of the group.
  • For N≥3: use the line when ≥ N-1 agents agree on it; otherwise use the
    first line of the group.
  The consolidator deterministically enforces this rule after your output —
  pick the line that matches it. A 1-1 tie at N=2 is not a majority.

Return ONLY a JSON array. Each element must have these fields:
  file        (string — copy from group)
  line        (integer — the majority agent line, or the first line of the group if no majority)
  issue       (string — synthesised issue prose, no agent names, no line references)
  suggestion  (string — unified recommendation)
  severity    (string — highest severity among group members: high/medium/low/info)

No markdown, no explanation, just JSON array.
"""


class NovaSingleShotStrategy:
    """Pass B: single-shot Nova synthesis for non-singleton groups (Decision 3).

    Singleton groups are passed through without an AI call.
    On Nova failure (network error, invalid JSON), falls back to deterministic
    concatenation with attribution headers. Callers cannot observe which path ran.
    """

    def __init__(
        self,
        ai_client: Any,
        system_prompt: str | None = None,
        diff_by_file: dict[str, str] | None = None,
        repo_root: Path | None = None,
    ) -> None:
        self._client = ai_client
        self._system_prompt = system_prompt or _SYNTHESIS_SYSTEM_PROMPT
        self._diff_by_file = diff_by_file or {}
        self._repo_root = repo_root or Path.cwd()
        # Pre-built once: avoids re-creating the tool, schema dict, and
        # handler map on every group (parallel Pass B amplified the waste:
        # 8 workers × ~30 groups = ~240 tool allocations per run).
        self._read_file_tool = ReadFileTool(
            repo_root=self._repo_root,
            allowed_paths=set(self._diff_by_file.keys()),
        )
        self._tools = [ReadFileTool.tool_definition()]
        self._tool_handlers = {"read_file": self._read_file_tool.execute}

    def synthesise(self, group: SynthesisGroup) -> ConsolidatedFinding:
        """Synthesise a SynthesisGroup into a ConsolidatedFinding.

        All groups — including singletons — route through Nova so the synthesiser
        can use file context (via read_file tool) to anchor the comment to the
        relevant span. There is no passthrough path.
        """
        try:
            return self._synthesise_via_nova(group)
        except Exception:
            Log.nova.exception(
                "NovaSingleShotStrategy: Nova call failed for group at %s:%s — falling back to deterministic",
                group.file_path,
                group.line_range,
            )
            return self._deterministic_fallback(group)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _synthesise_via_nova(self, group: SynthesisGroup) -> ConsolidatedFinding:
        prompt_content = (
            "Synthesise the following finding groups and return a JSON array, one entry per group.\n\n"
            + json.dumps(self._build_group_payload(group), indent=2, ensure_ascii=False)
        )

        # Tool, schema, and handler map built once in __init__ and reused.
        if hasattr(self._client, "complete_with_tools"):
            result = self._client.complete_with_tools(
                messages=[{"role": "user", "content": prompt_content}],
                system=self._system_prompt,
                tools=self._tools,
                tool_handlers=self._tool_handlers,
                max_tokens=4096,
                temperature=0.2,
                agent_name=_NOVA,
            )
        else:
            # Legacy clients without tool-use — single-shot, no file context.
            result = self._client.complete(
                [{"role": "user", "content": prompt_content}],
                system=self._system_prompt,
                max_tokens=4096,
                temperature=0.2,
                agent_name=_NOVA,
            )

        parsed = self._parse_response(result.text)
        if not parsed:
            return self._deterministic_fallback(group)

        syn = parsed[0]
        return self._build_consolidated(group, syn)

    def _build_group_payload(self, group: SynthesisGroup) -> list[dict]:
        return [
            {
                "file": group.file_path,
                "line": group.line_range[0],
                "group_type": group.group_type,
                "findings": [
                    {
                        "agent": f.agent_name,
                        "severity": f.severity,
                        "issue": f.issue,
                        "suggestion": f.suggestion,
                        "language": getattr(f, "language", "unknown"),
                    }
                    for f in group.findings
                ],
            }
        ]

    def _parse_response(self, raw: str) -> list[dict]:
        clean = raw.strip()
        if clean.startswith("```"):
            lines = clean.splitlines()
            inner = lines[1:] if lines[0].startswith("```") else lines
            close = next((i for i, ln in enumerate(inner) if ln.strip() == "```"), len(inner))
            clean = "\n".join(inner[:close]).strip()

        clean = re.sub(r",\s*([}\]])", r"\1", clean)

        try:
            data = json.loads(clean)
        except json.JSONDecodeError:
            return []

        if not isinstance(data, list):
            return []

        return [d for d in data if isinstance(d, dict)]

    def _build_consolidated(self, group: SynthesisGroup, syn: dict) -> ConsolidatedFinding:
        """Map Nova's JSON synthesis to a ConsolidatedFinding.

        Nova's ``line`` and ``replacement_line_count`` are authoritative — she
        has read the file via ``read_file`` and her anchor reflects that
        context, not the agents' first guess. The consolidator only falls
        back to ``group.line_range[0]`` when Nova omits ``line``.

        No deterministic anchor validation runs here. Indent matching and
        span-bounds checks were rejecting legitimate refactors (e.g. 5
        lines → 2 lines simplifications) while still missing the destructive
        cases they were meant to catch. Semantic validation is the job of
        the verifier agent (Vex — REVUE-240). Until Vex lands, suggestions
        are post-and-pray and the BodyBuilder appends a hallucination
        disclaimer so developers verify before clicking "Commit suggestion".
        """
        severity = str(syn.get("severity", "medium")).lower()
        if severity not in _SEVERITY_ORDER:
            severity = "medium"

        nova_code_replacement = syn.get("code_replacement")
        code_replacement = (
            filter_code_replacement(nova_code_replacement)
            if nova_code_replacement is not None
            else None
        )

        # Fall back to the highest-confidence source finding's code_replacement
        # when Nova omits hers — preserves the agent's suggested fix verbatim.
        if code_replacement is None:
            best_source = max(
                (f for f in group.findings if f.code_replacement),
                key=lambda f: f.confidence,
                default=None,
            )
            if best_source is not None:
                code_replacement = best_source.code_replacement
                rlc_fallback = best_source.replacement_line_count
            else:
                rlc_fallback = 1
        else:
            rlc_fallback = len(code_replacement)

        anchor_line = _coerce_positive_int(syn.get("line"), default=group.line_range[0])
        # REVUE-248 §D3 — deterministic majority-vote overrides Nova's choice.
        anchor_line = _reconcile_anchor_line(group, anchor_line, group.file_path)

        replacement_line_count = _coerce_positive_int(
            syn.get("replacement_line_count"), default=rlc_fallback
        )
        # An anchor-only comment (no code_replacement) has rlc=1 by definition.
        if code_replacement is None:
            replacement_line_count = 1

        attribution = [
            Attribution(agent_name=f.agent_name, category=f.category)
            for f in group.findings
            if f.agent_name
        ]
        if not attribution:
            # ConsolidatedFinding invariant: attribution must be non-empty.
            attribution = [Attribution(agent_name=_NOVA, category=group.findings[0].category)]

        return ConsolidatedFinding(
            file_path=group.file_path,
            line_number=anchor_line,
            severity=severity,
            issue=str(syn.get("issue", "")),
            suggestion=str(syn.get("suggestion", "")),
            confidence=max(f.confidence for f in group.findings),
            category=group.findings[0].category,
            attribution=attribution,
            code_replacement=code_replacement,
            replacement_line_count=replacement_line_count,
            snippet=group.findings[0].snippet,
            group_type=group.group_type,
        )

    def _deterministic_fallback(self, group: SynthesisGroup) -> ConsolidatedFinding:
        """Deterministic concatenation with attribution headers (α fallback, Decision 3)."""
        agent_names = [f.agent_name for f in group.findings if f.agent_name]
        issues = "\n".join(
            f"[{f.agent_name}] {f.issue}" for f in group.findings if f.agent_name
        )
        suggestions = "\n".join(
            f"[{f.agent_name}] {f.suggestion}" for f in group.findings if f.agent_name
        )

        # Highest severity in group
        best_sev = min(
            (f.severity for f in group.findings),
            key=lambda s: _SEVERITY_ORDER.get(s, 99),
        )

        # Best code_replacement from highest-confidence finding
        best_source = max(
            (f for f in group.findings if f.code_replacement),
            key=lambda f: f.confidence,
            default=None,
        )

        # Build attribution: deduplicated by agent_name, preserving each finding's own category
        seen_agents: dict[str, str] = {}
        for f in group.findings:
            if f.agent_name and f.agent_name not in seen_agents:
                seen_agents[f.agent_name] = f.category
        attribution = [
            Attribution(agent_name=name, category=cat)
            for name, cat in seen_agents.items()
        ]
        if not attribution:
            attribution = [Attribution(agent_name=_NOVA, category=group.findings[0].category)]

        # REVUE-248 §D3 — apply majority-vote reconciliation on the fallback
        # path too so the deterministic vs LLM-synthesis split doesn't change
        # the anchored line.
        anchor_line = _reconcile_anchor_line(group, group.line_range[0], group.file_path)
        return ConsolidatedFinding(
            file_path=group.file_path,
            line_number=anchor_line,
            severity=best_sev,
            issue=issues,
            suggestion=suggestions,
            confidence=max(f.confidence for f in group.findings),
            category=group.findings[0].category,
            attribution=attribution,
            code_replacement=best_source.code_replacement if best_source else None,
            replacement_line_count=best_source.replacement_line_count if best_source else 1,
            snippet=group.findings[0].snippet,
            group_type=group.group_type,
        )


# ---------------------------------------------------------------------------
# Post-processor: NoOpSuggestionDropper
# ---------------------------------------------------------------------------


class NoOpSuggestionDropper:
    """Post-processor: set code_replacement=None when it equals the snippet (Decision 5).

    When code_replacement after stripping diff sigils equals snippet, the suggestion
    does nothing visible to the developer. The finding is preserved; the suggestion is dropped.
    """

    def process(self, finding: ConsolidatedFinding) -> ConsolidatedFinding | None:
        if not finding.code_replacement:
            return finding

        snippet = finding.snippet or ""
        snippet_lines = snippet.splitlines()
        replacement = finding.code_replacement
        stripped_replacement = [_strip_sigils(line) for line in replacement]

        # Three-way match: direct, sigil-stripped line-by-line, or sigil-stripped joined
        # The third check handles the edge case where snippet="" and replacement=[""].
        is_noop = (
            replacement == snippet_lines
            or stripped_replacement == snippet_lines
            or "\n".join(stripped_replacement) == snippet
        )
        if is_noop:
            return ConsolidatedFinding(
                file_path=finding.file_path,
                line_number=finding.line_number,
                severity=finding.severity,
                issue=finding.issue,
                suggestion=finding.suggestion,
                confidence=finding.confidence,
                category=finding.category,
                attribution=finding.attribution,
                code_replacement=None,
                replacement_line_count=finding.replacement_line_count,
                snippet=finding.snippet,
                group_type=finding.group_type,
            )

        return finding


# ---------------------------------------------------------------------------
# Post-processor: UnanchoredFindingExtractor
# ---------------------------------------------------------------------------


class UnanchoredFindingExtractor:
    """Post-processor: pass findings through to the poster.

    The earlier heuristic — demote any finding with empty snippet AND no
    code_replacement — predated REVUE-239 Phase 1, where Nova became the
    authoritative owner of ``line_number`` and the ConsolidatedFinding
    dataclass started enforcing ``line_number > 0``. That made the heuristic
    silently strip every prose-only finding from the inline stream, even
    though the poster could anchor them by their line number.

    Genuine unanchored detection (line not in any diff hunk) now lives in
    the position adapter and poster, which feed their own summary_sink at
    the layer that actually knows about hunk geometry. This post-processor
    is kept as a stable shape in the consolidator chain so future regressions
    can be expressed here without re-threading wiring.
    """

    def __init__(self, summary_sink: list[ConsolidatedFinding]) -> None:
        self._sink = summary_sink

    def process(self, finding: ConsolidatedFinding) -> ConsolidatedFinding | None:
        return finding
