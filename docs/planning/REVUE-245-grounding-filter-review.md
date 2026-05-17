# REVUE-245 — Plan, Architecture & Product Value Review

**Ticket:** [REVUE-245](https://urukia.atlassian.net/browse/REVUE-245) — Pipeline-side FP enforcement (Vex pre-filter + Nova confidence reweighting)
**Status when reviewed:** To Do (implementation gated on empirical signal)
**Date:** 2026-05-16
**Review forum:** BMAD party-mode follow-up
**Participants:** Winston (Architect), Amelia (Dev), John (PM), Mary (BA), Sally (UX)
**Predecessor ticket:** REVUE-244 (prompt-side tightenings — Done)
**Sibling ticket:** REVUE-246 (three-state response contract — Done)

---

## Context

REVUE-244 shipped the prompt-side defenses (anti-pattern list, confidence calibration, verification rule) to reduce HIGH-confidence false-positive reviewer findings. REVUE-245 was always designed as **belt-and-braces**: the prompt asks the agent nicely; the pipeline makes the failure mode unreachable to the developer.

A 2026-05-16 party-mode session re-reviewed the original 2026-05-13 plan after REVUE-244 and REVUE-246 shipped, to confirm scope, refine the architecture, and articulate product value. Original ACs stand; refinements below tighten the plan.

---

## Architecture refinements

### 1. Pipeline-order swap (Winston, Architect)

**Change:** AC4 wiring becomes `reviewer → grounding_filter → calibrator → Vex → Nova` (was: `reviewer → calibrator → grounding_filter → Vex → Nova`).

**Rationale:** `grounding_filter` *drops*; `calibrator` *re-weights*. Drop first, weight what remains. The original order risks double-jeopardy — the calibrator halves a finding's confidence, then the grounding filter drops the same finding anyway. The swap is commutative for kept findings and decisive for dropped ones.

### 2. Split `AgentRunResult.tool_calls` promotion as a precursor ticket (Amelia, Dev)

**Change:** AC3 (`tool_calls` promoted from observability log lines to first-class data on `AgentRunResult`) should land as a separate precursor ticket before REVUE-245 implementation.

**Rationale:** `src/revue/core/tool_loop.py:573` already captures the tool-call list. Promotion is ~30 LOC across `tool_loop.py`, `agent_runner.py`, and `AgentRunResult`. Splitting reduces REVUE-245 to ~350 LOC and decouples the dataclass change from the filter logic for cleaner review.

### 3. UX refinements (Sally, UX)

- **Group AC7 filter footer with REVUE-246's "Reviewed by N/4 agents" coverage line into a single "Review integrity" disclosure block.** The two trust signals should live together — proximity is itself a composable trust signal.
- **Audit trail must also list reweighted-but-not-dropped findings**, not only grounding-filter drops. Otherwise the calibrator silently kills legitimate no-tool-call findings (e.g. "missing trailing newline" — correct without a `find_code` call) by demoting them below the HIGH threshold, with no user awareness.
- **Display confidence in the audit trail as percentages (48%)**, not decimals (0.475). Same UX call as REVUE-244.

---

## Product value summary

### John (PM) — Who benefits and how

| Stakeholder | Value |
|-------------|-------|
| Developer reviewing a PR | Fewer "HIGH" false positives in inline comments. Less time refuting Revue's wrong claims; more time on real issues. |
| PR author | Notification reads "Revue found 2 issues" not "Revue found 5 issues, but 3 are wrong." Trust signal preserved. |
| Engineering manager | Fewer "is this tool worth keeping?" conversations after a confident-but-wrong review. |
| Revue as a product | Directly counters the "cried wolf" anti-pattern that is the #1 retention risk for code-review tools. |

The wins are **invisible by design** — REVUE-245 prevents bad findings from appearing. The developer never sees what was filtered (unless they expand the audit trail). Good UX outcome, hard marketing story.

### Mary (BA) — Strategic / business value

The cost-of-FP curve in code-review tools is **non-linear**. A single confident HIGH-severity false positive in a developer's first or second Revue PR is disproportionately destructive:

- **First-impression bias.** A new user's tolerance for tool errors is much lower than a 6-month user's. First wrong HIGH → "this tool is unreliable" → session abandonment.
- **Confidence asymmetry.** A correct LOW finding builds ~1 unit of trust; a wrong HIGH finding destroys ~10 units.
- **Network effects in teams.** One developer saying "Revue gave me a wrong HIGH" in Slack poisons N teammates' onboarding.

REVUE-245 specifically targets the **highest-cost error class**:

- HIGH severity (max attention)
- Confident phrasing ("X is undefined" — falsifiable, embarrassing when wrong)
- Zero supporting evidence (procedural failure: agent didn't call `find_code`)

The combination is the worst kind of error a code-review tool can make. REVUE-245 makes it *unreachable* in the rendered comment.

### Sally (UX) — User-visible outcome

The collapsed-by-default audit-trail block is a **trust-preserving** surface, not a feature flex. Silent drops break trust; the inspectable details block strikes the right balance — invisible at first glance, transparent on demand.

---

## Quantifiable metrics REVUE-245 enables

- **HIGH-FP rate per dogfood replay** (target: 0, down from REVUE-244 baseline of 3 / PR #140)
- **Total filter activations per run** (`[grounding-filter-stats] drops=N reweights=M` log line — AC6)
- **Per-agent tool-call counts** (latent metric: "which agents review carefully?")

## Qualitative value (hard to measure)

- **Trust compounds.** A developer who sees clean, accurate HIGHs starts treating them as actionable.
- **The pattern scales.** Future grounding/filtering rules (e.g. REVUE-159 won't-fix fingerprinting, future symbol-level grounding) plug in as siblings to Vex without contaminating its scope.

---

## Empirical gate (decision rule)

Implementation is blocked on a **post-244 dogfood replay** against the 14,916-line PR #140 baseline diff.

| HIGH-FP count post-244 | Decision |
|------------------------|----------|
| ≤ 2 | Defer REVUE-245 — park ticket, revisit if FP rate creeps later |
| > 2 | Ship REVUE-245 with the three refinements above |

**Status of the replay (2026-05-16):**

The team attempted the replay on 2026-05-13 (`_bmad-output/implementation-artifacts/revue-244-fp-measurements.md`). Result was **inconclusive** — the dogfood diff exceeded the 15,000-line analyzer cap, and the verdict came back "Review cycle complete" with no findings returned. REVUE-246's three-state contract was specifically designed to disambiguate this kind of silent-empty-vs-clean outcome, but it wasn't on main at measurement time.

A decisive replay still needs to be scheduled — either against the next non-trivial feature branch (opportunistic) or against a synthetic ~5,000-line diff containing known FP-prone code patterns (controlled).

---

## References

- Original 2026-05-13 plan: see REVUE-245 description
- Architecture Decision Record: [`docs/architecture/anchor-correction-authority.md`](../architecture/anchor-correction-authority.md) (D2 — PositionAdapter purity, related but separate scope)
- Predecessor measurement: `_bmad-output/implementation-artifacts/revue-244-fp-measurements.md`
- REVUE-244 baseline: PR #140 dogfood run, 2026-05-13 (3 HIGH-FP findings)
