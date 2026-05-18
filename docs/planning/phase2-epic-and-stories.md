# Phase 2 — `/revue-local` Productisation: Epic & Story Breakdown

**Source PRD:** `docs/planning/prd.md` (v2.0) — §12 Phased Roadmap
**Source Brief:** `docs/planning/product-brief-revue-local-distribution.md` — §10 Phased Plan
**Parent Epic Track:** Phase 2 — strategic focus of v2.0 pivot
**Status:** Draft — ready for Jira filing
**Updated:** 2026-05-18

---

## Context

This document carves Phase 2 (the v2.0 strategic-pivot focus) into Jira epics and stories. Phase 1 is shipped; Phase 3 is out of scope.

**Scope anchored to:**
- PRD §12 (Phased Roadmap — Phase 2 + Phase 2b features)
- Brief §10 (Phased Plan — Phase 2.a/2.b/2.c sub-phases with exit criteria)

**Sub-phase mapping (brief → epic):**

| Brief sub-phase | Jira epic | Goal |
|---|---|---|
| 2.a — Minimum Viable Distribution | **E-P2A** | Ship `/revue-local` to first 50 paying customers via signed releases + Claude Code installer + licence plumbing |
| 2.b — Polish | **E-P2B** | Expand to Cursor / Windsurf; ship web dashboard; soft-paywall UX |
| 2.c — Scale & Monetisation | **E-P2C** | Anthropic registry submission; billing-API integration; conversion experiments |

**Independent tracks (PRD-only Phase 2 features):**

| PRD feature | Jira epic | Notes |
|---|---|---|
| Sage v2 — auto-commit + multi-round loop | **E-SAGE2** | Builds on shipped Sage MVP; can ship in parallel with 2.b |
| Custom agent authoring (UI) | **E-CAA** | P1; ships after 2.a stabilises |
| Slack / Teams notifications + analytics dashboard | **E-NA** | P2 polish; ships in 2.b window |

**Total:** 6 epics, ~35 stories.

---

## Epic Dependency Graph

```
E-P2A (MVD)
  ↓
  ├──→ E-P2B (Polish) ──→ E-P2C (Scale)
  ├──→ E-SAGE2 (parallel with 2.b)
  ├──→ E-CAA  (parallel with 2.b)
  └──→ E-NA   (parallel with 2.b)
```

**Critical path:** E-P2A licence/paywall plumbing must finish before E-P2A wire-up rollout. Phase 2.c is gated on external dependencies (Anthropic registry maturity, billing-API customer demand).

---

## Epic 1: E-P2A — Minimum Viable Distribution

**Goal:** Ship `/revue-local` as a publicly installable, licence-gated Claude Code skill with cost-saving telemetry.

**Exit criteria (from brief §10.1):**
- End-to-end install on clean Claude Code workstation under 5 minutes (G4 target)
- Fresh-install user runs `/revue-local` on a real diff and sees findings + cost-saving footer in one session
- 25-review free-tier cap enforced end-to-end
- Daily-check + 24h-cache behaviour verified across all four edge cases (online, cached, network-fail-in-window, offline-beyond-24h)

**Effort:** 3–5 engineer-weeks. Largest items: signed-release pipeline + licence-validate / usage-emit endpoints with daily-cache semantics.

### Stories

**E-P2A-S1: Skill bundle packaging & signed releases (Sigstore/cosign)**
- **AC1:** Skill bundled as a Python wheel published to PyPI as `revue-local`
- **AC2:** Each release artefact signed via Sigstore; verification documented in install script
- **AC3:** Version manifest published at `revue.io/skills/manifest.json` (lists current version + sha256 + signature URL) — pre-MVP uses GitHub raw URL with identical schema
- **AC4:** Skill bundle includes agent prompts (loaded by the binary at runtime; not server-delivered)
- **AC5:** Repo `github.com/revue-io/revue-local` public, includes signed-release CI workflow

