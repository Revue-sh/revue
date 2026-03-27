# Kanban Board — Revue.io
**Last Updated:** 2026-03-27 16:46 GMT
**Source of Truth:** Taiga — http://localhost:9000/project/revueio/kanban
**⚠️ This file is a mirror. If Taiga is available, prefer Taiga. Update this file whenever a story is closed.**

---

## Story Status Summary
**Total:** 62 stories (51 Done + 6 open + 5 archived)
**Done:** 51 (82%)
**To Do:** 6 (E6 active backlog)
**Archived:** 5 (E6 duplicates — superseded by [62]–[67])
**In Progress:** 0

---

## ✅ Done (51)

### Epic E1 — Core Review Engine (9/9 ✅)
- [x] **[7]** Diff ingestion — parse raw VCS diff into structured FileChange objects
- [x] **[8]** Hard diff limit check — stop and suggest breakdown for oversized PRs
- [x] **[9]** Shared analysis — single upfront AI call for diff complexity and risk classification
- [x] **[10]** Parallel agent execution with timeout and graceful degradation
- [x] **[11]** Contradiction detection between specialist findings
- [x] **[12]** Contradiction resolution via orchestrator
- [x] **[13]** Nova consolidation — deduplicate and prioritise findings into unified output
- [x] **[14]** Noise filters — suppress false positives post-consolidation
- [x] **[51]** Local diff input mode — run review from a local .diff file for testing

### Epic E2 — VCS Platform Integration (9/9 ✅)
- [x] **[15]** VCSAdapter protocol and DiffPosition abstraction
- [x] **[16]** GitHub App setup and webhook handling
- [x] **[17]** GitLab OAuth integration and webhook handling
- [x] **[18]** GitHub adapter — fetch PR diff and post inline review comments
- [x] **[19]** GitLab adapter — fetch MR diff and post inline review comments
- [x] **[20]** CI runner integration — GitHub Actions step
- [x] **[21]** CI runner integration — GitLab CI include template
- [x] **[37]** Configurable blocking behaviour
- [x] **[38]** Review summary comment — structured output with all agent contributions

### Epic E3 — Agent System & Routing (16/16 ✅)
- [x] **[22]** Agent definition loader — parse declarative YAML/Markdown agent files
- [x] **[23]** Cleo routing — Step 1: team auto-selection
- [x] **[24]** Cleo routing — Step 2: agent trigger evaluation within team
- [x] **[25]** Agent definition — Zara (Security analyst)
- [x] **[26]** Team config — team-swift-ios
- [x] **[27]** Nova and Cleo agent definitions
- [x] **[52]** Agent definition — Kai (Performance expert)
- [x] **[53]** Agent definition — Maya (Code quality expert)
- [x] **[54]** Agent definition — Leo (Architecture reviewer)
- [x] **[55]** Team config — team-security-focus
- [x] **[56]** Team config — team-performance
- [x] **[57]** Team config — team-full-review
- [x] **[58]** Team config — team-quick
- [x] **[59]** Team config — team-kotlin-android
- [x] **[60]** Team config — team-python
- [x] **[61]** Team config — team-typescript

### Epic E4 — Sage: The Resolver Agent (5/5 ✅)
- [x] **[28]** Sage fixability classifier — categorise each finding as fixable or needs-human
- [x] **[29]** Sage fix generator — produce code fix for self-contained findings
- [x] **[30]** Sage GitHub integration — post fix as Suggested Change
- [x] **[31]** Sage GitLab integration — post fix as Apply Suggestion
- [x] **[32]** Sage summary section — aggregate resolver output in review summary

### Epic E5 — AI Backend & Configuration (4/4 ✅)
- [x] **[33]** AIClient protocol and provider factory
- [x] **[34]** .revue.yml config schema and loader
- [x] **[35]** Environment variable handling and BYOK support
- [x] **[36]** Custom agent support — load project-specific agent definitions

### Epic E7 — Post-MVP Tech Debt & Improvements (8/8 ✅)
- [x] **[70]** FileChange model: add language field + centralise diff parser + edge case tests
- [x] **[71]** Align agent timeout — AC says 90s, impl uses 120s
- [x] **[72]** Noise filters — implement DI-pattern/language-aware suppression per AC
- [x] **[73]** Add verify_webhook_signature() to VCSAdapter protocol
- [x] **[74]** Standardise adapter method names + test pagination + MR approval
- [x] **[75]** Publish GitHub Actions as proper revue-io/action@v1
- [x] **[76]** Fix Cleo size heuristic thresholds + add team-quick routing
- [x] **[77]** Extract team configs to standalone YAML files

---

## 📋 To Do — E6 Active Backlog (6 stories)

### Epic E6 — Onboarding, Observability & Launch (0/6, Not started 🔜)

**Recommended delivery order (per architecture dependencies):**
1. [62] → [63] + [64] (parallel) → [65] → [66] | [67] anytime

- [ ] **[62]** Workspace onboarding UI — sign-up, connect VCS, install app *(L, ~1 week)*
- [ ] **[63]** Free tier enforcement — BYOK, 100 runs/month cap *(M, ~2 days)*
- [ ] **[64]** Stripe billing — Pro and Team tier subscription management *(L, ~1 week)*
- [ ] **[65]** Run history dashboard — list reviews with status and findings summary *(M, ~2 days)*
- [ ] **[66]** Basic analytics — finding trends by category and severity *(M, ~2 days)*
- [ ] **[67]** Documentation site — getting started guide and .revue.yml reference *(M, ~2 days)*

### 🗄️ Archived (5 — superseded, do not implement)
- ~~[39]~~ Self-service workspace onboarding web UI → superseded by **[62]**
- ~~[40]~~ Free tier enforcement 100 runs/month → superseded by **[63]**
- ~~[41]~~ Stripe billing Pro and Team tier → superseded by **[64]**
- ~~[42]~~ Basic analytics run history + trends → split into **[65]** + **[66]**
- ~~[43]~~ Documentation site → superseded by **[67]**

---

## Epic Status Summary

| Epic | Stories | Done | Status |
|------|---------|------|--------|
| E1 — Core Review Engine | 9 | 9/9 | ✅ Done |
| E2 — VCS Platform Integration | 9 | 9/9 | ✅ Done |
| E3 — Agent System & Routing | 16 | 16/16 | ✅ Done |
| E4 — Sage: The Resolver Agent | 5 | 5/5 | ✅ Done |
| E5 — AI Backend & Configuration | 4 | 4/4 | ✅ Done |
| E6 — Onboarding, Observability & Launch | 6 active (5 archived) | 0/6 | 🔜 Not started |
| E7 — Post-MVP Tech Debt & Improvements | 8 | 8/8 | ✅ Done |
