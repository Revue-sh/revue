---
name: codex-bmad-fanout
description: 'Generates a Codex-ready prompt that explicitly instructs Codex to spawn sub-agents in parallel for BMad multi-agent skills (bmad-code-review, bmad-party-mode, bmad-review-adversarial-general, bmad-correct-course). Use when the user wants to run a BMad multi-agent flow on headless Codex (e.g. "run bmad-code-review on codex", "fanout bmad-party-mode to codex", "/codex-bmad-fanout <skill>"). Compensates for Codex not auto-delegating sub-agents by description (which Claude Code does).'
---

# codex-bmad-fanout

Codex's headless mode doesn't auto-delegate to sub-agents based on description metadata the way Claude Code does. This skill produces an explicit, structured Codex prompt that names each sub-agent, points Codex at its `.agents/skills/bmad-agent-*/SKILL.md`, and tells Codex to fan them out in parallel and synthesize their outputs.

The output prompt is designed to be passed to `./scripts/codex-revue.sh '<prompt>'` (see [[reference_codex_exec_recipe]]).

## When to use

The user says:
- "run bmad-code-review on codex"
- "fanout <bmad-skill> to codex"
- "codex bmad-party-mode"
- "/codex-bmad-fanout <skill>"
- Or any phrasing that asks to run a multi-agent BMad skill headlessly on Codex.

Only fanout these BMad skills (they spawn sub-agents). Other BMad skills are single-agent and don't need this — invoke them directly via `./scripts/codex-revue.sh`:

| BMad skill | Sub-agents fanned out |
|---|---|
| `bmad-code-review` | bmad-agent-blind-hunter, bmad-agent-edge-case-hunter, bmad-agent-pm (acceptance auditor layer) |
| `bmad-party-mode` | All agents in `_bmad/_config/agent-manifest.csv` (dynamic) |
| `bmad-review-adversarial-general` | bmad-agent-blind-hunter, bmad-agent-edge-case-hunter |
| `bmad-correct-course` | bmad-agent-pm, bmad-agent-sm, bmad-agent-architect |

If a user asks to fanout a skill not in this table, scan its `.claude/skills/<skill>/workflow.md` for explicit agent references; if none found, tell the user it's already a single-agent skill and recommend invoking it directly via `codex-revue.sh`.

## Steps

### 1. Identify the source skill and sub-agent roster

- Resolve the skill name from the user's request.
- Look up the sub-agent roster from the table above (or scan `workflow.md` for unknown skills).
- For `bmad-party-mode`, read `_bmad/_config/agent-manifest.csv` and use the `name` column as the roster.

### 2. Collect each sub-agent's role excerpt

For each agent in the roster, extract the role line from `.agents/skills/<agent>/SKILL.md` (first non-frontmatter sentence, or the `**Your Role:**` line from `workflow.md` if present). Keep each excerpt under 200 chars.

### 3. Compose the Codex prompt

Use this template (substitute placeholders):

```
You are orchestrating a {SOURCE_SKILL} run. Codex does NOT auto-delegate sub-agents — you must explicitly spawn each one in parallel using your subagent mechanism, then synthesize.

CONTEXT FROM CALLER:
{CALLER_CONTEXT}

STEP 1 — Spawn the following {N} sub-agents IN PARALLEL (single message, multiple agent tool uses):

{FOR EACH AGENT}
  - Agent: {AGENT_NAME}
    SkillFile: .agents/skills/{AGENT_NAME}/SKILL.md
    Role: {ROLE_EXCERPT}
    Task: {AGENT_SPECIFIC_TASK}   # see "agent-specific tasks" below
{END FOR}

Each sub-agent MUST:
  1. Load and follow the instructions in its SkillFile.
  2. Operate independently — do not coordinate with other sub-agents during the run.
  3. Return a structured finding list with severity (critical / high / medium / low / info), file:line references where applicable, and a one-line rationale per finding.

STEP 2 — Once all sub-agents have returned, synthesize:
  - Deduplicate findings across agents.
  - Triage into: must-fix-now (critical + high), should-fix (medium), nice-to-have (low), info.
  - Output as markdown with one section per triage bucket.
  - End with a "Reviewer agreement" line: which findings ≥2 agents flagged independently.

STEP 3 — Print the final synthesized report. Do not request approval; this is a one-shot run.
```

### 4. Agent-specific tasks

| Source skill | Agent | Task to inject |
|---|---|---|
| `bmad-code-review` | bmad-agent-blind-hunter | "Adversarial review of the diff in {DIFF_REF}. Diff-only context; no project files." |
| `bmad-code-review` | bmad-agent-edge-case-hunter | "Enumerate every branching path and boundary condition in {DIFF_REF}. Report only unhandled edge cases." |
| `bmad-code-review` | bmad-agent-pm | "Acceptance audit: does {DIFF_REF} satisfy the ACs of {JIRA_KEY}? Use jira-ticket skill to fetch ACs first." |
| `bmad-party-mode` | (all) | "Contribute to the discussion on '{TOPIC}' from your specialist perspective. One paragraph max." |
| `bmad-review-adversarial-general` | bmad-agent-blind-hunter | "Cynical review of {ARTIFACT}." |
| `bmad-review-adversarial-general` | bmad-agent-edge-case-hunter | "Edge-case walkthrough of {ARTIFACT}." |
| `bmad-correct-course` | bmad-agent-pm | "From PM perspective: re-scope {CHANGE_REQUEST} against current sprint goal." |
| `bmad-correct-course` | bmad-agent-sm | "From SM perspective: how does {CHANGE_REQUEST} affect WIP and capacity?" |
| `bmad-correct-course` | bmad-agent-architect | "From architect perspective: technical implications of {CHANGE_REQUEST}." |

### 5. Output to the user

Print the composed prompt inside a fenced code block, then offer to invoke it directly:

```
Composed prompt (N sub-agents). To run now:

./scripts/codex-revue.sh '<paste-prompt-here>'

Or pipe it directly:
```

If the user confirms, invoke `./scripts/codex-revue.sh "$PROMPT"` from the project root.

## Constraints

- Sub-agent count caps: respect `~/.codex/config.toml` `[agents].max_threads` if set (default Codex limit applies otherwise).
- Caller must provide context (diff ref, Jira key, topic, artifact path) — if missing, ask once via `AskUserQuestion` before composing.
- Do NOT include any 1Password-backed secret values in the prompt; the wrapper script already handles env loading.
- The synthesis step in the prompt is opinionated for code-review-style outputs; for `bmad-party-mode`, replace it with: "Synthesize: present each agent's contribution verbatim under their displayName heading, then add a 'Convergence' section listing points ≥2 agents agreed on."
