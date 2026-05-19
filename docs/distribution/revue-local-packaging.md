# revue-local — Packaging & Signed Releases

Internal guide for the team. Covers how the `revue-local` Python wheel is built, signed, and published.

## Tree layout

```text
packaging/revue-local/
├── pyproject.toml              # Hatch build config; entry point `revue-local`
├── README.md                   # End-user readme (rendered on PyPI)
├── manifest.schema.json        # JSON Schema for the served manifest
├── manifest.example.json       # Sample manifest (used by tests)
├── src/
│   └── revue_local/
│       ├── __init__.py         # version constant
│       ├── cli.py              # `revue-local install-skill | verify | version`
│       ├── install.py          # copies bundled skill into ~/.claude/skills
│       ├── manifest.py         # jsonschema validation
│       ├── sigstore_verify.py  # Sigstore artefact verification
│       ├── skill/              # ⟵ vendored at build time by tools/vendor_sources.py
│       └── vendored/           # ⟵ vendored at build time
├── tools/
│   ├── sources.yaml            # source-of-truth → vendored target mapping
│   └── vendor_sources.py       # vendor tool (run before `python -m build`)
└── tests/
    ├── test_wheel_publishes_to_pypi.py
    ├── test_release_artefact_signed.py
    ├── test_manifest_schema_validates.py
    ├── test_signature_verification_in_installer.py
    ├── test_agent_prompts_packaged.py
    └── test_vendored_sources_in_sync.py
```

## Vendoring policy

The wheel must work offline within the 24-hour licence cache window. To avoid a runtime
dependency on the `revue` PyPI package (not currently published), the orchestration
modules below are **vendored into the wheel** by `tools/vendor_sources.py`:

| Source-of-truth                                  | Vendored as                                            |
|--------------------------------------------------|--------------------------------------------------------|
| `src/revue/comments/position_adapter.py`         | `revue_local.vendored.position_adapter`                |
| `src/revue/core/terminal_state.py`               | `revue_local.vendored.terminal_state`                  |
| `src/revue/core/logging_channels.py`             | `revue_local.vendored.logging_channels`                |
| `src/revue/core/finding_schema.py`               | `revue_local.vendored.finding_schema`                  |
| `src/revue/core/display.py`                      | `revue_local.vendored.display`                         |
| `src/revue/core/log.py`                          | `revue_local.vendored.log`                             |
| `scripts/positioning/*.py`                       | `revue_local.vendored.positioning_adapters.*`          |
| `.claude/skills/revue-local/SKILL.md`            | `revue_local.skill.SKILL.md`                           |
| `scripts/local_run.py`                           | `revue_local.skill.local_run.py` (with import rewrites)|
| `_revue/{agents,adapters,clients}/`              | `revue_local.skill._revue.…`                           |

`test_vendored_sources_in_sync.py` re-runs the vendor tool into a temp dir and fails CI on drift.

## Build

```bash
cd packaging/revue-local
python tools/vendor_sources.py --clean   # populate src/revue_local/{skill,vendored}
python -m build                          # writes wheel + sdist into dist/
```

## Local smoke test

```bash
pip install dist/revue_local-*.whl
revue-local install-skill --skip-verify --target-dir /tmp/skills
ls /tmp/skills/revue-local/   # SKILL.md, local_run.py, _revue/
```

## Release flow (CI)

A tag `revue-local-v<semver>` triggers `.github/workflows/revue-local-release.yml`:

1. **build** — vendor → `python -m build` → upload wheel + sdist.
2. **sign** — Sigstore OIDC certificate via `sigstore/gh-action-sigstore-python@v3`. Produces `.sigstore` bundles next to the artefacts.
3. **release** — attach wheel + sdist + bundles to the GitHub release.
4. **publish-pypi** — push the wheel to PyPI via Trusted Publishing (OIDC, no API token).

## Manifest schema

`manifest.schema.json` documents the version manifest served at
`https://revue.io/skills/manifest.json` (pre-MVP fallback:
`https://raw.githubusercontent.com/cbscd/revue/main/manifest.json`).

The install script fetches this manifest, validates it against the schema, then verifies the
declared wheel hash + Sigstore signature before copying the skill into the user's home.

## External prerequisites (deferred from REVUE-275)

These are intentionally NOT met by this PR — they require human setup:

- [ ] **PyPI Trusted Publisher** for `revue-local` (configured under `pypa/gh-action-pypi-publish` env `pypi`)
- [ ] **Public repo** `github.com/revue-io/revue-local` (or interim host on this repo)
- [ ] **`revue-io` GitHub org** created with the maintainer team

Once those exist, tag `revue-local-v0.1.0` and the workflow does the rest.

## Known gap — vendor graph not yet fully closed

`scripts/local_run.py` has been vendored with **top-level** import rewrites (it now imports
`revue_local.vendored.position_adapter` etc.). It also contains ~30 **late imports**
inside function bodies that still resolve to `revue.core.*` and `revue.comments.*` modules
which have not been vendored.

Practical effect: the wheel is installable and `revue-local install-skill` drops the bundled
SKILL.md + orchestrator into `~/.claude/skills/`, but the Mode-2 dry-run path will fail at
runtime unless the user also has the `revue.io` source tree on `PYTHONPATH`. Mode-1
position fixtures work standalone.

Two follow-up paths to close the gap (file as a separate Jira story):

1. **Expand vendoring** — chase the late-import graph and add every reachable module to
   `tools/sources.yaml`. Estimated 10–20 additional modules.
2. **Depend on the `revue` PyPI package** — publish the main package, declare it as a
   dependency in this `pyproject.toml`. Cleaner long-term, but blocked on the same PyPI
   account prereq as REVUE-275.

`tests/test_vendored_sources_in_sync.py::test_vendor_manifest_covers_all_late_imports_in_local_run`
is a sentinel test that keeps this gap visible. Remove it once the graph is closed.

## Updating after upstream change

Any change to a source-of-truth file listed above requires re-vendoring before merge:

```bash
python packaging/revue-local/tools/vendor_sources.py --clean
git add packaging/revue-local/src/revue_local/{skill,vendored}
```

Skipping this step trips `test_vendored_sources_in_sync.py` in CI.