**E-P2A-S2: One-command install flow (Claude Code path)**
- **AC1:** `curl -fsSL https://revue.io/install.sh | bash` (post-MVP) installs `revue-local` via `uv tool install`, falls back to `pipx`
- **AC2:** Pre-MVP variant points at `https://raw.githubusercontent.com/cbscd/revue/main/scripts/install.sh` with identical script content
- **AC3:** Installer is idempotent — re-running upgrades in place
- **AC4:** Installer auto-detects Claude Code and writes `~/.claude/commands/revue-local.md` (skill descriptor)
- **AC5:** Installer auto-detects `.revue.yml` in workspace and reuses it; falls back to defaults if missing
- **AC6:** Doc page at `revue.io/install` shows the one-command snippet + manual `uv tool install` path

**E-P2A-S3: Licence JWT issuance — `revue.io/activate` browser flow**
- **AC1:** Browser flow at `revue.io/activate` accepts licence key, returns JWT with workspace_id + tier + issuance_ts + expiry_ts
- **AC2:** JWT stored in `~/.config/revue/licence.jwt` with 0600 perms
- **AC3:** CLI command `revue-local activate <key>` performs headless activation against `/api/v2/licence/activate`
- **AC4:** Activation errors (invalid key, exhausted seat) return actionable messages; never silently fail

**E-P2A-S4: Daily-check + 24h-cache licence validation contract**
- **AC1:** Server endpoint `POST /api/v2/licence/validate` returns `{valid: bool, tier, reviews_remaining, refresh_after_ts}` for a presented JWT
- **AC2:** Skill caches validation response for 24h in `~/.config/revue/licence-cache.json`; subsequent invocations within window skip the network round-trip
- **AC3:** Network failure inside 24h cache window: continues with cached result (graceful)
- **AC4:** Network failure outside 24h cache window: blocks invocation with documented message ("Revue needs to verify your licence — check connection or run `revue-local activate`")
- **AC5:** Identical behaviour across Free / Indie / Pro / Enterprise tiers (no tier-graded grace)
- **AC6:** Telemetry endpoint `POST /api/v2/usage/emit` accepts per-invocation usage records (workspace_id, reviews_run, findings_count, ts)

**E-P2A-S5: Free-tier 25-review cap enforcement**
- **AC1:** Server-side counter increments per accepted `/api/v2/usage/emit` event for Free-tier workspaces
- **AC2:** Counter resets on calendar month boundary (UTC)
- **AC3:** When counter ≥ 25, `/api/v2/licence/validate` returns `{valid: true, paywall_state: "exhausted"}`
- **AC4:** Skill renders upgrade prompt (copy from brief §9.6) inline below findings when paywall_state is exhausted; review still runs (soft cap)
- **AC5:** E2E test harness exhausts free-tier counter and verifies the upgrade prompt appears

**E-P2A-S6: CLI cost-saving footer**
- **AC1:** Every `/revue-local` invocation prints a footer below findings: monthly reviews × per-review API cost saved
- **AC2:** Per-review API cost computed from the dispatcher's model registry (DeepSeek default × current OpenRouter price)
- **AC3:** Counterfactual baseline = Anthropic Sonnet 4.5 per-review cost (CI-only equivalent)
- **AC4:** Footer copy reads: "Saved ~$X this month vs. CI-only review (Y reviews × ~$Z/review)"
- **AC5:** Saving figure is locally computed; no server round-trip required
- **AC6:** Hidden behind `--no-footer` flag for piped/CI usage

**E-P2A-S7: Cost-care messaging rollout (README + website hero + pricing page)**
- **AC1:** Repo README opens with: "Revue cuts your AI API spend by ~79–88% on code review..." (matches PRD §1)
- **AC2:** `revue.io` hero swaps to cost-savings headline + TCO table teaser
- **AC3:** Pricing page header carries the savings narrative; pricing tiers show "vs. Anthropic baseline" delta column
- **AC4:** Launch post draft prepared (publication gated on Phase 2.b)
- **AC5:** Copy reviewed against `feedback_customer_cost_messaging` memory rules

---

