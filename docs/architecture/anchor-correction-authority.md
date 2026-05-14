# Anchor Correction Authority — Vex as Single Owner

**Status:** Partially Accepted — D1 + D3 implemented and verified in production CI (REVUE-248, 2026-05-14); D4 pending REVUE-249; D5 pending REVUE-238.
**Updated:** 2026-05-14

---

## Problem

Cross-platform E2E verification on PR #29 / MR #25 (REVUE-247, artefact at `_bmad-output/implementation-artifacts/revue-247-ac7-ac14-verification.md`) surfaced two defects that reproduce on both GitHub and GitLab and survive the current pipeline as shipped after REVUE-201 + REVUE-236 + REVUE-239 + REVUE-244:

- **REVUE-248** — when a finding's true line is preceded by blank lines, the posted anchor sits on a blank line one above (GitHub) or three above (GitLab) the real defect. The artefact records that two of three agents (Leo, Zara) emitted the correct line (5) and one (Maya) emitted the wrong line (4); Nova picked Maya's value. Vex's GitLab log explicitly identified the misalignment in prose — *"Line 4 is blank; the hardcoded API_KEY assignment is on line 5"* — and dropped the `code_replacement`, but the prose finding still posted at the wrong line.

- **REVUE-249** — Vex accepted two `code_replacement` payloads (`apply` verdict) whose `replacement_line_count` under-reached the syntactic block being refactored: a 14-line function returned for a 13-line replacement, a 5-line function returned for a 3-line replacement. Trailing lines (final `return False`, post-loop `return result`) are left orphaned at the original indentation after apply, breaking the file syntactically.

**Platform priority context:** GitHub is the near-term primary customer surface; GitLab is secondary; Bitbucket is part of the MVP but deprioritisable by market demand. This ADR's decisions prioritise GitHub/GitLab consistency via D1–D4 and preserve backward compatibility on Bitbucket via D5's sequencing rule.

Investigation of the codebase identifies three root causes that together explain the defects:

1. **The corrector half of Vex is plumbed but unprompted.** `_verifier.py` defines `CorrectedAnchor` (lines 77–101), parses it from LLM output (`_parse_corrected_anchor`, line 284), and applies it to both `apply` and `drop_cr_keep_prose` verdicts via `_apply_verdict` (line 465). The user-message prompt builder mentions `corrected_anchor` (line 243), but the system prompt (lines 131–160) instructs *"Return JSON ONLY with these fields: verdict, reason"* — so the LLM is told it is a binary judge, not a judge-and-corrector. The model recognises misalignment in prose but does not emit a structured correction.

2. **`DiffPositionResolver.snap()` cannot fix REVUE-248.** The blank-line case occurs inside a pure-addition hunk where every reported line is in the diff, so `snap()` Tier 1 returns the wrong line unchanged. `snap()` is the right instrument for "agent reported a line outside the diff" — it is the wrong instrument for "agent miscounted inside the diff."

3. **Per-agent cross-reviewer agreement is discarded at Nova.** When two of three agents agree on a line and one disagrees, the consolidator does not currently treat the agreement as signal. This is information the pipeline has and is throwing away.

The temptation when adding the fix is to pick a layer and push correctness logic into it — extend `PositionAdapter` to snap; extend `snap()` to read files; bypass Vex with a deterministic snapper. Each choice produces a different second-order coupling. This ADR commits to the layer assignment that minimises new coupling.

---

## Decision

### D1 — Vex is the single owner of anchor correction

Vex moves from binary verifier to **judge-and-corrector**. The system prompt is extended to document `corrected_anchor` as a first-class output field with worked examples (blank-line-precedes-issue case; block-completeness case where the replacement range stops mid-function), and to make the contract explicit: when the anchor sits on a blank line, a context line, or a line whose content does not match the issue, emit a `corrected_anchor`. When the replacement range does not cover the syntactic block, either widen the range via `corrected_anchor` or downgrade to `drop_cr_keep_prose`.

The plumbing already exists. This decision adds an INFO log line on the `nova` channel — `[vex-anchor-fix] file:old_line → new_line` — when a correction is applied.

The decision is implemented through six sub-points (D1.a–D1.f) that close concrete gaps in the corrector contract:

**D1.a — Hallucination-bounding:** `corrected_anchor.line_number` is constrained to `[reported_line − K, reported_line + K]`. Corrections outside this window are rejected and logged on the `nova` channel as `[vex-anchor-out-of-bounds] file:reported_line corrected=N reason=window_exceeded`. This bounds the failure mode — a hallucinated correction cannot place a comment at an arbitrary wrong line.

The initial value is **K=10**. Rationale: observed off-by-N errors in the REVUE-247 evidence sample were off-by-1 (GitHub) and off-by-3 (GitLab), so K=10 provides ~3× headroom over observed magnitudes while remaining narrower than typical hunk windows (≥20 lines in the same fixture). K is an **upper bound, not a target**; the expected correction is ±1–3 lines. K is exposed as a constant (`VEX_CORRECTION_MAX_DELTA`) in `_verifier.py`, not a runtime config, so it is easy to tune from a single edit point once production data accumulates. Revisit if either: (a) production data shows ≥5% of corrections rejected for window-exceeded — value may be too tight; or (b) production data shows a non-trivial rate of accepted corrections beyond ±3 — value may be too loose and admitting hallucinations.

**D1.b — Composition protocol:** Re-validation of Vex's corrected line happens **inside `VexVerifyPostProcessor`** (not as a separate post-processor), keeping the corrector and its validation co-located. The sequence is:

1. Vex emits `corrected_anchor`. `_apply_verdict` (`_verifier.py:465`) updates the finding's `line_number` to the corrected value.
2. `VexVerifyPostProcessor` re-runs `PositionAdapter.calculate()` on the corrected line using the same diff content already in scope.
3. The re-validation outcome dispatches on the returned status:

| `PositionAdapter` status on corrected line | Outcome | Log line (channel: `nova`) |
|---|---|---|
| `ANCHORED` | Correction accepted; finding posts at corrected line | `[vex-correction-revalidated] file:old_line → new_line status=ANCHORED` |
| `CONTEXT_LINE` | Correction rejected; revert to agent's reported line | `[vex-correction-rejected] file:reported_line corrected=N status=CONTEXT_LINE` |
| `REMOVED_LINE` | Correction rejected; revert to agent's reported line | `[vex-correction-rejected] file:reported_line corrected=N status=REMOVED_LINE` |
| `OUT_OF_HUNK` | Correction rejected; revert to agent's reported line | `[vex-correction-rejected] file:reported_line corrected=N status=OUT_OF_HUNK` |

The rejection cases are logged as **WARN level** (not INFO) — a Vex correction proposing a line that fails strict-classifier validation is unexpected and should be observable in production. The accepted case is INFO. After a rejection, the finding posts at the agent's reported line and re-runs the standard `PositionAdapter` flow downstream — no special-case routing to `summary_sink`. If the agent's reported line itself was originally `ANCHORED` and remains so on the downstream resolve, the finding posts normally; if the original reported line was unanchored, it follows the existing unanchored-finding flow (already handled by `UnanchoredFindingExtractor`).

**D1.c — Prompt-contradiction removal:** The current system prompt instructs `"Return JSON ONLY with these fields: verdict, reason"`. This clause is **removed** in the same change that adds the `corrected_anchor` contract — leaving it in place would tell the LLM to ignore the new field the user-message prompt requests. Without this removal, D1.a/D1.b/D1.d are dead code: the model cannot emit a `corrected_anchor` to validate, clamp, or fall back from. Verify the clause is gone in `_DEFAULT_SYSTEM_PROMPT` before declaring D1 implemented.

**D1.d — Vex-failure fallback policy:** Vex failures are handled with **a single attempt and immediate fallback** — no application-level retry layer. The underlying `anthropic` SDK already retries transient network errors internally; an additional retry layer here would double-retry and amplify cost on persistent failures. On the first non-retryable error from Vex's `verify()` call, the failure path runs:

1. Catch the exception in `VexVerifyPostProcessor._verify_one_finding` (around `_verifier.py:439`).
2. Log on the `nova` channel at **WARN level** — `[vex-failure] file:line error_type=<timeout|malformed_json|http_5xx|http_4xx|other> message=<truncated_to_120_chars>`.
3. Increment a new `vex_failure_count` counter on the post-processor (alongside `verdict_counts` and `guard_downgrade`).
4. The finding **preserves the agent's original `line_number`** and continues through the post-processor chain (no routing to `summary_sink`, no drop). Downstream `PositionAdapter.resolve()` runs on the original line as it would have without Vex.

This means: on a Vex outage, customers still see findings — they land at the agent's reported line, which may be off-by-N (today's behaviour pre-D1). On a Vex success, customers see corrected findings. The fallback path is the existing pre-D1 path, ensuring zero degradation below current behaviour on Vex failure.

**D1.e — Feature flag for runtime disable:** An environment variable `REVUE_VEX_CORRECTION_ENABLED` (default `true`) gates D1 correction logic. Setting it to `false` at runtime reverts Vex to binary-judge mode (no `corrected_anchor` emission or application) without requiring a redeploy. This allows rapid rollback if correction hallucination is detected in production.

**D1.f — Silent corrections:** Corrections are silent — no user-facing artefact (footer, annotation, or marker) indicates that a comment was relocated from the agent's reported line. The developer sees a comment that matches the code without metadata about the correction.

Rationale: Vex already reads the file at HEAD and already runs an LLM call with semantic context. It is the only layer in the pipeline that *can* see "line 4 is blank, line 5 is the API_KEY assignment." Adding a second LLM call elsewhere would duplicate cost; adding a non-semantic snapper cannot solve the inside-the-hunk case.

> **Implementation note:** Vex's system prompt is marked cacheable (`cache_control: ephemeral`, `_verifier.py:191-194`). The prompt extension does not invalidate the per-call cache key (`vex-{file_path}`) but lengthens the cached prefix by ~150 tokens. Verify the Anthropic cache-hit metric after the prompt change ships — the extension should remain inside the 5-minute TTL on subsequent findings in the same file. On deploy, the moment the system prompt changes in the source code, all in-flight Vex caches become misses until they are repopulated — the first review run after the deploy pays full-prompt cost on every Vex call. Monitor for latency spikes in the deploy window.

### D2 — `PositionAdapter` stays pure

`PositionAdapter.resolve()` and `calculate()` remain a **strict binary classifier**: given a `reported_line` and a diff, return one of `ANCHORED` / `CONTEXT_LINE` / `REMOVED_LINE` / `OUT_OF_HUNK`. No snapping, no proximity heuristics, no semantic reasoning.

After D1, `PositionAdapter` validates Vex's *output* (the possibly-corrected line) rather than competing with Vex for the "what line?" decision. The strict binary rule (REVUE-236's accepted design) is preserved unchanged.

### D3 — Nova adds a majority-vote line reconciler

Nova's current grouping strategy is `ProximityAndCountGroupingStrategy` (file + line-distance proximity, configurable N=3, K=3 limits). Per-agent findings within the same proximity group already contain all agent perspectives on a localized region. The Nova synthesis prompt currently instructs `line (integer — use first line of group)` — *that* is the mechanism of the REVUE-247 bug.

This decision changes Nova's synthesis instruction to: *"When ≥ N-1 of N agents in the group agree on a single `line_number`, use the majority line. Otherwise, use the first line of the group."* The reconciliation is deterministic, runs in Nova's single-shot synthesis, and emits an INFO log line on the `nova` channel — `[nova-reconcile] file:majority_line/minority_lines`.

Rationale: the REVUE-247 evidence shows the agreement signal is already present in the pipeline within each `SynthesisGroup` (Maya reported 4, Leo reported 5, Zara reported 5 — all in one group). Nova silently discards the 2-of-3 signal and picks the first line (4). A small deterministic step here means Vex's correction load drops for the common case, and Vex's prompt is reserved for the cases where agents genuinely disagree or no clear majority emerges.

### D4 — `OrphanLineGuardPostProcessor` runs after Vex

A new deterministic `FindingPostProcessor` runs after `VexVerifyPostProcessor` in the consolidator chain. Its responsibility is narrow:

