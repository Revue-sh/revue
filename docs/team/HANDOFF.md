# Session Handoff - 2026-05-20
**Duration:** ~6h GMT | **Agent:** Claude Opus 4.7 (1M context)

## Session Summary

Autonomous overnight run landed REVUE-310 (extract `revue_core`, rename
`revue` -> `revue-ci`, close skill wheel vendor graph). Tasks 6 through 14
shipped in a single branch + one PR; Task 13 was marked N/A (no public
users yet, no migration to message). The 3-package atomic publish topology
is wired end-to-end with leaf-package AST guard, manifest schema field,
fresh-venv smoke test, and pipeline contract tests. PR #160 open, Jira at
Code Review, 1964 tests passing.

## Project Status

| Metric | Value |
|--------|-------|
| Tickets shipped this session | REVUE-310 (open PR, awaiting review/merge) |
| Tests passing | 1964 (15 + 1827 + 82 + 1 + 39) |
| Open PRs (this session) | #160 |
| Open PRs (stale, pre-session) | #114, #120 |
| Branch HEAD | `b96916a` on `feat/REVUE-310-revue-core-extraction` |

## Completed this session

Chronological, with commit hashes:

- `fa1f196` Task 6 - extract `packaging/revue-ci/`, retire `src/revue/`
  tree; drop dead Nuitka build code under repo-root `build/`.
- `0e1e838` Task 7 - pipeline rewrite for 3-package atomic publish
  (revue_core -> revue-ci -> revue skill); fail-fast publish chain;
  tag-release sed bumps all three pyprojects atomically.
- `392809a` Task 8 - optional `revue_core_min_version` field added to
  the skill manifest schema (backwards-compatible).
- `71c3580` Task 9 - restore per-platform revue-ci Nuitka build
  (responding to Daniel's review feedback about dropped macOS/Linux
  builds); rewrite `docs/distribution/revue-skill-packaging.md` with
  3-package overview and Mermaid diagrams.
- `c50d933` Task 10 - fresh-venv integration smoke test under
  `tests/integration/test_fresh_venv_install.py`, gated by
  `@pytest.mark.slow`.
- `c458864` Task 11 - dedicated leaf-package constraint test at
  `packaging/revue_core/tests/test_leaf_constraint.py` (AST walk +
  side-effect check; precise, no string-match false positives).
- `a09ccdc` Task 12 - pipeline + manifest contract tests:
  `test_three_package_publish.py` and `revue_core_min_version` cases
  in `test_manifest_schema_validates.py`.
- `0c6e233` fix - drop stale `revue_core.tests` reference in
  `test_pipeline.py` that surfaced during the final full-suite run.
- `b96916a` hardening (advisor feedback) - wire fresh-venv smoke into
  CI `&run-tests` step so the test isn't dormant; tighten
  `read_revue_core_constraint()` regex to anchor at the
  `dependencies = [...]` block so a future optional-deps entry can't
  drift the pinned version baked into the compiled wheel METADATA.

## What We Built (Session Highlights)

### revue_core (`packaging/revue_core/`)

The leaf package. 182 modules of shared orchestration moved out of
`src/revue/`. Per-platform Nuitka-compiled wheel for IP protection. pyproject now
declares the real runtime deps (`jsonschema`, `PyYAML`, `tomli_w`,
`httpx`, `anthropic`, `openai`) - earlier extraction left them
implicit, which surfaced during the fresh-venv install. Constraint:
must not import from `revue_skill` or `revue_ci`. Enforced by AST
walk + side-effect check; the latter catches the lazy-upward-import
escape hatch where a top-level grep passes but `from x import y`
inside a function body still creates the cycle on use.

### revue-ci (`packaging/revue-ci/`)

CI / CLI entry point package. `revue-ci = revue_ci.cli:main` console
script. Depends on `revue_core~=0.1.0`. Ships as a per-platform
Nuitka-compiled wheel (macOS ARM64 + Linux x86_64), mirroring the
skill wheel build shape. `cli.py` is the only compile target; revue_core
itself remains a runtime dependency (Nuitka-compiled wheel on PyPI).

NOTE: The Nuitka build scripts at `packaging/revue-ci/build/` were
mirrored from the skill wheel pattern but never executed against a real
Nuitka toolchain in this branch - first real run is the tag pipeline.
Expect possible iteration on these scripts the first time a `v*` tag is
cut.

### revue skill (`packaging/revue/`)

Vendor pipeline rewired to copy source-of-truth from
`packaging/revue_core/src/revue_core/`. `tools/sources.yaml` paths and
the import-rewrite rules updated accordingly. Pre-commit hook at
`.githooks/pre-commit` auto-runs `vendor_sources.py --clean` on every
staged source-of-truth change and fails the commit on drift; the same
file gates direct commits to `main` and `develop`. Manifest schema
extended with optional `revue_core_min_version` so future installers
can refuse to load against an older coexisting `revue_core`.

### Pipeline rewrite (`bitbucket-pipelines.yml`)

Tag pipeline flow:

```
Run Tests
  -> Build revue_core macOS (Nuitka)
  -> Build revue_core Linux (Nuitka)
  -> Build revue-ci macOS (Nuitka)
  -> Build revue-ci Linux (Nuitka)
  -> Build skill macOS (Nuitka)
  -> Build skill Linux (Nuitka)
  -> Publish revue_core -> PyPI
  -> Publish revue-ci -> PyPI
  -> Publish skill -> PyPI
```