## Epic 2: E-P2B — Polish

**Goal:** Expand `/revue-local` beyond Claude Code, ship the web cost-saving dashboard, and tighten the soft-paywall UX.

**Exit criteria (from brief §10.2):**
- Wire-up rate (G2) ≥ 60% for new installs in trailing 30 days
- Web dashboard receives ≥ 100 unique workspace visits per week
- Phase 2.a + 2.b cumulative installs ≥ 200

**Effort:** 3–4 engineer-weeks. Dashboard front-end is the largest item.

### Stories

**E-P2B-S1: Cursor installer path**
- **AC1:** Installer detects Cursor (presence of `~/.cursor/`) and writes `.cursor/rules/revue-local.mdc`
- **AC2:** Rule file invokes `/revue-local` before commit per Cursor's rules schema
- **AC3:** Doc page at `revue.io/install#cursor` shows manual setup if auto-detect fails
- **AC4:** E2E test on a clean Cursor profile verifies wire-up

**E-P2B-S2: Windsurf installer path**
- **AC1:** Installer detects Windsurf (presence of `windsurfrules` or `~/.windsurf/`) and writes `windsurfrules` snippet
- **AC2:** Snippet invokes `/revue-local` before commit per Windsurf's rules schema
- **AC3:** Doc page at `revue.io/install#windsurf` shows manual setup if auto-detect fails

**E-P2B-S3: `revue-local doctor` diagnostic command**
- **AC1:** `revue-local doctor` checks: licence JWT presence + freshness, `.revue.yml` validity, AI workflow detection (Claude Code/Cursor/Windsurf), network reachability to `/api/v2/licence/validate`
- **AC2:** Each check returns ✅ / ⚠️ / ❌ with remediation hint
- **AC3:** Exits non-zero if any check fails (suitable for CI debugging)

**E-P2B-S4: Web cost-saving dashboard — monthly aggregate view**
- **AC1:** `revue.io/dashboard` accepts licence JWT (header or query string) and renders the workspace's saving aggregate for the current month
- **AC2:** Monthly view shows: reviews run, findings raised, $-saved vs CI baseline, sparkline over trailing 30 days
- **AC3:** Server aggregates usage records from `/api/v2/usage/emit` keyed by workspace_id
- **AC4:** Page loads in under 2 seconds for 95th percentile workspaces

**E-P2B-S5: Dashboard share-link generator**
- **AC1:** Dashboard exposes "Share saving" CTA → generates signed read-only URL valid for 30 days
- **AC2:** Share URL renders aggregate without exposing workspace_id or licence JWT
- **AC3:** Suitable for social posts (Open Graph metadata populated)

**E-P2B-S6: Saving-calculation refinement — telemetry-derived probability**
- **AC1:** Replace hardcoded `1.0` "would-have-caught-in-CI" probability with a telemetry-derived figure
- **AC2:** Probability sourced from Phase 2.a usage data once ≥ 10k reviews available
- **AC3:** Methodology documented in `docs/methodology/saving-calculation.md`
- **AC4:** Footer + dashboard both consume the refined figure

**E-P2B-S7: Soft-paywall upgrade prompt copy**
- **AC1:** Upgrade prompt copy (brief §9.6) lands in skill when free-tier exhausted
- **AC2:** Copy A/B test scaffold ready (server returns variant flag in `/api/v2/licence/validate`)
- **AC3:** Variant performance measurable via Free → Indie conversion telemetry

---

## Epic 3: E-P2C — Scale & Monetisation

**Goal:** Drive cumulative installs ≥ 500, deliver the 6-month customer AI-spend reduction target of ≥ 40% (per PRD §3.5 two-stage phrasing — instrumented baseline first, then ≥ 40% at month 6), and Free → Indie conversion ≥ 7%.

**Exit criteria (from brief §10.3):**
- Cumulative installs ≥ 500 (G1)
- Customer AI-spend reduction (G3) reaches ≥ 40% for the active-user cohort with billing-API connection at month 6 post-launch (baseline cohort established in Phase 2.a — see brief §3.1 G3)
- Free → Indie conversion (G5) ≥ 7% within 90 days for the Phase 2.c install cohort

