# Revue.io — Sprint Plan
**Version:** 1.0  
**Date:** March 2026  
**Status:** Active  
**Total:** 8 sprints × 2 weeks = 16 weeks to MVP launch + monetisation

---

## Principles
- **Foundation first** — nothing is testable until E1 core engine + E5 AI backend exist
- **Vertical slices** — each sprint ends with something runnable end-to-end
- **Dependencies respected** — agent loader before agent definitions, VCSAdapter before adapters, AIClient before engine
- **Story sizing** — S=1pt, M=3pts, L=5pts. Target ~18pts per sprint

---

## Sprint 1 — Foundation (Weeks 1–2)
**Goal:** The engine pipeline runs end-to-end locally with a real AI backend.  
**Deliverable:** `revue review --diff=sample.diff` works locally with real AI calls.

| Story | Subject | Size |
|-------|---------|------|
| [027] | AIClient protocol and provider factory | L |
| [029] | Environment variable handling and BYOK support | S |
| [028] | .revue.yml config schema and loader | M |
| [009] | VCSAdapter protocol and DiffPosition abstraction | M |
| [001] | Diff ingestion — parse raw VCS diff into FileChange objects | M |
| [045] | Local diff input mode — run review from local .diff file | M |
| **Total** | | **18pts** |

---

## Sprint 2 — Core Pipeline (Weeks 3–4)
**Goal:** Full multi-agent pipeline runs locally — parallel agents, contradiction resolution, Nova consolidation, noise filters.  
**Deliverable:** Full pipeline runs on real diff, all agents, structured output.

| Story | Subject | Size |
|-------|---------|------|
| [002] | Hard diff limit check — stop and suggest breakdown | M |
| [003] | Shared analysis — upfront AI call for classification | S |
| [004] | Parallel agent execution with timeout and graceful degradation | M |
| [005] | Contradiction detection between specialist findings | S |
| [006] | Contradiction resolution via orchestrator | S |
| [007] | Nova consolidation — deduplicate and prioritise findings | M |
| [008] | Noise filters — suppress false positives | S |
| **Total** | | **16pts** |

---

## Sprint 3 — Agent System (Weeks 5–6)
**Goal:** Declarative agents loaded from YAML/Markdown. All 4 specialist agents defined and producing real findings.  
**Deliverable:** All 6 agents (Cleo, Zara, Kai, Maya, Leo, Nova) loaded declaratively, producing real findings on test diffs.

| Story | Subject | Size |
|-------|---------|------|
| [016] | Agent definition loader — parse YAML/Markdown agent files | M |
| [021] | Nova and Cleo agent definitions | S |
| [019] | Agent definition — Zara (Security analyst) | M |
| [046] | Agent definition — Kai (Performance expert) | M |
| [047] | Agent definition — Maya (Code quality expert) | M |
| [048] | Agent definition — Leo (Architecture reviewer) | M |
| **Total** | | **18pts** |

---

## Sprint 4 — Routing & Teams (Weeks 7–8)
**Goal:** Cleo auto-routing working. All team configs defined. Custom agents supported.  
**Deliverable:** Cleo picks the right team automatically for any language/size/risk combination.

| Story | Subject | Size |
|-------|---------|------|
| [017] | Cleo routing — Step 1: team auto-selection | M |
| [018] | Cleo routing — Step 2: agent trigger evaluation | S |
| [020] | Team config — team-swift-ios | S |
| [049] | Team config — team-security-focus | S |
| [050] | Team config — team-performance | S |
| [051] | Team config — team-full-review | S |
| [052] | Team config — team-quick | S |
| [053] | Team config — team-kotlin-android | S |
| [054] | Team config — team-python | S |
| [055] | Team config — team-typescript | S |
| [030] | Custom agent support | M |
| [031] | Configurable blocking behaviour | S |
| **Total** | | **19pts** |

---

## Sprint 5 — VCS Integration (Weeks 9–10)
**Goal:** GitHub App + GitLab integration live. Real PRs/MRs trigger reviews. Inline comments posted.  
**Deliverable:** End-to-end review on a real GitHub PR and GitLab MR. 🎉 First real review posted.