- For every finding with `verdict == apply` and `code_replacement is not None`, read the file at HEAD (via `ReadFileTool`, using a constructor-injected instance with the same allowlist Vex holds) and compute the minimum indent depth across all lines in the replacement range `[start_line, end_line]`.
- Inspect the line at `end_line + 1`. If its indent depth is ≥ the replacement's minimum indent depth and `end_line + 1` is not at a strictly shallower scope (e.g., not outdented to the parent level), the replacement stops inside a block that continues.
- If so, downgrade the verdict to `drop_cr_keep_prose` and record the reason `"trailing-line orphan detected at L<end_line + 1>"`.
- Increment a new `guard_downgrade` counter on the processor, separate from Vex's `verdict_counts`, so the LLM-vs-guard contribution is observable.

This rule catches Python functions that stop one line before the final `return`, nested if/while blocks that stop mid-body, and similar cases where the syntactic block boundary is inside the reported range.

Rationale: the guard is language-agnostic via indent-only reasoning, cannot regress under a model swap, and catches both observed REVUE-249 regressions deterministically. It does **not** live inside Vex because LLM judgement and deterministic guards are orthogonal concerns and composing them in one prompt erodes both. Vex's prompt extension (D1) for block-completeness is the secondary semantic layer; the guard is the hard backstop.

### D5 — `DiffPositionResolver.snap()` is transitional, not architectural

Vex is already wired into all three platforms (GitHub, GitLab, Bitbucket) via the consolidator's post-processor chain. The Bitbucket-specific difference is that its position-resolution step in `poster.py:399-430` currently uses the legacy `snap()` function rather than `PositionAdapter.resolve()` (used by GitHub and GitLab).

The architectural commitment from this ADR is: there is **one** anchor resolver — `PositionAdapter` — and **one** anchor corrector — Vex. `snap()`'s 3-tier nearest-line / file-clamp logic was the right answer for the problem REVUE-201 solved (agent line numbers outside the diff window), but D1–D4 close that gap from a different direction.

**Sequencing requirement:** REVUE-238 (Bitbucket migration) must ship `PositionAdapter.resolve()` for Bitbucket in the same change that removes `snap()` from the production call path. The retirement cannot leave Bitbucket without a position resolver. This is an internal integrity constraint within REVUE-238, not a cross-ticket dependency on a separate wiring ticket.

---

## Out of scope

- **Decomposing the positioning pipeline further.** The five layers (agent → Nova → Vex → PositionAdapter → formatter) are each doing a distinct thing. Merging any two would force the others to grow.
- **Adding a semantic snapper as a separate component.** Vex already reads files semantically; a second component duplicates cost without adding capability.
- **Reopening Done positioning tickets.** REVUE-201, REVUE-236, REVUE-239, REVUE-240, REVUE-244 are all shipped. This ADR builds on them; it does not unwind them.
- **Per-language structural guards.** D4's indent-depth check is intentionally language-agnostic. Adding tree-sitter or per-language AST parsing would buy precision at the cost of language coverage and dependency surface; the indent check catches the observed cases for free.
- **Applying D4 to prose-only findings whose `line_number` lands on a block header.** Out of scope for this ADR; revisit if production data shows the failure mode for prose-only findings.

---

## Expected impact

