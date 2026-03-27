# Kanban Board — Revue.io
**Last Updated:** 2026-03-27 14:27 GMT  
**Source of Truth:** Taiga — http://localhost:9000/project/revueio/kanban  
**⚠️ This file is a mirror. If Taiga is available, prefer Taiga. Update this file whenever a story is closed.**

---

## Story Status Summary
**Total:** 42 stories (30 Done + 12 open)  
**Done:** 30 (71%)  
**To Do:** 12 (E6 + 6 unassigned orphan stories)  
**In Progress:** 0

---

## ✅ Done (30)

### Epic E1 — Core Review Engine (8/8 ✅)
- [x] **[7]** Diff ingestion — parse raw VCS diff into structured FileChange objects
- [x] **[8]** Hard diff limit check — stop and suggest breakdown for oversized PRs
- [x] **[9]** Shared analysis — single upfront AI call for diff complexity and risk classification
- [x] **[10]** Parallel agent execution with timeout and graceful degradation
- [x] **[11]** Contradiction detection between specialist findings
- [x] **[12]** Contradiction resolution via orchestrator
- [x] **[13]** Nova consolidation — deduplicate and prioritise findings into unified output
- [x] **[14]** Noise filters — suppress false positives post-consolidation

### Epic E2 — VCS Platform Integration (7/7 ✅)
- [x] **[15]** VCSAdapter protocol and DiffPosition abstraction
- [x] **[16]** GitHub App setup and webhook handling
- [x] **[17]** GitLab OAuth integration and webhook handling
- [x] **[18]** GitHub adapter — fetch PR diff and post inline review comments
- [x] **[19]** GitLab adapter — fetch MR diff and post inline review comments
- [x] **[20]** CI runner integration — GitHub Actions step
- [x] **[21]** CI runner integration — GitLab CI include template

### Epic E3 — Agent System & Routing (6/6 ✅)
- [x] **[22]** Agent definition loader — parse declarative YAML/Markdown agent files
- [x] **[23]** Cleo routing — Step 1: team auto-selection
- [x] **[24]** Cleo routing — Step 2: agent trigger evaluation within team
- [x] **[25]** Agent definition — Zara (Security analyst)
- [x] **[26]** Team config — team-swift-ios
- [x] **[27]** Nova and Cleo agent definitions

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

---

## 📋 To Do (12)

### Epic E6 — Onboarding, Observability & Launch (0/6, In Progress 🔜)
- [ ] **[62]** Workspace onboarding UI — sign-up, connect VCS, install app
- [ ] **[63]** Free tier enforcement — BYOK, 100 runs/month cap
- [ ] **[64]** Stripe billing — Pro and Team tier subscription management
- [ ] **[65]** Run history dashboard — list reviews with status and findings summary
- [ ] **[66]** Basic analytics — finding trends by category and severity
- [ ] **[67]** Documentation site — getting started guide and .revue.yml reference

### ⚠️ Orphan Stories — No Epic Assigned (6)
> These stories exist on Taiga but are not linked to any epic. Needs triage — assign to an epic or close as duplicate/superseded.

- [ ] **[37]** Configurable blocking behaviour
- [ ] **[38]** Review summary comment — structured output with all agent contributions
- [ ] **[39]** Self-service workspace onboarding — web UI
- [ ] **[40]** Free tier enforcement — 100 review runs per workspace per month
- [ ] **[41]** Stripe billing integration — Pro and Team tier
- [ ] **[42]** Basic analytics — review run history and issue trends

> Note: Stories 37–42 appear to be earlier versions of E6 stories (62–67). Likely created before E6 was structured. Recommend closing as duplicates after confirming scope overlap.

---

## Epic Status Summary

| Epic | Stories | Done | Status |
|------|---------|------|--------|
| E1 — Core Review Engine | 8 | 8/8 | ✅ Done |
| E2 — VCS Platform Integration | 7 | 7/7 | ✅ Done |
| E3 — Agent System & Routing | 6 | 6/6 | ✅ Done |
| E4 — Sage: The Resolver Agent | 5 | 5/5 | ✅ Done |
| E5 — AI Backend & Configuration | 4 | 4/4 | ✅ Done |
| E6 — Onboarding, Observability & Launch | 6 | 0/6 | 🔜 Not started |