**Effort:** 4–6 engineer-weeks + marketing investment.

### Stories

**E-P2C-S1: Anthropic skill registry submission**
- **AC1:** `/revue-local` submitted to Anthropic skill registry with required metadata (description, install command, version)
- **AC2:** Anthropic-registry install path routes through `revue.io/install.sh` (no duplicate skill bundle)
- **AC3:** Submission reviewed and accepted; published URL recorded
- **AC4:** Telemetry distinguishes Anthropic-registry installs from `revue.io/skills` direct installs

**E-P2C-S2: OpenRouter billing-API integration (opt-in)**
- **AC1:** Customer can connect their OpenRouter account read-only via OAuth in the dashboard
- **AC2:** Server fetches monthly OpenRouter spend and computes actual saving (not estimated)
- **AC3:** Dashboard distinguishes "estimated" vs "verified" saving figures
- **AC4:** Disconnect path purges OAuth token and reverts to estimated mode

**E-P2C-S3: Anthropic billing-API integration (opt-in)**
- **AC1:** Customer can connect Anthropic account read-only via API key in dashboard (Anthropic does not yet offer OAuth)
- **AC2:** Same "verified saving" treatment as OpenRouter path
- **AC3:** API key stored encrypted (KMS), accessible only by aggregator job

**E-P2C-S4: Free → Indie conversion experiments**
- **AC1:** Variant test infrastructure (CTA copy, paywall timing) ready in `/api/v2/licence/validate` flag-payload
- **AC2:** Minimum 3 copy variants tested over 30-day windows
- **AC3:** Conversion measured per variant; ≥ 7% within 90 days hit at least once

**E-P2C-S5: Self-hosted telemetry sink for Enterprise**
- **AC1:** Enterprise tier supports `usage_endpoint` override in `.revue.yml`
- **AC2:** When overridden, skill POSTs `/api/v2/usage/emit` payloads to customer-hosted sink instead of `revue.io`
- **AC3:** Daily-validate still hits `revue.io` (anti-piracy constraint preserved)
- **AC4:** Customer-side sink reference implementation (Docker compose) published

**E-P2C-S6: Per-seat workspace dashboard (multi-licence-per-team)**
- **AC1:** Enterprise / multi-seat workspace dashboard shows per-developer review counts + savings
- **AC2:** Admin can see team aggregate + per-seat breakdown
- **AC3:** Suitable for procurement-justification screenshots

---

## Epic 4: E-SAGE2 — Sage v2 (Auto-Commit + Multi-Round Loop)

**Goal:** Promote Sage from suggestion-only (MVP) to auto-applying fixes with confidence-gated commits and multi-round refinement.

**Anchored to:** PRD §7.1 Sage; §12 Phase 2 P1; Non-Goal in v1 ("Sage does not auto-commit"). Phase 2 lifts that constraint.

**Exit criteria:**
- ≥ 80% of Sage suggestions over a 30-day window apply cleanly to the diff (no merge conflicts)
- False-positive auto-apply rate (developer reverts the auto-commit) < 5%
- Multi-round loop converges (no findings remain at end of round 3) for ≥ 70% of triggered reviews

### Stories

**E-SAGE2-S0: Confidence-threshold spike — derive default from Phase 1 telemetry**
- **AC1:** Pull all Phase 1 Sage suggestions from the Postgres KB (REVUE-87) along with their confidence score and developer-acceptance outcome (accepted / rejected / ignored)
- **AC2:** Build a confidence-vs-acceptance curve; identify the lowest threshold T at which acceptance rate is ≥ 95%
- **AC3:** Report T as a memo / decision record in `docs/planning/sage-v2-threshold-decision.md` with the supporting data
- **AC4:** If Phase 1 sample size is < 200 suggestions, document the gap and propose a telemetry top-up before E-SAGE2-S1 starts
- **AC5:** Spike output reviewed and signed off by PM before E-SAGE2-S1 unblocks

