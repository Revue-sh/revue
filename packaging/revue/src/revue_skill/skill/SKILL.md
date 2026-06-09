---
name: revue
model: haiku
description: >
  Run Revue's AI code review pipeline locally — no real PR, no platform APIs, no Anthropic SDK calls.
  Agents run in the current session, never via subprocess or the Anthropic SDK.
allowed-tools: Bash Task Read
---

# revue

Runs Revue's code review pipeline locally. All AI steps use native Claude Code Agent
tool forks inside the current session — no Anthropic SDK calls, no platform APIs, no
posting to GitHub / GitLab / Bitbucket.

Agent definitions live in `_revue/agents/` — independently editable copies of
`src/revue/agents/`, never imported by production code.

---

## Pipeline — full review (Task-based, three phases)

**Invocation:** `/revue [--base <branch>] [--platform <P>] [--files <glob> ...]`

**CRITICAL: No subprocess calls, no `claude --print`, no Anthropic SDK.** Agents run as
Agent tool forks inside the current Claude Code session — they consume this session's
context, not external API credits.

Three phases: `prepare` (pure Python) → Agent forks per agent → `consolidate` (pure Python).

### Phase 1 — prepare

Run `prepare` to build one job JSON per agent. No AI calls.

```bash
JOBS_DIR=$(mktemp -d /tmp/revue_jobs_XXXXXX)

revue local-run prepare \
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

**Dispatch by file path, never by inline content.** Read `manifest.json` and pass
each Agent fork ONLY the job-file and output-file paths from the manifest entry.
The fork itself reads the job file with the Read tool. Do NOT construct an
f-string that embeds `system_prompt + diff_text + user_prompt` and paste it into
the Agent prompt — diffs are typically 50KB+ per agent, and an orchestrator that
abbreviates, truncates, or pastes a placeholder for any of the four agents silently
drops that agent's review coverage. File-path dispatch makes the diff physically
impossible to drop: the fork cannot proceed without reading the file.

**Exact prompt shape for each Agent fork** (substitute only the two paths from
`manifest.json`):

> Read the JSON file at `<entry.job_file>`. It contains three string fields:
> `system_prompt`, `diff_text`, and `user_prompt`. Treat `system_prompt` as your
> reviewer instructions, `diff_text` as the code to review, and `user_prompt` as
> the response-format directive.
>
> Respond with ONLY a JSON array of finding objects. No prose, no markdown fences.
> Each object must have: file_path, line, severity, issue, suggestion. Optional
> fields: code_replacement (array of strings), replacement_line_count (int).
> Write the raw JSON to `<entry.output_file>` using the Write tool.
>
> IMPORTANT: Do NOT make any HTTP requests to api.anthropic.com or any other
> Claude/Anthropic API URL. Do NOT use the Bash tool to call `curl`, `python`,
> `claude`, or any command that contacts an external API. Produce your findings
> using only the `diff_text` field in the job file.

**Tool allowlist for the fork** (mirrors Phase 3b's Vex pattern): the fork must
be spawned with `allowed-tools: Read Write`. Specifically:
- No `Bash` — the fork has nothing to execute; reasoning is in-context.
- No `Agent` — recursive Agent spawning is forbidden.
- No `Grep`, `WebFetch`, network tools, or anything else.

Launch all four agents (maya, zara, kai, leo) in **one message** with four parallel
`Agent` tool calls (description: "Agent review: <agent_name>", no subagent_type —
fork this session). After all four forks have written their output files, proceed
to Phase 3a.

### Phase 3 — split into 3a → 3b (Agent forks) → 3c for production-parity Vex

Phase 3 is split so the Vex semantic verifier runs at **zero Anthropic API cost**.
Prompt construction (3a) and verdict application (3c) stay in subprocess Python;
the LLM step (3b) is externalised to orchestrator Agent forks in this session.

#### Phase 3a — classify, consolidate, build Vex job files

```bash
revue local-run classify-and-build-vex-jobs \
  --jobs-dir "$JOBS_DIR" \
  --max-vex-forks 20
