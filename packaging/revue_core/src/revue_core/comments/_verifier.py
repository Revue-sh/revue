"""Vex semantic verifier — language-agnostic safety check on AI suggestions.

Sits in the Consolidator's post-processor chain. For each ConsolidatedFinding
that carries a ``code_replacement``, Vex reads the file at HEAD and asks an
LLM: "If a developer clicks 'Commit suggestion' on this patch, will the
result be safe?"

Three outcomes, each turning into a different post-processing action:

  * ``apply``              — patch is safe; pass the finding through.
  * ``drop_cr_keep_prose`` — patch is unsafe (wrong anchor, orphaned control
                              flow, bad indent); strip ``code_replacement``,
                              keep prose so the developer still gets the
                              insight.
  * ``reject_finding``     — finding itself is wrong (issue already
                              addressed, misidentified); drop entirely.

Failure modes are biased toward keeping suggestions visible:

  * Malformed Vex response → treat as ``apply`` (with a warning log).
  * Unreadable file → skip Vex, keep the finding unchanged.

Vex must not have a wider blast radius than the bug it was added to catch.
"""
from __future__ import annotations

import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from .models import ConsolidatedFinding
from .position_adapter import PositionStatus, calculate as _classify_position
from ..core.logging_channels import Log
from ..core.tools import ReadFileTool


# ---------------------------------------------------------------------------
# Verdict value object
# ---------------------------------------------------------------------------

def _ensure_trailing_newline(text: str) -> str:
    """Return *text* with exactly one trailing newline (empty input → empty output)."""
    if not text:
        return ""
    return text if text.endswith("\n") else text + "\n"


def _number_lines(text: str, *, start: int = 1) -> str:
    """Prefix every line of *text* with its 1-based number.

    Format: ``  42 | def foo():`` (right-aligned 5-digit, pipe separator).
    Empty trailing lines are preserved so the LLM can match anchor exactly.
    Used in Vex's prompt so the model can correlate ``anchor_line: N`` with
    a concrete line in the file content.
    """
    if not text:
        return ""
    lines = text.split("\n")
    # If the text ends with a newline, ``split("\n")`` leaves a trailing
    # empty string. Drop it so the rendered output stays faithful to the
    # source line count.
    trailing_newline = text.endswith("\n")
    if trailing_newline and lines and lines[-1] == "":
        lines = lines[:-1]
    width = max(4, len(str(start + len(lines) - 1)))
    numbered = "\n".join(f"{i + start:>{width}} | {line}" for i, line in enumerate(lines))
    return numbered + "\n" if trailing_newline else numbered


_VALID_VERDICTS: frozenset[str] = frozenset({"apply", "drop_cr_keep_prose", "reject_finding"})

VerdictLiteral = Literal["apply", "drop_cr_keep_prose", "reject_finding"]


# REVUE-248 — ADR §D1.a hallucination-clamp window.
# corrected_anchor.line is constrained to [reported_line - K, reported_line + K].
# Initial K=10 (≈3× the observed off-by-3 max in REVUE-247 evidence; tune from
# production data if rejection rate exceeds 5% or accepted-correction deltas
# cluster beyond ±3 lines).
VEX_CORRECTION_MAX_DELTA: int = 10


# REVUE-248 — ADR §D1.e feature flag.
# Setting REVUE_VEX_CORRECTION_ENABLED to any of {"0","false","no","off",""} disables
# the D1 correction logic and reverts Vex to binary-judge mode (no clamp, no
# re-validation, no correction logs). Read once at VexVerifyPostProcessor.__init__.
_FLAG_FALSY: frozenset[str] = frozenset({"0", "false", "no", "off", ""})


