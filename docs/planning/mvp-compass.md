# MVP Compass — /revue-local public launch

**Last updated:** 2026-06-10 (REVUE-435 **Done** — position-fixtures mode removed from /revue skill. **0 hard launch blockers · 0 pre-launch polish items remaining.**) Forward-looking only — the full Done history and authoritative status live in **Jira**; run `/epic-progress REVUE-269` for the live tally.
**Source of truth for "next pick."** Jira's priority field ≠ launch-path order; *this doc* is the launch-path order.

---

## North Star

Ship **/revue-local** as a publicly installable, licence-gated Claude Code skill that runs the Revue pipeline inside the customer's AI agent **before commits**, using DeepSeek-V4-Pro on OpenRouter by default. The launch narrative is **"we save your AI bill"** (~79–88% TCO vs Sonnet baseline).

---

## Progress

**~79 done · 0 hard launch blockers · run `/epic-progress REVUE-269` for the live tally.**
The narrative critical path (REVUE-275 → 280 → 281) and the launch spine — install path (354/395), platform guard (360), legal pages (357), billing config in test mode (315), activation hardening + observability (325/362), licence-path robustness (369/370/371/397) — are shipped. The full activation-UX cluster Lane 1 (361 + 413 + 382) and Lane 2 (408 + 409) are done. REVUE-409 (staging E2E gate) is **Done** — validated on a green main run (#1122) with the full state matrix (active/lapsed/free/not-activated) converged and the suite green against staging; the gate now blocks prod promotion at runtime. Launch is gated only on the pre-launch polish items below.

**Recently shipped (last 5):**
- **REVUE-435** — remove position-fixtures mode from /revue skill *(Done)*
- **REVUE-431** — web UI design standards (brand colours, glow-card, copy/layout rules) *(Done)*
- **REVUE-364** — install → first-review activation telemetry *(Done)*
- **REVUE-363** — launch comms across HN/PH/Reddit/Twitter/blog *(Done)*
- **REVUE-127** — POST /usage/track live on production; free-tier enforcement active *(Done)*

---

## What's ahead

### 🔴 Hard launch blockers

**None.** The last hard blocker (REVUE-409 — post-merge staging E2E gate) is **Done**: the gate is live and validated on a green main run (#1122) — full state matrix converged in the Provision step, suite green against staging — and TC-7 (a red E2E run blocks prod promotion) was reproduced in Docker. Launch is now gated only on the pre-launch polish items below.

### ⚪ Post-launch deferred

| Jira | Story | Why deferred |
|------|-------|--------------|
| REVUE-378 | Verify wheel sha256 + Sigstore signature against signed per-platform manifest | The *real* supply-chain control; ticket states not MVP-gating. Prereq: per-platform manifest first. Blocked-by REVUE-374 |
| REVUE-385 | support/legal mailbox backup + retention automation | Operational hygiene; non-gating |

### 🔧 Tooling follow-up (open)

- **REVUE-398** — run install suite on macOS Bitbucket runner for dscl/AC2 CI coverage (relates 395)
- **REVUE-399** — consolidate install-wizard stub fixtures into a shared factory (INFO; relates 395)
- **REVUE-373** — `install.sh` calls non-existent `revue --version` (Low; in-flight fix)

---

## Parallelism — same-file collisions (NEVER run these in parallel)

Jira `Blocks` links tell you *order*; same-file edits are the real parallel killer and live nowhere else. Only open-ticket collisions are listed: **none currently.**

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
