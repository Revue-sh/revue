---
title: "Product Brief Distillate: revue.io"
type: llm-distillate
source: "product-brief-revue.io.md"
created: "2026-04-20"
purpose: "Token-efficient context for downstream PRD creation"
---

# Revue.io — Detail Pack

## Origin & Motivation

- Product grew from an internal GitLab code review tool
- First versions generated noisy, undifferentiated feedback — team stopped reading it
- Core design insight: a reviewer that produces noise actively trains teams to ignore
  automated feedback — worse outcome than no reviewer at all
- Revue rebuilt from scratch with noise reduction as the primary design constraint, not
  a feature — severity calibration, confidence gating, and deduplication are core pipeline
  behaviour, not configuration options
- Daniel's stated goal: give value to customers by reducing friction in code review
  processes, not just adding another tool layer

## Platform Support (Confirmed)

- GitHub: P1, MVP launch platform
- GitLab: P1, MVP launch platform (also the origin platform — most mature adapter)
- Bitbucket: **Confirmed supported at launch** — primary development and testing platform
  (Daniel confirmed; contradicts PRD which listed Bitbucket as Phase 2)
- Azure DevOps: Phase 2, post-MVP
- All three launch platforms use the same `.revue.yml` config schema
- Platform abstraction: GitHub uses diff-position offset; GitLab uses `line_code` hash;
  Bitbucket has its own comment threading model — all handled by platform-specific adapters
  behind a common `VCSAdapter` protocol

## Deployment Architecture (Critical for Enterprise Positioning)

- Orchestrator runs **entirely inside customer's CI runner** — compiled to native binary
  via Nuitka (cannot be decompiled)
- Revue's cloud API receives only: `{ key, repo_id, ci_run_id }` for licence validation
  and `{ key, agents_used, duration_ms }` for usage tracking — zero source code, zero diffs
- Free/Indie/Pro: distributed as platform-specific `.whl` (Python wheel)
- Enterprise: distributed as Nuitka Docker image
- 72-hour offline grace period for air-gapped Enterprise environments
- All incoming webhooks verified via platform-native secret tokens before processing
- IP protection: source cannot be decompiled from distributed binary

## Agent System — Technical Detail

- Agents defined as declarative YAML/Markdown files with frontmatter — no code changes
  required to add or modify agents; fully data-driven
- Agent roster (MVP): Cleo (orchestrator), Zara (security), Kai (performance), Maya
  (code quality), Leo (architecture), Nova (consolidator), Sage (resolver)
- Planned Phase 2+ agents: Finn (test coverage), Dara (documentation), Arlo
  (accessibility), Remy (migrations), Sora (concurrency — Swift 6, Kotlin coroutines),
  Rex (dependencies/CVEs)
- Cleo routing: hard diff limit check first → security keyword override → size heuristic
  (< 50 lines → team-quick) → language detection → team selection
- Hard diff limit default: 2,000 lines; posts breakdown suggestion comment, exits with
  warning (non-blocking)
- Sage confidence threshold: 90% default, configurable per project in `.revue.yml`
- Sage v1 (MVP): classify + suggest only; Sage v2 (Phase 2): auto-commit to branch +
  multi-round fix loop
- Token budget: each agent currently receives full diff (MVP); diff slicing per agent is
  a Phase 2 optimisation — deferred until real token cost data available

## Upcoming ADRs — Proposed Features (Not Yet Implemented)

### System Context Injection (REVUE-169, Proposed)
- Three decisions: D1 (architecture doc injection), D2 (adjacent file contract injection),
  D3 (intent validation in Nova)
- D1: reads `.revue/context.md` or `ARCHITECTURE.md` at repo root; injects as system
  context prefix into Maya and Leo prompts; cap at 2,000 tokens (configurable)
- D2: Cleo reads public signatures (not bodies) of files directly imported by diff (depth
  1 only); injects compact contract summary — requires repo checkout access in CI, not
  just diff API (implementation dependency to verify)
- D3: Nova receives PR description intent + consolidated findings; generates `## Intent
  Alignment` section; flags mismatches as `Intent Mismatch` at High severity
- New finding class: `Architecture Drift` — sits above High severity; always blocks merge
  when blocking mode enabled
- Token cost: +5–15% for D1, +10–20% for D2; D2 may be config-gated if cost unacceptable
- Teams must maintain `.revue/context.md` — stale docs will produce false Architecture
  Drift findings (known trade-off, documented)

### Critical Path Protection (REVUE-169, Proposed)
- D1: `critical_paths` stanza in `.revue.yml` — declare sensitive paths, labels, and
  reviewer lists
- When critical path touched: findings promoted one severity level for blocking threshold
  only (displayed severity unchanged); top-level PR comment posted; @-mentions reviewers
- D2: `escalation` stanza — declarative policy mapping conditions to actions
- Condition grammar: `critical_count`, `high_count`, `critical_path_touched` with AND/OR
  and comparison operators (no nested parens in MVP)
- Actions: `post_comment` or `block_merge` — platform review assignment API deferred to
  Phase 2 (requires additional OAuth scopes)
- Risk: false-alarm fatigue if too many critical paths declared; recommend ≤5 paths
- Escalation comments deduplicated within a PR run (edit/update, not duplicate)

## Pricing — Full Detail

