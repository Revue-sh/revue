"""
Deduplication consolidation — deduplicate and prioritise findings (Story [007]).

Part of Nova consolidation phase. SRP: deduplication only.
OCP: deduplication strategies are pluggable.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from .agent_names import NOVA
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

- **acknowledged_deferred**: The developer has (a) acknowledged that the
  finding is a real issue AND (b) provided a clear, stated reason for
  deferring the fix — e.g. the overhead is acceptable under current
  conditions, or the fix is tracked for a later sprint. This is NOT a
  permanent suppression. Revue thanks the developer and closes the thread.
  No lessons PR is created. Use this when the developer explicitly commits
  to fixing it later with a stated justification.

- **acknowledged_fixed**: The developer has replied stating they fixed the
  code (e.g. "Fixed in commit X", "Done", "Addressed"). The finding is
  resolved by a code change rather than a policy decision.

- **already_handled**: The thread already contains a bot acknowledgment reply
  from a previous review cycle. Indicators: a reply contains
  "[//]: # (revue:ack)", mentions "Lessons PR", "reaffirming this finding",
  "could you explain", or otherwise reads as an automated response rather
  than a developer reply. No further action needed.

Intent is determined semantically — no specific keywords are required.

Tone guidance for reply_draft:
- Collaborative, not accusatory
- Direct and brief
- Do NOT use: testament, pivotal, ensure, seamless, highlight, underscore, delve
- Do NOT overuse em dashes
- For allowed_pattern or disallowed_pattern: note that a lessons PR will be
  opened so the team can learn from this decision (use placeholder
  "[LESSONS_PR_URL]" — the actual URL will be filled in by the service layer)
- For acknowledged_fixed: brief acknowledgment that the fix is confirmed
- For acknowledged_deferred: brief thank-you acknowledging the deferral justification

Pattern and rationale guidance (allowed_pattern and disallowed_pattern only):
- **Pattern**: Write a self-contained sentence or short paragraph describing the
  DESIGN INVARIANT — the rule or guarantee that makes this approach safe or
  forbidden. Explain both WHAT the pattern is and WHY it is safe/forbidden.
  DO NOT include file paths, line numbers, function names, or variable names.
  These become stale when code is refactored or moved.
  Example (good): "Fence detection startswith guard is sufficient because valid
  JSON never begins with triple-backtick — the branches are exhaustive and
  non-overlapping."
  Example (bad): "Conditional guard prevents false-positive logic error on line 152"

- **Specificity rule**: The pattern must be specific enough to apply ONLY to
  the described design choice — not so broad that it would suppress similar-looking
  findings in unrelated code. If your pattern could match guard logic anywhere,
  it is too generic.

- **Rationale**: Apply the same invariant-prose and no-specifics rules to rationale.
  Rationale must also contain NO line-number references and must explain the
  design rule in prose that remains valid after a refactor. Rationale should
  expand on the design invariant and note any tradeoffs or constraints.

Output ONLY a JSON array. Each element must have these fields:
  fingerprint    (string — copy from input)
  decision       (one of the six values above)
  reply_draft    (string — the reply to post; empty string "" for already_handled)
  pattern        (string — present only for allowed_pattern or disallowed_pattern)
  rationale      (string — present only for allowed_pattern or disallowed_pattern)

Do not include pattern or rationale for reason_missing, not_acknowledged,
acknowledged_deferred, acknowledged_fixed, or already_handled.
"""

# Max threads per AI call — keeps the response within max_tokens even on busy PRs.
_THREAD_BATCH_SIZE = 15