def _correction_enabled_from_env() -> bool:
    raw = os.environ.get("REVUE_VEX_CORRECTION_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() not in _FLAG_FALSY


@dataclass(frozen=True)
class CorrectedAnchor:
    """Optional correction to Nova's ``line`` and ``replacement_line_count``.

    When Vex sees that Nova's *content* is right but the *span boundaries*
    are wrong, she can emit a corrected anchor instead of throwing away the
    whole patch. Pairs with any verdict — ``apply`` means "use the
    correction"; ``drop_cr_keep_prose`` means "reposition the prose-only
    comment to the corrected line".

    Invariants: ``line`` ≥ 1 and ``replacement_line_count`` ≥ 1. Anything
    smaller is rejected at construction so malformed LLM output never reaches
    downstream code.
    """

    line: int
    replacement_line_count: int

    def __post_init__(self) -> None:
        if self.line < 1:
            raise ValueError(f"CorrectedAnchor.line must be ≥ 1, got {self.line}")
        if self.replacement_line_count < 1:
            raise ValueError(
                f"CorrectedAnchor.replacement_line_count must be ≥ 1, got {self.replacement_line_count}"
            )


@dataclass(frozen=True)
class VexVerdict:
    """Vex's structured decision on a proposed code_replacement.

    Invariant: ``verdict`` must be one of the three contract values.
    Unknown verdicts raise at construction so callers can't smuggle in
    arbitrary strings (LLM output is validated *before* a VexVerdict is built).

    ``corrected_anchor`` is optional. When present, the consolidator applies
    the corrected line/rlc to the finding regardless of which verdict fired.
    """

    verdict: VerdictLiteral
    reason: str
    corrected_anchor: "CorrectedAnchor | None" = None

    def __post_init__(self) -> None:
        if self.verdict not in _VALID_VERDICTS:
            raise ValueError(
                f"VexVerdict.verdict must be one of {sorted(_VALID_VERDICTS)}, got {self.verdict!r}"
            )


# ---------------------------------------------------------------------------
# Default system prompt (overridable from .yaml at wiring time)
# ---------------------------------------------------------------------------

_DEFAULT_SYSTEM_PROMPT = """\
You are Vex, the semantic judge-and-corrector for Revue.

Given a proposed code change, answer TWO questions:
  1. "If a developer clicks 'Commit suggestion' to apply this patch, will the
     resulting code be safe?" → emit a verdict.
  2. "If the anchor or replacement span is wrong but the issue is real, what
     are the correct values?" → emit a corrected_anchor.

You are language-agnostic. The file may be Python, JavaScript, Go, Rust,
TypeScript, anything. Use semantic reasoning — not per-language tooling.

Verify ALL of the following. If any fail, the patch is NOT safe.

1. Span coherence: removing lines [anchor_line, anchor_line+rlc-1] and
   inserting code_replacement in their place produces a syntactically and
   semantically valid file.
2. No orphaned control flow: the patch doesn't leave a dangling
   else/elif/except/finally/catch without its matching if/try/etc.
3. Indentation preservation: the replacement maintains the indent level
   expected at anchor_line.
4. Semantic coherence: the patch actually addresses the stated issue.

Return JSON ONLY (no prose, no markdown) with these fields:
  verdict:          "apply" | "drop_cr_keep_prose" | "reject_finding"
  reason:           one sentence explaining the decision
  corrected_anchor: null | {"line": <int ≥ 1>, "replacement_line_count": <int ≥ 1>}

Verdicts:
  - apply              — patch is safe
  - drop_cr_keep_prose — patch is unsafe but prose suggestion is still useful
  - reject_finding     — the finding itself is wrong (e.g. issue already addressed)

## Corrected anchor — blank-line / context-line case

Emit corrected_anchor whenever the reported anchor line does NOT contain the
issue the finding describes. The two most common situations:

  • the anchor lands on a blank line that precedes the real defect, or
  • the anchor lands on a context line (e.g. an import, comment, or
    declaration) that is unrelated to the issue prose.

Procedure:
  1. Locate the first non-blank line at or below the reported anchor whose
     content matches the issue prose ("the API_KEY assignment", "the
     hardcoded password", etc.).
  2. Set corrected_anchor.line to that line number.
  3. Set corrected_anchor.replacement_line_count to the number of lines the
     code_replacement should overwrite (1 for a single-line fix; otherwise
     the full syntactic span being replaced).
  4. Keep the verdict you already chose — corrected_anchor is independent.
     "apply" + corrected_anchor means "the content is right, the span was
     wrong"; "drop_cr_keep_prose" + corrected_anchor means "drop the patch,
     but anchor the prose comment at the corrected line".

Worked example (prose only — language-agnostic):
  Reported anchor: line 4 (a blank line).
  File context:
    line 2: an import / require / use statement
    line 3: <blank>
    line 4: <blank>          ← reported anchor; not the issue
    line 5: a hardcoded API-key assignment   ← the actual issue
  Expected output:
    { "verdict": "drop_cr_keep_prose",
      "reason":  "Reported anchor on a blank line; the secret is on line 5.",
      "corrected_anchor": {"line": 5, "replacement_line_count": 1} }

If the anchor IS correct, set corrected_anchor to null.

## Replacement-span completeness — does the range cover the whole block?

When code_replacement spans multiple lines (replacement_line_count > 1) AND
the original range begins with a block-introducing construct (function
declaration, conditional, loop, try, switch, class body, etc.), verify that
the range extends to the natural terminator of that block before emitting
"apply".

A semantically equivalent rewrite that stops one line short of the block's
end is NOT safe: the trailing lines (a final return, a post-loop statement,
a terminal else-branch) are left in place at their original indent and
become orphaned siblings of the replacement. The resulting code may parse,
but the control flow is broken.

Procedure when the range begins inside a block:
  1. Look at the line at end-of-range + 1 (skip blank lines while probing).
  2. If that line is still at or deeper than the outermost (shallowest)
     indent inside the range, the block continues — the range is incomplete.
     "Outermost" means the indent of the first/least-nested statement in the
     range: the block must close past that level to be considered complete.
  3. If that line is strictly outdented from the range's outermost indent (or
     end-of-file is reached), the block terminated cleanly and the range is
     complete.

When the range is incomplete you have two valid outputs:
  • Widen the range: emit corrected_anchor with replacement_line_count set
    to the count that reaches the true block terminator. Keep verdict
    "apply" only if the replacement content is still semantically equivalent
    once the wider span is considered.
  • Drop the patch: emit verdict "drop_cr_keep_prose". The prose finding
    still posts; only the destructive code_replacement is dropped.

Worked example (prose only — language-agnostic):
  Reported anchor: line 38 (start of a loop-bearing function body).
  Reported replacement_line_count: 3 (covers lines 38–40).
  File context:
    line 37: function header                          ← unchanged
    line 38: a variable initialised to empty          ← start of range
    line 39: a loop header                            ← inside range
    line 40: first statement of loop body             ← end of range
    line 41: second statement of loop body            ← orphaned (same indent as line 40)
    line 42: a final return at outer indent           ← orphaned (post-loop terminator)
  The next line after the range (line 41) is at the same indent as the
  loop body inside the range — strictly deeper than the range's outermost
  indent — so the block continues past the range and the range does NOT
  cover the whole block.
  Expected output:
    { "verdict": "drop_cr_keep_prose",
      "reason":  "Replacement covers lines 38–40 but the loop body continues on line 41 and the final return is on line 42; the range under-reaches the block terminator.",
      "corrected_anchor": null }
"""


# ---------------------------------------------------------------------------
# VexVerifier — low-level LLM call wrapper
# ---------------------------------------------------------------------------


class VexVerifier:
    """LLM client wrapper that turns (file_content, finding) into a VexVerdict.

    The class is intentionally narrow: it formats the prompt, calls
    ``ai_client.complete``, and parses the response. No file I/O, no
    consolidator coupling. The post-processor wraps this with the I/O and
    finding-mutation logic.
    """

    def __init__(self, ai_client: Any, system_prompt: str | None = None) -> None:
        self._client = ai_client
        self._system_prompt = system_prompt or _DEFAULT_SYSTEM_PROMPT

    def verify(self, *, file_content: str, finding: ConsolidatedFinding) -> VexVerdict:
        prompt = self._build_prompt(file_content=file_content, finding=finding)

        # P9 — caching.
        # Anthropic: the system prompt is identical across every Vex call in
        # a review (≈3K tokens), so mark it cacheable. cache_control on a
        # list-form system parameter is the supported mechanism; the OpenAI
        # clients defensively strip cache_control if they receive it.
        system_blocks: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": self._system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        # OpenAI providers use prefix caching keyed by ``prompt_cache_key``.
        # Keying by file path means multiple findings on the same file in
        # the same review reuse the same prefix.
        cache_key = f"vex-{finding.file_path}"

        # Bounded retry on empty content: deepseek/deepseek-v4-pro on OpenRouter
        # occasionally returns 200 OK with empty content (provider-side filter,
        # truncation, transient routing). One retry recovers most cases; if it's
        # still empty, fall open to ``apply`` (same as before). Retry budget is
        # scoped tightly to the empty failure mode — malformed-but-non-empty
        # responses fall through immediately as before.
        text = ""
        for attempt in range(2):
            result = self._client.complete(
                [{"role": "user", "content": prompt}],
                system=system_blocks,
                max_tokens=512,
                temperature=0.0,
                agent_name="vex",
                cache_key=cache_key,
            )
            text = (getattr(result, "text", "") or "").strip()
            if text:
                break
            if attempt == 0:
                Log.nova.warning(
                    "[vex] empty response on %s:%d — retrying once before fail-open.",
                    finding.file_path, finding.line_number,
                )
        return _parse_verdict(text)

    @staticmethod
    def _build_prompt(*, file_content: str, finding: ConsolidatedFinding) -> str:
        """Build the verification prompt.

        File and replacement content are presented with 1-based line numbers
        prefixed to every line, and wrapped in unique ``===VEX_FILE_…===``
        markers (not triple-backticks) so that Markdown / docstring / YAML
        files containing fences inside their content don't terminate the
        block prematurely.
        """
        cr = finding.code_replacement or []
        replacement_text = "\n".join(cr)
        # Guarantee a trailing newline so the closing sentinel always lands on its own line.
        file_numbered = _ensure_trailing_newline(_number_lines(file_content))
        replacement_numbered = _ensure_trailing_newline(
            _number_lines(replacement_text, start=finding.line_number)
        )
        return (
            "Verify the following proposed code change.\n\n"
            f"File: {finding.file_path}\n"
            f"Anchor line (1-based): {finding.line_number}\n"
            f"replacement_line_count: {finding.replacement_line_count}\n"
            f"Issue: {finding.issue}\n"
            f"Suggestion: {finding.suggestion}\n\n"
            "Current file content (line numbers are 1-based and prefixed for reference):\n"
            "===VEX_FILE_BEGIN===\n"
            f"{file_numbered}"
            "===VEX_FILE_END===\n\n"
            "Proposed code_replacement (drops in at the anchor, line numbers shown for reference only):\n"
            "===VEX_REPLACEMENT_BEGIN===\n"
            f"{replacement_numbered}"
            "===VEX_REPLACEMENT_END===\n\n"
            'Return JSON: {"verdict": "...", "reason": "...", "corrected_anchor": null | {"line": int, "replacement_line_count": int}}'
        )


# ---------------------------------------------------------------------------
# Response parsing — defensive, fail-open on anything weird
# ---------------------------------------------------------------------------


_FENCED_OBJECT_RE = re.compile(r"```(?:json)?\s*\n(\{.*?\})\s*\n```", re.DOTALL)
_BARE_OBJECT_RE = re.compile(r"(\{[^{}]*\}|\{.*\})", re.DOTALL)


def _extract_json_object(text: str) -> "dict[str, object] | None":
    """Find a JSON object anywhere in *text* — fenced or bare.

    LLMs frequently violate "JSON only" instructions by adding leading or
    trailing prose. Try the fenced form first (most structured), then the
    bare form (greedy match on the outermost braces). Returns ``None`` when
    nothing parses.
    """
    fenced = _FENCED_OBJECT_RE.search(text)
    if fenced:
        try:
            data = json.loads(fenced.group(1))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    # Fall back to scanning for the first balanced top-level brace block.
    for match in _BARE_OBJECT_RE.finditer(text):
        try:
            data = json.loads(match.group(1))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    return None


def _parse_corrected_anchor(value: object) -> "CorrectedAnchor | None":
    """Parse the ``corrected_anchor`` field. Returns ``None`` on absent/invalid."""
    if not isinstance(value, dict):
        return None
    line_raw = value.get("line")
    rlc_raw = value.get("replacement_line_count")
    if not isinstance(line_raw, int) or isinstance(line_raw, bool):
        return None
    if not isinstance(rlc_raw, int) or isinstance(rlc_raw, bool):
        return None
    try:
        return CorrectedAnchor(line=line_raw, replacement_line_count=rlc_raw)
    except ValueError:
        return None


def _parse_verdict(text: str) -> VexVerdict:
    """Parse Vex's LLM response into a VexVerdict; fail open with ``apply`` on errors.

    Tolerant of: trailing/leading prose, missing fields, ``null`` values,
    arrays masquerading as strings, capitalised verdicts, surrounding
    whitespace. Anything that can't be coerced cleanly falls open to
    ``apply`` so Vex's failure mode does not silently strip suggestions.
    """
    data = _extract_json_object(text)
    if data is None:
        Log.nova.warning("[vex] response contained no parseable JSON object; falling back to 'apply'. raw=%r", text[:200])
        return VexVerdict(verdict="apply", reason="Vex response was not parseable JSON; defaulting to apply.")

    verdict_raw = data.get("verdict")
    if not isinstance(verdict_raw, str):
        Log.nova.warning("[vex] verdict field is not a string (%r); falling back to 'apply'.", verdict_raw)
        return VexVerdict(verdict="apply", reason=f"Vex returned non-string verdict {verdict_raw!r}; defaulting to apply.")

    verdict_str = verdict_raw.strip().lower()
    reason_raw = data.get("reason", "")
    reason = (str(reason_raw).strip() if reason_raw is not None else "") or "(no reason supplied)"

    if verdict_str not in _VALID_VERDICTS:
        Log.nova.warning("[vex] unknown verdict %r; falling back to 'apply'.", verdict_str)
        return VexVerdict(verdict="apply", reason=f"Vex returned unknown verdict {verdict_str!r}; defaulting to apply.")

    corrected = _parse_corrected_anchor(data.get("corrected_anchor"))
    # Cast is safe — membership in _VALID_VERDICTS was just checked.
    return VexVerdict(verdict=verdict_str, reason=reason, corrected_anchor=corrected)  # type: ignore[arg-type]


_VexFailureType = Literal["timeout", "malformed_json", "http_5xx", "http_4xx", "other"]


def _classify_vex_exception(exc: BaseException) -> _VexFailureType:
    """Classify a Vex-call exception into one of five buckets for telemetry.

    Resolution order (most specific first):
      1. status_code in 5xx/4xx → http_5xx / http_4xx. SDK errors carry the
         response status; check before class-name heuristics so a 504 doesn't
         become a "timeout" just because its class is named ReadTimeoutError.
      2. ``isinstance(TimeoutError)`` — Python's stdlib timeout marker.
      3. ``isinstance(json.JSONDecodeError)`` — strict JSON parse failure.
      4. Anything else → ``other``.

    Earlier revisions used substring matching on the class name (e.g.
    ``"timeout" in type(exc).__name__.lower()``) and caught ``ValueError`` as
    malformed_json. Both produced false positives — a user-defined
    ``BillingTimeoutPolicyError`` would land in ``timeout``; a
    ``VexVerdict(verdict="bad")`` constructor ``ValueError`` would land in
    ``malformed_json``. The current strict-isinstance approach trades a
    little recall for high precision in the telemetry signal.
    """
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        if 500 <= status_code < 600:
            return "http_5xx"
        if 400 <= status_code < 500:
            return "http_4xx"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, json.JSONDecodeError):
        return "malformed_json"
    return "other"


