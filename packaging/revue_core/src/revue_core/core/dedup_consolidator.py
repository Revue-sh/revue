"""
Reply-thread analysis for Nova won't-fix detection (REVUE-112).

Finding-consolidation logic (consolidate(), AIContradictionSynthesiser, ConsolidationResult,
SameFileLineStrategy, SimilarIssueStrategy, DeduplicationStrategy, _detect_contradiction_groups,
_build_synthesis_prompt, _parse_synthesis_response, _synthesise_contradictions) was migrated
to comments/consolidator.py in REVUE-210.

This module retains NovaConsolidator and its reply-thread analysis helpers.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from .agent_names import NOVA

from revue_core.core.logging_channels import Log  # logging_channels

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
    """Nova consolidation: reply-thread analysis.

    Constructed with an AIClient for the reply-thread analysis path.
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
                Log.pipeline.exception(
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
            Log.pipeline.exception(
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
