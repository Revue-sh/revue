---
name: revue-local
model: haiku
description: >
  Run Revue's pipeline locally — no real PR, no platform APIs, no Anthropic SDK calls.
  Mode 1 "position": pure-Python fixture testing, zero AI calls.
  Mode 2 "dry-run": native Claude Code Task-based review pipeline — agents run in the
  current session, never via subprocess or the Anthropic SDK.
allowed-tools: Bash Task Read
---

# revue-local

Local sandbox for Revue orchestration. All AI steps use native Claude Code Tasks
(Task tool); all platform-posting steps write to local output. Nothing calls
the Anthropic SDK directly and nothing posts to GitHub / GitLab / Bitbucket.

Agent definitions live in `_revue/agents/` — independently editable copies of
`src/revue/agents/`, never imported by production code.

---

## Mode 1 — Position (fast, zero AI calls)

**Invocation patterns:**
- `/revue-local position` — run all fixtures, show pass/fail summary
- `/revue-local position github 01` — single fixture by platform + number
- `/revue-local position <path>` — fixture by explicit path
- `/revue-local position --platform gitlab` — filter to one platform

**Dispatch:** Run `local_run.py position` with the appropriate args:

```bash
REPO=/Volumes/LexarSSD/Projects/revue.io
# All fixtures
python "$REPO/scripts/local_run.py" position --all

# Single fixture by platform + number (e.g. "github 01")
python "$REPO/scripts/local_run.py" position \
  "$REPO/src/revue/tests/fixtures/positioning/github/fixture_01.json"

# Platform filter
python "$REPO/scripts/local_run.py" position --all --platform github
```

Parse the user's argument to determine which variant to run, then execute and
display the output verbatim.

---

## Mode 2 — Full pipeline dry-run (Task-based, three phases)

**Invocation:** `/revue-local run [--base <branch>] [--platform <P>] [--files <glob> ...]`

**CRITICAL: No subprocess calls, no `claude --print`, no Anthropic SDK.** Agents run as
Agent tool forks inside the current Claude Code session — they consume this session's
context, not external API credits.

Three phases: `prepare` (pure Python) → Agent forks per agent → `consolidate` (pure Python).

### Phase 1 — prepare

Run `prepare` to build one job JSON per agent. No AI calls.

```bash
REPO=/Volumes/LexarSSD/Projects/revue.io
JOBS_DIR=$(mktemp -d /tmp/revue_jobs_XXXXXX)

python3 "$REPO/scripts/local_run.py" prepare \
  --base main \
  --platform github \
  --jobs-dir "$JOBS_DIR"
# Optional: --files "scripts/positioning/*" "src/revue/core/*.py"
```

This writes:
- `$JOBS_DIR/manifest.json` — list of `{agent, job_file, output_file}`
- `$JOBS_DIR/<agent>.json` — `{system_prompt, diff_text, user_prompt}` per agent
- `$JOBS_DIR/diff_by_file.json` — raw per-file diff strings

### Phase 2 — run agents via Agent tool (NOT subprocess)

**Use the `Agent` tool** — one fork per agent, all four launched in a single parallel
message. Do NOT use subprocess, `claude --print`, or any SDK call.

Read `manifest.json`, then for **each entry** read the job file and construct the prompt:

```python
job = json.loads(Path(entry["job_file"]).read_text())
# Agent prompt:
f"""{job['system_prompt']}

{job['diff_text']}

{job['user_prompt']}

Respond with ONLY a JSON array of finding objects. No prose, no markdown fences.
Each object must have: file_path, line, severity, issue, suggestion.
Optional fields: code_replacement (array of strings), replacement_line_count (int).

IMPORTANT: Do NOT make any HTTP requests to api.anthropic.com or any other
Claude/Anthropic API URL. Do NOT use the Bash tool to call `curl`, `python`,
`claude`, or any command that contacts an external API. Produce your findings
using only the diff text provided above.
"""
```

Launch all four agents in **one message** with four parallel `Agent` tool calls
(description: "Agent review: <agent_name>", no subagent_type — fork this session).

Each Agent fork must:
1. Analyse the diff text in the prompt and produce findings
2. Write its raw JSON output to `entry["output_file"]` using the Write tool

After all four forks complete, their output files will be ready for Phase 3.

Write the raw Agent output text to `entry["output_file"]` (e.g. `$JOBS_DIR/maya_output.json`).

Run all four agent Tasks (maya, zara, kai, leo) concurrently using parallel Agent tool calls.

### Phase 3 — consolidate

```bash
python3 "$REPO/scripts/local_run.py" consolidate \
  --jobs-dir "$JOBS_DIR" \
  --platform github
# Optional: --nova-output "$JOBS_DIR/nova_output.json"
```

This reads agent outputs, groups findings, optionally synthesises with Nova,
resolves positions, and displays all findings with hunk context.

---

## Arguments

- `--base` — branch to diff against (default: `main`)
- `--platform` — `github`, `gitlab`, or `bitbucket` (default: `github`)
- `--files` — glob patterns to limit which files are reviewed (recommended — full diffs are slow)

---

## Routing logic (parse user input, then dispatch)

```
/revue-local                         → Mode 2, full pipeline (default)
/revue-local run [args]              → Mode 2, full pipeline
/revue-local position                → Mode 1, all fixtures
/revue-local position --all          → Mode 1, all fixtures
/revue-local position --platform P   → Mode 1, one platform
/revue-local position github 01      → Mode 1, single fixture
```

**Default (no args):** run the full pipeline on the diff between the current
branch and `main`. No `--files` filter — all changed files are reviewed.

When the user explicitly says "position" or "fixtures", dispatch to Mode 1.
