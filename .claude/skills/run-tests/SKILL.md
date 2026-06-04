---
name: run-tests
model: haiku
description: Run the Revue test suite. Use when the user asks to run tests, check tests, or verify a change. Invoked as /run-tests (full suite) or /run-tests <target> [<target> ...] where target is a file or file::test_name path relative to the repo root.
allowed-tools: Bash
---

Run the Revue pytest suite. The tree spans several disjoint test roots
(`packaging/revue/tests`, `packaging/revue-ci/tests`, `packaging/revue_core/tests`,
`src/web/tests`, and root `tests/`), each run as a separate pytest invocation
against the repo virtualenv at `.venv`.

> **CI note (REVUE-393):** the `src/web/tests` suite (non-e2e) is gated in CI by
> the dedicated `&run-web-tests` step in `bitbucket-pipelines.yml`, which installs
> `src/web/requirements.txt` into an isolated `.venv-web`. That step also runs
> `tests/test_activation_rate_limit.py` (which imports fastapi), so the light
> `&run-tests` step `--ignore`s that file. The `src/web/tests/e2e` subset and the
> slow `tests/integration/test_activation_concurrency.py` stay local-only — do not
> remove the web gate without re-homing that coverage.

## Scripts

All scripts live at `.claude/skills/run-tests/scripts/` relative to the repo root.
Always resolve the absolute path from `SKILL_DIR` before calling them.

```
SKILL_DIR="/Volumes/LexarSSD/Projects/revue.io/.claude/skills/run-tests"
```

---

## Full suite

No arguments — run every test root:

```bash
"$SKILL_DIR/scripts/run_all.sh"
```

Output contract:
- **All pass** → one `<suite>: <pytest summary>` line per suite, then `ALL SUITES PASSED` (exit 0).
- **Any fail** → each failing suite's last ~60 lines, a `SUITE SUMMARY` block, then a nonzero exit (the first failing suite's code).
- A suite that collects zero tests (pytest rc 5) is reported as a `WARNING`, not a failure.

---

## Specific tests

Pass one or more pytest node IDs **relative to the repo root** (not to any
suite). Use the path as it actually sits in the tree:

```bash
# Whole file
"$SKILL_DIR/scripts/run_tests.sh" packaging/revue/tests/test_atomic_version_invariant.py

# Single test
"$SKILL_DIR/scripts/run_tests.sh" tests/test_resolve_cli.py::test_main_invokes_service

# Multiple tests (space-separated)
"$SKILL_DIR/scripts/run_tests.sh" \
  packaging/revue/tests/test_packaging.py::test_foo \
  packaging/revue/tests/test_packaging.py::test_bar
```

`src/` is placed on `PYTHONPATH` only for root `tests/` targets; packaging and
`src/web` targets run without it (to avoid shadowing top-level modules). Don't
mix root `tests/` targets with packaging/web targets in one call — run them
separately (the script warns if you do).

Output: last 30 lines of `pytest -v --tb=long`.

---

## Parsing the user's argument

| User says | Script to call | Argument(s) |
|-----------|---------------|-------------|
| `/run-tests` | `run_all.sh` | — |
| `/run-tests packaging/revue/tests/test_x.py` | `run_tests.sh` | `packaging/revue/tests/test_x.py` |
| `/run-tests tests/test_x.py::test_foo` | `run_tests.sh` | `tests/test_x.py::test_foo` |
| `/run-tests <fileA>::test_foo <fileA>::test_bar` | `run_tests.sh` | both node IDs |

Pass paths through verbatim — `run_tests.sh` does not rewrite or prefix them.

---

## Output format

After running, present:
1. **Result line**: `PASSED`, `FAILED`, or `ERROR` with counts.
2. **Failures**: paste the relevant excerpt if any test failed.
3. **Next step**: suggest a fix or ask the user what to do next.
