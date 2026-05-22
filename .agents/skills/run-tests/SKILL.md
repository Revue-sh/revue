---
name: "run-tests"
description: "Run the Revue test suite. Use when the user asks to run tests, check tests, or verify a change. Invoked as /run-tests (full suite) or /run-tests <target> [<target> ...] where target is a file or file::test_name relative to src/revue/tests/."
---

Run the Revue pytest suite from `src/`.

## Scripts

All scripts live at `.claude/skills/run-tests/scripts/` relative to the repo root.
Always resolve the absolute path from `SKILL_DIR` before calling them.

```
SKILL_DIR="/Volumes/LexarSSD/Projects/revue.io/.claude/skills/run-tests"
```

---

## Full suite

No arguments — run everything:

```bash
"$SKILL_DIR/scripts/run_all.sh"
```

Output: full `pytest -q --tb=short`. Passing tests are dots; failures print a short traceback.

---

## Specific tests

Pass one or more pytest node IDs relative to `src/revue/tests/`:

```bash
# Whole file
"$SKILL_DIR/scripts/run_tests.sh" comments/test_service.py

# Single test
"$SKILL_DIR/scripts/run_tests.sh" comments/test_service.py::test_collect_threads_uses_platform_for_store_lookup

# Multiple tests (space-separated)
"$SKILL_DIR/scripts/run_tests.sh" \
  core/test_pipeline.py::test_foo \
  core/test_pipeline.py::test_bar
```

The script also accepts full paths starting with `revue/tests/` — it won't double-prefix them.

Output: last 30 lines of `pytest -v --tb=long`.

---

## Parsing the user's argument

| User says | Script to call | Argument(s) |
|-----------|---------------|-------------|
| `/run-tests` | `run_all.sh` | — |
| `/run-tests comments/test_service.py` | `run_tests.sh` | `comments/test_service.py` |
| `/run-tests comments/test_service.py::test_foo` | `run_tests.sh` | `comments/test_service.py::test_foo` |
| `/run-tests core/test_pipeline.py::test_foo core/test_pipeline.py::test_bar` | `run_tests.sh` | both node IDs |

Strip any leading `src/revue/tests/` the user may have typed — the script normalises it.

---

## Output format

After running, present:
1. **Result line**: `PASSED`, `FAILED`, or `ERROR` with counts.
2. **Failures**: paste the relevant excerpt if any test failed.
3. **Next step**: suggest a fix or ask the user what to do next.

## MANUAL MIGRATION REQUIRED

Claude `allowed-tools` was preserved as prompt guidance, not a Codex permission boundary.

You're allowed to use these tools:

- Bash

Review unsupported Claude skill fields manually: `model`.