| Story | Subject | Size |
|-------|---------|------|
| [010] | GitHub App setup and webhook handling | M |
| [011] | GitLab OAuth integration and webhook handling | M |
| [012] | GitHub adapter — fetch PR diff and post inline comments | L |
| [013] | GitLab adapter — fetch MR diff and post inline comments | L |
| [032] | Review summary comment — structured output | M |
| **Total** | | **20pts** |

---

## Sprint 6 — Sage (Weeks 11–12)
**Goal:** Sage suggests fixes on real PRs. Developers can accept with one click.  
**Deliverable:** Sage suggests 1-click fixes on real PRs. First agentic resolver loop working.

| Story | Subject | Size |
|-------|---------|------|
| [022] | Sage fixability classifier | L |
| [023] | Sage fix generator | L |
| [024] | Sage GitHub integration — post as Suggested Change | M |
| [025] | Sage GitLab integration — post as Apply Suggestion | M |
| [026] | Sage summary section | S |
| **Total** | | **18pts** |

---

## Sprint 7 — CI Templates & Launch (Weeks 13–14)
**Goal:** Self-service onboarding. GitHub Actions + GitLab CI templates. Ship free tier.  
**Deliverable:** 🚀 **MVP LAUNCH** — any developer can install Revue in under 10 minutes.

| Story | Subject | Size |
|-------|---------|------|
| [014] | CI runner integration — GitHub Actions step | M |
| [015] | CI runner integration — GitLab CI include template | S |
| [033] | Self-service workspace onboarding — web UI | L |
| [034] | Free tier enforcement — 25 runs/month cap + license key validation | M |
| [037] | Documentation site | M |
| **Total** | | **18pts** |

---

## Sprint 8 — Monetisation & Observability (Weeks 15–16)
**Goal:** Paying customers. Analytics. Product complete.  
**Deliverable:** Revenue flowing. Observable platform. All MVP stories done.

| Story | Subject | Size |
|-------|---------|------|
| [035] | Stripe billing integration — Indie, Pro, and Enterprise tiers | L |
| [036] | Basic analytics — review run history and issue trends | M |
| **Total** | | **8pts** |

---

## Dependency Map

```
[027] AIClient ──────────────────────────────────────────► All agent AI calls
[029] BYOK ──────────────────────────────────────────────► [027]
[028] .revue.yml ────────────────────────────────────────► All config
[009] VCSAdapter ────────────────────────────────────────► [012] [013]
[001] Diff ingestion ────────────────────────────────────► [002] [003] [004]
[045] Local diff mode ───────────────────────────────────► Sprint 2-4 testing
[016] Agent loader ──────────────────────────────────────► [019] [046] [047] [048] [021]
[019][046][047][048] Agent definitions ──────────────────► [004] parallel execution
[017][018] Cleo routing ─────────────────────────────────► [020][049-055] team configs
[010][011] VCS webhooks ─────────────────────────────────► [012] [013]
[012][013] Adapters ─────────────────────────────────────► [022] [023] [024] [025]
[022][023] Sage core ────────────────────────────────────► [024] [025] [026]
[033] Onboarding ────────────────────────────────────────► [034] [035]
```

---

## Timeline Summary

| Sprint | Weeks | Theme | Key Deliverable |
|--------|-------|-------|-----------------|
| S1 | 1–2 | Foundation | Local review with real AI |
| S2 | 3–4 | Core Pipeline | Full multi-agent pipeline |
| S3 | 5–6 | Agent System | All agents declared & working |
| S4 | 7–8 | Routing & Teams | Cleo auto-routes correctly |
| S5 | 9–10 | VCS Integration | First real PR reviewed ⭐ |
| S6 | 11–12 | Sage | 1-click fix suggestions |
| S7 | 13–14 | Launch | 🚀 MVP live |
| S8 | 15–16 | Monetisation | First paying customers |

---

*Sprint plan generated from PRD v1.3 and 48 Taiga stories.*  
*Board: http://localhost:9000/project/revueio/kanban*
