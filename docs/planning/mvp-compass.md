# MVP Compass — /revue-local public launch

**Last updated:** 2026-06-06 (REVUE-408 + REVUE-418 merged — site-wide two-mode messaging + CSRF protection; 1 hard blocker remains). Forward-looking only — the full Done history and authoritative status live in **Jira**; run `/epic-progress REVUE-269` for the live tally.
**Source of truth for "next pick."** Jira's priority field ≠ launch-path order; *this doc* is the launch-path order.

---

## North Star

Ship **/revue-local** as a publicly installable, licence-gated Claude Code skill that runs the Revue pipeline inside the customer's AI agent **before commits**, using DeepSeek-V4-Pro on OpenRouter by default. The launch narrative is **"we save your AI bill"** (~79–88% TCO vs Sonnet baseline).

---

## Progress

**~60 done · 1 hard launch blocker (staging E2E gate, below) · run `/epic-progress REVUE-269` for the live tally.**
The narrative critical path (REVUE-275 → 280 → 281) and the launch spine — install path (354/395), platform guard (360), legal pages (357), billing config in test mode (315), activation hardening + observability (325/362), licence-path robustness (369/370/371/397) — are shipped. The full activation-UX cluster Lane 1 (361 + 413 + 382) and Lane 2 (408) are now done. The remaining gate is **409** (staging E2E).

**Recently shipped (last 5):**
- **REVUE-418** — systemic CSRF protection on all session-cookie form POSTs
- **REVUE-408** — site-wide two-mode (CLI/CI) messaging + new review-quality landing hero
- **REVUE-382** — Account → Plan licence-status page
- **REVUE-413** — persist subscription `current_period_end` + real lapsed transition
- **REVUE-361** — CLI-first post-purchase activation handoff (`/billing/success` + `/onboarding`)

---

## What's ahead

### 🔴 Hard launch blockers — Activation UX cluster (epic REVUE-269, label `mvp`)

The post-purchase **"now what?"** gap: today `/billing/success` shows no key or command, and `/onboarding` is CI-first — a just-paid user has no on-screen path to `revue activate`. The licence-key **email was rejected** (REVUE-383); activation is fully CLI + authenticated-web. This cluster closes the gap. Design spec: `docs/planning/ux-activation-flow-spec.md`.

**Build order: `409`.**

| Jira | Story | Role in the chain |
|------|-------|-------------------|
| REVUE-409 | Post-merge Playwright E2E vs staging | Reuses 361/382/384 tests via `E2E_BASE_URL`; per-state staging accounts |

### 🟡 Important pre-launch polish — ship before public launch, not a hard technical gate

| Jira | Story | Why it's not a hard blocker |
|------|-------|-----------------|
| REVUE-328 | Honour XDG_CONFIG_HOME for licence file location | Most users have default config home; minority first-week feedback |
| REVUE-341 | Per-surface default agent_timeout_seconds (/revue-local=1200s, CI=600s, CLI=600s) | UX consistency; current defaults work. Can now run solo (collision partner 339 is done). |
| REVUE-363 | Launch comms (HN/PH/Reddit/Twitter/blog) | Time-locked; the post (REVUE-281) is shipped but undelivered. Plan pre-launch, fire on launch day |
| REVUE-364 | Install → first-review activation telemetry | Conversion-funnel measurement; blocked by REVUE-127 (`/usage/track`). Backfillable from week-2 cohort |
| REVUE-365 | Clarify Pro tier features on /pricing | Sets Free-vs-Pro expectations; prevents upgrade-churn |
| REVUE-366 | "Claude Code only at launch" disclaimer on hero + install page | Avoids Cursor/Windsurf install attempts + churn |

### ⚪ Post-launch deferred

| Jira | Story | Why deferred |
|------|-------|--------------|
| REVUE-378 | Verify wheel sha256 + Sigstore signature against signed per-platform manifest | The *real* supply-chain control; ticket states not MVP-gating. Prereq: per-platform manifest first. Blocked-by REVUE-374 |
| REVUE-385 | support/legal mailbox backup + retention automation | Operational hygiene; non-gating |
| REVUE-389 | Go live with Stripe — live key, live prices, customer portal | ⚠️ **Deferred — now UNBLOCKED (REVUE-381 entity registration is Done), pending the go-live decision.** REVUE-315 (config) is done + staging-verified in test mode. Not needed for the pre-revenue /revue-local free launch; pick up when paid revenue is imminent. **Go-live remit now also covers cancel-at-period-end (webhook persist + "won't renew" UI) and revisiting the 413 webhook edge cases — folded in from rejected REVUE-415/414 to avoid deferral tickets.** |

