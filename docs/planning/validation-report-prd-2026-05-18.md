# PRD Validation Report — Revue v2.0

**Report date:** 2026-05-18
**PRD under review:** `docs/planning/prd.md` (v2.0, dated May 2026)
**Rubric:** `_bmad/bmm/2-plan-workflows/create-prd/data/prd-purpose.md`
**Strategic context:** [[project_anthropic_deprioritisation]] + [[feedback_customer_cost_messaging]]
**Validator:** BMAD validation pass
**Scope:** Quality of the v1.4 → v2.0 edit, not the strategic content itself.

---

## 1. Executive Summary

**Overall verdict: Pass with major edits.**

The strategic content is right — the cost-driven pivot, `/revue-local` as primary surface, DeepSeek default, AI-model-agnostic principle, and CI deprecate-but-maintain track are all present and broadly internally coherent in the *touched* sections. However, the v2.0 edit was surgical across 12 of 17 sections, leaving stale v1.x framing in the un-touched sections (§4.2, §5, §10, §11.1 tier table) and producing a cluster of cross-section inconsistencies (agent count, default-model examples, custom-agent support flag, phase versioning). Cost-savings positioning is *present* but is not *load-bearing* in §1 — the Executive Summary still leads with the technical-capability narrative ("multi-agent BMAD architecture"), which is a direct hit on the documented anti-pattern in [[feedback_customer_cost_messaging]]. Measurability problems exist on the headline ≥40% AI-spend KPI in §3.5.

**Severity counts:**

| Severity | Count |
|----------|-------|
| Critical | 9 |
| Warning | 14 |
| Informational | 11 |
| **Total** | **34** |

**Top 3 findings (must-fix before sign-off):**

1. **CRIT-01 (§1, lines 11–27):** Executive Summary leads with technical capability ("multi-agent BMAD architecture") instead of customer cost outcome. Cost narrative is buried in the third bullet of "Core Principles" (line 21), violating the headline rule in [[feedback_customer_cost_messaging]] and the documented anti-pattern "leads with technical capabilities before customer cost outcome."
2. **CRIT-02 (multi-section):** Agent count is inconsistent. §3.2 line 74 and §12 line 794 list 8 agents (Cleo, Zara, Kai, Maya, Leo, Nova, Sage, Vex). §7.1 documents 6 (no Sage, no Vex). §11.1 line 728 says "All 6". §9.3 line 637 lists "Cleo, Zara, Kai, Maya, Leo, Nova, Vex, Sage" = 8. The reader cannot tell what ships. Vex in particular has *zero* specification anywhere in §7.
3. **CRIT-03 (§5.2, lines 329–338):** GitHub Actions example uses `ai_provider: openai` and `ai_model: gpt-4o`. This directly contradicts the §6.1 declaration that DeepSeek-V4-Pro on OpenRouter is the default, the §6.3 example, the §8.1 example, the §11.2 TCO table, and the §12 ✅-shipped row. A new reader copying §5.2 walks straight into the v1.x default.

---

## 2. Findings by Severity

### 2.1 Critical findings (must-fix)

#### CRIT-01 — Executive Summary buries the cost-savings pillar
- **Section / lines:** §1, lines 11–27
- **Category:** Customer-cost-messaging pillar audit
- **Evidence (line 13):** *"Revue is a platform-agnostic, AI-powered code review service designed for the AI-first development era. It uses a multi-agent BMAD architecture where specialised AI agents (Security, Performance, Code Quality, Architecture, and more) review code in parallel..."*
- **Why this fails:** The first 10 lines lead with platform-agnosticism + multi-agent architecture. The cost narrative appears at line 21, after the two-configuration list and inside a third-rank bullet of "Core Principles." Per [[feedback_customer_cost_messaging]]: "cost-savings narrative belongs in the first 10 lines" and "Leads with technical capabilities before customer cost outcome" is a named anti-pattern. The rule also calls for "Numbers (% reduction, $-saved scenario) preferred over adjectives" — the §1 lead carries neither.
- **Fix:** Rewrite the opening paragraph so the first sentence states the customer cost outcome with a number. Suggested rewrite: *"Revue cuts the AI-coding API spend by ~80% at typical review volumes (§11.2 TCO table) and eliminates it entirely on the `/revue-local` path. It is a multi-agent code reviewer that catches issues inside the customer's AI-coding session — before the AI commits — so the team stops re-paying for the same defects in CI."* Then describe the two configurations, then the principles. Move "We care about your AI bill" from bullet 3 to the first sentence of the doc; demote "Multi-agent by default" and "AI-model-agnostic" below it. Acceptance test: the first 5 lines of §1 contain at least one number and the word "spend" or "bill".