# ---------------------------------------------------------------------------
# VexVerifyPostProcessor — wires Vex into the Consolidator's chain
# ---------------------------------------------------------------------------


class VexVerifyPostProcessor:
    """Implements FindingPostProcessor: read file, call Vex, apply verdict.

    Exposes both ``process(finding)`` (single) and ``process_all(findings)``
    (parallel batch). The Consolidator prefers ``process_all`` when present
    so Vex's LLM calls run concurrently up to ``max_workers``.

    Observability (P8 + REVUE-248): every invocation increments either
    ``verdict_counts`` (one of apply / drop_cr_keep_prose / reject_finding) or
    ``failure_counts`` (no_code_replacement / read_error /
    timeout / malformed_json / http_5xx / http_4xx / other). Counters are
    accessible via the read-only properties of the same names and are also
    written to ``Log.nova`` with a structured ``[vex-verdict]`` /
    ``[vex-failure]`` prefix so they can be grepped from pipeline logs.
    """

    def __init__(
        self,
        *,
        verifier: VexVerifier,
        repo_root: Path,
        diff_by_file: dict[str, str],
        max_workers: int = 1,
    ) -> None:
        self._verifier = verifier
        self._diff_by_file = dict(diff_by_file)
        self._correction_enabled = _correction_enabled_from_env()
        self._read_tool = ReadFileTool(
            repo_root=repo_root,
            allowed_paths=set(diff_by_file.keys()),
        )
        # max_workers mirrors AIConfig.max_parallel_agents so Vex respects the
        # same TPM budget the user already set for reviewer agents.
        self._max_workers = max(1, int(max_workers))
        # P8 — observability counters.
        # _counters_lock guards both dicts: process_all runs up to max_workers
        # concurrent threads, each doing a read-modify-write on a shared dict.
        # Without a lock the two-op .get(k, 0) + 1 writeback has a race window
        # that silently drops increments (Sonnet 4.6 dogfood finding M2).
        self._counters_lock = threading.Lock()
        self._verdict_counts: dict[str, int] = {
            "apply": 0,
            "drop_cr_keep_prose": 0,
            "reject_finding": 0,
        }
        self._failure_counts: dict[str, int] = {
            "no_code_replacement": 0,
            "read_error": 0,
            # REVUE-248 §D1.d — classified Vex-call failures.
            "timeout": 0,
            "malformed_json": 0,
            "http_5xx": 0,
            "http_4xx": 0,
            "other": 0,
        }

    @property
    def verdict_counts(self) -> dict[str, int]:
        with self._counters_lock:
            return dict(self._verdict_counts)

    @property
    def failure_counts(self) -> dict[str, int]:
        with self._counters_lock:
            return dict(self._failure_counts)

    def process_all(
        self,
        findings: list[ConsolidatedFinding],
    ) -> list[ConsolidatedFinding | None]:
        """Run ``process`` on every finding concurrently.

        Order of the returned list matches the input. Each entry is the same
        possible-value as ``process``: the (possibly mutated) finding, or
        ``None`` for ``reject_finding``.
        """
        if not findings:
            return []
        if self._max_workers <= 1:
            return [self.process(f) for f in findings]

        results: list[ConsolidatedFinding | None] = [None] * len(findings)
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            future_to_index = {
                pool.submit(self.process, f): i for i, f in enumerate(findings)
            }
            for future in as_completed(future_to_index):
                idx = future_to_index[future]
                try:
                    results[idx] = future.result()
                except Exception:
                    Log.nova.exception(
                        "[vex] process() raised on finding %s — keeping original",
                        findings[idx].file_path,
                    )
                    results[idx] = findings[idx]
        return results

    def process(self, finding: ConsolidatedFinding) -> ConsolidatedFinding | None:
        # Prose-only finding — nothing for Vex to verify.
        if finding.code_replacement is None:
            with self._counters_lock:
                self._failure_counts["no_code_replacement"] += 1
            return finding

        read_result = self._read_tool.execute(path=finding.file_path)
        if read_result.is_error:
            # Can't read the file — fail open and keep the finding unchanged.
            # Vex's blast radius must not exceed the bug it was added to catch.
            with self._counters_lock:
                self._failure_counts["read_error"] += 1
            Log.nova.info(
                "[vex-failure] read_error %s: %s — keeping finding as-is.",
                finding.file_path,
                read_result.content,
            )
            return finding

        try:
            verdict = self._verifier.verify(
                file_content=read_result.content,
                finding=finding,
            )
        except Exception as exc:
            # Verifier crashed (rate limit, network, parse). Fail open and
            # classify by error_type so dogfood log greps can see *why* Vex
            # failed without having to scrape stack traces.
            error_type = _classify_vex_exception(exc)
            with self._counters_lock:
                self._failure_counts[error_type] = self._failure_counts.get(error_type, 0) + 1
            truncated = str(exc)[:120]
            Log.nova.warning(
                "[vex-failure] %s:%s error_type=%s message=%s",
                finding.file_path,
                finding.line_number,
                error_type,
                truncated,
            )
            return finding

        with self._counters_lock:
            self._verdict_counts[verdict.verdict] = self._verdict_counts.get(verdict.verdict, 0) + 1
        Log.nova.info(
            "[vex-verdict] %s %s:%s — %s",
            verdict.verdict,
            finding.file_path,
            finding.line_number,
            verdict.reason,
        )

        verdict = self._sanitize_correction(finding, verdict)
        result = self._apply_verdict(finding, verdict)
        if result is not None and result.line_number != finding.line_number:
            Log.nova.info(
                "[vex-anchor-fix] %s:%d → %d",
                finding.file_path,
                finding.line_number,
                result.line_number,
            )
        return result

    def _sanitize_correction(
        self, finding: ConsolidatedFinding, verdict: VexVerdict
    ) -> VexVerdict:
        """Sanitize ``verdict.corrected_anchor`` before ``_apply_verdict`` consumes it.

        Two gates run in order (ADR §D1.a then §D1.b):

          1. Clamp: drop corrections whose delta from the agent's reported line
             exceeds ``VEX_CORRECTION_MAX_DELTA``. Bounds Vex's blast radius —
             a hallucinated line can shift a comment by at most K rows.
          2. Re-validate: feed the corrected line back through ``PositionAdapter
             .calculate()`` (strict binary classifier). Only ``ANCHORED`` is
             accepted; CONTEXT_LINE / REMOVED_LINE / OUT_OF_HUNK revert to the
             agent's reported line (i.e. ``corrected_anchor`` is set to None,
             and ``_apply_verdict`` mutates nothing).

        Both rejections log on ``Log.nova`` so dogfood greps surface them.
        """
        anchor = verdict.corrected_anchor
        if anchor is None:
            return verdict

        # Gate 0 — feature flag (D1.e): silently strip corrections when disabled
        # so behaviour is bit-identical to pre-D1 (no clamp/revalidate logs).
        if not self._correction_enabled:
            return replace(verdict, corrected_anchor=None)

        # Gate 1 — clamp
        delta = abs(anchor.line - finding.line_number)
        if delta > VEX_CORRECTION_MAX_DELTA:
            Log.nova.warning(
                "[vex-anchor-out-of-bounds] %s:%d corrected=%d reason=window_exceeded",
                finding.file_path,
                finding.line_number,
                anchor.line,
            )
            return replace(verdict, corrected_anchor=None)

        # Gate 2 — composition re-validation. Fail CLOSED: if no diff is
        # available for the file (unexpected — diff_by_file should always
        # cover the finding's file), revert to the agent's reported line
        # rather than accept an unvalidated correction. This bounds Vex's
        # blast radius even when the post-processor wiring is incomplete.
        diff = self._diff_for(finding.file_path)
        if diff is None:
            Log.nova.warning(
                "[vex-correction-rejected] %s:%d corrected=%d status=NO_DIFF",
                finding.file_path,
                finding.line_number,
                anchor.line,
            )
            return replace(verdict, corrected_anchor=None)

        result = _classify_position(
            diff,
            anchor.line,
            finding.file_path,
            anchor.replacement_line_count,
        )

        if result.status is PositionStatus.ANCHORED:
            Log.nova.info(
                "[vex-correction-revalidated] %s:%d → %d status=%s",
                finding.file_path,
                finding.line_number,
                anchor.line,
                result.status.value.upper(),
            )
            return verdict

        # Any non-ANCHORED status → revert to agent's reported line.
        Log.nova.warning(
            "[vex-correction-rejected] %s:%d corrected=%d status=%s",
            finding.file_path,
            finding.line_number,
            anchor.line,
            result.status.value.upper(),
        )
        return replace(verdict, corrected_anchor=None)

    def _diff_for(self, file_path: str) -> "str | None":
        """Return the diff snippet ``calculate()`` needs for re-validation."""
        diff = self._diff_by_file.get(file_path)
        if diff is None or not diff.strip():
            return None
        return diff

    @staticmethod
    def _apply_verdict(
        finding: ConsolidatedFinding,
        verdict: VexVerdict,
    ) -> ConsolidatedFinding | None:
        """Map a Vex verdict (+ optional corrected anchor) to a finding mutation."""
        # Optional repositioning — applied regardless of verdict (D4/D5 — Vex
        # is a fixer, not just a judge). ``apply`` + correction means "Nova's
        # content is right, span boundaries were wrong"; ``drop_cr_keep_prose``
        # + correction means "drop the patch, but anchor the prose here instead".
        anchor = verdict.corrected_anchor

        if verdict.verdict == "apply":
            if anchor is None:
                return finding
            return replace(
                finding,
                line_number=anchor.line,
                replacement_line_count=anchor.replacement_line_count,
            )

        if verdict.verdict == "drop_cr_keep_prose":
            new_line = anchor.line if anchor is not None else finding.line_number
            return replace(
                finding,
                line_number=new_line,
                code_replacement=None,
                replacement_line_count=1,
                # P7: stale snippet would describe the now-rejected span.
                snippet="",
            )

        # reject_finding — correction is meaningless when nothing is posted.
        return None
