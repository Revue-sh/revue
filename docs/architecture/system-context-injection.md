# System-Context Injection for Agents

**Status:** Proposed
**Updated:** 2026-04-20
**Jira:** REVUE-169

---

## Problem

Revue's agents receive the diff and the PR description as their primary input. They have no knowledge of the system the diff is entering.

This creates a structural ceiling on review quality: a diff can be internally correct (no SOLID violations, no SQL injection, clean patterns) and still be wrong — because it makes an implicit assumption about the system that is false.

Examples of the class of defect this misses today:

- Code that assumes a DB connection is already open when the calling context uses lazy init
- Rate-limiting logic applied after the expensive DB lookup it was supposed to protect
- Session invalidation that calls `cache.delete()` when the production environment uses a distributed cache with a different invalidation contract
- A new service method that calls the repository layer directly, bypassing the existing transaction wrapper in the service base class

These are not syntax errors. They are not even pattern errors. They are **assumption errors** — the code is correct in isolation but wrong in context. Maya and Leo can detect internal SOLID violations; they cannot detect contract violations against a system they have never seen.

This gap is not theoretical. It is the exact failure mode that AI-generated code produces most frequently: the model generates code that is locally coherent but systemically incorrect, because it infers system behaviour from patterns in the diff rather than from the actual system.

Root causes:
1. Agents receive only the changed lines — no interface definitions, no documented contracts, no architectural constraints
2. There is no mechanism for teams to express "here is what our system guarantees" in a form Revue can use
3. Nova's consolidation step has no baseline to compare intent against implementation

---

## Decision

### D1 — Architecture Document Injection

Revue reads a project-level context document and injects it as a system-understanding prefix into Maya and Leo's prompts.

**Source document (in priority order):**
1. `.revue/context.md` — a Revue-specific file teams maintain for review context
2. `ARCHITECTURE.md` at repo root — if `.revue/context.md` is absent
3. No injection if neither exists — behaviour unchanged

**What the injection contains:**
The full content of the context document, injected before the diff in the agent prompt under a `## System Context` heading.

**What teams put in the context document:**
- Layering rules ("no raw SQL outside `db/repositories/`")
- Module boundaries ("services never import from `infrastructure/`")
- Key contracts ("all external HTTP calls go through `HttpGateway`, never `requests` directly")
- Runtime environment facts ("production uses Redis cluster — `cache.delete()` is not atomic")

**New finding class: Architecture Drift**
When an agent detects that the diff violates a contract stated in the system context, it flags the finding as `Architecture Drift` — a new severity class sitting above `High`. Architecture Drift findings always block merge when blocking mode is enabled.

> **Implementation note**: Token cost implications must be measured before shipping. Cap the context document at 2,000 tokens (configurable via `context_max_tokens` in `.revue.yml`). If the document exceeds the cap, Cleo truncates from the bottom and logs a warning.

---

### D2 — Adjacent File Contract Injection

Cleo's pre-pass reads the interfaces and public signatures of files directly touched or imported by the diff, and injects a compact "contract summary" into each agent's prompt.

**What is injected:**
- Function/method signatures (name, parameters, return type, docstring first line) from files the diff modifies or imports
- Class-level docstrings from modified classes
- No implementation bodies — signatures only

**Why signatures only:**
Full file contents would exhaust the context window on any non-trivial diff. Signatures give agents enough to detect contract violations (wrong return type assumed, unexpected parameter added, interface broken) without the token cost of full source.

**Scope boundary:**
Only files in the diff's direct import graph (depth 1). Not transitive dependencies. This keeps injection size bounded and predictable.

> **Implementation note**: This requires Revue to clone or checkout the repo during the CI run, which it does not currently do. The current implementation reads only the diff from the VCS API. D2 requires access to the full repository tree — verify CI runner permissions and checkout strategy before implementing.

---

### D3 — Intent Validation in Nova

Nova's consolidation step gains a second output: an intent alignment check.