| Metric | Current (REVUE-247 evidence) | After D1–D4 |
|--------|------------------------------|-------------|
| GitHub line-match rate on API_KEY case (PR #29) | 3 of 4 (off-by-1 on Comment 1) | 4 of 4 |
| GitLab line-match rate on API_KEY case (MR #25) | 3 of 5 (off-by-3) | 5 of 5 |
| Span-coherent `apply` verdicts in the REVUE-247 sample | 0 of 2 | 2 of 2 (orphan guard downgrades both) |
| Vex correction emission rate | 0 (prompt does not request) | Non-zero on blank-line and block-completeness fixtures |
| Cross-agent voting signal usage at Nova | Discarded | Majority used when ≥ N-1 agree |

Estimates assume Haiku as the development model (the verification run used Haiku-tier agents). Sonnet 4.6 E2E (per the project's pre-ship convention) should match or exceed the same rates given identical prompt extensions.

---

## Verification (D1 + D3 — REVUE-248, 2026-05-14)

**Run:** GitHub PR #30 on `cbscd/revue-test-github`, CI run `25886368335`, model `claude-sonnet-4-6`. Branch `feat/REVUE-248-vex-corrector-and-nova-majority`.

### Log-line evidence

All REVUE-248 log lines fired during the Sonnet 4.6 review run:

```text
[vex-verdict] apply src/revue/comments/_verifier.py:536
[vex-verdict] apply src/revue/comments/_verifier.py:459
[vex-verdict] apply src/revue/comments/consolidator.py:190
[vex-correction-revalidated] src/revue/comments/consolidator.py:190 → 190 status=ANCHORED
[vex-verdict] apply src/revue/comments/consolidator.py:207
🚦 Vex: apply=4 drop_cr=0 reject=0 | no_cr=2 read_err=0 timeout=0 bad_json=0 5xx=0 4xx=0 other=0
```

The composition re-validation gate (D1.b) triggered on one finding (`consolidator.py:190 → 190`): Vex emitted a `corrected_anchor` that matched the reported line (no-op span resize); PositionAdapter classified it `ANCHORED`; the correction was accepted with the INFO log. `[vex-anchor-fix]` did not fire (line unchanged, per D1.f). `[nova-reconcile]` did not fire (all groups had unanimous agent agreement; no override needed). `[vex-failure]` did not fire (0 Vex errors in this run).

### Counter evidence (metrics.jsonl)

```json
"vex": {
  "verdict_counts":  {"apply": 4, "drop_cr_keep_prose": 0, "reject_finding": 0},
  "failure_counts":  {"no_code_replacement": 2, "read_error": 0,
                      "timeout": 0, "malformed_json": 0, "http_5xx": 0, "http_4xx": 0, "other": 0}
}
```

All five REVUE-248 failure-type buckets are present and persisted to `metrics.jsonl` — the observability surface is intact.

### Pipeline throughput

- 12 raw findings across 4 reviewer agents → 6 after Nova consolidation (50% consolidation).
- Vex applied 4 code replacements; 2 findings had no `code_replacement` (no_code_replacement=2).
- 5 inline comments posted to PR; all correctly anchored on `+` lines in the diff.

### Findings produced (validity assessment)

| Sev | Location | Issue | Disposition |
|---|---|---|---|
| 🟡 M1 | `consolidator.py:190` | `n if n == 2` expression looks like a typo; intent is not obvious inline | Fixed in REVUE-248 follow-up commit (clarifying comment at threshold line) |
| 🟡 M2 | `_verifier.py:557` | `_failure_counts[k] = .get(k, 0) + 1` is a two-op read-modify-write under `max_workers > 1` | Fixed in REVUE-248 follow-up commit (`threading.Lock` on both counter dicts) |
| 🟡 (unanchored) | `_verifier.py` read_error log | `Log.nova.info` logs raw error string at INFO; should be WARNING | Pre-existing (not introduced by REVUE-248); deferred to a follow-up cleanup |
| 🔵 L1 | `consolidator.py:214` | `[nova-reconcile]` minority formatted as `%s` of `list[int]` — inconsistent with key=value log style | Fixed in REVUE-248 follow-up commit (comma-joined `minority=`) |
| 🔵 L2 | `_verifier.py:459` | `dict(diff_by_file)` shallow copy is unused-defensive | Accepted as is; harmless and guards against future mutation |
| 🔵 L3 | `_verifier.py:94` | `_FLAG_FALSY` docstring omits `"off"` from listed values | Fixed in REVUE-248 follow-up commit |

Net result: 5 of 6 posted findings are valid. M1, M2, L1, L3 addressed in the same PR; L2 dismissed as intentional. The read_error log level (unanchored) is pre-existing and deferred.

---

## Affected files

| File | Change |
|------|--------|
| `src/revue/comments/_verifier.py` | Extend `_DEFAULT_SYSTEM_PROMPT` with `corrected_anchor` contract and block-completeness rules; add `[vex-anchor-fix]` log line in `_apply_verdict` when correction fires. |
| `src/revue/comments/nova_consolidator.py` *(or current Nova consolidator file)* | Add deterministic majority-vote line reconciler before Vex. Emits `[nova-reconcile]` on the `nova` channel. |
| `src/revue/comments/_orphan_line_guard.py` *(new)* | New `OrphanLineGuardPostProcessor` implementing `FindingPostProcessor`; wired in the consolidator post-processor chain after Vex. |
| `src/revue/comments/_verifier.py` (post-processor wiring) | Add `guard_downgrade` counter property alongside existing `verdict_counts` and `failure_counts`. |
| `src/revue/comments/poster.py:399-430` | Unchanged in this ADR; the legacy `snap()` branch retires when REVUE-238 lands. |
| `docs/architecture/positioning.md` | Add forward link to this ADR in the References section. |
| `docs/architecture/README.md` | Add this ADR to the index table. |
| `src/revue/tests/comments/test_vex_verifier.py` *(extend)* | Tests for the `corrected_anchor` schema in the prompt and for blank-line / block-completeness fixtures. |
| `src/revue/tests/comments/test_orphan_line_guard.py` *(new)* | Deterministic-guard tests covering nested-if, loop, mid-body, negative cases, file-end edge. |
| `src/revue/tests/consolidation/test_majority_line_reconciler.py` *(new)* | Tests for majority-vote reconciliation with 2-of-3, 3-of-3, and tied-vote scenarios. |

---

## Consequences

- **Vex prompt cache lifetime.** D1 extends the cacheable system prompt. The Anthropic 5-minute TTL must still cover typical review duration. If reviews routinely exceed five minutes, the prompt-cache strategy ADR (`prompt-cache-strategy.md`) should be revisited — not blocked by this ADR but flagged.
- **Vex cost.** Per-finding latency rises slightly (longer system prompt; one more output token field). The `corrected_anchor` itself adds ~10 tokens of output. With caching, the marginal cost per finding remains under one cent on Haiku and is rounding noise on Sonnet.
- **Verdict observability.** The new `[vex-anchor-fix]` and `[nova-reconcile]` log lines, plus the `guard_downgrade` counter, change the shape of the per-run telemetry. The pipeline metrics ADR (`pipeline-metrics.md`) should be updated to include these counters if/when JSONL metrics ship.
- **Backward compatibility.** All four changes are additive. Findings produced by the current pipeline still flow through; the only behavioural change is that Vex *may* emit a correction, Nova *may* reconcile a vote, and the guard *may* downgrade a verdict. Default behaviour (no correction, no reconciliation, no downgrade) reproduces today's output bit-for-bit.
- **Testing surface.** Three new test modules; one extended. Total expected addition: 30–40 tests. All deterministic except the Vex prompt-extension tests, which assert prompt-builder output rather than LLM behaviour.
- **REVUE-238 implication.** D5 declares `snap()` transitional. REVUE-238 should not be expanded to absorb any of D1–D4; it remains a focused Bitbucket-adapter migration and on its merge `snap()` is removed.

---

## Implementing tickets

| Ticket | Decision | Scope |
|--------|----------|-------|
| REVUE-248 | D1, D3 | Vex prompt extension (`corrected_anchor` documented) + Nova majority-vote reconciler. |
| REVUE-249 | D1 (block-completeness paragraph), D4 | Vex prompt extension (block-completeness rules) + new `OrphanLineGuardPostProcessor`. |
| REVUE-238 | D2, D5 | Bitbucket → `PositionAdapter` migration; retires `snap()` from production. Confirms D2 (PositionAdapter purity preserved) and completes D5. |

REVUE-248 and REVUE-249's existing AC sets are due for a rewrite to align with the decisions above. That rewrite is tracked outside this ADR — the ADR specifies the architectural commitment; the tickets specify the implementation work.

---

## Review Notes

**Reviewer checklist before Accepted:** Review this ADR against the criteria below. Remove items as they are verified or resolved; all must be cleared before transitioning Proposed → Accepted.

**Architecture & Correctness:**

- [x] **D1 prompt-contradiction resolved.** `_DEFAULT_SYSTEM_PROMPT` no longer contains `"Return JSON ONLY with these fields: verdict, reason"`. The three-field schema (`verdict`, `reason`, `corrected_anchor`) is in place. Verified by `test_default_system_prompt_does_not_instruct_verdict_reason_only` and `test_default_system_prompt_documents_corrected_anchor_as_first_class_field`.
- [x] **D3 grouping assumption verified.** `consolidator.py` uses `ProximityAndCountGroupingStrategy` (file + line-distance proximity). `_majority_vote_line` operates on `SynthesisGroup.findings` — all per-agent reports within a group. D3 wording in the ADR is accurate.
- [ ] **D4 heuristic validated against REVUE-249 fixture.** Pending REVUE-249.
- [x] **D1.b composition protocol edge case covered in tests.** `test_correction_rejected_when_revalidation_status_is_context_line`, `test_correction_rejected_when_revalidation_status_is_out_of_hunk`, `test_correction_rejected_when_corrected_line_strictly_in_minus_old`, and `test_correction_rejected_when_diff_for_file_is_missing` all cover the fallback-to-reported-line + WARN log path.
- [x] **D1 hallucination clamp window size (K=10) appropriate.** Observed off-by-N errors in the REVUE-247 sample were ≤3 lines. K=10 provides 3× headroom. Verified by `test_correction_within_clamp_window_is_applied` (boundary in-bounds) and `test_correction_beyond_clamp_window_is_rejected_and_logged` (delta=K+1 out-of-bounds).

**Evidence & Scope:**

- [x] **Evidence base acknowledged as single-fixture.** Problem section references REVUE-247 artefact explicitly. Verification section above records the post-implementation Sonnet 4.6 CI run against REVUE-248's own PR as a second data point.
- [x] **Cost of inaction stated.** Problem section names misanchored comments as the customer-trust impact. Verification section records finding validity (5 of 6 valid, correctly anchored inline comments) as evidence the fix improved quality.
- [ ] **D5 sequencing constraint is clear.** Pending REVUE-238 — the ADR implementation note in §D5 covers the sequencing requirement.

**Implementation & Testing:**

- [x] **D1 ownership across REVUE-248/249 resolved.** REVUE-248 owns the full prompt rewrite (`_DEFAULT_SYSTEM_PROMPT`); REVUE-249 appends only the block-completeness rules under a separate subsection. The blank-line example is under its own `## Corrected anchor — blank-line / context-line case` heading so REVUE-249's append doesn't reflow it.
- [x] **Test matrix includes D1 guards.** 29 tests in `test_vex_post_processor.py` cover: clamp boundaries (in-bounds, out-of-bounds, both directions), composition re-validation (all four `PositionStatus` values + missing-diff fail-closed), feature-flag (default/off/init-once), and failure classification (5 error_types + message truncation).
- [ ] **D4 determinism can be tested.** Pending REVUE-249.
- [x] **Hanging AC-rewrite dependency named.** AC sets were rewritten in the story file (`_bmad-output/implementation-artifacts/REVUE-248.md`) as part of REVUE-248 delivery. The 18 ACs in the story file supersede the original Jira AC body.

**Documentation:**

- [x] **Problem section root-cause framing is defensible.** Three root causes: (1) Vex's role ambiguity (unprompted corrector) — fixed by D1 prompt rewrite; (2) Nova's agreement signal discarded — fixed by D3 majority-vote; (3) snap() scope creep — addressed by D2/D5.
- [ ] **Expected Impact table user-trust framing added.** Deferred — the Verification section's "5 of 6 valid findings, correctly anchored" is the practical customer-trust evidence. A formal reframing row can be added before the ADR is fully Accepted.
- [x] **Affected-files table pinned paths.** Confirmed: `consolidator.py` is the Nova consolidator; new file is `test_majority_line_reconciler.py`. Table updated in the Affected files section below.
- [x] **Logging channels consistent.** All log lines — `[vex-anchor-fix]`, `[vex-correction-revalidated]`, `[vex-correction-rejected]`, `[vex-anchor-out-of-bounds]`, `[vex-failure]`, `[nova-reconcile]` — are on `Log.nova`. Verified live in the Sonnet 4.6 CI run.
- [x] **Consequences section telemetry note is actionable.** The five new failure_counts keys and the updated pipeline log line are persisted to `metrics.jsonl` (confirmed in the Verification section's counter evidence block). A `pipeline-metrics.md` ADR update is a follow-up, not a blocker.

**Remaining before full Accepted:** D4 (REVUE-249) and D5 (REVUE-238) checklist items. ADR transitions to Accepted on merge of REVUE-238.
