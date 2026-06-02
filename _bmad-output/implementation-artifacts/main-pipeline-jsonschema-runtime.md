# Story: Repair main pipeline web deploy startup failure

Status: review

## Story

As a Revue maintainer,
I want the web image runtime dependencies to include every package imported during application startup,
so that the Bitbucket `main` pipeline can deploy the `REVUE-374` image to Fly staging without the app crashing.

## Acceptance Criteria

1. `src/web/requirements.txt` declares `jsonschema`, because `src/web/services/manifest_builder.py` imports `jsonschema.Draft202012Validator` during web app startup.
2. A regression test fails when a third-party module imported by the web application is absent from `src/web/requirements.txt`, and passes when `jsonschema` is declared.
3. The relevant web test suite passes after the dependency declaration is added.
4. The fix does not change the manifest endpoint behaviour, Fly promotion chain, or unrelated packaging configuration.

## Tasks / Subtasks

- [x] Add a failing runtime dependency contract test (AC: 1, 2)
  - [x] Assert that `src/web/requirements.txt` declares `jsonschema`
  - [x] Run the focused test and capture the expected red failure
- [x] Add the missing web runtime dependency (AC: 1, 4)
  - [x] Declare `jsonschema` in `src/web/requirements.txt`
  - [x] Run the focused test and capture the green result
- [x] Verify the repaired web app dependency contract (AC: 3, 4)
  - [x] Run the web test suite
  - [x] Record verification results and changed files

## Dev Notes

### Failure Evidence

- Bitbucket pipeline log `pipelineLog-{e849cdfc-7427-40fb-ba90-8cff441d3382}.txt` fails during `Deploy Web -> Staging`.
- Fly finds and starts image `registry.fly.io/revue-staging:eb5d868c3b1f9e03327d2f8bdc9f8a3573200825`, then reports: `smoke checks ... failed: the app appears to be crashing`.
- Commit `eb5d868` added `from jsonschema import Draft202012Validator` in `src/web/services/manifest_builder.py`.
- `src/web/requirements.txt` does not declare `jsonschema`, so a clean Docker image lacks the imported runtime package.
- A local import can pass when the developer environment already has `jsonschema`; the regression test must inspect the clean-image dependency contract rather than rely on the ambient interpreter.

### Scope Boundaries

- Do not change `bitbucket-pipelines.yml`: the pipeline is exposing an application image dependency defect, not a Fly deploy command defect.
- Do not change manifest endpoint mapping, caching, schema validation, or routing logic.
- Do not add a broad dependency scanner in this repair. Add a focused contract assertion for the missing startup dependency.
- Do not modify the existing `sec/REVUE-377-install-skill-trust-model-doc` checkout or its untracked scripts.

### Architecture And Testing Constraints

- Preserve the route -> service -> infrastructure layering established by `REVUE-374`.
- Follow TDD: add the failing assertion before adding `jsonschema` to `src/web/requirements.txt`.
- Pipeline and cross-platform fixes require failure-path evidence in addition to unit tests. The supplied Bitbucket log is the red deploy evidence; the focused contract test is the repeatable local guard.

### References

- `src/web/services/manifest_builder.py`
- `src/web/requirements.txt`
- `src/web/Dockerfile`
- `bitbucket-pipelines.yml`
- `docs/guides/testing.md`
- `_bmad-output/implementation-artifacts/revue-374-version-manifest-endpoint.md`

## Dev Agent Record

### Agent Model Used

GPT-5 Codex

### Debug Log References

- 2026-06-02: Read the Bitbucket failure log. Fly staging deploy starts image `eb5d868`, then reports that the app is crashing during smoke checks.
- 2026-06-02: Traced startup imports from `src/web/main.py` to `src/web/services/manifest_builder.py`, which imports `jsonschema.Draft202012Validator`. Confirmed `src/web/requirements.txt` omitted `jsonschema`.
- 2026-06-02: RED: added `test_web_runtime_requirements_include_jsonschema_for_manifest_builder_startup`; focused run failed because `jsonschema` was absent from the declared runtime requirements.
- 2026-06-02: GREEN: added `jsonschema>=4.0.0`; focused runtime dependency test passed.
- 2026-06-02: Verified startup import with the complete root virtualenv: `startup-import-ok`.
- 2026-06-02: Verified manifest-focused tests: `11 passed`.
- 2026-06-02: Verified existing main-pipeline promotion contract tests: `8 passed`.
- 2026-06-02: Verified web tests excluding browser E2E: `202 passed, 1 failed`. The sole failure is the pre-existing `test_dashboard.py::test_landing_page` marketing-copy assertion; reproduced unchanged in the untouched checkout.
- 2026-06-02: Ran the canonical full-suite skill. Existing unrelated failures remain in `packaging/revue-ci/tests` and legacy root `tests`; sandbox policy also prevents browser E2E from binding localhost.

### Completion Notes List

- Added the missing `jsonschema` runtime dependency installed by `src/web/Dockerfile`.
- Added a clean-image requirements contract test so ambient developer packages cannot hide this startup dependency regression.
- Kept the Fly deploy chain and manifest endpoint implementation unchanged.

### File List

- `_bmad-output/implementation-artifacts/main-pipeline-jsonschema-runtime.md`
- `src/web/requirements.txt`
- `src/web/tests/test_runtime_dependencies.py`

## Change Log

- 2026-06-02: Created maintenance story from Bitbucket staging deploy crash evidence.
- 2026-06-02: Added `jsonschema` web runtime dependency and focused regression guard.
