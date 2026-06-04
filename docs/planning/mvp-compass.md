# MVP Compass — /revue-local public launch

**Last updated:** 2026-06-04 (REVUE-362 merged — production observability on /api/v2/licence/activate: Prometheus /metrics, Fly alert-rules, Grafana dashboard, observability runbook. Done count 45→46. Strongly-should count 3→2. ⚠️ do-not-run-automation-after-merge label applies — staging validation required before ticket moves to Done. Earlier 2026-06-04: REVUE-403 merged — chore(tooling): track mvp-compass.md in git + commit-compass skill for safe post-merge persistence. Done count 44→45. REVUE-331 merged — E2E activate round-trip: CLI activate happy path + browser paste-key fallback. Strongly-should count 4→3. REVUE-360 confirmed Done — unsupported-platform guard + supported-list consistency; was shipped but not reflected in compass. REVUE-400 merged — fixed bitbucket-merge-pr skill Step 5 dispatch pattern + added context-aware mvp-compass.md automation. REVUE-395 merged — install-wizard edge-case hardening: AC1 truly-unset-HOME `set -u` crash fixed [caught by a new Docker e2e harness, not the unit tests], AC2 dscl whitespace-tolerant, AC3 no-path project-scope warning, pty test harness for interactive prompts, committed Docker e2e tool under `packaging/revue/tests/e2e/`, CLAUDE_HOME guard consolidated into `resolve_scope`; 45 unit + 24 e2e green. REVUE-354 merged earlier 2026-06-04 — interactive install wizard [global-vs-project scope + path prompt] — **launch blocker closed.** 3 follow-ups filed from 395's review: REVUE-397 [licence-validator retry-on-transient], REVUE-398 [run install suite on macOS runner for dscl/AC2 CI coverage], REVUE-399 [consolidate install-wizard stub fixtures]. REVUE-396 merged — risk-tiered review classifier + Step-10 gate; REVUE-325 merged — rate-limit + auth on `/api/v2/licence/activate`; REVUE-393 merged — `src/web/tests/` gated in CI. Earlier 2026-06-03: REVUE-315/390/391/334 merged; REVUE-381 rerouted to pre-revenue sole-trader launch [1st Formations KYC rejected])
**Source of truth — refresh after every story ships.**

---

## North Star

Ship **/revue-local** as a publicly installable, licence-gated Claude Code skill that runs the Revue pipeline inside the customer's AI agent **before commits**, using DeepSeek-V4-Pro on OpenRouter by default. The launch narrative is **"we save your AI bill"** (~79–88% TCO vs Sonnet baseline).

---

## MVP scope (epic REVUE-269 / fixVersion `MVP`)

**Headline progress: 46 Done / 69 active (67%) — 0 hard launch blockers — 2 strongly-should remain** (+1 Done on 2026-06-04: REVUE-362 — production observability on /api/v2/licence/activate (Prometheus /metrics, Fly alert-rules, Grafana dashboard, observability runbook); ⚠️ do-not-run-automation-after-merge — staging validation required before ticket moves to Done. Denominator 65→69: +3 follow-ups filed from REVUE-395's review — REVUE-397/398/399; +1 for REVUE-400 now Done. +1 Done on 2026-06-04: REVUE-403 — mvp-compass.md git tracking + commit-compass skill. +1 Done on 2026-06-04: REVUE-331 — E2E activate round-trip (CLI happy path + browser paste-key fallback). +1 Done on 2026-06-04: REVUE-400 — bitbucket-merge-pr Step 5 dispatch fix + mvp-compass automation. +2 Done on 2026-06-04: REVUE-354 — interactive install wizard (**launch blocker closed**) + REVUE-395 — install-wizard edge-case hardening + Docker e2e tool. Earlier 2026-06-04: REVUE-393 merged — `src/web/tests/` gated in CI (PR #206); REVUE-396 merged — risk-tiered review classifier + Step-10 gate (PR #209); REVUE-325 merged — activation endpoint rate-limited + header-authed. REVUE-334/315/390/391 merged 2026-06-03.)

| Bucket | Count | Meaning |
|--------|------:|---------|
| ✅ Done | 46 | Shipped. +1 on 2026-06-04: REVUE-362 (production observability on /api/v2/licence/activate — Prometheus /metrics, Fly alert-rules, Grafana dashboard, observability runbook; ⚠️ do-not-run-automation-after-merge — staging validation required before ticket moves to Done). +1 on 2026-06-04: REVUE-403 (mvp-compass.md git tracking + commit-compass skill for safe post-merge persistence). +1 on 2026-06-04: REVUE-331 (E2E activate round-trip — CLI happy path + browser paste-key fallback). +1 on 2026-06-04: REVUE-360 (unsupported-platform guard + single-source platform list — was merged earlier, not captured in compass until now). +1 on 2026-06-04: REVUE-400 (bitbucket-merge-pr Step 5 dispatch fix + mvp-compass automation). +2 on 2026-06-04: REVUE-354 (interactive install wizard — merged PR #210, **launch blocker closed**) + REVUE-395 (install-wizard edge-case hardening + Docker e2e tool — merged PR #212). Earlier 2026-06-04: REVUE-393 (gate `src/web/tests/` — PR #206) + REVUE-396 (risk-tiered review classifier — PR #209) + REVUE-325 (rate-limit + auth `/activate` — PR #205). +4 on 2026-06-03: REVUE-334 + REVUE-315 + REVUE-390 + REVUE-391. +1 on 2026-06-02: REVUE-357. +2 on 2026-06-02: REVUE-374 + REVUE-372. +3 on 2026-06-01: REVUE-369/370/371 |
| 🔵 In Code Review | 0 | — |
| 🔴 Launch blocker | 0 | **All hard launch blockers cleared.** Install path closed (354 + 395 ✅). Platform guard shipped (360 ✅). (389 Stripe go-live is **deferred post-launch** behind 381 entity reg — pre-revenue test-mode launch.) |
| 🟠 Strongly-should | 2 | High risk to ship without; close before launch if at all possible |
| 🟡 Pre-launch polish | 6 | Nice-to-have; safe to slip post-launch |
| ⚪ Post-launch deferred | 5 | Edge cases / code quality; explicitly not gating |
| 🔧 Tooling follow-up | 6 | **REVUE-397** — licence-validator retry transient API-unreachable before failing (HIGH; filed 2026-06-04 from PR #210 transient pipeline failure; relates 278/371). **REVUE-398** — run install suite on macOS Bitbucket runner for dscl/AC2 CI coverage (filed from 395 review; relates 395). **REVUE-399** — consolidate install-wizard stub fixtures into a shared factory (INFO; relates 395). REVUE-373 — `install.sh` calls non-existent `revue --version` (Low; non-gating). REVUE-387 — Tailwind Typography plugin for prose pages (Low; cosmetic). REVUE-386 — reconcile /revue-local vs /revue name across docs (Low; non-gating). (REVUE-393 ✅ done. REVUE-403 ✅ done — mvp-compass.md git tracking + commit-compass skill.) |

Narrative critical path (REVUE-275 → 280 → 281) is shipped — the "we save your AI bill" story is now live in every external surface. All hard launch blockers are now cleared.

---

## ✅ Launch blockers — ALL CLEARED

| Jira | Story | Resolved |
|------|-------|----------|
| REVUE-360 | Unsupported-platform guard + supported-list consistency | ✅ Done — `revue.sh` guard exits non-zero with named platform + install page link + CI workaround; single-source platform list in `revue_core.platform_support`. |
| REVUE-354 | Interactive install wizard (global-vs-project scope) | ✅ Done 2026-06-04 |
| REVUE-395 | Install-wizard edge-case hardening + Docker e2e | ✅ Done 2026-06-04 |

> ✅ **Merged 2026-06-04 (PR #212):** REVUE-395 — install-wizard edge-case hardening. AC1 truly-unset-`HOME` crash (`set -u` tripped at the top-level `CLAUDE_HOME=${HOME}/.claude` before `expand_tilde`'s guard ran) — **caught by a new Docker e2e harness, not the 45-test unit suite** (the unit test fed `HOME=""`, empty-but-set, so never hit the path); fixed by lazy/safe `CLAUDE_HOME` resolution with an actionable error and no silent root-relative `/.claude`. AC2 dscl parsing made whitespace-tolerant (`[[:space:]]*`). AC3 no-path project-scope in a no-tty context now warns + names cwd. Added a pty harness for the interactive prompts and committed the Docker e2e tool (`packaging/revue/tests/e2e/`, one-command runner, 24/24 checks). CLAUDE_HOME guard consolidated into `resolve_scope` (single owner of scope viability). 4 review findings dispositioned (1 fixed, 1 won't-fix, 1 deferred→399, 1 false-premise corrected + improved). **Coverage gap disclosed:** AC2's dscl test is `@skipif getent present` → skipped on Linux CI; tracked in REVUE-398 (macOS runner). Follow-ups filed: REVUE-397/398/399.
> ✅ **Merged 2026-06-04 (PR #210):** REVUE-354 — interactive install wizard (global-vs-project scope + path prompt) for `/revue-local`. **Closes the install-path launch blocker** (project-scoped installs were broken without it; required pre-launch per REVUE-276 follow-up). Hardening + interactive-prompt test coverage shipped as REVUE-395 immediately after.
> ✅ **Merged 2026-06-04 (PR #205):** REVUE-325 — rate-limit + auth on `/api/v2/licence/activate`. Per-IP limit (5 req/10 min) and per-key limit (10 successful/24h), both hardcoded (not env-overridable); attempts logged with hashed key+fingerprint; `licence.activation.flood` structured alert on a key crossing; header validation (User-Agent + `Content-Type: application/json`); trusted client IP from the non-forgeable `Fly-Client-IP` header (X-Forwarded-For is not trusted); state persisted to SQLite (survives restart). Per-IP check runs **before** the key lookup so key brute-forcing is throttled too. Hardened across **two adversarial review rounds** (2 High + 7 Medium findings resolved — incl. an FK-crash on invalid-key logging, an IP-spoofing bypass, and a flood off-by-one) plus an **empirical pen-test** (gibberish flood → 0 DB writes; brute-force capped at 5; spoof ineffective; 40-way concurrency held the cap with no lock errors). REVUE-277 activation contract preserved (field errors still 422). **Follow-up (branch `fix/REVUE-325-ci-web-test-deps`):** the web-stack tests import fastapi/uvicorn, which the root CI suite (requirements-ci.txt) omits — guarded with `pytest.importorskip` so they skip in CI and run in full locally; verified in a CI-replica venv. Unblocks **REVUE-362** (observability, strongly-should). Coverage gap noted: `src/web/tests/` is not CI-gated (pre-existing) — candidate follow-up.
> ✅ **Merged 2026-06-03:** REVUE-315 (Stripe billing config + webhook hardening). Test-mode Stripe wired on **staging** (`revue-staging`): sandbox key, 6 USD price IDs, webhook endpoint → `staging.revue.sh/webhooks/stripe`. Standardised pricing on **USD** (single `CURRENCY_SYMBOL` source). Fixed 5 real billing defects surfaced by live E2E — webhook null-field crash (A), stripe-v15 `StripeObject` incompatibility (B, the prod 500 root cause), webhook ordering race (C), `tier_from_price_id` None-collision (D), stale dashboard tier badge (E) — all regression-tested. **Validated 3 ways:** local E2E, deployed staging UI (PRO · $29/mo), and server-side staging DB (`tier=pro`). Prod (`revue-io`) deliberately has **no** Stripe key → billing "not configured" until go-live (REVUE-389). Live key/prices/portal/prod-E2E split to **REVUE-389** (gated on REVUE-381 entity verification).
> ✅ **Merged 2026-06-03:** REVUE-390 (header wordmark). Dropped the legacy ".io" TLD from the site header across 10 templates — wordmark is now plain **"Revue"** (PM decision: a TLD in a logo dates badly; `.sh` stays in URL/footer/`support@revue.sh`). Also persisted staging `APP_BASE_URL` in `fly.staging.toml`.
> ✅ **Merged 2026-06-03 (PR #203):** REVUE-391 — restored orphaned root `tests/` to the current package namespace (pre-split rot) + wired the suite into CI so it can't silently rot again. Also fixed a test-hermeticity bug (`BITBUCKET_PR_ID` leaking into GitHub/GitLab platform-detection tests under Bitbucket CI) and repaired the GitHub/GitLab **mirror CI** configs, which still referenced the deleted flat `src/revue/` namespace (now invoke the `revue-ci` console script + run the post-split suites). Validated: Bitbucket tests green, GitLab `unit-tests` green (MR !26), GitHub install/validate green (PR #35). Non-gating test-infra; local `run_all.sh` now matches CI. **Mirror follow-ups (non-gating):** GitHub `AI_MODEL` var uses a stale alias (`claude-sonnet-4-5` → needs `claude-sonnet-4-5-20250929`); GitLab review (openai) trace unverified.
> ✅ **Merged 2026-06-03 (PR #204):** REVUE-334 — routed all three JWT verify sites through a call-time `get_jwt_public_key()` accessor + added an AST guard (`test_jwt_accessor_binding.py`) and a build-mode guard (`test_nuitka_module_mode_prevents_folding.py`). An empirical Nuitka experiment proved that under the project's `nuitka --module` (per-file) build, the embedded public key is **not** constant-folded across the wheel boundary — each `.py` compiles as an independent unit, so the cross-wheel verify read stays a runtime lookup. The original "compiled binary silently breaks verification" vulnerability **does not exist** in this build mode; the accessor + guards are kept as defensive future-proofing and the ticket was reframed (Jira comment + README finding). **Nuitka-verified end-to-end:** tag pipeline **#981 / v0.25.0** compiled `revue_core` (jwt_keys + accessor) and the `revue` skill (activate/validate) cleanly on **macOS ARM64 + Linux x86_64** and published all three to PyPI — the genuine ships-to-customer compile evidence. Follow-up **REVUE-392** filed: parity test for the duplicated web-server key copy (`src/web/jwt_verify.py`, from REVUE-345). Jira moved to Done manually (Bitbucket→Jira automation silently failed — free-plan monthly cap).
>
> ✅ **Merged 2026-06-02:** REVUE-357 (Privacy Policy + Terms of Service for revue.sh). Shipped: `/terms` + `/privacy` pages rendered from Markdown (FastAPI + Jinja2), operator entity "Token Labs Ltd" (England & Wales), UK GDPR framing, legal@revue.sh contact, shared site footer with Terms/Privacy/Support links, consent line on /activate. Pages carry `[PENDING LEGAL REVIEW]` + `[PENDING REGISTRATION]` markers — counsel sign-off + Companies House registration of Token Labs Ltd still required before public launch (tracked in REVUE-381). Legal gate is cleared for Stripe (REVUE-315 is now unblocked).
> ✅ **Merged 2026-06-02:** REVUE-372 (tag-release `__version__` bump, PR #194). Confirmed live: PyPI `revue-0.24.2` was shipping wheel `__version__ "0.1.0"` across 24 releases — now fixed on the next tag.
> ✅ **Merged 2026-06-01:** REVUE-369 (wheel distribution, `c57db6b`), REVUE-370 (env-var bypass, `7b61c74`), REVUE-371 (cache-trust bypass + C1 `workspace_id` cache-persistence fix, `e0725bd`). The two licence-bypass surfaces are now closed; the customer wheel installs and runs.
>
> ✅ **REVUE-374 validated end-to-end 2026-06-02:** the manifest endpoint that 404'd in prod was built and shipped (PR #196 merged; `8e6d43f` fixed a missing `jsonschema` runtime dep). Triggering the prod deploy auto-cut tag `v0.24.3` → pipeline #957 published `revue/revue-core/revue-ci 0.24.3` to PyPI; the 0.24.3 wheel reports `__version__ 0.24.3` (REVUE-372 fix confirmed — no longer `0.1.0`); the prod manifest auto-flipped to `0.24.3`; `revue install-skill` strict returned **exit 0** in an isolated venv. 372 + 374 together close the default install path. Ticket transitioned to **Done** 2026-06-02 after validation (the `do-not-run-automation-after-merge` label had held it in Code Review so AC5 could be validated post-merge; label now moot).
>
> 🔎 **Two side-gaps logged 2026-06-02:** (1) **REVUE-377** ✅ **resolved (PR #198, in Code Review)** — corrected the false doc claim via Option B (doc now describes the real trust model: https fetch + schema + version match; no hash/Sigstore). The *real* signing control (sha256 + Sigstore over a per-platform manifest) is deferred to **REVUE-378** (post-launch, see below). (2) `.git/config` carries plaintext GitHub + GitLab PATs that were exposed in-session — **rotate both now** and move auth to a credential helper / 1Password.

Sequencing + parallelization across all open tickets (not just blockers) is in the **Dependency map & parallel work plan** section below.

---

## 🟠 Strongly-should — high-risk to skip

| Jira | Story | Risk if skipped |
|------|-------|-----------------|
| REVUE-339 | Cooperative deadline + finalize budget reservation for agent_timeout | Users see truncated/garbage output on hard-kills instead of a graceful finalize — terrible first impression on the very feature we're selling |
| REVUE-361 | Validate transactional email deliverability for activate flow | JWT-via-email landing in spam = zero activations = silent launch failure. **Now unblocked — REVUE-358 shipped the DNS auth (SPF/DKIM/DMARC all pass, mail-tester 8/10) 2026-05-29.** |

---

## 🟡 Pre-launch polish — safe to slip

| Jira | Story | Why it can wait |
|------|-------|-----------------|
| REVUE-328 | Honour XDG_CONFIG_HOME for licence file location | Most users have default config home; affects a minority on first-week feedback |
| REVUE-341 | Per-surface default agent_timeout_seconds (/revue-local=1200s, CI=600s, CLI=600s) | UX consistency; current defaults work, just not optimized per context |
| REVUE-363 | Launch comms (HN/PH/Reddit/Twitter/blog) | Time-locked coordination; the launch post (REVUE-281) is shipped but undelivered to channels. Can be planned pre-launch and executed on launch day |
| REVUE-364 | Install → first-review activation telemetry | Conversion-funnel measurement; blocked by REVUE-127 (`/usage/track` upstream). Good to have at launch, can backfill from week 2 cohort |
| REVUE-365 | Clarify Pro tier features on /pricing | Pricing page exists (REVUE-281); this clarifies Free-vs-Pro deltas + "Coming to Pro" copy to set expectations and prevent upgrade-churn |
| REVUE-366 | "Claude Code only at launch" disclaimer on hero + install page | Sets expectations to avoid Cursor/Windsurf install attempts + churn |

---

## ⚪ Post-launch deferred

| Jira | Story | Why deferred |
|------|-------|--------------|
| REVUE-316 | Refactor cmd_consolidate duplication in scripts/local_run.py | Code quality, no user impact |
| REVUE-317 | Make anthropic + openai optional deps in revue_core | Install-footprint reduction; nice-to-have |
| REVUE-330 | Detect non-POSIX filesystem for licence file and warn | Edge case |
| REVUE-336 | Unique tmp file names + cleanup for concurrent revue activate | Edge case |
| REVUE-342 | Heartbeat / progress signal during long reviews | Tagged `[BACKLOG]` in its own title |
| REVUE-378 | Verify wheel sha256 + Sigstore signature against signed per-platform manifest (REVUE-377 Option A) | The *real* supply-chain control; ticket itself states not MVP-launch-gating. Hard prerequisite: make the manifest per-platform first. Blocked-by REVUE-374; relates REVUE-377/360 |
| REVUE-379 | Manifest endpoint cache lock — coalesce concurrent cold-cache PyPI fetches | REVUE-374 deferred item; idempotent/low-severity at current traffic |
| REVUE-380 | Manifest must skip yanked PyPI releases for `current_version` | REVUE-374 deferred item; low likelihood under single-maintainer release flow |
| REVUE-385 | support/legal mailbox backup + retention automation | Operational hygiene; non-gating at launch |
| REVUE-381 | Register Token Labs Ltd (Companies House) + remove `[PENDING REGISTRATION]` markers from /terms + /privacy | ⚠️ **Deferred — pre-revenue sole-trader launch (updated 2026-06-04):** 1st Formations KYC rejection halted incorporation. Rerouted to solo-trader pre-revenue launch — ToS/Privacy `[PENDING REGISTRATION]` markers remain accurate and uncontroversial during pre-revenue phase. Incorporation becomes critical when: (a) revenue spikes (Stripe payout requirements), or (b) KYC path resolves via alternative agent. Revisit when revenue is imminent or KYC blocker is cleared. |
| REVUE-389 | Go live with Stripe — live key, live prices, customer portal | ⚠️ **Deferred — blocked by REVUE-381 entity registration (updated 2026-06-04):** REVUE-315 (billing config) is ✅ done + staging-verified in test mode. Live Stripe payouts require a registered, verified legal entity (REVUE-381). Pre-revenue MVP operates in test mode (no payouts, no customer transactions). Revisit when REVUE-381 is unblocked (incorporation path resolves) and revenue is imminent. |

---

## Dependency map & parallel work plan

Built from formal Jira `Blocks` issue-links (`inwardIssue=blocker`, `outwardIssue=blocked`) across all 26 open tickets. Last refreshed 2026-06-01.

### Open chains (must sequence)

```
REVUE-357 (Privacy/ToS)         ──✅ DONE──▶ REVUE-315 (Stripe config) ──✅ DONE──▶ [DEFERRED] REVUE-389 (Stripe live)
REVUE-325 (rate-limit /activate)──✅ DONE──▶ REVUE-362 (observability layer) ──✅ DONE──
REVUE-369 (wheel distribution)  ──✅ DONE──▶ REVUE-269 (customer wheel now installs)
REVUE-370 (licence env bypass)  ──✅ DONE──▶ REVUE-269
REVUE-371 (cache-trust bypass)  ──✅ DONE──▶ REVUE-269
REVUE-372 (__version__ bump)    ──✅ DONE──▶ REVUE-269 (wheel-side of install-skill)
REVUE-374 (manifest endpoint)   ──✅ DONE──▶ REVUE-269 (other half of install-skill; 374 + 372 together)
```

**Bottleneck progress (2026-06-04):** Install path ✅ CLOSED. Legal pages ✅ DONE. Billing config ✅ DONE (test mode). Activation hardening ✅ DONE (325). Pre-revenue MVP has no further blockers. The **REVUE-381 → REVUE-389** revenue chain is deferred post-launch (incorporation + Stripe live require entity registration + revenue justification, both absent pre-revenue).

**Critical path now (315/357/372/374/325 done) — pre-revenue MVP launch:**

The **REVUE-381 (entity registration) → REVUE-389 (Stripe live)** chain is **deferred post-launch.** REVUE-315 (billing config) is ✅ done and staging-verified in test mode — sufficient for pre-revenue phase. Pre-revenue MVP launches without Stripe payouts (test mode indefinitely; live payouts gate to post-launch when entity + revenue exist).

**Parallel, independent, all MVP-gating:**
1. ✅ **REVUE-325 (rate-limit /activate).** Merged 2026-06-04. Unblocked **REVUE-362** (observability, strongly-should).
2. ✅ **REVUE-354 (install wizard).** Merged 2026-06-04 (PR #210) — install-path blocker closed. Hardened by ✅ **REVUE-395** (PR #212).
3. ✅ **REVUE-360** (wheel platform coverage) — **Done.** Platform guard + single-source list shipped.
4. ✅ **REVUE-362** (observability on /activate) — merged 2026-06-04. ⚠️ do-not-run-automation-after-merge — staging validation required before Jira moves to Done.
5. ✅ **REVUE-393 (gate `src/web/tests/` in CI).** Merged 2026-06-04 (PR #206).

### Ready-to-start (no open blockers)

315/357/372/374/334/325/**354/395/393/360/331/362** merged — all hard launch blockers cleared. The **REVUE-381 → REVUE-389** revenue go-live chain is deferred post-launch. REVUE-362 ✅ done (observability). Next priority: REVUE-361, REVUE-339.

### Worktree slots for 5-way parallel work

File-conflict-aware (Jira deps only tell you order; same-file edits are the real parallel killer):

| Slot | Ticket | Surface | Conflict risk |
|---|---|---|---|
| 1 | **REVUE-359** (358 ✅ done — rotate in 359) | CLI support@ wiring (`local_run.py`, skill bootstrap) | See collisions table |
| 2 | **REVUE-357** ✅ DONE | `src/web/templates/{terms,privacy}.html` — shipped 2026-06-02 | — |
| 3 | **REVUE-315** ✅ DONE (config) → **REVUE-389** (go-live, blocked-by 381) | `src/web/` + Fly prod secrets | Stripe config shipped + staging-verified; live key/prices/portal = 389 |
| 4 | **REVUE-325** | `src/web/api/licence.py` (server) | Zero from this group |
| 5 | **REVUE-354** | `packaging/revue/install/` (client) | Zero from this group |

Worktree creation:

```bash
# REVUE-357 ✅ DONE; REVUE-358/359 ✅ DONE — replaced by 315 in slot 3
# REVUE-315 ✅ DONE — go-live work is REVUE-389 (after REVUE-381 entity reg)
git worktree add ../revue-389 REVUE-389-stripe-go-live
git worktree add ../revue-325 REVUE-325-rate-limit-activate
git worktree add ../revue-354 REVUE-354-install-wizard
git worktree add ../revue-360 REVUE-360-wheel-coverage
```

Per memory `feedback_worktrees`: previously moved off worktrees for solo story work; the 5-way parallel case is the one they were designed for. Each worktree needs its own venv if the dep set differs — REVUE-360 in particular touches `pyproject.toml`.

### Same-file collisions — NEVER run these in parallel

| Pair / group | Shared file(s) |
|---|---|
| REVUE-365 + REVUE-366 | `src/web/templates/landing.html` (pricing copy + hero disclaimer) |
| REVUE-339 + REVUE-341 | `agent_timeout` config in `revue_core` |
| REVUE-328 + REVUE-330 + REVUE-336 + REVUE-371 | licence/cache path modules (`cache_paths.py`, `validate.py`) — REVUE-371 rewrites validate.py JWT verification |
| ~~REVUE-325 + REVUE-334~~ | REVUE-334 ✅ done — collision retired; 325 now runs solo |
| REVUE-354 + REVUE-359 + REVUE-364 + REVUE-370 | install / CLI bootstrap surface (`local_run.py`, skill bootstrap) — REVUE-370 vendor-rewrites `local_run.py` env-var bypass |
| REVUE-369 + REVUE-372 | `__init__.py` (REVUE-372 rewrites version handling) + `bitbucket-pipelines.yml` sed step. Land REVUE-369 first, then REVUE-372 against post-merge main. |

### Suggested next wave (updated 2026-06-04 — all hard blockers cleared; REVUE-381/389 deferred post-launch)

| Slot | Ticket | Surface | Safe to run with |
|---|---|---|---|
| 1 | ✅ **REVUE-362** (observability on /activate) — merged 2026-06-04; ⚠️ do-not-run-automation-after-merge | `src/web/` (consumes REVUE-325's flood event + `activation_attempts`) | — |
| 2 | **REVUE-339** (cooperative deadline + agent_timeout finalize) — strongly-should | `revue_core` agent_timeout config | 1, 3 |
| 3 | **REVUE-397** (licence-validator retry on transient) — HIGH tooling follow-up | `revue_core/.../license_validator.py` | 1, 2 |
| +  | **REVUE-398** (install suite on macOS runner) — follow-up | `bitbucket-pipelines.yml` (new step) | conflict-free from all above (360 done) |

**Caveats — do NOT run in parallel:**
- **REVUE-362 + REVUE-361** (email deliverability): both likely touch `src/web/` — verify file overlap before starting together; default to serialise.
- **REVUE-339 + REVUE-341** (per-surface agent_timeout defaults): explicit same-file collision on the agent_timeout config.

**Net parallelism (as of 2026-06-04 — all hard blockers cleared; REVUE-331 + REVUE-362 ✅ done):** zero hard launch blockers remain. Cleanest 2-way parallel: **339 + 397** — distinct codebases, zero shared files. REVUE-361 (email deliverability) is independent too. The **REVUE-381 → 389** revenue chain stays **deferred to post-launch** (pre-revenue sole-trader launch + 1st Formations KYC rejection).

Worktree creation:

```bash
# REVUE-315/325/354/395/393/360/362 ✅ DONE. Revenue go-live = REVUE-389 (after REVUE-381 entity reg, externally blocked).
git worktree add ../revue-339 REVUE-339-agent-timeout-deadline    # revue_core agent_timeout
git worktree add ../revue-397 REVUE-397-licence-retry             # revue_core license_validator (HIGH follow-up)
# REVUE-398 safe alongside any of the above — pipelines.yml only, 360 no longer competing
git worktree add ../revue-398 REVUE-398-macos-install-ci
```

The **REVUE-381 → REVUE-389** chain is the active revenue pole (deferred post-launch) — REVUE-315 (Stripe config) is ✅ done and staging-verified; going live needs the registered entity (381) then the live key/prices/portal (389). The install cluster (354/395/369/370/371/372 + 374), the legal pages (357), the billing config (315), the web-suite CI gate (393), the platform guard (360), the E2E activate round-trip (331), and the observability layer (362) are fully done. **All hard launch blockers are cleared.** Top priority: REVUE-361 (email deliverability), REVUE-339 (cooperative deadline + agent_timeout).

> ⚠️ **Conflict note for 315 vs 325:** both touch `src/web` (Stripe wiring + `api/licence.py`) — review file-level overlap before running in parallel. 374 already registered its manifest router in `src/web/main.py`; any new web ticket that adds a router must coordinate that file to avoid a merge clash. REVUE-357 is ✅ done and no longer a parallel concern.

---

## ✅ Done (46 of 69)

| Jira | Story | Shipped |
|------|-------|---------|
| REVUE-362 | Production observability on `/api/v2/licence/activate` — Prometheus /metrics endpoint, Fly.io alert-rules, Grafana dashboard, observability runbook. ⚠️ do-not-run-automation-after-merge label applies — staging validation required before ticket moves to Done in Jira. | 2026-06-04 |
| REVUE-403 | chore(tooling): track mvp-compass.md in git + commit-compass skill for safe post-merge persistence (compass now committed on every merge; commit-compass skill handles the safe single-commit flow) | 2026-06-04 |
| REVUE-331 | E2E test for activate flow — CLI activate happy path + browser paste-key fallback round-trip (re-scoped 2026-06-02 to CLI-first; REVUE-382/383/384 cover activation flow redesign) | 2026-06-04 |
| REVUE-400 | Fix bitbucket-merge-pr skill Step 5 dispatch pattern + add context-aware mvp-compass.md automation (post-merge epic-progress recap now context-driven; dispatch pattern corrected so Step 5 fires reliably) | 2026-06-04 |
| REVUE-395 | Install-wizard edge-case hardening (AC1 truly-unset-HOME crash fixed — caught by new Docker e2e; AC2 dscl whitespace-tolerant; AC3 no-path project warn; pty harness for interactive prompts; committed Docker e2e tool `packaging/revue/tests/e2e/`; CLAUDE_HOME guard consolidated into resolve_scope; 45 unit + 24 e2e green; 3 follow-ups filed 397/398/399) | 2026-06-04 |
| REVUE-354 | Interactive install wizard — global-vs-project scope + path prompt for `/revue-local` (closes install-path launch blocker; hardened by 395) | 2026-06-04 |
| REVUE-325 | Rate-limit + auth `/api/v2/licence/activate` (per-IP 5/10min + per-key 10/24h, hashed-attempt logging, flood alert, header validation, Fly-Client-IP trust; 2 review rounds + pen-test; CI follow-up guards web-stack tests) | 2026-06-04 |
| REVUE-334 | JWT verify accessor + folding investigation (no vuln under `--module`; reframed) | 2026-06-03 |
| REVUE-275 | Package skill as Nuitka-compiled wheel + publish to PyPI | 2026-05-18 |
| REVUE-310 | Extract revue_core, rename revue→revue-ci, close vendor graph | — |
| REVUE-313 | Rebrand "Revue.io" to "Revue", migrate URLs to revue.sh | — |
| REVUE-314 | Migrate Fly app revue-io → revue-sh, configure custom domain | — |
| REVUE-324 | Vex Option C: DeepSeek-specific reasoning channel | — |
| REVUE-337 | Extend DeepSeek reasoning channel to reviewer agents | — |
| REVUE-340 | Raise agent_timeout_seconds validator cap 600→1800 | — |
| REVUE-344 | /epic-progress skill | — |
| REVUE-345 | Inline JWT constants — restore api.revue.io after import crash | — |
| REVUE-349 | Worktree detection + removal in bitbucket-merge-pr | — |
| REVUE-277 | Licence JWT issuance — revue.sh/activate browser flow | — |
| REVUE-278 | Daily-check + 24h-cache licence validation contract | 2026-05-24 |
| REVUE-276 | One-command install flow (Claude Code path) | 2026-05-28 |
| REVUE-279 | Free-tier 25-review monthly paywall with soft cap | 2026-05-28 |
| REVUE-352 | fix(ci) DOCKER_BUILDKIT disable for Build Web Image | 2026-05-28 |
| REVUE-353 | fix(packaging) build_wheel.py reads deps from pyproject.toml | 2026-05-28 |
| REVUE-356 | fix(skill) customer-shipped SKILL.md hardcoded path | 2026-05-28 |
| REVUE-280 | CLI cost-saving footer | 2026-05-28 |
| REVUE-281 | Cost-care messaging rollout (README + website hero + pricing) | 2026-05-28 |
| REVUE-358 | support@revue.sh email (Cloudflare Email Routing + Brevo SMTP + DNS auth) — runbook in `docs/team/` | 2026-05-29 |
| REVUE-359 | Surface support@revue.sh in /revue-local error output + docs (boundary emit pattern, SRP-clean support module, guard tests for COMPILE_ROOTS + BaseException passthrough) | 2026-05-30 |
| REVUE-369 | Fix 6 latent wheel-distribution defects (customer install non-functional) — F1–F6 + internal H1/H2/M*/L* + Codex H1/M5/M7/L9 | 2026-06-01 |
| REVUE-370 | Remove `REVUE_SKIP_LICENCE_CHECK` env-var bypass from vendored wheel (vendor-time `rewrite_imports` strip + real-wheel release gate) | 2026-06-01 |
| REVUE-371 | Verify JWT signature before trusting plaintext cache + persist `workspace_id` to cache (C1 offline-lockout fix) | 2026-06-01 |
| REVUE-372 | Tag-release pipeline bumps `__init__.py:__version__` (wheel-side install-skill fix) + 3 regression tests | 2026-06-02 |
| REVUE-357 | Privacy Policy + Terms of Service for revue.sh (`/terms` + `/privacy` pages, FastAPI + Jinja2, "Token Labs Ltd" operator entity, UK GDPR framing, legal@revue.sh contact, shared footer, /activate consent line; `[PENDING LEGAL REVIEW]` + `[PENDING REGISTRATION]` markers; REVUE-381 tracks Companies House reg) | 2026-06-03 |
| REVUE-315 | Stripe billing config + webhook hardening — test-mode Stripe on staging (sandbox key, 6 USD prices, webhook endpoint), USD pricing via single `CURRENCY_SYMBOL`, 5 billing defects fixed (null-field, stripe-v15 StripeObject, webhook ordering, price-map None, stale dashboard tier), validated 3 ways (local + staging UI + staging DB). Live key/prices/portal → REVUE-389 | 2026-06-03 |
| REVUE-390 | Header wordmark → plain "Revue" (dropped legacy ".io" TLD across 10 templates; `.sh` stays in URL/footer/email) + staging `APP_BASE_URL` in fly.staging.toml | 2026-06-03 |
| REVUE-391 | Restore orphaned root `tests/` to current package namespace + gate in CI; fix `BITBUCKET_PR_ID` env-leak in platform-detection tests; repair GitHub/GitLab mirror CI after monorepo split (`revue-ci` console script + post-split suites + per-mirror provider wiring) | 2026-06-03 |

Excluded from totals: REVUE-311, REVUE-312 (rejected/cancelled).

---

## Out of MVP scope

These look adjacent but are **not** gating the launch:

- **Epic REVUE-87 (E8 — Review Intelligence & Knowledge Base)** — soft-MVP / quality-boost parallel track. Includes REVUE-200 (retry mechanism), REVUE-207 (auto-resolve gate), REVUE-253 (fingerprint collision), REVUE-203 (AnchorVerifier), etc. Pick from this epic only after critical-path tickets are in flight or shipped.
- **REVUE-245 (pipeline-side FP enforcement)** — **parked** 2026-05-24 per `_bmad-output/implementation-artifacts/revue-244-fp-measurements.md`. Post-244 dogfood baseline showed 0 HIGH-FPs across 2 replays; pipeline-side enforcement has no demonstrated problem to solve. Reopen-trigger: HIGH-FP rate >2 per ~2k-line PR on real PRs over 2+ consecutive reviews.
- **REVUE-351 (multi-cycle review hygiene)** — filed 2026-05-24, under REVUE-87. Auto-resolve never firing + hunk positioning drift. Real bug but not launch-gating.
- **E-P2B Polish stories** (Cursor / Windsurf installers) — Phase 2.b, post-MVP.
- **E-P2C Scale stories** (Anthropic registry submission, billing-API integration beyond REVUE-315) — Phase 2.c, post-MVP.
- **E-SAGE2 Sage v2** — runs in parallel, not MVP-gating.
- **E-CAA Custom agents** — Pro+ feature, ships post-MVP.

---

## Scope decisions on record

- **Q1 Free-tier behaviour** → **Soft cap** at 25 reviews/month (review runs + upgrade prompt appended). No hard block.
- **Q2 Licence check cadence** → Daily check + 24h cache, identical across tiers. ✅ shipped REVUE-278.
- **Q3 Custom-agent tier gating** → Pro+ only (Free/Indie ❌, Pro/Enterprise ✅), enforced at runtime. Post-MVP.
- **Q4 Sage v2 sequencing** → Confidence-threshold spike (E-SAGE2-S0) first, blocks S1. Parallel, not MVP-gating.

---

## Reminders

- **Update this file after every story ships** (mark Done in the table; advance critical-path arrow).
- **Compare suggestions against this compass.** Priority field in Jira ≠ launch-path order. The compass is the source of truth for "next pick."
- Maintain DoD: Jira state transitions are mandatory; commits follow `type(scope)[REVUE-XX]: …`; never `--no-verify`; never amend on PR branches.
- Default model is now DeepSeek — keep the cost-savings narrative load-bearing in every customer-facing surface (README, CLI footer, docs).