- BYOK model: Revue charges orchestration only; user pays AI provider directly
- This is a structural margin advantage vs competitors absorbing AI costs
- Free: $0, 25 reviews/month, 1 basic agent — designed to create upgrade urgency
- Indie: $9/month or $79/year ($6.58/month); all 6 agents; 100 reviews/month
- Pro: $29/month or $249/year ($20.75/month); all 6 agents; unlimited reviews
- Enterprise Starter: $59/month or $499/year; 1–10 seats; self-serve; no human sales
- Enterprise Growth: $149/month or $1,249/year; 11–50 seats; light-touch sales (~5 min)
- Enterprise Plus: custom; 51+ seats; full sales cycle; 30-day 10-seat trial before commit
- Free → Indie conversion target: >5% within 90 days post-launch; adjust limit if <3%
- Typical TCO team of 5 (Pro): $59–79/month total vs CodeRabbit $60/month for same team
- TCO at enterprise scale (20 devs): Revue ~$149–199/month vs CodeRabbit $240/month

## Competitive Intelligence

- CodeRabbit: closest direct competitor; $12/dev/month; single-agent; SaaS-only (code
  leaves customer infra); GitHub, GitLab, Bitbucket, AzDO; no BYOK; no resolver
- Greptile: $20/dev/month; codebase-aware single agent; GitHub + GitLab only
- GitHub Copilot Code Review: GitHub-only; $19/dev/month (bundled); no BYOK; no multi-platform
- Sourcery: Python-focused; limited language support
- Qodo (fka CodiumAI): complex setup; test-generation focused
- SonarQube/SonarCloud: rules-based static analysis; not truly LLM-powered
- Snyk Code: security-only; not general review
- No competitor has shipped: multi-agent parallelisation at CI level, a resolver with
  one-click fix suggestions, or full BYOK + hybrid deployment combined
- CodeRabbit State of AI vs Human Code report (Dec 2025): AI code 1.7× more issues per PR,
  75% more logic errors — **note: source is a competitor; independent corroboration
  recommended before high-visibility use**
- Georgetown CSET (2024): 32.8% of AI-generated Python code contains security
  vulnerabilities; 40% of LLM solutions contain detectable security flaws — independent,
  citable, strong for security angle

## Research Gap — Flagged by Daniel

- No independent, peer-reviewed study specifically quantifying Revue's multi-agent
  improvement vs single-agent for this codebase
- PRD references "35% better critical issue detection vs single-agent" — this figure is
  from AI-generated market research, not real user data
- Short-term action: commission or run controlled comparison (same diffs, single-agent vs
  multi-agent) to produce a defensible internal number
- Until then: use "industry research suggests" framing, not first-person data claims

## GTM Strategy — Detail

- Primary launch channels: Product Hunt, Hacker News, dev Twitter/X
- Target: 500 free installs → 50 paid conversions within 6 months of launch
- Bottom-up: developers discover → tell manager → manager approves and scales
- Content strategy: write around "4 layers of code review" framework; agentic review loop
- Phase 2 target: 200 paying workspaces, $12K MRR (average $60/workspace)
- Phase 3 target: $500K ARR; marketplace listings; partnerships with Cursor, Codeium
- Enterprise Starter fully self-serve (auto licence); Enterprise Growth light-touch
  (Slack alert, 4-hour review, 99% approve); Enterprise Plus full sales cycle
- Enterprise Plus sales tooling budget: ~$361/month total (Intercom + Calendly + CRM +
  sales time); break-even = one Enterprise Growth deal ($149/month)

## Domain & Infrastructure

- Domain `revue.io` **not yet purchased** — will buy at MVP launch
- Current staging URL: `revue-io.fly.dev` — use as placeholder in all docs and code until
  domain is live; swap is a single find-and-replace across the codebase
- Licence validation and usage tracking: `POST /api/license/validate` on Revue's cloud API
- Usage tracker currently points to `revue-io.fly.dev` (production URL commented out in
  `usage_tracker.py` for easy swap)

## Explicitly Rejected / Out of Scope

- Sage auto-commits (MVP): Sage never commits autonomously — suggestions only; developer
  must explicitly accept. Auto-commit deferred to Sage v2 (Phase 2)
- Full codebase vector store / embeddings: rejected for MVP — latency, cost, persistent
  infra required. May revisit post-MVP if depth-1 contract injection proves insufficient
- Dynamic/runtime analysis: Revue is a static diff reviewer only
- Auto-generate `.revue/context.md`: teams own this document; Revue provides schema only
- Transitive import graph injection (depth > 1): too expensive in tokens, too noisy
- Platform review assignment API (Phase 2): GitHub `requested_reviewers` / GitLab API
  requires additional OAuth scopes; comment + @-mention covers 80% of value for MVP
- Escalation audit log: post-MVP analytics dashboard (REVUE-87)
- Revue does not replace linters or SAST tools — it complements Layer 1
- Revue does not store or index the codebase — diffs only, no full context

## Open Questions

- Independent bug-rate research: should commission controlled study (single-agent vs
  multi-agent on real diffs) to replace AI-generated market analysis figure
- Domain purchase timing: `revue.io` — buy at launch; until then, `revue-io.fly.dev`
- Free tier limit adjustment trigger: if <3% Free→Indie conversion in 90 days, lower to
  15 reviews/month; if >7%, hold at 25
- Sage confidence threshold: 90% default — should this be configurable at team level?
  (PRD recommendation: yes, via `.revue.yml`)
- Bitbucket support level: confirmed as launch platform by Daniel but PRD marks it Phase 2
  — PRD should be updated to reflect actual launch scope
- Agent marketplace: community agents hosted on revue.io vs GitHub? PRD recommendation:
  GitHub-hosted with curated index on revue.io
