# Reviewer-Agent Tool-Use (Lazy Full-File Reads)

**Status:** Proposed
**Updated:** 2026-05-12

---

## Problem

Reviewer agents (Maya, Leo, Kai, Zara) see only the diff hunk. They cannot read the surrounding file, so they file findings that are factually wrong: a guard, branch, or call site outside the hunk addresses the very concern the agent flags.

**Evidence — PR #25:** 17 prose-only findings posted; ~7 were factually incorrect (claims of "missing null check", "no error handling", etc., contradicted by code 5–30 lines outside the hunk). Vex (REVUE-240) is the verifier of *patch safety*, not *claim accuracy* — it passes prose-only findings through by design. Nova has `read_file` (REVUE-239) but only sees what the reviewers surface; it cannot retract a finding that was never substantiated against the file.

Root causes:
1. `LoadedAgent.analyse()` ([agent_loader.py:254](../../src/revue/core/agent_loader.py)) calls `self._client.complete(...)` — no `tools`, no `tool_handlers`.
2. Reviewers have no mechanism to **verify their own claim** before emitting it.
3. The complementary ADR REVUE-169 (`system-context-injection.md` D2) proposes *eager* signature injection — useful, but signatures are not enough to verify "is the guard already there".

---

## Decision

### D1 — Extend `read_file` tool-use to Maya, Leo, Kai, Zara

Each reviewer agent gets the same `ReadFileTool` instance Nova uses, scoped to `allowed_paths = set(diff_by_file.keys())`. The agent prompt instructs the model to call `read_file` **before** filing any finding whose validity depends on context outside the diff hunk (null checks, prior error handling, alternative call sites, surrounding control flow).

> **Implementation note:** Reviewer definitions are `.md` with YAML frontmatter (Maya/Leo/Kai/Zara). The single load-bearing wiring change is `LoadedAgent.analyse()` → call `complete_with_tools(..., tools=[ReadFileTool.tool_definition()], tool_handlers={"read_file": tool.execute})` when the client supports it; fall back to `complete()` for legacy clients. `max_iterations` defaults to 5 (matches Nova).

### D2 — Shared per-file cache prefix

Each `read_file` response is wrapped as a system block with `cache_control: ephemeral` keyed on `(file_path, head_sha)`. The four reviewers run in parallel ([agent_runner.py:79](../../src/revue/core/agent_runner.py)) and read the same files; without prefix-sharing the eager case (4 agents × every touched file) is unaffordable. With caching, the second reader of any file pays 0.1× input cost on its content. This is consistent with the prefix-sharing strategy already accepted in `prompt-cache-strategy.md`.

### D3 — Relationship to REVUE-169 (system-context-injection)

REVUE-169 D2 is **eager** (every review pays, signatures only). This ADR is **lazy** (pay only when an agent decides to verify, full file). They are complementary: signatures answer "what does this function promise?"; full reads answer "is the guard already on line 47?". Ship this ADR first — it directly fixes the PR #25 failure mode; REVUE-169 D2 can layer on later.

---

## Out of scope

- **Reading non-PR files.** `allowed_paths` stays restricted to the diff's file set (sandbox preserved). Cross-repo reasoning is a separate RFC.
- **Repository-wide RAG / vector store.** Rejected in `system-context-injection.md`; same reasons apply.
- **Removing Vex.** Vex verifies *patch* safety; this ADR addresses *claim* safety. Both layers stay.
- **Auto-retracting findings post-hoc.** The point of D1 is to prevent the wrong finding from ever being emitted.

---

## Expected impact

| Metric | Current | After |
|--------|---------|-------|
| Factually-wrong prose findings (PR #25 baseline) | 7 / 17 (41%) | ≤2 / 17 target (≤12%) |
| Tool calls per reviewer per PR | 0 | 0–N (lazy; expect 1–3 on average per agent) |
| Input tokens / review (avg PR: 20 files, 500 LOC diff) | Baseline (~40K) | +15–30% worst-case; +5–10% with D2 caching |
| Wall-clock latency / review | Baseline | +1–3s per tool call; agents run in parallel so wall-clock = max(agent), not sum |

**Cost math (Sonnet 4.6, avg PR):** 4 agents × ~3 file reads × ~4K tokens = ~48K extra input tokens. At $3/Mtok base = $0.144 worst-case; with D2 cache hits (each file read once at write cost 1.25×, three times at read cost 0.1×) the marginal cost drops to ~$0.05. On Haiku 4.5 the delta is negligible. On Opus 4.7 expect ~3.5× the Sonnet figure.

---

## Affected files

| File | Change |
|------|--------|
| `src/revue/core/agent_loader.py` | `LoadedAgent.analyse()` — pass tools/handlers via `complete_with_tools` when client supports it |
| `src/revue/agents/{maya,leo,kai,zara}.md` | Add "When to call `read_file`" section to each system prompt |
| `src/revue/core/pipeline.py` | Build `ReadFileTool` once per run; thread `repo_root` + `allowed_paths` into agent construction |
| `src/revue/tests/core/test_agent_loader_tools.py` | New — reviewer tool-use wiring |
| `docs/architecture/README.md` | ADR index update |

---

## Consequences

- **Token cost rises** even with caching. Mitigation: cap `max_iterations=5`, keep `max_bytes=200_000` from existing `ReadFileTool`, and ship behind a `reviewer_tool_use: true` flag in `.revue.yml` so teams can opt out.
- **Latency rises** on tool-heavy reviews. Parallel-agent execution caps wall-clock at max(agent), not sum, but a chatty reviewer that issues 5 reads adds ~5–15s to *that* agent's slice.
- **Prompt-discipline risk:** if the prompt is weak, an agent may read every file unconditionally. The prompt must be specific: "only call `read_file` when your finding depends on code outside the hunk". Measure tool-call rate post-ship; tune prompt if avg > 3/agent/PR.
- **Market parity:** Greptile and Qodo Merge already do full-codebase context; CodeRabbit pulls surrounding-file context; Sourcery is diff-only. This ADR moves Revue from the Sourcery tier to the CodeRabbit tier without taking on graph-index infrastructure.

---

## Review Notes

*Populated during the Proposed phase. Add your name, date, and comment.*
</content>
</invoke>