#### CRIT-02 — Agent count is inconsistent across §3.2, §7.1, §9.3, §11.1, §12
- **Section / lines:**
  - §3.2 line 74 — *"Cleo, Zara, Kai, Maya, Leo, Nova, Sage, Vex"* (8 agents shipped)
  - §7.1 lines 434–441 — table lists Cleo, Zara, Kai, Maya, Leo, Nova (6 agents)
  - §9.3 line 637 — *"Cleo, Zara, Kai, Maya, Leo, Nova, Vex, Sage"* (8)
  - §11.1 line 728 — Free *"Basic (1)"*, paid tiers *"All 6"*
  - §12 line 794 — *"Cleo, Zara, Kai, Maya, Leo, Nova, Sage, Vex"* (8 shipped)
- **Category:** Internal consistency + spec completeness
- **Why this fails:** Three numbers in play (6, 8, "All"). Vex has no entry in §7. Sage is in §4.4 but missing from §7.1's "Core Agents (MVP)" table despite §12 marking it ✅ Done. A downstream consumer (epic-breakdown LLM, architect agent) cannot determine the agent inventory.
- **Fix:** Pick the truth: shipped is 8 (Cleo, Zara, Kai, Maya, Leo, Nova, Sage, Vex). Update §7.1 to include Sage and Vex with focus/triggers. Update §11.1 to say "All 8" (and reconcile the Free-tier *"Basic (1)"* count — see WARN-04). Add a one-line description of Vex in §7.1 since it is referenced in §3.2, §9.3, §12 with zero specification.

#### CRIT-03 — §5.2 GitHub Actions example contradicts the DeepSeek default
- **Section / lines:** §5.2, lines 329–338
- **Category:** Internal consistency / strategic-intent fidelity
- **Evidence:** `ai_provider: openai`, `ai_model: gpt-4o`
- **Why this fails:** §6.1 line 365, §6.3 lines 385–390, §8.1 lines 540–544, §11.2 TCO table, and §12 ✅-shipped row all set DeepSeek-V4-Pro on OpenRouter as the default. §5.2's example is the first concrete config snippet a CI-integrating user will copy; leaving it on OpenAI/gpt-4o silently disagrees with every other surface and re-anchors the customer's mental model to the legacy default.
- **Fix:** Rewrite the GitHub Actions snippet to mirror the GitLab snippet (lines 341–351): `ai_provider: openrouter`, `ai_model: deepseek/deepseek-v4-pro`, `ai_api_key: ${{ secrets.OPENROUTER_API_KEY }}`. Optionally add a commented "swap to Anthropic" alternative.

#### CRIT-04 — Vex is referenced but never defined
- **Section / lines:** §3.2 line 74, §9.3 line 637, §12 line 794 (all reference Vex); §7.1 lines 434–441 (Vex absent)
- **Category:** Spec completeness
- **Why this fails:** Vex is presented as a shipped agent in three sections and in `/revue-local`'s execution pipeline, yet §7 — the section whose job is to specify agents — does not name it. A downstream architect or epic-breakdown agent has no focus, trigger, or persona to reason from.
- **Fix:** Add a Vex row to §7.1 table with focus area and trigger conditions. If Vex is the verifier agent (implied by surrounding tooling-in-loop context), say so explicitly. If Vex is internal-only and never customer-facing, mark it accordingly in §7.1 and remove the references from §3.2/§9.3 customer-pipeline copy.

#### CRIT-05 — §4.2 "Deployment Model" describes only the CI surface
- **Section / lines:** §4.2, lines 136–173
- **Category:** Hidden inconsistencies / strategic-intent fidelity
- **Evidence (line 138):** *"Revue's orchestrator runs entirely inside the customer's CI environment."*
- **Why this fails:** §1 and §9 declare `/revue-local` the primary surface. §4.2 still describes the deployment model as CI-only; the diagram shows "User's Repo → CI Trigger → Revue Orchestrator (runs on CI runner)." A reader who reaches §4.2 first will conclude Revue is a CI tool. The licence-validation flow described here also implicitly applies to the CI track only — `/revue-local`'s licence/paywall enforcement is deferred to the sibling brief (§14 Q1) but §4.2 doesn't even acknowledge two execution surfaces exist.
- **Fix:** Split §4.2 into 4.2a "Deployment — `/revue-local` (primary)" and 4.2b "Deployment — CI orchestrator (deprecated-but-maintained)." 4.2a describes the in-session Claude Code execution model; 4.2b is the current §4.2 content (CI-runner, Nuitka, licence validation). The IP-protection/Nuitka paragraph likely applies only to 4.2b — confirm and scope it.