### 🔧 Tooling follow-up (open)

- **REVUE-398** — run install suite on macOS Bitbucket runner for dscl/AC2 CI coverage (relates 395)
- **REVUE-399** — consolidate install-wizard stub fixtures into a shared factory (INFO; relates 395)
- **REVUE-373** — `install.sh` calls non-existent `revue --version` (Low; in-flight fix)

---

## Parallelism — same-file collisions (NEVER run these in parallel)

Jira `Blocks` links tell you *order*; same-file edits are the real parallel killer and live nowhere else. Only open-ticket collisions are listed:

| Pair / group | Shared file(s) |
|---|---|
| REVUE-365 + REVUE-366 | `src/web/templates/landing.html` (pricing copy + hero disclaimer) |

**Concurrency lanes — activation-UX cluster:**
- **Lane 0 (landed):** REVUE-332 (E2E infra), REVUE-384 (`/activate` + shared Command-Box), REVUE-407 (CI setup page) — all done.
- **Lane 1 (landed):** REVUE-361 (`/billing/success` + `/onboarding` activation handoff), REVUE-413 (persist subscription `current_period_end` + real lapsed transition), and REVUE-382 (Account → Plan licence-status page) — all merged.
- **Lane 2 (now open):** REVUE-408 is merged. REVUE-409 (staging-E2E gate) is the sole remaining item — gated on Lane 1 (361/382) + staging-deploy infra (347/348); reuses 361/382/384/407 tests via `E2E_BASE_URL`. Its pipeline step gates prod promotion at *runtime*.

**Hard serial points:** none — REVUE-409 depends on Lane 1's tests + staging deploy. Everything else parallelizes.

---

## Scope decisions on record

- **Q1 Free-tier behaviour** → **Soft cap** at 25 reviews/month (review runs + upgrade prompt appended). No hard block.
- **Q2 Licence check cadence** → Daily check + 24h cache, identical across tiers. ✅ shipped REVUE-278.
- **Q3 Custom-agent tier gating** → Pro+ only (Free/Indie ❌, Pro/Enterprise ✅), runtime-enforced. Post-MVP.
- **Q4 Sage v2 sequencing** → Confidence-threshold spike (E-SAGE2-S0) first, blocks S1. Parallel, not MVP-gating.

---

## Out of MVP scope

Adjacent but **not** gating launch:

- **Epic REVUE-87 (E8 — Review Intelligence & Knowledge Base)** — soft-MVP / quality-boost track (REVUE-200/207/253/203, etc.). Pick from here only after critical-path work is in flight.
- **REVUE-245 (pipeline-side FP enforcement)** — parked 2026-05-24; no demonstrated problem. Reopen if HIGH-FP rate >2 per ~2k-line PR over 2+ consecutive reviews.
- **REVUE-351 (multi-cycle review hygiene)** — under REVUE-87; real bug, not launch-gating.
- **E-P2B Polish** (Cursor / Windsurf installers) — Phase 2.b, post-MVP.
- **E-P2C Scale** (Anthropic registry, billing-API beyond REVUE-315) — Phase 2.c, post-MVP.
- **E-SAGE2 Sage v2** / **E-CAA Custom agents** — parallel / Pro+, post-MVP.

---

## Reminders

- **Refresh after every story ships** — via the `commit-compass` skill (bitbucket-merge-pr Step 5b-2), which commits this file to main (origin-only) and cycles the reusable `compass-auto` ticket.
- **Keep it lean.** "Done" is a count + the last-5 list — do **not** re-grow a per-ticket Done archive here; the full record is Jira's job. The detail belongs in Jira/`/epic-progress`, not the compass.
- **Compare suggestions against this compass** before picking next work — it is the launch-path order, not Jira priority.
- Maintain DoD: Jira state transitions are mandatory; commits follow `type(scope)[REVUE-XX]: …`; never `--no-verify`; never amend on PR branches.
- Default model is DeepSeek — keep the cost-savings narrative load-bearing on every customer-facing surface.