**E-SAGE2-S1: Auto-apply path with confidence gate**
- **Blocked by:** E-SAGE2-S0 (formal Jira issue-link)
- **AC1:** Sage applies suggestions when confidence ≥ T (the threshold derived in E-SAGE2-S0; configurable in `.revue.yml`)
- **AC2:** Below threshold, falls back to MVP suggestion-only behaviour
- **AC3:** Auto-applied changes committed with `[revue-sage]` marker in commit message

**E-SAGE2-S2: Multi-round refinement loop**
- **AC1:** After Sage applies fixes, agents re-run on the patched diff (max 3 rounds)
- **AC2:** Loop terminates early when no new findings or all remaining are below threshold
- **AC3:** Per-round token budget enforced (default: 2× single-pass budget total)

**E-SAGE2-S3: Conflict-safe patch application**
- **AC1:** Patches applied via three-way merge; conflicts cause Sage to defer that suggestion to the developer
- **AC2:** Sage never silently overwrites concurrent developer edits

**E-SAGE2-S4: Sage v2 telemetry**
- **AC1:** Per-suggestion outcome recorded: applied / deferred / reverted
- **AC2:** Confidence threshold A/B test scaffold ready
- **AC3:** Sage v2 dashboard shows apply-rate, revert-rate, round-count distribution

**E-SAGE2-S5: Sage v2 opt-out**
- **AC1:** `.revue.yml` flag `sage_auto_apply: false` reverts to MVP behaviour
- **AC2:** Default for Free tier = `false` (auto-apply is a paid feature)
- **AC3:** Doc page explains the opt-in/opt-out trade-off

---

## Epic 5: E-CAA — Custom Agent Authoring UI

**Goal:** Let Pro/Enterprise customers define their own agents (persona, trigger patterns, prompt) without shipping a fork.

**Anchored to:** PRD §12 Phase 2 P1.

**Exit criteria:**
- ≥ 20 Pro/Enterprise workspaces have authored ≥ 1 custom agent within 90 days of GA
- Custom agents execute in the same pipeline as built-in agents (no separate runtime path)

### Stories

**E-CAA-S1: Custom-agent YAML schema**
- **AC1:** Schema published at `revue.io/agent-schema.json` (JSON Schema draft-07)
- **AC2:** Fields: id, persona, trigger_patterns, prompt_template, severity_mapping
- **AC3:** Schema validation in the skill before pipeline dispatch
- **AC4:** Tier gate — schema loader rejects custom-agent definitions on Free and Indie tiers with a clear "upgrade to Pro to author custom agents" message; Pro and Enterprise pass through unchanged
- **AC5:** PRD §11 pricing-page entry reflects "Custom agents (Pro+)"

**E-CAA-S2: Web-based agent editor**
- **AC1:** Dashboard hosts a YAML editor with live validation against the schema
- **AC2:** "Test run" CTA executes the agent against a sample diff and renders findings
- **AC3:** Save persists the agent definition to workspace config

**E-CAA-S3: Custom-agent pipeline integration**
- **AC1:** Cleo (router) treats custom agents identically to built-in agents
- **AC2:** Custom-agent token budget capped (default: same as built-in agent)
- **AC3:** Custom-agent findings render with the same severity/format as built-in

**E-CAA-S4: Custom-agent versioning & rollback**
- **AC1:** Each save creates a versioned snapshot in the database
- **AC2:** Dashboard exposes diff between versions
- **AC3:** "Roll back to version N" restores the prior definition

---

## Epic 6: E-NA — Notifications & Analytics Polish

**Goal:** Ship Slack/Teams notifications and the review-analytics trend dashboard.

**Anchored to:** PRD §12 Phase 2 P2.

**Exit criteria:**
- ≥ 30% of Pro/Enterprise workspaces have wired Slack or Teams notifications within 90 days of GA
- Analytics dashboard surfaces false-positive rate trends + agent-quality metrics

### Stories

