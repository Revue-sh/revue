"""Consolidator pipeline stage (REVUE-210).

Orchestrates Pass A (GroupingStrategy) + Pass B (SynthesisStrategy) + post-processor chain.
Architecture spec: docs/architecture/comment-posting.md
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from .models import (
    AgentFinding,
    Attribution,
    ConsolidatedFinding,
    FindingPostProcessor,
    GroupingStrategy,
    SynthesisGroup,
    SynthesisStrategy,
)

_log = logging.getLogger(__name__)

_NOVA = "nova"

_SEVERITY_ORDER: dict[str, int] = {"high": 0, "medium": 1, "low": 2, "info": 3}

# Diff sigil characters that prefix lines in code_replacement blocks
_DIFF_SIGILS = frozenset({"+", "-", " "})


def _strip_sigils(line: str) -> str:
    """Remove a single leading diff sigil character from a line."""
    if line and line[0] in _DIFF_SIGILS:
        return line[1:]
    return line


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
    ) -> None:
        self._grouping = grouping
        self._synthesis = synthesis
        self._post_processors = list(post_processors)

    def consolidate(self, findings: list[AgentFinding]) -> list[ConsolidatedFinding]:
        """Run Pass A → Pass B → post-processor chain, then sort output.

        Returns findings sorted by severity (high → medium → low → info) then
        confidence descending within each severity tier.
        """
        if not findings:
            return []

        # Pass A — deterministic grouping
        groups: list[SynthesisGroup] = self._grouping.group(findings)

        # Pass B — synthesis (one call per group)
        consolidated: list[ConsolidatedFinding] = []
        for group in groups:
            try:
                result = self._synthesis.synthesise(group)
                consolidated.append(result)
            except Exception:
                _log.exception(
                    "Consolidator: synthesis failed for group at %s:%s — skipping",
                    group.file_path,
                    group.line_range,
                )

        # Post-processor chain
        for processor in self._post_processors:
            next_batch: list[ConsolidatedFinding] = []
            for finding in consolidated:
                try:
                    out = processor.process(finding)
                except Exception:
                    _log.exception(
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
For proximity groups: produce a unified finding anchored to the first line, summarising all concerns.

Return ONLY a JSON array. Each element must have these fields:
  file        (string — copy from group)
  line        (integer — use first line of group)
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

    def __init__(self, ai_client: Any) -> None:
        self._client = ai_client

    def synthesise(self, group: SynthesisGroup) -> ConsolidatedFinding:
        """Synthesise a SynthesisGroup into a ConsolidatedFinding."""
        if len(group.findings) == 1:
            return self._passthrough(group.findings[0], group.group_type)

        try:
            return self._synthesise_via_nova(group)
        except Exception:
            _log.exception(
                "NovaSingleShotStrategy: Nova call failed for group at %s:%s — falling back to deterministic",
                group.file_path,
                group.line_range,
            )
            return self._deterministic_fallback(group)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _passthrough(self, finding: AgentFinding, group_type: str) -> ConsolidatedFinding:
        return ConsolidatedFinding(
            file_path=finding.file_path,
            line_number=finding.line_number,
            severity=finding.severity,
            issue=finding.issue,
            suggestion=finding.suggestion,
            confidence=finding.confidence,
            category=finding.category,
            attribution=[Attribution(agent_name=finding.agent_name, category=finding.category)],
            code_replacement=finding.code_replacement,
            replacement_line_count=finding.replacement_line_count,
            snippet=finding.snippet,
            group_type=group_type,
        )

    def _synthesise_via_nova(self, group: SynthesisGroup) -> ConsolidatedFinding:
        prompt_content = (
            "Synthesise the following finding groups and return a JSON array, one entry per group.\n\n"
            + json.dumps(self._build_group_payload(group), indent=2, ensure_ascii=False)
        )
        result = self._client.complete(
            [{"role": "user", "content": prompt_content}],
            system=_SYNTHESIS_SYSTEM_PROMPT,
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
        severity = str(syn.get("severity", "medium")).lower()
        if severity not in _SEVERITY_ORDER:
            severity = "medium"

        # Pick best code_replacement: from highest-confidence finding that has one
        best_source = max(
            (f for f in group.findings if f.code_replacement),
            key=lambda f: f.confidence,
            default=None,
        )

        attribution = [
            Attribution(agent_name=f.agent_name, category=f.category)
            for f in group.findings
            if f.agent_name
        ]
        if not attribution:
            # Ensure attribution is never empty (ConsolidatedFinding invariant)
            attribution = [Attribution(agent_name=_NOVA, category=group.findings[0].category)]

        return ConsolidatedFinding(
            file_path=group.file_path,
            line_number=group.line_range[0],
            severity=severity,
            issue=str(syn.get("issue", "")),
            suggestion=str(syn.get("suggestion", "")),
            confidence=max(f.confidence for f in group.findings),
            category=group.findings[0].category,
            attribution=attribution,
            code_replacement=best_source.code_replacement if best_source else None,
            replacement_line_count=best_source.replacement_line_count if best_source else 1,
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

        return ConsolidatedFinding(
            file_path=group.file_path,
            line_number=group.line_range[0],
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
    """Post-processor: demote findings without anchor evidence to summary_sink (Decision 6).

    A finding with neither snippet nor code_replacement has no verifiable anchor.
    Returns None (removes from inline stream) and appends to summary_sink.

    Must run after NoOpSuggestionDropper so that a finding with only a no-op
    code_replacement and no snippet is correctly identified as unanchored.
    """

    def __init__(self, summary_sink: list[ConsolidatedFinding]) -> None:
        self._sink = summary_sink

    def process(self, finding: ConsolidatedFinding) -> ConsolidatedFinding | None:
        if not finding.snippet and not finding.code_replacement:
            self._sink.append(finding)
            return None
        return finding
