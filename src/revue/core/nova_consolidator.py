"""
Nova consolidation — deduplicate and prioritise findings (Story [007]).

SRP: consolidation only.
OCP: deduplication strategies are pluggable.

Primary deduplication is coordinate-based: all findings at the same
(file, line) are merged into a single comment with agent attribution.
When multiple agents flag the same (file, line), Nova uses a SINGLE batch
LLM call to synthesise ALL conflict groups into unified recommendations.
Secondary deduplication via pluggable strategies handles nearby-line fuzzy
matches on the already-merged set.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from .models import AIReview

if TYPE_CHECKING:
    from .ai_client import AIClient

logger = logging.getLogger(__name__)


class DeduplicationStrategy(Protocol):
    """Pluggable deduplication strategy (OCP)."""
    def are_duplicates(self, a: AIReview, b: AIReview) -> bool: ...


class SameFileLineStrategy:
    """Same file + line + same severity = duplicate."""
    def are_duplicates(self, a: AIReview, b: AIReview) -> bool:
        return (
            a.file_path == b.file_path
            and a.line_number == b.line_number
            and a.severity == b.severity
        )


class SimilarIssueStrategy:
    """Same file, nearby line, high word overlap in issue text = duplicate."""
    _LINE_PROXIMITY = 3
    _OVERLAP_THRESHOLD = 0.6
    _STOPWORDS = {"a", "an", "the", "is", "in", "at", "to", "for", "of", "this", "that"}

    def are_duplicates(self, a: AIReview, b: AIReview) -> bool:
        if a.file_path != b.file_path:
            return False
        if abs(a.line_number - b.line_number) > self._LINE_PROXIMITY:
            return False
        words_a = set(a.issue.lower().split()) - self._STOPWORDS
        words_b = set(b.issue.lower().split()) - self._STOPWORDS
        if not words_a or not words_b:
            return False
        overlap = len(words_a & words_b) / min(len(words_a), len(words_b))
        return overlap >= self._OVERLAP_THRESHOLD


_SEVERITY_ORDER = {"critical": 0, "major": 1, "minor": 2, "suggestion": 3, "info": 4}

_DEFAULT_STRATEGIES: list[DeduplicationStrategy] = [
    SameFileLineStrategy(),
    SimilarIssueStrategy(),
]

MAX_BATCH_SIZE = 50


@dataclass
class ConsolidationResult:
    findings: list[AIReview]
    duplicates_removed: int
    original_count: int

    @property
    def deduplication_ratio(self) -> float:
        if self.original_count == 0:
            return 0.0
        return self.duplicates_removed / self.original_count


def _concatenate_group(group: list[AIReview]) -> AIReview:
    """Fallback: merge a conflict group by concatenating findings with agent attribution."""
    group.sort(key=lambda f: (_SEVERITY_ORDER.get(f.severity, 99), -f.confidence))
    lead = group[0]
    best_severity = lead.severity
    best_confidence = max(f.confidence for f in group)

    seen_issues: list[str] = []
    seen_suggestions: list[str] = []
    seen_issue_texts: set[str] = set()
    seen_suggestion_texts: set[str] = set()

    for f in group:
        agent = f.category or "unknown"
        if f.issue and f.issue not in seen_issue_texts:
            seen_issue_texts.add(f.issue)
            seen_issues.append(f"*{agent}:* {f.issue}")
        if f.suggestion and f.suggestion not in seen_suggestion_texts:
            seen_suggestion_texts.add(f.suggestion)
            seen_suggestions.append(f"*{agent}:* {f.suggestion}")

    return AIReview(
        file_path=lead.file_path,
        line_number=lead.line_number,
        severity=best_severity,
        issue="\n".join(seen_issues),
        suggestion="\n".join(seen_suggestions),
        confidence=best_confidence,
        category=lead.category,
    )


def _build_batch_prompt(conflict_groups: list[list[AIReview]]) -> str:
    """Build TOML-formatted prompt with all conflict groups for a single LLM call."""
    sections = []
    for group in conflict_groups:
        lead = group[0]
        findings = []
        for f in group:
            agent = f.category or "unknown"
            findings.append(
                f"[[group.finding]]\n"
                f'agent = "{agent}"\n'
                f'severity = "{f.severity}"\n'
                f'issue = "{f.issue}"\n'
                f'suggestion = "{f.suggestion}"'
            )
        section = (
            f"[[group]]\n"
            f'file = "{lead.file_path}"\n'
            f"line = {lead.line_number}\n\n"
            + "\n\n".join(findings)
        )
        sections.append(section)

    groups_text = "\n\n".join(sections)

    return (
        "You are Nova, a code review consolidator. Multiple review agents have "
        "flagged the same code locations. For each group of findings at the same "
        "location, synthesise their findings into ONE unified finding.\n\n"
        f"{groups_text}\n\n"
        "Respond with ONLY a JSON array (no markdown fences, no explanation). "
        "One entry per group:\n"
        '[{"file": "...", "line": N, "issue": "...", "suggestion": "..."}, ...]\n\n'
        "Rules:\n"
        "- issue: synthesised framing (not a list, one clear statement)\n"
        "- suggestion: ONE concrete actionable recommendation that addresses ALL agents' concerns\n"
        "- Do NOT include agent names in the output — the output is for the developer, not internal\n"
        "- Match file+line exactly as given in input"
    )


def _parse_batch_response(
    response: str, conflict_groups: list[list[AIReview]],
) -> list[AIReview]:
    """Parse JSON array response and match entries back to conflict groups by file+line."""
    text = response.strip()
    # Strip markdown fences if present
    if text.startswith("```"):
        first_newline = text.find("\n")
        text = text[first_newline + 1:] if first_newline != -1 else text[3:]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
        text = text.strip()

    # Build lookup: (file, line) → group
    group_lookup: dict[tuple[str, int], list[AIReview]] = {}
    for group in conflict_groups:
        lead = group[0]
        group_lookup[(lead.file_path, lead.line_number)] = group

    try:
        data = json.loads(text)
        if not isinstance(data, list):
            return [_concatenate_group(g) for g in conflict_groups]
    except (json.JSONDecodeError, TypeError):
        return [_concatenate_group(g) for g in conflict_groups]

    # Match entries by file+line key
    matched_keys: set[tuple[str, int]] = set()
    results: list[AIReview] = []

    for entry in data:
        if not isinstance(entry, dict):
            continue
        file_path = entry.get("file", "")
        line = entry.get("line", -1)
        key = (file_path, line)
        if key in group_lookup and key not in matched_keys:
            group = group_lookup[key]
            matched_keys.add(key)
            group.sort(key=lambda f: (_SEVERITY_ORDER.get(f.severity, 99), -f.confidence))
            best_severity = group[0].severity
            best_confidence = max(f.confidence for f in group)
            results.append(AIReview(
                file_path=file_path,
                line_number=line,
                severity=best_severity,
                issue=entry.get("issue", ""),
                suggestion=entry.get("suggestion", ""),
                confidence=best_confidence,
                category="nova",
            ))

    # Unmatched groups fall back to concatenation
    for group in conflict_groups:
        lead = group[0]
        key = (lead.file_path, lead.line_number)
        if key not in matched_keys:
            results.append(_concatenate_group(group))

    return results


def _batch_synthesise(
    conflict_groups: list[list[AIReview]], ai_client: AIClient | None,
) -> list[AIReview]:
    """Synthesise all conflict groups via batch LLM calls.

    - Chunks into batches of MAX_BATCH_SIZE
    - On exception or missing ai_client: falls back to concatenation
    """
    if ai_client is None:
        return [_concatenate_group(g) for g in conflict_groups]

    results: list[AIReview] = []
    for i in range(0, len(conflict_groups), MAX_BATCH_SIZE):
        chunk = conflict_groups[i:i + MAX_BATCH_SIZE]
        try:
            prompt = _build_batch_prompt(chunk)
            response = ai_client.complete(
                [{"role": "user", "content": prompt}],
                max_tokens=4096,
                temperature=0.2,
            )
            results.extend(_parse_batch_response(response, chunk))
        except Exception:
            logger.warning(
                "Batch synthesis failed for %d groups, falling back to concatenation",
                len(chunk),
            )
            results.extend(_concatenate_group(g) for g in chunk)

    return results


def consolidate(
    findings: list[AIReview],
    strategies: list[DeduplicationStrategy] | None = None,
    min_confidence: float = 0.0,
    ai_client: AIClient | None = None,
) -> ConsolidationResult:
    """
    Deduplicate and prioritise findings.

    1. PRIMARY: group by (file, line)
       - Singletons (group of 1) pass through unchanged — zero LLM cost
       - Conflict groups (2+) → single batch LLM call → synthesised findings
    2. SECONDARY: apply pluggable strategies on the combined set
    3. Filter out findings below min_confidence threshold
    4. Sort: critical → major → minor → suggestion, then by confidence desc
    - Never raises
    """
    active = strategies if strategies is not None else _DEFAULT_STRATEGIES
    original_count = len(findings)

    # --- Step 1: group by (file, line) ---
    groups: dict[tuple[str, int], list[AIReview]] = defaultdict(list)
    for f in findings:
        groups[(f.file_path, f.line_number)].append(f)

    singletons = [g[0] for g in groups.values() if len(g) == 1]
    conflicts = [g for g in groups.values() if len(g) >= 2]

    # Batch-synthesise all conflict groups
    synthesised = _batch_synthesise(conflicts, ai_client) if conflicts else []

    merged = singletons + synthesised

    # --- Step 2: secondary strategy-based dedup on merged set ---
    kept: list[AIReview] = []
    removed = 0

    for candidate in merged:
        is_dup = False
        for i, existing in enumerate(kept):
            for strategy in active:
                try:
                    if strategy.are_duplicates(candidate, existing):
                        if candidate.confidence > existing.confidence:
                            kept[i] = candidate
                        removed += 1
                        is_dup = True
                        break
                except Exception:
                    pass
            if is_dup:
                break
        if not is_dup:
            kept.append(candidate)

    # Filter by confidence
    filtered = [f for f in kept if f.confidence >= min_confidence]

    # Sort by severity then confidence desc
    sorted_findings = sorted(
        filtered,
        key=lambda f: (_SEVERITY_ORDER.get(f.severity, 99), -f.confidence),
    )

    return ConsolidationResult(
        findings=sorted_findings,
        duplicates_removed=original_count - len(sorted_findings),
        original_count=original_count,
    )