#### CRIT-06 — §5.1 still framed as "MVP" and lists Bitbucket as Phase 2
- **Section / lines:** §5.1, lines 285–324
- **Category:** Hidden inconsistencies
- **Evidence:**
  - Line 285 — heading *"5.1 MVP: GitHub + GitLab"*
  - Line 322 — *`class BitbucketAdapter(VCSAdapter): ...  # Phase 2`*
  - Line 323 — *`class AzureDevOpsAdapter(VCSAdapter): ...  # Phase 2`*
- **Why this fails:** §12 line 793 marks Bitbucket adapter ✅ Done. §1, Appendix A line 890 list Bitbucket as a first-class supported platform. §5.1 is presenting a snapshot from before Bitbucket shipped. Azure DevOps is now Phase 3 (§3.4 line 92, §12 line 828) — calling it Phase 2 is wrong.
- **Fix:** Rename heading to "5.1 Supported Platforms (GitHub, GitLab, Bitbucket)". Add a Bitbucket section parallel to the GitHub/GitLab blocks. Update the protocol example: Bitbucket adapter is no longer `# Phase 2`; AzureDevOps is `# Phase 3`.

#### CRIT-07 — §10 sample review output is CI-shaped only
- **Section / lines:** §10, lines 649–708
- **Category:** Hidden inconsistencies / strategic-intent fidelity
- **Evidence (line 681):** *"A consolidated summary posted to the PR/MR"* — and the entire output example is a GitHub PR comment with Markdown rendering.
- **Why this fails:** `/revue-local` is the primary surface but §10 shows no `/revue-local` output. A reader who is most interested in the primary product cannot see what it produces. The CI sample is appropriate for §10.1–10.2 but §10 has no equivalent block for the in-Claude-Code-session output.
- **Fix:** Add §10.3 "`/revue-local` output format" with a sample of what the Claude Code session sees (markdown digest, exit-code-style block-or-warn signal, what gets streamed back into the AI's working context). State explicitly whether the format matches §10.2 byte-for-byte or differs.

#### CRIT-08 — §11.1 tier table contradicts §3.2 / §7.3 on custom agents
- **Section / lines:** §11.1, line 734
- **Category:** Internal consistency
- **Evidence (line 734):** *"Custom agents ❌ ❌ ❌ ❌ (Post-MVP)"* — all four tiers.
- **Counter-evidence:**
  - §3.2 line 75 — *"Configurable specialised agents: Cleo, Zara, Kai, Maya, Leo, Nova, Sage, Vex"* (shipped)
  - §7.3 lines 502–507 — *"Custom agents can be added per project without touching Revue's core code"* with sample YAML
  - §8.1 lines 585–586 — `custom_agents: - path: .revue/agents/domain-expert.md`
- **Why this fails:** The PRD says custom agents both work and don't work. Either §11.1 is wrong (custom agents ship, just not via UI), or §3.2/§7.3/§8.1 are wrong. Without resolution, billing positioning and feature delivery diverge.
- **Fix:** Disambiguate. Likely truth: custom-agent *YAML files* ship today (per §7.3, §8.1); custom-agent *authoring UI* is Phase 2 (per §12 line 818). Update §11.1 to row-split: `Custom agents (YAML)` ✅ all tiers; `Custom agents (UI)` Phase 2 across all tiers.

#### CRIT-09 — §3.5 ≥40% AI-spend reduction KPI has no measurement methodology
- **Section / lines:** §3.5 line 102, §14 Q2 lines 869
- **Category:** Measurability
- **Evidence (line 102):** *"Customer AI-spend reduction (/revue-local users vs CI-only baseline) ... n/a / ≥40% reduction"*
- **Why this fails:** This is the v2.0 headline KPI — the load-bearing metric for the cost-savings pillar. The methodology is only sketched in §14 Open Question 2 as a *proposal*, not a committed measurement plan. A 6-month target without a defined baseline, sample, instrumentation, or attribution model is unmeasurable in the BMAD sense (`prd-purpose.md` lines 90–110). Specifically unaddressed: what is "CI-only baseline" (per customer historical CI cost? Industry benchmark? Synthetic?); is the 40% measured per-customer or in aggregate; what is the n for a credible read at 6 months.
- **Fix:** Either (a) commit the methodology inline in §3.5 with a footnote: baseline = customer's prior 90-day OpenRouter/Anthropic CI spend; measurement = same diff cohort A/B; attribution = issues caught locally × measured per-review CI token cost. Or (b) downgrade the 6-month target to "instrumented; baseline cohort established" and defer the ≥40% claim to a v2.1 target once data exists.

### 2.2 Warning findings (should-fix)

#### WARN-01 — §1 Core Principles ordering inverts the priority claim
- **Section / lines:** §1, lines 20–26
- **Category:** Customer-cost-messaging pillar audit
- **Evidence:** Bullet order is (1) "We care about your AI bill" (2) "Multi-agent by default" (3) "AI-model-agnostic" (4) "Bring your own key" (5) "Platform agnostic" (6) "Configurable blocking."
- **Note:** Order is correct *within Core Principles* but the prior paragraph (lines 13–18) leads with technical capability, undercutting the priority. See CRIT-01. Independent issue: there is no parallel "headline number" for the AI-bill claim in §1 (no "% reduction", no "$" figure). [[feedback_customer_cost_messaging]] says "Numbers (% reduction, $-saved scenario) preferred over adjectives."
- **Fix:** Cite the ~88%/~82% TCO saving from §11.2 inline in §1's opening paragraph.

#### WARN-02 — "~10× cheaper" claim cited inconsistently
- **Section / lines:** §3.2 line 80, §6.1 line 365, §11 line 716
- **Category:** Measurability / source citation
- **Evidence:** §3.2 says *"~10× cheaper per review than Anthropic Sonnet"*. §6.1 says *"~10× cheaper per typical review than Anthropic Sonnet 4.5"*. §11.2 footnote (line 760) gives precise rates: $3/M prompt + $15/M completion (Sonnet) vs $0.435/M prompt + $0.87/M completion (DeepSeek). At those rates the ratio is ~7× input, ~17× output — depending on prompt/completion mix, ~10× is a reasonable summary but the source ticket for the headline is REVUE-265 (smoke evaluation), which §6.1 names but §3.2 does not.
- **Fix:** Add `(REVUE-265; see docs/research/deepseek-v4-pro-evaluation.md)` to the §3.2 occurrence so every "~10×" claim has the same source anchor.

#### WARN-03 — §4.4 "Sage v1 vs v2" header uses "v1.5" not in roadmap
- **Section / lines:** §4.4, line 271
- **Category:** Internal consistency
- **Evidence (line 271):** *"| Capability | MVP (v1.0) | Phase 2 (v1.5) |"*
- **Why this fails:** Roadmap (§12) uses Phase 1 / Phase 2 / Phase 2b / Phase 3 framing and version 2.0. There is no "v1.5". §3.3 uses "v2.x" for Phase 2. Document version itself is 2.0.
- **Fix:** Change column header to "Phase 2 (v2.x)".

#### WARN-04 — §11.1 tier table: Free tier "Basic (1)" agent contradicts §7.1
- **Section / lines:** §11.1 line 728
- **Category:** Internal consistency
- **Evidence:** *"Agents | Basic (1) | All 6 | All 6 | All 6"*. §7.1 mandates Cleo + Nova always run as Orchestrator and Consolidator. Even a single-agent review cannot work without those two.
- **Fix:** Either rename to "Single specialised agent (Cleo + 1 reviewer + Nova)" or rework the tier model. Likely the intent is "1 reviewer agent" not "1 total agent".

#### WARN-05 — §3.2 mixes v1.x and v2.0 milestones in one checklist
- **Section / lines:** §3.2, lines 72–81
- **Category:** Information density / clarity
- **Evidence:** Heading reads *"MVP Goals — shipped (v1.0–v2.0)"*. The list mixes pre-pivot items (multi-agent CI, GitHub App, blocking) with the pivot items (DeepSeek default, `/revue-local` Mode 2). For a reader trying to understand "what changed in v2.0", this conflates strategic-pivot deliverables with original-MVP deliverables.
- **Fix:** Split into two checklists or annotate each row with the version that shipped it.

#### WARN-06 — §4.3 BMAD pipeline diagram omits Sage and Vex
- **Section / lines:** §4.3, lines 175–222
- **Category:** Internal consistency / spec completeness
- **Evidence:** Diagram shows Cleo → Zara/Kai/Maya/Leo → Nova → Sage. Vex is absent. Maya is grouped with the security pillar visually rather than the quality pillar.
- **Fix:** Add Vex node (after Nova? In parallel with Sage?) and re-line the agent grid so Maya is grouped with the code-quality agents.

#### WARN-07 — §6.4 architecture diagram lacks DeepSeek/OpenRouter as default
- **Section / lines:** §6.4, lines 406–426
- **Category:** Strategic-intent fidelity
- **Evidence:** The `create_ai_client(config)` factory and `AIClient` protocol are provider-neutral as intended, but the worked-through example lists Anthropic/OpenRouter/OpenAI/Azure/Custom — no indication that OpenRouterClient is the default route. A reader could plausibly assume Anthropic is the canonical example because it is first.
- **Fix:** Reorder so OpenRouterClient appears first; add a single sentence: *"`create_ai_client(default_config())` returns an `OpenRouterClient` configured for `deepseek/deepseek-v4-pro`."*

#### WARN-08 — §7.3 agent definition format does not encode the model-agnostic principle
- **Section / lines:** §7.3, lines 454–500
- **Category:** Strategic-intent fidelity
- **Evidence:** The Zara YAML example has `system_prompt`, `triggers`, `review_focus`, etc., but no `model` constraint, `provider` constraint, or comment that the prompt must remain provider-neutral. [[feedback_agent_prompts_language_agnostic]] in memory establishes that agent prompts must not encode model-specific framing — the PRD does not enforce that here.
- **Fix:** Add a short note in §7.3 stating "Agent system prompts must be provider-neutral — no model-specific instructions, no Anthropic/OpenAI-shaped framing." Optionally show a `tier:` field in the YAML for downstream registry binding.

#### WARN-09 — §9.4 references the "sibling product brief" without a path
- **Section / lines:** §9.4 line 645, §14 Q1 line 868
- **Category:** Traceability
- **Evidence (line 645):** *"covered in the sibling product brief — this PRD only defines the customer-facing behaviour."*
- **Why this fails:** The brief is referenced four times in this PRD (§3.3 line 84, §9.4, §12 line 813, §14 Q1) but never named. A downstream consumer (LLM or human) cannot follow the cross-reference.
- **Fix:** Replace each occurrence with a concrete path (e.g. `docs/planning/revue-local-distribution-brief.md`) or mark "TBC — sibling brief not yet authored" until it exists.

#### WARN-10 — §11.2 TCO methodology buried in a sub-table footnote
- **Section / lines:** §11.2, line 760
- **Category:** Information density
- **Evidence:** The pricing-rate assumptions (Anthropic $3/$15 per M; DeepSeek $0.435/$0.87 per M; ~120K prompt + ~20K completion) sit in a one-line italic footer.
- **Why this fails:** These are the load-bearing assumptions for the TCO claim. Burying them as a single paragraph below a four-column table makes the table look like an unsourced marketing number.
- **Fix:** Promote the assumption block above the TCO table or alongside it as an "Assumptions" panel. Add the date the rate card was sampled (rates drift).

#### WARN-11 — §3.5 Success Metrics has no measurement-method column
- **Section / lines:** §3.5, lines 97–106
- **Category:** Measurability (prd-purpose.md NFR criteria)
- **Why this fails:** Every row is a target with no test method. BMAD NFR template: *"The system shall [metric] [condition] [measurement method]"*. Examples: "Avg review time <3min" — measured at what percentile? Across what sample? "False positive rate <15%" — by whose adjudication?
- **Fix:** Add a "Measurement method" column to the §3.5 table. Each row gets one row-specific method.

#### WARN-12 — §13 Non-Goals don't address the `/revue-local` shift
- **Section / lines:** §13 Non-Goals, lines 857–862
- **Category:** Hidden inconsistencies
- **Evidence:** Non-goals are: no Sage auto-commit, no fix-outside-diff, no replace-linters, no full-codebase-index, no refactoring tool. All five are scoped to the CI-era product.
- **Why this fails:** With `/revue-local` as primary, fresh non-goals apply: "does *not* replace the customer's AI-coding agent", "does *not* run as a git pre-commit hook", "does *not* require Revue-side AI API key in the `/revue-local` path". These are the *exact* misconceptions a reader of the v2.0 pivot will form. None are stated.
- **Fix:** Add three `/revue-local`-specific non-goals.

#### WARN-13 — Open Question 2 deferral target is "we" not a person/role
- **Section / lines:** §14 Q2, lines 869
- **Category:** Traceability
- **Evidence:** *"How do we measure customer AI-bill reduction credibly? → Proposal: instrument `/revue-local` to count..."*
- **Why this fails:** The proposal text is good but unowned. Compare to Q5 (line 872) which states a decision; Q2 should state a decision-owner and a target date for resolving the methodology gap (especially since this open question gates the §3.5 headline KPI — see CRIT-09).
- **Fix:** Add "Owner: PM. Target resolution: before Phase 2 dashboard ticket enters sprint planning."

#### WARN-14 — Appendix A row order doesn't lead with cost-savings
- **Section / lines:** Appendix A, lines 882–898
- **Category:** Customer-cost-messaging pillar audit
- **Evidence:** Row 1 is *"AI-workflow integration (pre-commit review by AI agent)"*. Row 2 is *"Cost-savings positioning (we reduce customer AI bill)"*. Per [[feedback_customer_cost_messaging]] the cost-savings pillar should be first, not second.
- **Note:** It is good that cost-savings is a *row* (not a footnote — Appendix A passes that part of the audit). The complaint is order only.
- **Fix:** Swap rows 1 and 2.

### 2.3 Informational findings (consider)

#### INFO-01 — §1 line 18 uses "deprecated-but-maintained (v1.x)" parenthetical
- **Category:** Information density
- **Evidence (line 18):** *"CI/CD service — deprecated-but-maintained (v1.x)"*. The "(v1.x)" is ambiguous — does it mean "supported on v1.x only" or "originally a v1.x feature, still maintained"? Per [[project_anthropic_deprioritisation]] it's the latter, but the abbreviation invites the former reading.
- **Fix:** Spell out: *"CI/CD service — deprecated-but-maintained (originally shipped v1.x, still supported on v2.0+)"*.

#### INFO-02 — Passive-voice anti-pattern instances
- **Category:** Information density (prd-purpose.md "system will allow")
- **Findings:**
  - §2.2 line 51 — *"Most teams have Layer 1. Almost none have Layer 2 in a structured form"* — OK, dense.
  - §5.2 line 357 — *"Direct (any CI platform): `curl -sSL ...`"* — OK.
  - §9.1 line 611 — *"It runs Revue's full multi-agent pipeline against the work-in-progress diff"* — OK.
  - **§4.4 line 226** — *"Sage evaluates each of Nova's findings and decides whether a fix can be safely suggested"* — passive *"can be safely suggested"*. Could be tightened to *"Sage classifies each finding as auto-fixable or human-required."*
  - **§9.2 line 621** — *"a multi-agent reviewer with specialised focus areas (Security, Performance, etc.) intercepts before commit"* — fine.
- **Fix:** Minor copy-edit; not blocking.

#### INFO-03 — §2.1 mixes informal and formal voice
- **Section / lines:** §2.1, lines 32–38
- **Evidence:** *"AI coding tools ... are creating a paradox"* — colloquial. The numeric claims (2x, 5–10x, 10x) lack citations.
- **Fix:** Cite source for "AI-generated code has 2x more bugs" claim. Without a source this is the kind of "we read it on Twitter" stat that won't survive a customer questioning it.

#### INFO-04 — §3.1 quoted vision is unattributed
- **Section / lines:** §3.1, line 66
- **Evidence:** *"> Revue is the AI review layer that every engineering team installs once and relies on forever..."* — blockquoted as if a quote, but it's the team's own statement. Quote formatting is misleading.
- **Fix:** Drop the blockquote or attribute clearly: *"Vision statement (team):"*.

#### INFO-05 — §6.2 supported providers table has dated default for OpenAI
- **Section / lines:** §6.2, line 375
- **Evidence:** OpenAI default model listed as `GPT-4o`. If supported as "customer-extended tier" (per the Notes column), no default needs to ship.
- **Fix:** Clarify "Default suggested" vs "Customer-chosen". Minor.

#### INFO-06 — §11.4 Enterprise sales playbook reference is implementation detail
- **Section / lines:** §11.4, line 780
- **Evidence:** *"See `docs/enterprise-sales-playbook.md` for full call script."* This is operational/sales-process content in a product requirements doc.
- **Fix:** Move to a sales-process artefact and link from §11.4 with a short summary, not the call script reference.

#### INFO-07 — §12 Phase 2b heading uses "current focus" but Phase 2b is *not* the focus
- **Section / lines:** §12, lines 807, 822
- **Evidence:** Phase 2 (line 807) is annotated *"(current focus)"*. Phase 2b (line 822) follows immediately. Naming "Phase 2" and "Phase 2b" is fine, but the lettering reads like a sub-phase rather than a parallel-but-deprioritised track.
- **Fix:** Rename Phase 2b → "Phase 2 — CI Maintenance Track" or move it to a clearly separated section.

#### INFO-08 — Implementation leakage: file paths in §6.4
- **Section / lines:** §6.4, lines 403–404
- **Evidence:** Specific paths `src/revue/core/models_registry.yml`, `src/revue/core/models_registry.py`. PRDs describe capabilities, not paths.
- **Note:** These are in context-setting paragraphs, not in goal/requirement statements, so per prd-purpose.md they are tolerable. Logged here so PM can decide.
- **Fix:** Optional — replace with role names ("the per-model registry file", "the dispatcher module").

#### INFO-09 — Implementation leakage: REVUE-XXX ticket IDs in §3.2 and §12
- **Section / lines:** §3.2 lines 80–81, §12 lines 795–797
- **Evidence:** REVUE-262/263/264/267, REVUE-259/260/261, REVUE-267 cited inline.
- **Note:** Same as INFO-08 — context-setting, not requirement statements. Tolerable in §12 (delivery-status table) but slightly weaker in §3.2 (goals).
- **Fix:** Optional — keep in §12, drop from §3.2.

#### INFO-10 — §4.2 Nuitka mention pre-empts an architecture decision
- **Section / lines:** §4.2, line 170
- **Evidence:** *"Orchestrator compiled to native C binaries using Nuitka — source cannot be decompiled or read."*
- **Note:** This is *technology-choice* leakage in a requirements doc. Nuitka is an implementation choice; the requirement is "binary distribution that resists decompilation". This is OK if the PRD is intentionally locking the choice for downstream consumers — flag for PM judgement.
- **Fix:** Optional — generalise to *"binary distribution (currently Nuitka)"* or leave if intentional.

#### INFO-11 — §10 missing severity scale definition
- **Section / lines:** §10
- **Evidence:** Critical/High/Medium severity icons used in §10.2 example but the severity scale itself is never defined in §10 (or elsewhere). Glossary (§Appendix B) mentions severity once (line 916, in the "Finding" definition).
- **Fix:** Add a short severity-scale block in §10 or Appendix B (what gates blocking, what doesn't, examples).

---

## 3. Findings by Section

| Section | Findings | Notes |
|---------|----------|-------|
| Frontmatter (lines 1–9) | none | Version/date/owner present; v2.0 change-log paragraph good. |
| §1 Executive Summary | CRIT-01, WARN-01, INFO-01 | Cost narrative buried; principles order correct but undercut by lead paragraph. |
| §2 Problem Statement | INFO-03 | Numeric claims need sources. |
| §3 Vision & Goals | CRIT-09, WARN-05, WARN-11, INFO-04, INFO-09 | Headline KPI lacks methodology; success-metrics table missing measurement-method column. |
| §4 Architecture | CRIT-05, WARN-03, WARN-06, INFO-10 | §4.2 still CI-only; §4.3 diagram misses Vex; "v1.5" framing stale. |
| §5 Platform Integration | CRIT-03, CRIT-06 | GitHub Actions example on legacy default; §5.1 heading + Bitbucket framing stale. |
| §6 AI Backend Support | WARN-02, WARN-07, INFO-05, INFO-08 | DeepSeek default present; example ordering still leads Anthropic-first in places. |
| §7 Agent System | CRIT-02, CRIT-04, WARN-08 | Vex undefined; Sage missing from §7.1 table; agent definition format silent on model-neutral prompting. |
| §8 Configuration | (covered by §6 cross-refs) | §8.1 example correct (OpenRouter default). |
| §9 `/revue-local` | WARN-09 | Sibling brief never path-named. |
| §10 Review Output Format | CRIT-07, INFO-11 | CI-only sample; no `/revue-local` sample; severity scale undefined. |
| §11 Pricing & Tiers | CRIT-08, WARN-04, WARN-10 | Custom-agents row contradicts §3.2/§7.3; Free-tier agent count nonsensical; TCO assumptions buried. |
| §12 Phased Roadmap | INFO-07 | Phase-naming readability; otherwise the table is the most internally-consistent section. |
| §13 Constraints & Non-Goals | WARN-12 | Non-goals don't acknowledge `/revue-local` shift. |
| §14 Open Questions | WARN-09, WARN-13 | Q1/Q2 unowned and unpathed. |
| Appendix A | WARN-14 | Row order — cost row should be first. |
| Appendix B Glossary | INFO-11 | Severity term not defined. |
| Appendix C | none material | Pre-existing; not in v2.0 edit scope. |

---

## 4. Strategic-intent Fidelity Score

**Verdict: Faithful but underweighted.**

The v2.0 PRD encodes every load-bearing element of [[project_anthropic_deprioritisation]]: DeepSeek-V4-Pro on OpenRouter as default (§6.1, §6.3, §8, §11.2, §12), `/revue-local` as primary surface (§1, §2.2 Layer 2, §9, Appendix A), CI deprecate-but-maintain (§1, §2.2, §12 Phase 2b), AI-model-agnostic principle (§1, §6.4, §13), customer cost-savings as a positioning pillar (§1, §3.5, §11, Appendix A). All five strategic vectors land somewhere in the document.

What the PRD does *not* yet do is make those vectors load-bearing in the surfaces a first-time reader hits hardest: the Executive Summary (CRIT-01) and the Deployment Model (CRIT-05) still privilege the v1.x framing in the first paragraph; §5 still calls itself "MVP: GitHub + GitLab"; §10 shows only a CI-shaped review output; the headline 40% AI-spend KPI ships without a measurement method (CRIT-09). The pivot is *present in the text* but reads as "we added DeepSeek and `/revue-local` to the existing PRD" rather than "we rewrote the PRD around the new strategic centre of gravity." For a Phase-2 spearhead document, that gap is the difference between a strategic restatement and a strategic re-anchor.

Cost-messaging audit specifically: §11 leads correctly with the cost-care preamble (lines 714–719) and gets it right. §1 does not (CRIT-01). Appendix A includes cost-savings as a row but second (WARN-14). README/website/launch-post audit out of scope but flagged in §12 line 816 as a P0 — verify when those surfaces land.

---

## 5. Recommended Fix Order

Effort estimate units: S = <30 min, M = 30–90 min, L = 90 min+.

| # | Finding | Action | Effort |
|---|---------|--------|--------|
| 1 | CRIT-01 | Rewrite §1 opening paragraph to lead with cost outcome. | M |
| 2 | CRIT-03 | Fix §5.2 GitHub Actions snippet to DeepSeek/OpenRouter. | S |
| 3 | CRIT-02, CRIT-04 | Reconcile agent count to 8 across §3.2, §7.1, §9.3, §11.1, §12; add Vex row + description in §7.1; add Sage row in §7.1. | M |
| 4 | CRIT-08 | Split §11.1 "Custom agents" row into YAML (✅) and UI (Phase 2). | S |
| 5 | CRIT-06 | Update §5.1 heading + Bitbucket/AzureDevOps framing to match §12 reality. | S |
| 6 | CRIT-07 | Add §10.3 `/revue-local` output sample. | M |
| 7 | CRIT-05 | Split §4.2 into 4.2a `/revue-local` + 4.2b CI orchestrator. | L |
| 8 | CRIT-09 | Either commit methodology inline in §3.5 or downgrade the ≥40% target. Decision needed from PM. | M (decision) + S (edit) |
| 9 | WARN-04 | Fix §11.1 Free-tier "Basic (1)" to "Cleo + 1 reviewer + Nova". | S |
| 10 | WARN-11 | Add Measurement-method column to §3.5 table. | M |
| 11 | WARN-12 | Add three `/revue-local`-specific non-goals to §13. | S |
| 12 | WARN-14 | Swap Appendix A rows 1 and 2. | S |
| 13 | WARN-06 | Update §4.3 BMAD diagram to include Vex; re-line Maya. | M |
| 14 | WARN-03 | §4.4 column header v1.5 → v2.x. | S |
| 15 | WARN-08 | Add provider-neutral prompt note to §7.3. | S |
| 16 | WARN-09 | Name the sibling brief path everywhere. | S |
| 17 | WARN-13 | Add owner + resolution target to §14 Q2. | S |
| 18 | WARN-01, WARN-02 | Inline cost number in §1; cite REVUE-265 alongside every "~10×" claim. | S |
| 19 | WARN-05 | Split §3.2 checklist into v1.x and v2.0 milestones or annotate. | S |
| 20 | WARN-07 | Reorder §6.4 client list to OpenRouter-first; add factory-default sentence. | S |
| 21 | WARN-10 | Promote §11.2 TCO assumptions out of footnote. | S |
| 22 | INFO-01 through INFO-11 | Copy-edit pass — handle in one sweep after the structural fixes land. | M (bundle) |

**Critical-path budget:** Items 1–8 = ~6 hours focused work. Items 9–21 = ~4 hours. Total to clean-bill-of-health: ~1 day of PM time, assuming CRIT-09 methodology decision is unblocked.

---

## 6. Open Questions for PM

1. **CRIT-09 — Methodology for ≥40% AI-spend reduction KPI.** Two options on the table: (a) commit the per-customer baseline + A/B + attribution methodology inline in §3.5 now; (b) downgrade the 6-month target to "instrumented + baseline established", defer the % claim. Which does John want to take? This blocks publishing the v2.0 PRD because the headline cost-savings claim hangs on it.
2. **CRIT-04 — Is Vex customer-facing or internal-only?** §3.2/§9.3/§12 treat Vex as a shipped agent in the customer pipeline. §7 has nothing. If Vex is the verifier (internal tooling-in-loop), it should be documented in §4.3 / §7 with that role made explicit. If it's customer-facing, it needs a §7.1 row with focus + triggers.
3. **WARN-09 — Sibling brief path.** Has the `/revue-local` distribution brief been authored or is it still pending? If pending, every cross-reference in this PRD (§3.3, §9.4, §12, §14) needs a "TBC" annotation rather than a blind reference.
4. **INFO-10 — Lock Nuitka in the PRD?** §4.2's mention of Nuitka is a technology-lock-in in a requirements doc. Intentional architectural commitment, or premature lock?
5. **WARN-12 — Final list of `/revue-local`-specific non-goals.** Proposing three: (a) not a git pre-commit hook, (b) not a replacement for the customer's AI-coding agent, (c) no Revue-side AI API key required in `/revue-local`. Does the PM want to add or alter these?
6. **INFO-07 — Phase-naming convention.** Keep "Phase 2 / Phase 2b" or rename to "Phase 2 (`/revue-local`) / Phase 2 Maintenance (CI)". Stylistic; PM's call.

---

*End of validation report. Validator: BMAD validation pass, 2026-05-18.*
