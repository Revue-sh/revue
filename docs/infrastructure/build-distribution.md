# Build Distribution — Production Readiness

**Status:** In Progress
**Updated:** 2026-04-19

---

## Overview

The Bitbucket Pipelines build matrix compiles `revue/core/` to native `.so` binaries and packages
them into platform-specific wheels. Two infrastructure gaps must be closed before compiled wheels
reach customers.

---

## Gap 1 — Self-hosted runners for ARM64 and macOS

The Linux x86_64 build runs on Bitbucket Cloud. The other two platforms require self-hosted runners
registered in the Bitbucket workspace.

### Required runners

| Pipeline step | Runner labels required | Host OS |
|---|---|---|
| Build Linux ARM64 | `self.hosted`, `linux.arm64` | Ubuntu 22.04+ on ARM64 hardware or VM |
| Build macOS ARM64 | `self.hosted`, `macos.arm64` | macOS 14+ on Apple Silicon |

### Registration steps

1. In Bitbucket workspace settings → **Runners**, create a new runner for each platform.
2. Bitbucket generates a runner token and a Docker run command (Linux) or shell script (macOS).
3. Execute the command on the target host — the runner agent registers itself and begins polling.
4. Verify the runner appears as **Online** in workspace settings before triggering a build.

Reference: [Bitbucket Pipelines self-hosted runners](https://support.atlassian.com/bitbucket-cloud/docs/runners/)

### Current behaviour without runners

Steps tagged with unregistered labels queue indefinitely. They are marked `fail-fast: false` so
they do not block the x86_64 build or the Collect Artifacts step. The missing wheels are silently
absent from `dist/wheels/`.

---

## Gap 2 — Wheel registry publishing

After the build matrix completes, wheels exist only as ephemeral Bitbucket pipeline artifacts.
No publish step exists yet. The "Collect Artifacts" step in `bitbucket-pipelines.yml` is a
placeholder (`ls -la dist/wheels/`).

### Decision required: registry target

Choose one of the following before implementing:

| Option | Pros | Cons |
|---|---|---|
| **Bitbucket Downloads** (workspace-level) | Zero infrastructure, built-in to Bitbucket | No access control per tier; not a standard pip index |
| **Private PyPI (pypiserver / Artifactory / Gemfury)** | Standard `pip install`, per-token auth, tier gating possible | Requires hosting or paid SaaS |
| **PyPI (public)** | Easiest customer install | Source wheel would expose compiled binaries publicly; no tier gating |
| **GitHub Releases (cbscd/revue-dist)** | Simple, pip can install from GitHub releases | Non-standard, fragile URLs |

Recommendation: private PyPI index (Gemfury or self-hosted `pypiserver`) — standard install UX,
supports per-customer tokens for tier enforcement.

### Publish step to add (once registry chosen)

Add the following after the parallel build matrix in `bitbucket-pipelines.yml`:

```yaml
- step:
    name: "Publish Wheels"
    script:
      - pip install twine
      - twine upload
          --repository-url $PYPI_REGISTRY_URL
          --username $PYPI_USERNAME
          --password $PYPI_PASSWORD
          dist/wheels/*.whl
    artifacts:
      - dist/wheels/*.whl
```

Repository variables to add in Bitbucket:

| Variable | Value |
|---|---|
| `PYPI_REGISTRY_URL` | URL of chosen private index |
| `PYPI_USERNAME` | Registry username or `__token__` |
| `PYPI_PASSWORD` | Registry token (mark as **secured**) |

---

## Milestone checklist

- [ ] ARM64 Linux runner registered and online
- [ ] macOS ARM64 runner registered and online
- [ ] Registry target decided and provisioned
- [ ] `PYPI_REGISTRY_URL`, `PYPI_USERNAME`, `PYPI_PASSWORD` added as secured pipeline variables
- [ ] Publish step added to `bitbucket-pipelines.yml`
- [ ] End-to-end test: push to main → all three wheels built → published → `pip install revue` from registry succeeds on each platform