# Optional: --nova-output "$JOBS_DIR/nova_output.json"
```

This writes:
- `$JOBS_DIR/consolidated_findings_snapshot.json` — serialised ConsolidatedFinding list
- `$JOBS_DIR/vex_jobs/manifest.json` — `{"jobs": [{"finding_index", "job_file", "output_file"}, ...], "skipped_indices": [...]}`
- `$JOBS_DIR/vex_jobs/vex_job_<i>.json` — one per finding-with-code_replacement: `{"system_prompt", "user_prompt", "finding_index", "output_file_path"}`

Findings with `code_replacement=None` skip Vex (prose-only — nothing to verify).
If more than `--max-vex-forks` findings need verification, the first N are
processed and the rest pass through unmodified (stderr warning emitted).

#### Phase 3b — spawn one Agent fork per Vex job

**Read the Vex manifest:**

```python
vex_manifest = json.loads(Path(f"{JOBS_DIR}/vex_jobs/manifest.json").read_text())
```

**For each entry in `vex_manifest["jobs"]`,** spawn a parallel `Agent` tool call.
Each fork must:

1. Read its job file (`entry["job_file"]`) for `system_prompt` + `user_prompt`.
2. Treat `system_prompt` as the system message and `user_prompt` as the user message
   (these are byte-identical to the production Vex prompt — do NOT paraphrase or add prose).
3. Emit ONLY a JSON verdict object with the schema:
   ```json
   {
     "verdict": "apply" | "drop_cr_keep_prose" | "reject_finding",
     "reason": "<one sentence>",
     "corrected_anchor": null | {"line": <int>, "replacement_line_count": <int>}
   }
   ```
4. Write that JSON to the path declared at `entry["output_file"]` (which the
   job file also records as `output_file_path`) using the Write tool.

**Constraints for the fork:**
- Do NOT use the Agent tool recursively.
- Do NOT make HTTP requests to api.anthropic.com or any external API.
- Do NOT post anything anywhere.
- Reasoning happens in the fork; the only side-effect is the single Write call.

**Tool allowlist for the fork** (mirrors the reviewer-tool restriction):
The fork must be spawned with `allowed-tools: Read Write` — only these two tools
are necessary to read the job file and emit the verdict JSON. Specifically:
- No `Bash` — the fork has nothing to execute; reasoning is in-context.
- No `Agent` — recursive Agent spawning is forbidden (see above).
- No `Grep`, `WebFetch`, network tools, or anything else — verdict construction
  is a pure read-then-write pipeline.

Forks that violate the allowlist must be considered tainted: the verdict cannot
be trusted, and 3c should treat such verdicts the same as a missing file
(passthrough). The allowlist is enforced at spawn time via the Agent tool's
parameters, not at verdict-parsing time.

**Spawn all forks in one message** so they run concurrently (one parallel
`Agent` tool call per entry, description "Vex verdict: finding N").

The cap `--max-vex-forks 20` (default) is enforced in Phase 3a — the orchestrator
will never see more than that many job entries in one run. Findings beyond the
cap are listed in `vex_manifest["skipped_indices"]` and pass through unmodified
in Phase 3c.

#### Phase 3c — apply verdicts and render

After all Vex forks have written their verdict JSONs, run:

```bash
revue local-run apply-verdicts-and-finalize \
  --jobs-dir "$JOBS_DIR" \
  --platform github
```

This reads the snapshot + verdicts, applies each via
`VexVerifyPostProcessor._apply_verdict` (production code path), runs
`OrphanLineGuardPostProcessor` as the backstop, then renders findings with
positions and hunk context. Missing or malformed verdict files fail open
(finding passes through unmodified — mirrors production Vex fail-open semantics).

**Legacy fallback:** the original `consolidate` subcommand still exists for
quick smoke tests without Vex. New work should use 3a → 3b → 3c.

---

## Arguments

- `--base` — branch to diff against (default: `main`)
- `--platform` — `github`, `gitlab`, or `bitbucket` (default: `github`)
- `--files` — glob patterns to limit which files are reviewed (recommended — full diffs are slow)

---

## Routing logic

```
/revue           → run the full review pipeline (default)
/revue [args]    → run the full review pipeline with args
```

**Default (no args):** run the full pipeline on the diff between the current
branch and `main`. No `--files` filter — all changed files are reviewed.

---

## Troubleshooting

If you encounter an error while installing, activating, or running Revue, the error message will include:

> Need help? Email support@revue.sh

We'd like to help you resolve the issue. Please reach out with a description of what you were trying to do and the full error message. We'll respond quickly.
