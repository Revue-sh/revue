"""
Nova consolidation — deduplicate and prioritise findings (Story [007]).

SRP: consolidation only.
OCP: deduplication strategies are pluggable.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from .models import AIReview

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Won't-fix reply thread analysis (REVUE-112)
# ---------------------------------------------------------------------------

_REPLY_THREAD_SYSTEM_PROMPT = """\
You are Nova, the Revue code-review consolidator. Your task is to analyse
developer replies to Revue findings and determine developer intent.

For each thread, determine ONE of these decisions:

- **allowed_pattern**: The developer has (a) acknowledged the finding AND
  (b) provided a clear reason why this is an acceptable, intentional choice
  for this codebase or context. The reasoning is specific and convincing.

- **disallowed_pattern**: The developer has (a) acknowledged the finding AND
  (b) provided a clear reason why this approach is categorically wrong or
  forbidden in this codebase — they want to prevent it from appearing again.

- **reason_missing**: The developer has indicated intent to dismiss the
  finding but has NOT provided a sufficient reason. A reply is valid ONLY
  if it includes BOTH an acknowledgement AND a stated reason. Missing either
  means reason_missing.

- **not_acknowledged**: The developer has not addressed the finding at all,
  or the reply is unrelated to the finding.

- **already_handled**: The thread already contains a bot acknowledgment reply
  from a previous review cycle. Indicators: a reply mentions "Lessons PR",
  "reaffirming this finding", "could you explain", or otherwise reads as an
  automated response rather than a developer reply. No further action needed.

Intent is determined semantically — no specific keywords are required.

Tone guidance for reply_draft:
- Collaborative, not accusatory
- Direct and brief
- Do NOT use: testament, pivotal, ensure, seamless, highlight, underscore, delve
- Do NOT overuse em dashes
- For allowed_pattern or disallowed_pattern: note that a lessons PR will be
  opened so the team can learn from this decision (use placeholder
  "[LESSONS_PR_URL]" — the actual URL will be filled in by the service layer)

Output ONLY a JSON array. Each element must have these fields:
  fingerprint    (string — copy from input)
  decision       (one of the five values above)
  reply_draft    (string — the reply to post; empty string "" for already_handled)
  pattern        (string — present only for allowed_pattern or disallowed_pattern)
  rationale      (string — present only for allowed_pattern or disallowed_pattern)

Do not include pattern or rationale for reason_missing, not_acknowledged, or already_handled.
"""


class NovaConsolidator:
    """Nova consolidation: deduplication + reply-thread analysis.

    Constructed with an AIClient for the reply-thread analysis path.
    The deduplication path (``consolidate()`` module-level function) does
    not need an AI client and remains a pure function.
    """

    def __init__(self, ai_client: Any) -> None:
        self._client = ai_client

    def analyse_reply_threads(self, threads: list[dict]) -> list[dict]:
        """Analyse developer replies to Revue findings via a single AI call.

        This is a SEPARATE call from findings analysis (AC11) — never merged.

        Args:
            threads: List of thread dicts.  Each must contain at minimum:
                fingerprint, file_path, line, issue_type, severity,
                original_finding_summary, replies (list[str]).

        Returns:
            List of decision dicts, one per input thread (order may differ).
            Each dict has: fingerprint, decision, reply_draft, and optionally
            pattern and rationale (for allowed_pattern / disallowed_pattern).

        Raises:
            Re-raises any exception thrown by the AI client (AC10).
        """
        if not threads:
            return []

        user_content = (
            "Analyse the following developer reply threads and return a JSON array "
            "of decisions, one per thread.\n\n"
            + json.dumps(threads, indent=2, ensure_ascii=False)
        )

        try:
            raw = self._client.complete(
                [{"role": "user", "content": user_content}],
                system=_REPLY_THREAD_SYSTEM_PROMPT,
                max_tokens=4096,
                temperature=0.2,
            )
        except Exception:
            _log.exception(
                "NovaConsolidator.analyse_reply_threads: AI call failed. "
                "Thread count=%d, fingerprints=%s",
                len(threads),
                [t.get("fingerprint") for t in threads],
            )
            raise

        return _parse_thread_decisions(raw)


def _parse_thread_decisions(raw: str) -> list[dict]:
    """Parse the AI response into a list of decision dicts.

    Strips markdown code fences if present. Returns [] on parse failure
    (the caller can decide how to handle a partial failure).
    """
    clean = raw.strip()
    if clean.startswith("```"):
        # Strip ```json ... ``` or ``` ... ```
        lines = clean.splitlines()
        # Remove first and last fence lines
        inner_lines = lines[1:] if lines[0].startswith("```") else lines
        if inner_lines and inner_lines[-1].strip() == "```":
            inner_lines = inner_lines[:-1]
        clean = "\n".join(inner_lines).strip()

    try:
        data = json.loads(clean)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"NovaConsolidator: AI returned malformed JSON (first 500 chars): {raw[:500]}"
        ) from exc

    if not isinstance(data, list):
        raise ValueError(
            f"NovaConsolidator: AI returned non-list JSON (type={type(data).__name__}): {str(data)[:200]}"
        )

    return data


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


def consolidate(
    findings: list[AIReview],
    strategies: list[DeduplicationStrategy] | None = None,
    min_confidence: float = 0.0,
) -> ConsolidationResult:
    """
    Deduplicate and prioritise findings.

    - Remove duplicates using strategies (keep highest-confidence finding)
    - Filter out findings below min_confidence threshold
    - Sort: critical → major → minor → suggestion, then by confidence desc
    - Never raises
    """
    active = strategies if strategies is not None else _DEFAULT_STRATEGIES
    original_count = len(findings)

    # Deduplicate: for each group of duplicates, keep highest confidence
    kept: list[AIReview] = []
    removed = 0

    for candidate in findings:
        is_dup = False
        for i, existing in enumerate(kept):
            for strategy in active:
                try:
                    if strategy.are_duplicates(candidate, existing):
                        # Keep whichever has higher confidence
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
    removed += len(kept) - len(filtered)

    # Sort by severity then confidence desc
    sorted_findings = sorted(
        filtered,
        key=lambda f: (_SEVERITY_ORDER.get(f.severity, 99), -f.confidence),
    )

    return ConsolidationResult(
        findings=sorted_findings,
        duplicates_removed=removed,
        original_count=original_count,
    )