Fail-fast left to right via step sequencing. Each publish step
`exit 1`s when its dist/ is empty rather than skipping silently.
Tag-release step bumps all three `pyproject.toml` versions in one
commit so the released triple ships with coherent versions and the
`~=` constraint resolves on the next `pip install`. The PR-pipeline
`&run-tests` step now runs the fresh-venv smoke (`pytest
tests/integration/ -m slow`) so the test isn't dormant.

### Test surfaces added

- `packaging/revue_core/tests/test_leaf_constraint.py` - AST walk +
  side-effect check.
- `packaging/revue/tests/test_three_package_publish.py` - build /
  publish order, per-step `exit 1`, twine + PYPI_API_TOKEN per
  publish, tag-release sed covering all three pyprojects, on-disk
  presence of revue-ci build scripts.
- `packaging/revue/tests/test_manifest_schema_validates.py` extended
  with `revue_core_min_version` cases (optional, semver-only,
  schema declares the field correctly).
- `tests/integration/test_fresh_venv_install.py` - fresh venv,
  install all three packages in dependency order, smoke-import each,
  exercise `revue-ci --help`. Marked `slow`. Wired into CI.

## Remaining Work - Next Steps

1. **Check PR #160 review comments.** First action: pull comments
   via the bitbucket-pr-review skill or `gh api`-equivalent for
   Bitbucket. URL: https://bitbucket.org/cbscd/revue/pull-requests/160
2. **Configure PyPI Trusted Publisher rights for `revue-core` +
   `revue-ci`.** External prerequisite, listed in the dist doc. First
   action: confirm whether the existing `PYPI_API_TOKEN` has
   project-level rights for both new project names, or request new
   tokens. No code change if rights are added to the existing token.
3. **Dry-run the revue_core + revue-ci Nuitka builds locally before
   tagging.** Both packages own
   `packaging/<pkg>/build/{build_nuitka,build_wheel}.py` that have not
   run end-to-end against a real Nuitka toolchain in this branch — first
   real exercise is the tag pipeline. First action: `pip install nuitka
   ordered-set zstandard` into a venv and run each pair (`build_nuitka.py`
   then `build_wheel.py`) for `revue_core/` and `revue-ci/`. revue_core
   compiles 65+ modules in parallel and should produce a wheel under
   `packaging/revue_core/dist/wheels/`. Iterate on errors locally before
   the tag pipeline meets them in CI.
4. **Stale PRs #114, #120** (pre-session) - the "Revue lessons
   learned" chore branches. Triage and close or rebase. First action:
   `gh api repos/cbscd/revue/pulls/114` (or BB equivalent) to read
   their state.
5. **Task 13 follow-up (post-launch only).** No action required now.
   Documented in memory `project_revue_310_no_deprecation.md`.

## Key Architectural Decisions (Session)

1. **revue_core ships Nuitka-compiled per platform (CORRECTED 2026-05-20).**
   The earlier session decision to publish revue_core as a pure-Python
   wheel was wrong — Daniel flagged during PR #160 review that revue_core
   is project IP and must never ship as plain `.py` on PyPI. The branch
   now owns `packaging/revue_core/build/{build_nuitka,build_wheel}.py`
   matching the revue-ci shape; the tag pipeline builds revue_core on
   macOS ARM64 + Linux x86_64 before the dependents. Editable dev
   installs (`pip install -e packaging/revue_core/`) still use plain
   source. The IP-protection requirement is captured in CLAUDE.md.
2. **revue-ci publishes per-platform Nuitka wheels, not pure-Python.**
   Reversed mid-session after Daniel flagged that the dropped
   `&build-macos` / `&build-linux` symmetry should be restored. The
   compile target is just `cli.py`; revue_core stays a runtime dep.
3. **Task 13 (revue v0.6.x DeprecationWarning patch) marked N/A.**
   No public users exist; a deprecation cycle is only meaningful with
   real consumers to migrate. A separate ticket will handle migration
   messaging post-launch if needed. Documented in memory + story file.
4. **Three pyprojects bump atomically.** The tag-release sed updates
   all three `pyproject.toml`s in one commit so revue-ci and revue
   can pin `revue_core~={NEXT_VERSION}` and the constraint resolves
   on the next `pip install`. Releases ship a coherent triple.

## Session Stats

- Duration: ~6h GMT
- Stories: 1 completed (REVUE-310; Tasks 6-14 minus N/A 13)
- Commits: 9
- Tests: 1964 passing
- PRs opened: #160
- Party mode agents used: none - solo autonomous run; advisor
  consulted twice (before substantive work, before declaring done)

## Continuation Prompt (Next Session)

```
Continuing from the REVUE-310 overnight run.
- PR #160 open, Jira at Code Review, 1964 tests passing.
- Branch HEAD b96916a on feat/REVUE-310-revue-core-extraction.
- First action: check PR #160 review comments via the
  bitbucket-pr-review skill or BB API.
- Pre-merge verifications outstanding: PyPI Trusted Publisher rights
  for revue-core + revue-ci; first revue-ci Nuitka build runs on the
  first v* tag - expect to debug iteratively.
- Read docs/team/HANDOFF.md for full context.
```
