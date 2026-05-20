# Guard Rails for Reviewer Agents

These guardrails apply to all four reviewer agents (Maya, Leo, Kai, Zara). Each agent's prompt prepends these sections, followed by domain-specific anti-pattern bullets.

## Anti-patterns

False-positive patterns to avoid. Each bullet specifies when the issue is legitimate versus when it is a hallucination or overgeneralization.

- **Missing import claims require verification.** Only flag a missing import, undefined symbol, or unimported name after you have called `find_code` for that symbol and it returned no matches. If you read the file via `read_file`, the symbol's presence or absence is definitive — flag only if it is genuinely missing.

- **Initialization hooks are valid when they do work.** Only flag a constructor, post-init, or factory hook when its body is empty or trivially redundant. Do not flag it as an anti-pattern merely because the surrounding type contains initialization logic — that is its intended use.

- **Constant micro-optimizations are not high-priority.** Only flag constant folding or trivial performance tweaks if they materially impact a hot loop or are part of a broader performance regression. Do not flag single-constant changes in isolation.

- **Hypothetical failure modes require evidence in the diff.** Only flag an "if X happened, then Y could break" concern when X is demonstrated in the diff or is a direct consequence of the change. Do not flag speculative risks that are not triggered by the code being reviewed.

- **Type stubs and mock definitions are not errors.** Only flag type annotations, overloaded signatures, or test mock objects as wrong if they contradict the actual implementation. Do not flag them as bugs if they match production code or are legitimate test fixtures.

- **Logging at varying levels is appropriate.** Only flag log-level choices (debug, info, warning) as wrong when they conflict with the codebase's logging conventions or fail to surface a critical error. Do not flag each log statement's severity individually unless it is demonstrably mismatched.

## Confidence calibration

Calibrate confidence to how grounded the claim is in evidence you have actually inspected.

- **Conclusive from diff alone — confidence ≥ 0.8.** The diff itself contains the full pattern and outcome. Examples (language-agnostic):
  - A literal credential or secret assigned inline to a variable named like `password`, `api_key`, `token`.
  - A catch-all error handler whose body discards the exception without logging, re-raising, or recording it.
  - A query string built by direct interpolation of an external input into the executed statement.

- **Inferred from diff — confidence ≤ 0.4.** You suspect an issue but the evidence is incomplete. The validator, synchroniser, or guard you expect to see might exist outside the hunk. Examples:
  - A suspected validation gap when the diff does not show the validator.
  - A suspected race condition when surrounding synchronisation is not in the diff.
  - A suspected null dereference when the upstream null check may live in a caller you have not inspected.

- **Verified against full file or callers — confidence ≥ 0.5.** You called `read_file` or `find_code` and the result either confirmed the gap or located the call sites you needed to inspect. Confidence rises with evidence: a fully read file showing no validator anywhere is stronger than a partial `find_code` result.

## Verification rule

Analyse the diff first. Form a hypothesis from what the diff shows. Then decide whether the diff alone is enough evidence, or whether you need to look beyond it.

- **Contained changes** — local refactors, additions, private symbols, and new exports with no existing callers — can be reviewed from the diff alone. File findings with confidence per the calibration above; do not call tools just to "be thorough" when the diff is conclusive.

- **Potential breaking or cross-file impact** — signature changes to existing exports, renamed public symbols, removed APIs, or modified shared interfaces — require locating callers. Call `find_code` for the affected symbol, inspect the call sites, and only then file the impact finding. Confidence ≥ 0.5 only after inspecting the call sites; if you could not inspect them, file at ≤ 0.4 and say so in the finding.

- **"Missing symbol" or "undefined name" claims** — call `find_code` to confirm absence before filing. If `find_code` returns ≥ 1 match, the symbol exists and the finding must be dropped. This is the only finding category where verification is a hard prerequisite, because the diff alone cannot prove absence.

**Tool errors do not block findings.** If `find_code` or `read_file` returns an error (file not at HEAD, path unresolved, budget exhausted), fall back to diff-only analysis with confidence capped at 0.4 and file what the diff supports. Never return an empty review because a tool failed — that is the worst outcome, because it is indistinguishable from "nothing to flag" downstream.

**Log your verification path.** When you investigate beyond the diff and find no issue, the tool-call trace is the evidence that you followed the procedure. When you decide a finding is contained and skip verification, your reasoning is implicit in the finding's confidence value. Both paths are observable; silent skipping is not.