**E-NA-S1: Slack incoming-webhook notifications**
- **AC1:** `.revue.yml` accepts `notifications.slack.webhook_url`
- **AC2:** On review completion, posts a summary (workspace, branch, findings count by severity, link to dashboard)
- **AC3:** Failure to POST does not block the review

**E-NA-S2: Teams webhook notifications**
- **AC1:** `.revue.yml` accepts `notifications.teams.webhook_url`
- **AC2:** Same payload shape as Slack, adapted to Adaptive Card schema
- **AC3:** Failure to POST does not block the review

**E-NA-S3: Notification payload customisation**
- **AC1:** `.revue.yml` accepts `notifications.template` (Jinja2 string)
- **AC2:** Template receives review context object (findings, severity, model, tier, savings)
- **AC3:** Default template documented; custom templates validated on config load

**E-NA-S4: Review analytics dashboard — trend view**
- **Blocked by:** REVUE-89, REVUE-90, REVUE-91 (REVUE-87 epic Postgres KB) — formal Jira issue-links required, not just textual mention
- **AC1:** Dashboard `revue.io/dashboard/analytics` shows trailing-90-day trends: review volume, false-positive rate, agent-quality scores
- **AC2:** Trend data sourced from Postgres knowledge base (REVUE-87 epic) — no stopgap aggregator
- **AC3:** Per-agent breakdown available
- **AC4:** Story does not start until REVUE-89/90/91 are merged; if REVUE-87 epic slips past Phase 2.b cutoff, E-NA-S4 slips with it (acceptable — P2 priority)

**E-NA-S5: Agent-quality scorecard**
- **AC1:** Each agent shows: clarity score, actionability score, false-positive rate over trailing 30 days
- **AC2:** Quality scores fed by `finding_quality` table (REVUE-89)
- **AC3:** Underperforming agent flagged with ⚠️ when FP rate > 15%

---

## Jira filing plan

| Jira artefact | Action | Parent |
|---|---|---|
| **E-P2A** | Create epic: "Phase 2.a — Minimum Viable Distribution" | — (top-level epic) |
| **E-P2B** | Create epic: "Phase 2.b — Polish" | — |
| **E-P2C** | Create epic: "Phase 2.c — Scale & Monetisation" | — |
| **E-SAGE2** | Create epic: "Sage v2 — Auto-Commit Loop" | — |
| **E-CAA** | Create epic: "Custom Agent Authoring UI" | — |
| **E-NA** | Create epic: "Notifications & Analytics Polish" | — |
| All `S*` stories | Create as Task tickets linked to their parent epic | E-P2A / E-P2B / … |

**Story DoD format:** Every story ticket follows the mandatory DoD checklist (User Story, Background, AC, Test Cases, Out of Scope, Dependencies — per `feedback_jira_ticket_format` memory).

**Issue links:** Create formal Jira issue-link relationships (Blocks/Blocked by) per the dependency graph above — textual mentions are not enough (per `feedback_jira_formal_links` memory).

---

## Resolved scope decisions (2026-05-18)

| # | Question | Decision | Affected story |
|---|---|---|---|
| 1 | Free-tier 25-review cap UX | **Soft cap** — review runs, upgrade prompt appended | E-P2A-S5 AC5 (unchanged) |
| 2 | Sage v2 confidence-threshold default | **Spike first** — derive empirically from Phase 1 telemetry before picking a number | New **E-SAGE2-S0** spike added; E-SAGE2-S1 blocked on it |
| 3 | Custom-agent authoring tier gating | **Pro+ only** — Free and Indie cannot author custom agents | E-CAA-S1 AC pinned |
| 4 | E-NA-S4 sequencing vs REVUE-87 KB | **Block on REVUE-89/90/91** completing — formal Jira issue-link required | E-NA-S4 dependency noted |

---

## Revision history

| Date | Author | Change |
|---|---|---|
| 2026-05-18 | PM (Daniel) + Claude | Initial Phase 2 epic & story breakdown derived from PRD v2.0 §12 + distribution brief §10 |
