# MVP Compass — /revue-local public launch

**Last updated:** 2026-06-07 (REVUE-423 **DONE** — test-order contamination in test_local_run_dispatcher fixed; full suite green. **0 hard launch blockers.**) Forward-looking only — the full Done history and authoritative status live in **Jira**; run `/epic-progress REVUE-269` for the live tally.
**Source of truth for "next pick."** Jira's priority field ≠ launch-path order; *this doc* is the launch-path order.

---

## North Star

Ship **/revue-local** as a publicly installable, licence-gated Claude Code skill that runs the Revue pipeline inside the customer's AI agent **before commits**, using DeepSeek-V4-Pro on OpenRouter by default. The launch narrative is **"we save your AI bill"** (~79–88% TCO vs Sonnet baseline).

---

## Progress

**~62 done · 0 hard launch blockers · run `/epic-progress REVUE-269` for the live tally.**
The narrative critical path (REVUE-275 → 280 → 281) and the launch spine — install path (354/395), platform guard (360), legal pages (357), billing config in test mode (315), activation hardening + observability (325/362), licence-path robustness (369/370/371/397) — are shipped. The full activation-UX cluster Lane 1 (361 + 413 + 382) and Lane 2 (408 + 409) are done. REVUE-409 (staging E2E gate) is **Done** — validated on a green main run (#1122) with the full state matrix (active/lapsed/free/not-activated) converged and the suite green against staging; the gate now blocks prod promotion at runtime. Launch is gated only on the pre-launch polish items below.

**Recently shipped (last 5):**
- **REVUE-423** — fix test-order contamination in test_local_run_dispatcher; full suite green *(Done)*
- **REVUE-409** — post-merge staging E2E gate via signed synthetic-webhook provisioning *(Done — live + validated on green main #1122; TC-7 reproduced)*
- **REVUE-418** — systemic CSRF protection on all session-cookie form POSTs
- **REVUE-408** — site-wide two-mode (CLI/CI) messaging + new review-quality landing hero
- **REVUE-382** — Account → Plan licence-status page

---

## What's ahead

### 🔴 Hard launch blockers

**None.** The last hard blocker (REVUE-409 — post-merge staging E2E gate) is **Done**: the gate is live and validated on a green main run (#1122) — full state matrix converged in the Provision step, suite green against staging — and TC-7 (a red E2E run blocks prod promotion) was reproduced in Docker. Launch is now gated only on the pre-launch polish items below.

### 🟡 Important pre-launch polish — ship before public launch, not a hard technical gate

| Jira | Story | Why it's not a hard blocker |
|------|-------|-----------------|
| REVUE-328 | Remove unsupported licence-path override | Prevent activation and telemetry from disagreeing with the fixed runtime path |
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
- **REVUE-419** — API-triggerable `custom: deploy-production` pipeline **merged** to main; stays in Code Review (label `do-not-run-automation-after-merge`) until the first real API-triggered prod deploy validates it. CI-token split **done + verified** (new no-`pipeline:write` CI token confirmed to post review comments via throwaway PR #236). **Remaining before Done:** the first real API-triggered prod deploy.

---

## Parallelism — same-file collisions (NEVER run these in parallel)

Jira `Blocks` links tell you *order*; same-file edits are the real parallel killer and live nowhere else. Only open-ticket collisions are listed:

| Pair / group | Shared file(s) |
|---|---|
| REVUE-328 + REVUE-364 | `packaging/revue/src/revue_skill/skill/emit_usage.py` (shared licence-path helper + activation telemetry) |
| REVUE-365 + REVUE-366 | `src/web/templates/landing.html` (pricing copy + hero disclaimer) |

**Pre-launch polish execution lanes:**

| Lane | Tickets | Execution rule |
|---|---|---|
| **A — licence paths** | REVUE-328 | Can start independently. Remove the unsupported override from activation and usage emission; verify validation refresh and the local-run gate remain on the fixed path. |
| **B — timeout defaults** | REVUE-341 | Can run parallel with A/C/D after surface detection is corrected: `APP_ENV=staging` is already used by CI and dogfood, so it cannot uniquely identify `/revue-local`. |
| **C — website copy** | REVUE-365 → REVUE-366 | Serialise because both edit `landing.html`. Land pricing truth first, then add the Claude-Code-only hero/install disclaimer against the final copy. |
| **D — launch comms** | REVUE-363 | Draft channel variants in parallel with A/B/C; final copy review waits for Lane C so pricing, supported-client, and agent-count claims match the shipped site. |
| **Hold — activation telemetry** | REVUE-364 | Do not start implementation yet. It is blocked by REVUE-127 and must follow REVUE-328 to consume the shared licence-path helper instead of creating another path contract. Reconcile `/usage/track` vs `/api/v2/usage/emit` and the telemetry opt-out contract before coding. |

**Hard serial points:**
- REVUE-328 → REVUE-364.
- REVUE-365 → REVUE-366.
- Lane C final copy → REVUE-363 publication-ready sign-off.

**Maximum useful concurrency:** four lanes, but only A/B/C plus REVUE-363 drafting should be active initially; REVUE-364 remains held.

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
