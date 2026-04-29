# Build Distribution — Production Readiness

**Status:** Active
**Updated:** 2026-04-29

---

## Overview

The Bitbucket Pipelines build matrix compiles `revue/core/` to native `.so` binaries via Nuitka
and packages them into platform-specific wheels. Nuitka compilation is the primary IP protection
mechanism — it makes the review logic, agent prompts, and routing logic non-trivially reversible.
Shipping plain Python is not acceptable for distribution.

---

## Release pipeline — how it works

### Two pipelines, two triggers

| Pipeline | Trigger | What it does |
|---|---|---|
| **Main branch** | Every merge to `main` | Tests → tag release (if warranted) → Fly.io deploy |
| **Tag (`v*`)** | New `vX.Y.Z` tag pushed | Tests → build macOS + Linux wheels → publish to PyPI |

### Automated semantic versioning

On every main merge, `build/determine_release.py` parses the squash commit message and decides
whether a release is warranted based on the conventional commit type:

| Commit type | Bump | Example |
|---|---|---|
| `feat` | minor | `feat(auth)[REVUE-200]: add OAuth` → `v0.2.0` |
| `fix`, `perf`, `refactor` | patch | `fix(ci)[REVUE-191]: fix runner PATH` → `v0.1.1` |
| `feat!`, `fix!`, `BREAKING CHANGE` | major | `feat!: new API` → `v1.0.0` |
| `chore`, `ci`, `docs`, `style`, `test` | none | no tag, no publish |

When a bump is warranted, `python-semantic-release` is invoked with `--patch`, `--minor`, or
`--major`. It:

1. Updates `version` in `src/pyproject.toml`
2. Commits the bump with message `chore: bump version to X.Y.Z [skip ci]`
3. Creates and pushes a `vX.Y.Z` tag

The `[skip ci]` marker prevents the version bump commit from re-triggering the main pipeline.
The pushed tag triggers the `tags: v*:` pipeline which builds and publishes.

### Fly.io deploy

The web deploy runs on **every** main merge regardless of release status — it is a continuous
deploy, not a release gate.

### Required pipeline variable for tag push

`python-semantic-release` pushes to the repository using:
```
https://x-token-auth:${BITBUCKET_API_TOKEN}@bitbucket.org/${BITBUCKET_REPO_FULL_NAME}
```

`BITBUCKET_API_TOKEN` must have write access to the repository. If this fails, the alternative
is to configure an SSH key pair in **Bitbucket → Repository settings → Pipelines → SSH keys**.

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

### Sequential builds, not parallel

The two build steps run **sequentially** — macOS ARM64 first, then Linux x86_64.

**Why macOS first:** The self-hosted runner is more likely to surface issues early. Failures
surface before the 16-minute Linux build runs.

**Why not parallel:** Bitbucket Pipelines v5 self-hosted runners consume a concurrency slot from
the workspace plan. Running a managed runner and a self-hosted runner simultaneously uses 2 slots
concurrently, which triggers additional charges. Sequential execution uses 1 slot at a time —
no extra cost, no change to the IP protection guarantee.

**Trade-off:** Total pipeline duration is longer (~16 min macOS + 16 min Linux vs. ~16 min total
in parallel). For a tag-triggered pipeline that fires only on real releases, this is acceptable.

**To revert to parallel** if dedicated build infrastructure is available in future:

```yaml
# Replace the two sequential step entries with:
- parallel:
    steps:
      - step:
          name: "Build macOS ARM64"
          runs-on:
            - self.hosted
            - macos
      - step:
          name: "Build Linux x86_64"
          # ... (managed runner, no runs-on needed)
```

---

## macOS ARM64 self-hosted runner setup

The macOS build step uses a Bitbucket v5 self-hosted runner registered on an Apple Silicon Mac.

### Registration (one-time)

1. Go to **Bitbucket repo → Repository settings → Pipelines → Runners → Add runner**
2. Select macOS as the platform
3. Follow the agent install instructions Bitbucket generates
4. Verify the runner appears as **Online** before merging to main

### Starting the runner

```bash
nohup ~/bitbucket-runner/run.sh >> ~/bitbucket-runner/runner.log 2>&1 &
```

`run.sh` must `cd` into the runner `bin/` directory before calling `start.sh` — the runner uses
a relative `./runner.jar` path.

### Runner PATH

The `macos-bash` runtime launches bash non-interactively — Homebrew's `/opt/homebrew/bin` is not
in PATH by default. The build step prepends it explicitly:

```yaml
- export PATH="/opt/homebrew/bin:$PATH"
```

`python3.12` must be installed via `brew install python@3.12`.

### Known issues

| Issue | Cause | Fix |
|---|---|---|
| `git clone` fails: `.git/config: File exists` | Previous failed run left stale build dir | `rm -rf ~/bitbucket-runner/temp/<runner-uuid>/build` |
| Two runner processes conflict | `nohup` started twice | `pkill -f runner.jar` then restart once |
| `git clone` fails: missing `description` in templates | CLT update in progress (transient) | Retry; ensure `init.templateDir` is set to CLT path |

### Runner availability

The runner must be online when a tag pipeline triggers. If it is offline, the macOS build step
queues until it comes back online.

If the runner will be offline for an extended period, the macOS build step can be temporarily
commented out of `bitbucket-pipelines.yml` to unblock the Linux x86_64 + PyPI flow.

---

## Debugging the macOS build

A `debug-macos-build` custom pipeline is defined in `bitbucket-pipelines.yml` for iterating on
macOS build issues without creating a PR per attempt:

1. Push to any branch
2. **Bitbucket UI → Pipelines → Run pipeline → select branch → `debug-macos-build`**
3. No PR, no merge, no tag required

---

## Wheel publishing

Wheels are published to **public PyPI** by the `tags: v*:` pipeline after both builds complete.

| Variable | Purpose |
|---|---|
| `PYPI_API_TOKEN` | PyPI upload token (secured pipeline variable) |
| `BITBUCKET_API_TOKEN` | Used by semantic-release to push version bump commit + tag |

`--skip-existing` is passed to twine — if the same version is uploaded twice (e.g., a re-run),
it silently skips rather than erroring.

---

## Milestone checklist

- [x] Linux x86_64 build on Bitbucket Cloud managed runner
- [x] Nuitka CI hang fixed (`--assume-yes-for-downloads`, `--no-progressbar`)
- [x] Build output visible in CI log (`python -u`)
- [x] macOS ARM64 runner registered and online
- [x] macOS Homebrew PATH fix (`/opt/homebrew/bin` prepended in step)
- [x] PyPI publish wired and verified end-to-end (`PYPI_API_TOKEN`)
- [x] Fly.io deploy wired (`FLY_API_TOKEN`, full path for `flyctl`)
- [x] Automated semantic versioning (`determine_release.py` + `python-semantic-release`)
- [x] Tag-triggered release pipeline (`tags: v*:`)
- [ ] End-to-end test: `fix:` merge → auto-tag → both wheels built → PyPI publish → `pip install revue` succeeds on macOS ARM64
- [ ] Verify `BITBUCKET_API_TOKEN` has write access for tag push (or configure SSH key)