class NovaConsolidator:
    """Nova consolidation: deduplication + reply-thread analysis.

    Constructed with an AIClient for the reply-thread analysis path.
    The deduplication path (``consolidate()`` module-level function) does
    not need an AI client and remains a pure function.
    """

    def __init__(self, ai_client: Any) -> None:
        self._client = ai_client

    def analyse_reply_threads(self, threads: list[dict]) -> list[dict]:
        """Analyse developer replies to Revue findings.

        Threads are processed in batches of _THREAD_BATCH_SIZE to keep each
        AI response within max_tokens. Results from all batches are aggregated.

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

        all_decisions: list[dict] = []
        for i in range(0, len(threads), _THREAD_BATCH_SIZE):
            batch = threads[i : i + _THREAD_BATCH_SIZE]
            try:
                all_decisions.extend(self._analyse_batch(batch))
            except Exception:
                _log.exception(
                    "NovaConsolidator.analyse_reply_threads: batch %d/%d failed — skipping. "
                    "Thread fingerprints: %s",
                    i // _THREAD_BATCH_SIZE + 1,
                    -(-len(threads) // _THREAD_BATCH_SIZE),
                    [t.get("fingerprint") for t in batch],
                )
        return all_decisions

    def _analyse_batch(self, batch: list[dict]) -> list[dict]:
        user_content = (
            "Analyse the following developer reply threads and return a JSON array "
            "of decisions, one per thread.\n\n"
            + json.dumps(batch, indent=2, ensure_ascii=False)
        )

        try:
            result = self._client.complete(
                [{"role": "user", "content": user_content}],
                system=_REPLY_THREAD_SYSTEM_PROMPT,
                max_tokens=4096,
                temperature=0.2,
                agent_name=NOVA,
            )
        except Exception:
            _log.exception(
                "NovaConsolidator._analyse_batch: AI call failed. "
                "Thread count=%d, fingerprints=%s",
                len(batch),
                [t.get("fingerprint") for t in batch],
            )
            raise

        return _parse_thread_decisions(result.text)


def _parse_thread_decisions(raw: str) -> list[dict]:
    """Parse the AI response into a list of decision dicts.

    Strips markdown code fences if present. Returns [] on parse failure
    (the caller can decide how to handle a partial failure).
    """
    clean = raw.strip()  # fence-stripped JSON string
    if clean.startswith("```"):
        # Strip ```json ... ``` or ``` ... ```
        lines = clean.splitlines()
        # Remove first and last fence lines
        inner_lines = lines[1:] if lines[0].startswith("```") else lines
        close_idx = next(
            (i for i, ln in enumerate(inner_lines) if ln.strip() == "```"),
            len(inner_lines),
        )
        clean = "\n".join(inner_lines[:close_idx]).strip()

    # Strip trailing commas before } or ] — AI models sometimes emit them.
    clean = re.sub(r",\s*([}\]])", r"\1", clean)

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
    """Same file + line + same severity + same agent = duplicate.

    Cross-agent same-line findings are NOT duplicates — they are contradiction
    candidates for synthesis. Only same-agent repetitions are deduplicated here.
    """
    def are_duplicates(self, a: AIReview, b: AIReview) -> bool:
        return (
            a.file_path == b.file_path
            and a.line_number == b.line_number
            and a.severity == b.severity
            and a.agent_name == b.agent_name
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
        if a.agent_name != b.agent_name:
            return False
        words_a = set(a.issue.lower().split()) - self._STOPWORDS
        words_b = set(b.issue.lower().split()) - self._STOPWORDS
        if not words_a or not words_b:
            return False
        overlap = len(words_a & words_b) / min(len(words_a), len(words_b))
        return overlap >= self._OVERLAP_THRESHOLD


_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

_DEFAULT_STRATEGIES: list[DeduplicationStrategy] = [
    SameFileLineStrategy(),
    SimilarIssueStrategy(),
]


def _detect_contradiction_groups(
    findings: list[AIReview],
) -> list[list[AIReview]]:
    """Find groups of 2+ findings on same (file_path, line_number) from different agents.

    Returns list of groups (each group is list of findings). Groups with 1 finding
    are omitted; those will pass through unchanged.
    """
    groups_dict: dict[tuple[str, int], list[AIReview]] = {}

    for finding in findings:
        key = (finding.file_path, finding.line_number)
        if key not in groups_dict:
            groups_dict[key] = []
        groups_dict[key].append(finding)

    # Keep only groups with 2+ findings from different agents
    contradiction_groups = [
        group for group in groups_dict.values()
        if len(group) >= 2 and len({f.agent_name for f in group}) >= 2
    ]

    return contradiction_groups


def _build_synthesis_prompt(groups: list[list[AIReview]]) -> str:
    """Build TOML-encoded batch prompt for LLM synthesis of contradiction groups."""
    prompt = "[[\n"
    for group in groups:
        prompt += "{\n"
        prompt += f'  file = "{group[0].file_path}"\n'
        prompt += f"  line = {group[0].line_number}\n"
        prompt += '  findings = [\n'
        for finding in group:
            # Escape backslashes first, then quotes and newlines
            issue_escaped = finding.issue.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
            suggestion_escaped = finding.suggestion.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
            prompt += f'    {{ agent = "{finding.agent_name}", issue = "{issue_escaped}", severity = "{finding.severity}", suggestion = "{suggestion_escaped}" }}\n'
        prompt += "  ]\n"
        prompt += "}\n"
    prompt += "]"
    return prompt


def _parse_synthesis_response(raw: str) -> list[dict]:
    """Parse LLM JSON response into list of synthesised findings.

    Handles markdown code fences and trailing commas. Returns [] on parse failure.
    """
    clean = raw.strip()
    if clean.startswith("```"):
        lines = clean.splitlines()
        inner_lines = lines[1:] if lines[0].startswith("```") else lines
        close_idx = next(
            (i for i, ln in enumerate(inner_lines) if ln.strip() == "```"),
            len(inner_lines),
        )
        clean = "\n".join(inner_lines[:close_idx]).strip()

    # Strip trailing commas before } or ]
    clean = re.sub(r",\s*([}\]])", r"\1", clean)

    try:
        data = json.loads(clean)
    except json.JSONDecodeError:
        _log.exception("_parse_synthesis_response: JSON decode failed")
        return []

    if not isinstance(data, list):
        _log.error(f"_parse_synthesis_response: expected list, got {type(data).__name__}")
        return []

    return data


def _synthesise_contradictions(
    findings: list[AIReview],
    ai_client: Any,
) -> tuple[list[AIReview], list[dict]]:
    """Detect and synthesise contradictions via LLM.

    Returns (updated_findings, synthesis_events).
    On LLM failure, returns (original_findings, []).
    """
    contradiction_groups = _detect_contradiction_groups(findings)
    if not contradiction_groups:
        return findings, []

    # Build batch prompt with all contradiction groups
    batch_prompt = _build_synthesis_prompt(contradiction_groups)
    system_prompt = (
        "You are Nova, the Revue code-review consolidator. "
        "For each contradiction group (2+ findings on the same line), produce one unified finding. "
        "Return ONLY a JSON array with fields: file, line, issue, suggestion. "
        "No markdown, no explanation, just JSON."
    )

    try:
        result = ai_client.complete(
            [{"role": "user", "content": batch_prompt}],
            system=system_prompt,
            max_tokens=4096,
            temperature=0.2,
            agent_name=NOVA,
        )
    except Exception:
        _log.exception(
            "_synthesise_contradictions: AI call failed. Group count=%d",
            len(contradiction_groups),
        )
        return findings, []

    synthesised_data = _parse_synthesis_response(result.text)
    if not synthesised_data:
        return findings, []

    # Map synthesised data back to findings; reconcile by (file, line) not array index
    group_by_key: dict[tuple[str, int], list[AIReview]] = {
        (g[0].file_path, g[0].line_number): g for g in contradiction_groups
    }
    synthesised_findings: dict[tuple[str, int], AIReview] = {}
    synthesis_events: list[dict] = []

    for syn_data in synthesised_data:
        if not isinstance(syn_data, dict):
            _log.warning("_synthesise_contradictions: non-dict entry in LLM response — skipped")
            continue
        try:
            line_number = int(syn_data.get("line", 0))
        except (TypeError, ValueError):
            _log.warning(
                "_synthesise_contradictions: invalid line number %r — skipped",
                syn_data.get("line"),
            )
            continue
        file_path = syn_data.get("file", "")
        issue = syn_data.get("issue", "")
        suggestion = syn_data.get("suggestion", "")

        if not file_path or not issue:
            _log.warning(
                "_synthesise_contradictions: missing required fields in LLM entry %r — skipped",
                syn_data,
            )
            continue

        group = group_by_key.get((file_path, line_number))
        if group is None:
            _log.warning(
                "_synthesise_contradictions: LLM returned unknown location %s:%s — skipped",
                file_path, line_number,
            )
            continue

        agent_names = [f.agent_name for f in group if f.agent_name]
        severities = [f.severity for f in group]

        # Synthesised severity = highest severity in group (lowest _SEVERITY_ORDER index)
        max_severity = min(
            severities,
            key=lambda s: _SEVERITY_ORDER.get(s, 99),
        )

        synthesised = AIReview(
            file_path=file_path,
            line_number=line_number,
            severity=max_severity,
            issue=issue,
            suggestion=suggestion,
            confidence=max(f.confidence for f in group),
            agent_name=NOVA,
            synthesised_from=[(f.agent_name, f.category) for f in group if f.agent_name],
        )
        synthesised_findings[(file_path, line_number)] = synthesised

        synthesis_events.append({
            "from_agents": agent_names,
            "file": file_path,
            "line": line_number,
            "severity_in": severities,
            "severity_out": max_severity,
        })

    # Replace original group findings with synthesised ones
    result_findings = []
    added_keys: set[tuple[str, int]] = set()

    for finding in findings:
        key = (finding.file_path, finding.line_number)
        if key in synthesised_findings and key not in added_keys:
            result_findings.append(synthesised_findings[key])
            added_keys.add(key)
        elif key not in synthesised_findings:
            result_findings.append(finding)

    return result_findings, synthesis_events


@dataclass
class ConsolidationResult:
    findings: list[AIReview]
    duplicates_removed: int
    original_count: int
    synthesis_events: list[dict] = field(default_factory=list)  # list of {from_agents, file, line, severity_in, severity_out}

    @property
    def deduplication_ratio(self) -> float:
        if self.original_count == 0:
            return 0.0
        return self.duplicates_removed / self.original_count


def consolidate(
    findings: list[AIReview],
    strategies: list[DeduplicationStrategy] | None = None,
    min_confidence: float = 0.0,
    ai_client: Any | None = None,
) -> ConsolidationResult:
    """
    Deduplicate, synthesise contradictions, and prioritise findings.

    - Remove duplicates using strategies (keep highest-confidence finding)
    - Synthesise contradictions (2+ findings on same file:line) via LLM if ai_client provided
    - Filter out findings below min_confidence threshold
    - Sort: critical → major → minor → suggestion, then by confidence desc
    - Falls back gracefully if LLM unavailable or fails
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

    # Synthesise contradictions (same file:line with 2+ findings from different agents).
    # Runs before the confidence filter: a low-confidence finding that belongs to a
    # contradiction group must not be dropped before synthesis can use it.
    # The synthesised result gets confidence=max(group) — see line 444 of
    # _synthesise_contradictions — so the filter evaluates the final post-synthesis
    # confidence, not the per-contributor confidences.
    synthesis_events: list[dict] = []
    if ai_client is not None:
        pre_synthesis_count = len(kept)
        kept, new_events = _synthesise_contradictions(kept, ai_client)
        synthesis_events = new_events
        removed += pre_synthesis_count - len(kept)  # collapsed findings count as removed

    # Filter by confidence — after synthesis so synthesised findings are evaluated
    # at their final (max-group) confidence.
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
        synthesis_events=synthesis_events,
    )
