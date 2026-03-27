# Session Continuation
**Updated:** 2026-03-27 | **For:** Next session

---

## Completed this session

- **Project named and set up** — Revue.io, domain getrevue.io, email getrevue@gmail.com created
- **Market analysis written** — TAM/SAM/SOM, competitive landscape (CodeRabbit, Greptile, Copilot Review, Snyk, SonarCloud), platform priorities, business model recommendation (hybrid tier model) — `docs/market-analysis.md`
- **PRD written and iterated to v1.3** — `docs/prd.md` — covers architecture, 7-agent team, Sage resolver design, AI backend abstraction, VCS integration, configuration schema, phased roadmap
- **Agent team named** — Cleo (Orchestrator), Zara (Security), Kai (Performance), Maya (Code Quality), Leo (Architecture), Nova (Consolidator), Sage (Resolver). Planned: Finn, Dara, Arlo, Remy, Sora, Rex
- **PRD team review** (party mode) — 8 fixes applied: VCSAdapter moved to E1, DiffPosition abstraction, GitLab webhook algorithm clarified, rate limit backoff added, token rotation AC added, wall clock timeout clarified, shared analysis fallback added, hard diff limit behaviour defined
- **Cleo routing algorithm resolved** (PRD v1.2) — 2-step: team auto-selection (security override → size → language), then existing evaluate_triggers() carries over unchanged
- **Hard diff limit designed** (PRD v1.3) — 2000-line default, stop before any AI call, post breakdown suggestion, exit as warning (non-blocking). Phase 2: batch mode
- **48 user stories created in Taiga** across 6 epics — all with full acceptance criteria, linked to epics, team-reviewed and quality-improved (commit `537aa8a`)
- **8 sprints planned and created in Taiga** — 16 weeks, MVP launches Sprint 7 (22 Jun 2026), monetisation Sprint 8 (6 Jul 2026) — `docs/sprint-plan.md`
- **Handoff skill created** — `~/.openclaw/skills/handoff/SKILL.md` (adapted for OpenClaw)

---

## Sprint & Epic State

**Current sprint:** Not started — Sprint 1 kicks off 30 March 2026

| Sprint | Dates | Theme | Stories | Status |
|--------|-------|-------|---------|--------|
| S1 | 30 Mar – 12 Apr | Foundation | 6 | 🔲 Not started |
| S2 | 13 Apr – 26 Apr | Core Pipeline | 7 | 🔲 Not started |
| S3 | 27 Apr – 10 May | Agent System | 6 | 🔲 Not started |
| S4 | 11 May – 24 May | Routing & Teams | 12 | 🔲 Not started |
| S5 | 25 May – 7 Jun | VCS Integration | 5 | 🔲 Not started |
| S6 | 8 Jun – 21 Jun | Sage | 5 | 🔲 Not started |
| S7 | 22 Jun – 5 Jul | Launch 🚀 | 5 | 🔲 Not started |
| S8 | 6 Jul – 19 Jul | Monetisation | 2 | 🔲 Not started |

**Epic progress:** 0/48 stories done. All stories in New status on Kanban board.

**Taiga board:** http://localhost:9000/project/revueio/kanban  
**Taiga sprints:** http://localhost:9000/project/revueio/taskboard

---

## Remaining work — next steps

1. **Sprint 1 — Story [027]: AIClient protocol and provider factory** *(first story to implement)*
   - Create `AIReviewer/core/ai_client.py` — define `AIClient` Protocol, implement `OpenAIClient`, `AnthropicClient`, `AzureOpenAIClient`, `OpenRouterClient`, `CustomGatewayClient`
   - Factory function `create_ai_client(config: AIConfig) -> AIClient`
   - Tests: each provider instantiates, factory selects correctly, 429 retry with backoff, timeout handling

2. **Story [029]: Environment variable handling and BYOK**
   - Add `api_key_env` field to config, read at runtime, never log
   - Depends on [027]

3. **Story [028]: .revue.yml config schema and loader**
   - Define full schema, validate on startup with clear errors
   - Depends on [029]

4. **Story [009]: VCSAdapter protocol and DiffPosition abstraction**
   - `core/vcs_adapter.py` — Protocol + DiffPosition dataclass
   - GitHub position translation and GitLab line_code hash translation stubs

5. **Story [001]: Diff ingestion**
   - Parse unified diff into `FileChange(file_path, diff, language, lines_changed)`
   - Depends on [009]

6. **Story [045]: Local diff input mode**
   - CLI: `revue review --diff=path/to/file.diff --config=.revue.yml`
   - Enables Sprint 1-4 development without a live VCS

7. **Open decisions (2 remaining from PRD):**
   - Token budget strategy — deferred to Phase 2, no action needed now
   - Diff caching — deferred to Phase 2, no action needed now

---

## Continuation prompt

Read `Projects/revue.io/docs/session-continuation.md` for full context.

We're starting Sprint 1 of Revue.io (AI code review SaaS). PRD is v1.3, 48 stories across 6 epics are in Taiga (http://localhost:9000/project/revueio/kanban), 8 sprints planned.

First story to implement: **[027] AIClient protocol and provider factory** — `AIReviewer/core/ai_client.py`. Define AIClient Protocol + implementations for OpenAI, Anthropic, Azure, OpenRouter, Custom Gateway. Factory function. Tests for each provider, rate limit retry (429 + exponential backoff), and timeout handling.

Source project to port from: `Projects/revue.io/context/ai-code-review-service/AIReviewer/`
Target project location: `/Volumes/Lexar SSD/Projects/revue.io/` (or a new workspace folder)
