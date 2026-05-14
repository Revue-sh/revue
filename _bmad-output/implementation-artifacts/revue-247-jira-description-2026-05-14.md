*Note:* "AC" numbers below refer to this ticket's acceptance criteria (AC1–AC9). The "AC7 + AC14" in the title refer to REVUE-239's acceptance criteria, which this ticket exists to verify.

h2. User Story

As a Revue maintainer, I want to verify on real PRs across GitHub and GitLab that Revue's inline comments land on the correct line (REVUE-239 AC7) AND that any code_replacement is span-coherent — i.e. does not destructively replace unrelated surrounding code (REVUE-239 AC14, gated on the Vex semantic verifier). The goal is to mark both REVUE-239 AC7 and REVUE-239 AC14 complete with measurable, captured evidence.

h2. Background

REVUE-239 fixed three positioning gaps (resolve/calculate duplication, agent coordinate-system ambiguity, PlatformPosition losing status/reason) and one rendering bug (sentinel blank line). REVUE-244 (commit 269363c) added Vex, the semantic verifier that rejects destructive code_replacement patches. REVUE-246 fixed Bug B (sentinel-fingerprint rendering). All three ship together via PR #137 (merged as 4d6db83 on main), which means REVUE-239 AC7 and REVUE-239 AC14 can now be verified in the same E2E run on a real PR.

A local dogfood on the REVUE-239-only diff (2026-05-13, 1243 lines, 16 files, 3 reviewers, 21 raw findings → 1 medium after Nova consolidation) ran cleanly with zero HIGH-severity false positives. The single medium finding was a false positive (Maya and Leo misread an else-branch control flow). This confirms the pipeline is fit for E2E evidence collection.

h2. Acceptance Criteria

*AC1:* A GitHub PR is opened (or reused, e.g. https://github.com/cbscd/revue-test-github/pull/24) against a repo where PR #137 changes are merged. Revue posts at least one inline comment. The line_number sent to the GitHub API matches the line of the issue described in the comment body. Evidence captured.

*AC2:* A GitLab MR is opened (or reused) against a repo where PR #137 changes are merged. Revue posts at least one inline comment. Same line-correctness verification. Evidence captured.

*AC3:* For every comment carrying a code_replacement in both the GitHub and GitLab runs, the replacement span is coherent — it does not orphan surrounding code (e.g. it does not replace a single if line with a multi-line else block that breaks the surrounding control flow). Coherence is judged by visual inspection of the rendered suggestion fence against the diff context.

Span coherence is judged against these failure modes — any one of which is a ✗:
* A single-line condition (e.g. an {{if}}) is replaced with a multi-line block that breaks surrounding control flow.
* A function signature is replaced with body lines, or vice versa.
* The replacement spans more lines than the original without intent (e.g. swallows the next statement).
* The replacement orphans an opening/closing brace, bracket, or block delimiter.

*AC4:* Vex's semantic verification is active and observable in the run logs — at least one code_replacement either passes verification (evidence: log line confirming Vex ran and accepted) or is rejected (evidence: log line confirming Vex rejected, finding dropped or downgraded). If no code_replacement triggers Vex in the test PRs, AC4 is met by inspecting a synthetic destructive replacement injected via revue-local dry-run.

*AC5:* An artifact file {{_bmad-output/implementation-artifacts/revue-247-ac7-ac14-verification.md}} records per-platform: comment URL, file path, flagged line, claimed line in API request, actual line of the issue in the source, line-match? (✓/✗), span-coherence verdict (✓/✗ for code_replacement comments; N/A for prose-only), Vex verdict (passed/rejected/not-triggered).

*AC6:* For each platform, at least one screenshot of the rendered PR/MR comment is captured showing the comment anchored on the correct line. If any comment carries a code_replacement, a screenshot of the rendered suggestion fence is captured for span-coherence evidence.

*AC7:* Bug B verification: the AI fingerprint sentinel ({{[//]: # (revue:fp:...)}}) is invisible in the rendered markdown of the captured comments.

*AC8:* If any comment lands on the wrong line OR has a destructive code_replacement that Vex did not reject, a follow-up ticket is filed — no patching in this ticket. The artifact records the misalignment or false-negative as evidence. Follow-ups are filed under epic REVUE-87 (Review Intelligence & Knowledge Base) with labels matching the originating fix (e.g. {{revue-239-followup}} or {{revue-244-followup}}).

*AC9:* Full pytest src/revue/tests suite green at the time of the E2E run.

h2. Test Cases

*TC1:* GitHub PR — Revue posts at least one inline comment; line_number in the API request matches the flagged line in source (REVUE-239 AC7 evidence).
*TC2:* GitLab MR — Revue posts at least one inline comment; line_number in the API request matches the flagged line in source (REVUE-239 AC7 evidence).
*TC3:* GitHub PR — every comment carrying a code_replacement renders a span-coherent suggestion fence (no orphaned surrounding code; no destructive replacement of unrelated lines) (REVUE-239 AC14 evidence).
*TC4:* GitLab MR — every comment carrying a code_replacement renders a span-coherent suggestion fence (REVUE-239 AC14 evidence).
*TC5:* Vex semantic verification is observable in logs — at least one code_replacement passes or is rejected by Vex during the GitHub and/or GitLab runs. If neither run triggers Vex, a synthetic destructive replacement is injected via revue-local dry-run to demonstrate the verifier rejects it.
*TC6:* Sentinel fingerprint comment ({{[//]: # (revue:fp:...)}}) is invisible in rendered markdown — verifies Bug B fix.
*TC7:* Artifact file records every comment in both runs with both verdicts (line-match ✓/✗ and span-coherence ✓/✗); misalignments and destructive replacements do not silently pass.

h2. Out of Scope

* Bitbucket E2E inline comment verification — separate ticket if needed.
* Performance measurements (review latency, agent cost).
* Fixing any positioning or destructive-replacement bugs surfaced by the verification — those become follow-up tickets per AC8.
* Re-running unit tests for Vex's tool-call logic (covered by REVUE-244's unit suite).

h2. Dependencies

*Blocked by:* -PR #137 (must merge to main first)- — *Resolved:* PR #137 merged as commit 4d6db83 on main (2026-05-14). This ticket verifies the merged state of REVUE-239 (positioning), REVUE-244 (Vex), and REVUE-246 (sentinel rendering).

*Related:* REVUE-240, REVUE-241, REVUE-243 also land via PR #137 but are not in scope of this ticket's verification.

h2. Definition of Done

* (/) AC1 met (GitHub PR line-correctness evidence captured)
* (/) AC2 met (GitLab MR line-correctness evidence captured)
* (/) AC3 met (span coherence verified for every code_replacement comment)
* (/) AC4 met (Vex verification active and observable, or synthetic destructive replacement demonstrated)
* (/) AC5 met (artifact file populated at {{_bmad-output/implementation-artifacts/revue-247-ac7-ac14-verification.md}})
* (/) AC6 met (screenshots captured for both platforms, including suggestion fences where present)
* (/) AC7 met (sentinel verified invisible in rendered markdown)
* (/) AC8 met (any misalignments or false negatives filed as follow-ups, not patched here)
* (/) AC9 met (full pytest green)
* (/) Jira manually transitioned to Done after artifact is committed to main (this ticket has no implementation diff, so Bitbucket merge automation will not fire)
