# Build Distribution — Production Readiness

**Status:** Active
**Updated:** 2026-04-28

---

## Overview

The Bitbucket Pipelines build matrix compiles `revue/core/` to native `.so` binaries via Nuitka
and packages them into platform-specific wheels. Nuitka compilation is the primary IP protection
mechanism — it makes the review logic, agent prompts, and routing logic non-trivially reversible.
Shipping plain Python is not acceptable for distribution.

---

## Platform support decisions

### Supported platforms

| Platform | Build runner | Rationale |
|---|---|---|
| Linux x86_64 | Bitbucket Cloud managed | ~50% of target users (servers, CI, Linux devs). Zero infrastructure cost. |
| macOS ARM64 | Self-hosted on dev machine | ~30% of target users. Apple Silicon is the majority of new Mac sales since 2021. Intel Mac users run ARM64 binaries via Rosetta 2 with near-zero overhead. |

### Dropped platforms

| Platform | Reason |
|---|---|
| Linux ARM64 | <1% of target users for a developer CLI tool. Requires a self-hosted runner with no available hardware. Linux ARM64 users can install from source via `pip install revue`. |

---

## Pipeline structure

### Sequential, not parallel

The two build steps run **sequentially** — Linux x86_64 first, then macOS ARM64.

**Why not parallel:** Bitbucket Pipelines v5 self-hosted runners consume a concurrency slot from
the workspace plan. Running a managed runner and a self-hosted runner simultaneously uses 2 slots
concurrently, which triggers additional charges. Sequential execution uses 1 slot at a time —
no extra cost, no change to the IP protection guarantee.

**Trade-off:** Total pipeline duration is longer (~16 min x86_64 + macOS build time vs. ~16 min
total in parallel). For a main branch pipeline that triggers on infrequent merges, this is
acceptable.

**To revert to parallel** if dedicated build infrastructure is available in future:

```yaml
# Replace the two sequential step entries with:
- parallel:
    steps:
      - step:
          name: "Build Linux x86_64"
          # ... (managed runner, no runs-on needed)
      - step:
          name: "Build macOS ARM64"
          runs-on:
            - self.hosted
            - macos.arm64
          # ... (remove fail-fast: false — runners are reliable)
```

---

## macOS ARM64 self-hosted runner setup

The macOS build step uses a Bitbucket v5 self-hosted runner registered on an Apple Silicon Mac.

### Registration (one-time)

1. Go to **Bitbucket repo → Repository settings → Pipelines → Runners → Add runner**
2. Select macOS as the platform
3. Follow the agent install instructions Bitbucket generates
4. Verify the runner appears as **Online** before merging to main

### Runner availability

The runner must be online when a main branch pipeline triggers. If it is offline, the macOS
build step queues until it comes back online (Bitbucket does not time out self-hosted steps
immediately). The pipeline will not fail fast — it will wait.

If the runner will be offline for an extended period, the macOS build step can be temporarily
commented out of `bitbucket-pipelines.yml` to unblock the Linux x86_64 + deploy flow.

---

## Wheel publishing

Wheels are published to **public PyPI** after both builds complete.

| Variable | Purpose |
|---|---|
| `PYPI_API_TOKEN` | PyPI upload token (secured pipeline variable) |

The "Collect Artifacts" step lists available wheels before publishing. If the macOS runner is
offline, only the Linux x86_64 wheel is published for that release. macOS users can still
`pip install revue` and receive the Linux wheel, which runs under Rosetta 2, or wait for the
next release when the runner is online.

---

## Milestone checklist

- [x] Linux x86_64 build on Bitbucket Cloud managed runner
- [x] Nuitka CI hang fixed (`--assume-yes-for-downloads`, `--no-progressbar`)
- [x] Build output visible in CI log (`python -u`)
- [x] PyPI publish step wired (`PYPI_API_TOKEN`)
- [x] Fly.io deploy step wired (`FLY_API_TOKEN`)
- [ ] macOS ARM64 runner registered and online
- [ ] End-to-end test: push to main → both wheels built → published to PyPI → `pip install revue` succeeds on macOS ARM64
