# Kanban Board — Revue.io
**Last Updated:** 2026-03-27 11:16 GMT

---

## Story Status Summary
**Total:** 32 stories  
**Done:** 25 (78%)  
**In Progress:** 1  
**To Do:** 6

---

## 📋 To Do (6)

### Epic E4 — Sage (The Resolver Agent)
- [ ] **Story 29** — Sage fix generator (`sage_generator.py`)
- [ ] **Story 30** — Sage GitHub integration (Suggested Change API)
- [ ] **Story 31** — Sage GitLab integration (Apply Suggestion syntax)
- [ ] **Story 32** — Sage summary section in review output

### Epic E6 — Onboarding & Launch
- [ ] **Story 33** — Self-service workspace onboarding (web UI)
- [ ] **Story 34** — Free tier enforcement (100 runs/month)

---

## 🔄 In Progress (1)

### Epic E4 — Sage (The Resolver Agent)
- [ ] **Story 28** — Sage fixability classifier (`sage_classifier.py`) — **DS Complete, DEV in progress**

---

## ✅ Done (25)

### Epic E1 — Core Review Engine (8/8)
- [x] **Story 1** — Diff ingestion (parse VCS diff → FileChange objects)
- [x] **Story 2** — Hard diff limit check (stop & suggest breakdown)
- [x] **Story 3** — Shared analysis (upfront AI classification)
- [x] **Story 4** — Parallel agent execution (timeout + graceful degradation)
- [x] **Story 5** — Contradiction detection between specialist findings
- [x] **Story 6** — Contradiction resolution via orchestrator
- [x] **Story 7** — Nova consolidation (deduplicate + prioritize)
- [x] **Story 8** — Noise filters (suppress false positives)

### Epic E2 — VCS Platform Integration (7/7)
- [x] **Story 9** — VCSAdapter protocol + DiffPosition abstraction
- [x] **Story 10** — GitHub App setup + webhook handling (HMAC verify)
- [x] **Story 11** — GitLab OAuth + webhook handling (token verify)
- [x] **Story 12** — GitHub adapter (fetch PR diff + post inline comments + Review API)
- [x] **Story 13** — GitLab adapter (fetch MR diff + post inline comments + Discussions API)
- [x] **Story 14** — GitHub Actions CI template (`.github/workflows/revue-review.yml`)
- [x] **Story 15** — GitLab CI template (`ci-templates/gitlab-ci/revue-review.yml` + `post_review.py`)

### Epic E3 — Agent System & Routing (5/5)
- [x] **Story 16** — Agent definition loader (parse YAML/Markdown agent files)
- [x] **Story 17** — Cleo routing — Step 1: team auto-selection
- [x] **Story 18** — Cleo routing — Step 2: agent trigger evaluation
- [x] **Story 19** — Agent definitions (Zara, Kai, Maya, Leo — Markdown + YAML)
- [x] **Story 30** — Custom agent support (`agent_loader.py` with path-traversal protection)

### Epic E5 — AI Backend & Configuration (4/4)
- [x] **Story 27** — AIClient protocol + provider factory
- [x] **Story 28** — `.revue.yml` config schema + loader
- [x] **Story 29** — Environment variable handling + BYOK support
- [x] **Story 45** — Local diff input mode (`--diff=sample.diff`)

### Epic E6 — Onboarding & Launch (1/1 in Done)
- [x] **Story 32** — Review summary comment (structured output)

---

## Notes
- **Sprint 6** (Sage) starts now — 5 stories remaining (28, 29, 30, 31, 32)
- **Epic E4** is the last major feature epic before launch readiness
- Stories 30+31 can run in parallel (both depend on 29)
- **Test count:** 268 passing