**How it works:**
1. Cleo extracts stated intent from the PR description. Structured form preferred (`<!-- intent: ... -->` block); falls back to parsing the PR description's first paragraph.
2. If intent is extractable, Nova receives it alongside the consolidated findings.
3. Nova generates a `## Intent Alignment` section in the PR-level comment: a brief (3–5 sentence) assessment of whether the implementation delivers what the PR description claims.
4. Mismatches between stated intent and implementation surface as a `Intent Mismatch` finding at `High` severity.

**What Nova is NOT doing here:**
Nova is not running the code or performing dynamic analysis. It is performing LLM-based reasoning over the diff and the stated intent — the same kind of reasoning a senior engineer does when reading a PR. The check is heuristic, not definitive.

> **Implementation note**: Intent extraction is best-effort. If the PR description is empty or too vague to extract intent, Nova skips D3 silently — no finding, no comment section. Never generate a false "intent mismatch" from an ambiguous description.

---

## Out of scope

- **Full codebase vector store / embeddings**: Rejected for MVP — too complex to operate, significant latency and cost overhead, requires persistent infrastructure. May revisit post-MVP if D2's depth-1 injection proves insufficient.
- **Dynamic/runtime analysis**: Revue is a static review tool. Detecting runtime contract violations (e.g., actual Redis behaviour under load) is out of scope.
- **Automatic context document generation**: Revue will not auto-generate `.revue/context.md`. Teams own this document. Revue provides a schema and examples in the docs.
- **Transitive import graph injection (depth > 1)**: Too expensive in tokens and too noisy in findings. Depth-1 only for D2.
- **AI-provenance detection**: Whether the diff was AI-generated is a separate RFC. This ADR addresses system-context regardless of authorship.

---

## Expected impact

| Metric | Current | After |
|--------|---------|-------|
| Architecture Drift detections | 0 (no mechanism) | Detectable for teams with context docs |
| Intent mismatch detections | 0 (no mechanism) | Detectable when PR description has extractable intent |
| False-clean reviews on system-boundary violations | Unknown — undetectable today | Reduced; bounded by quality of `.revue/context.md` |
| Token cost per review | Baseline | +5–15% (D1 context doc) +10–20% (D2 signatures) |

Estimates are provisional. Token cost increase depends on context document size and diff size. D2 may be gated behind a config flag (`context_injection: signatures`) if cost impact is unacceptable in testing.

---

## Affected files

| File | Change |
|------|--------|
| `src/revue/core/pipeline.py` | Cleo pre-pass: detect and load context document (D1); extract adjacent file signatures (D2); extract PR intent (D3) |
| `src/revue/core/models.py` | New fields: `SystemContext`, `IntentStatement`, `AdjacentContracts` on the review context passed to agents |
| `src/revue/core/ai_client.py` | Agent prompt assembly: inject system context and contracts before diff |
| `src/revue/core/cleo_router.py` | Intent extraction logic; context document resolution (priority order) |
| `src/revue/comments/service.py` | Nova output: new `## Intent Alignment` section; new `Architecture Drift` severity class |
| `docs/revue-yml-reference.md` | New config keys: `context_max_tokens`, `context_injection` |
| `docs/architecture/README.md` | ADR index update |

---

## Consequences

- **Context document maintenance burden**: Teams must write and maintain `.revue/context.md`. If the document is stale, agents will flag Architecture Drift on valid changes. Mitigation: document the staleness risk clearly; suggest treating the context doc as a living spec, updated whenever a layer boundary changes.
- **Token cost increase**: D1 and D2 add tokens to every review. For large context documents or large diffs, this could become significant. The `context_max_tokens` cap and the option to gate D2 behind a flag are the primary mitigations.
- **D2 requires repo access**: The current pipeline reads only the diff via VCS API. Reading adjacent file signatures requires checkout access. This is a CI runner permission change — confirm it does not conflict with the on-premise security model (source code must not leave the CI runner).
- **Intent extraction is heuristic**: Poorly written PR descriptions will produce low-quality intent checks or no check at all. This is acceptable — D3 is additive, not a regression risk. Teams get better results when they write better PR descriptions, which is itself a good incentive.

---

## Review Notes

*Add name, date, and comment. Remove resolved items before moving to Accepted.*
