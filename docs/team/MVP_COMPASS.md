# MVP Compass — /revue-local public launch

**Last updated:** 2026-05-18
**Source of truth — refresh after every story ships.**

---

## North Star

Ship **/revue-local** as a publicly installable, licence-gated Claude Code skill that runs the Revue pipeline inside the customer's AI agent **before commits**, using DeepSeek-V4-Pro on OpenRouter by default. The launch narrative is **"we save your AI bill"** (~79–88% TCO vs Sonnet baseline).

## MVP = Epic E-P2A (REVUE-269) — 7 stories

| Order | Jira | Story | Status |
|------:|------|-------|--------|
| — | REVUE-267 | Default model = DeepSeek-V4-Pro on OpenRouter | ✅ Done (PR #156, 2026-05-18) |
| 1 | **REVUE-275** | **Skill bundle packaging & signed releases (Sigstore/cosign)** | 🟡 Code Review (PR #158) — AC3/AC4 ✅, AC1/AC2/AC5 deferred (external prereqs) |
| 1.5 | **REVUE-310** | **Close revue-local vendor graph OR depend on revue PyPI** | ⬜ To Do — MVP-critical; blocks standalone Mode 2 |
| 2 | REVUE-276 | Licence verification daemon (daily check + 24h cache) | ⬜ To Do |
| 3 | REVUE-277 | Free tier soft cap (25 reviews/month) | ⬜ To Do |
| 4 | REVUE-278 | Cost-saving footer (every review run) | ⬜ To Do |
| 5 | REVUE-279 | Install verification script | ⬜ To Do |
| 6 | REVUE-280 | Telemetry opt-in (anonymous usage counters) | ⬜ To Do |
| 7 | REVUE-281 | Public docs site (revue.sh/docs) | ⬜ To Do |

**External prereqs blocking REVUE-275 ACs 1/2/5:**
- [ ] PyPI Trusted Publisher entry for `revue-local` package
- [ ] Public GitHub repo `github.com/revue-io/revue-local`
- [ ] `revue-io` GitHub org with maintainer team

## Scope decisions on record

- **Q1 Free-tier behaviour** → **Soft cap** at 25 reviews/month (review runs + upgrade prompt appended). No hard block.
- **Q2 Licence check cadence** → Daily check + 24h cache, identical across tiers.
- **Q3 Custom-agent tier gating** → Pro+ only (Free/Indie ❌, Pro/Enterprise ✅), enforced at runtime.
- **Q4 Sage v2 sequencing** → Confidence-threshold spike (E-SAGE2-S0) first, blocks S1.
- **Phase 1 platform-adapter refactors** (REVUE-212/254–258) → **Run in parallel** with MVP (decision 2026-05-18).

## Critical path

```
✅ DeepSeek default (REVUE-267)
        │
        ▼
🟡 REVUE-275 — wheel + Sigstore + manifest + tests (Code Review, PR #158)
        │
        ▼
⬜ REVUE-310 — vendor closure or PyPI dep (unblocks standalone Mode 2)
        │
        ▼
⬜ REVUE-276 — licence daemon
        │
        ▼
⬜ REVUE-277 → 278 → 279 → 280 → 281 (E-P2A finishes)
        │
        ▼
🚀 MVP launch — /revue-local publicly installable, cost-saving narrative live
```

## What is NOT on the MVP critical path

- **Phase 1 platform-adapter refactors** (REVUE-212/254/255/256/257/258) — parallel track, not blocking.
- **E-P2B Polish stories** (Cursor/Windsurf installers) — Phase 2.b, post-MVP.
- **E-P2C Scale stories** (Anthropic registry submission, billing-API) — Phase 2.c, post-MVP.
- **E-SAGE2 Sage v2** — runs in parallel, not MVP-gating.
- **E-CAA Custom agents** — Pro+ feature, ships post-MVP.
- **E-NA Knowledge-base analytics** — post-MVP.
- **REVUE-156** (E9 Prompt Caching & Metrics) — MVP-relevant for cost-savings measurement; sequence after E-P2A core.
- **REVUE-245** (Vex FP pre-filter + Nova reweight) — soft-MVP (quality boost); not gating.

## Reminders

- Update this file **after every story ships** (mark Done, advance critical path arrow).
- Maintain DoD: Jira state transitions are mandatory; commits follow `type(scope)[REVUE-XX]: …`; never `--no-verify`; never amend on PR branches.
- Default model is now DeepSeek — keep the cost-savings narrative load-bearing in every customer-facing surface (README, CLI footer, docs).
