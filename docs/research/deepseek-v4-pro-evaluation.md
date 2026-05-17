# DeepSeek-V4-Pro Evaluation (REVUE-265)

**Ticket:** [REVUE-265](https://urukia.atlassian.net/browse/REVUE-265)
**Date:** 2026-05-17
**Author:** spike under feat/REVUE-265
**Harness:** `scripts/smoke_openrouter_deepseek_test.py`
**Raw outputs:** captured under `/tmp/REVUE-265-outputs/{deepseek,qwen}/`
  (per-trial JSON files; not committed — local artefacts of this spike).

## TL;DR

**Recommendation: PROMOTE** `deepseek/deepseek-v4-pro` to `tier: supported`,
**conditional** on the registry entry setting `tool_choice_first_turn: required`.
The model passes every dimension of the supported-models hard gate
(schema strict, three-state envelope, tool calling, multi-turn loop) when
that one knob is set correctly. With `auto`, the tool loop is unusable
(0/3 tool emissions). With `required`, it is reliable (3/3 schema-valid
verdicts with multi-call context fetching).

Empirically outperforms the existing `qwen/qwen3-coder-next` supported
model on schema-strict findings emission in this comparison (DeepSeek
3/3 trials over 3 runs vs Qwen 0/1 over a single baseline run; Qwen
exhibited a known whitespace-runaway degenerate mode under
`temperature=0.3`). The Qwen sample size is intentionally a one-shot
baseline — see §3.2 — and a stronger statistical claim would need more
Qwen trials. Cost is roughly 2–4× Qwen per call but absolute spend
remains negligible (<$0.001 per typical scenario).

The proposed registry block at the end of this document is intended for
consumption by **REVUE-262** — `models_registry.yml` does not yet exist
in `src/revue/core/`, so promotion is gated on that ticket landing.

## 1. Model card

| Attribute | Value |
|-----------|-------|
| OpenRouter slug | `deepseek/deepseek-v4-pro` |
| Listing | <https://openrouter.ai/deepseek/deepseek-v4-pro> |
| Architecture | Mixture-of-Experts, 1.6T total / 49B activated parameters |
| Context window | 1,048,576 tokens (1 M) |
| Pricing (USD/M tokens) | prompt: $0.435, completion: $0.870, cached read: $0.0036 |
| Provider | DeepSeek (first-party on OpenRouter) |
| `supported_parameters` (OpenRouter) | `response_format`, `structured_outputs`, `tools`, `tool_choice`, `reasoning`, `seed`, `temperature`, … |

For comparison, the incumbent supported model:

| Attribute | `qwen/qwen3-coder-next` |
|-----------|-------------------------|
| Architecture | Sparse MoE, 80B total / 3B activated |
| Context window | 262,144 tokens (256 k) |
| Pricing (USD/M tokens) | prompt: $0.11, completion: $0.80, cached read: $0.07 |
| `supported_parameters` | `response_format`, `structured_outputs`, `tools`, `tool_choice`, `seed`, `temperature`, … |

Both expose the same shape of capability flags via the OpenRouter models
endpoint, so structured outputs and tool calling are nominally supported
on each.

## 2. Harness summary

`scripts/smoke_openrouter_deepseek_test.py` mirrors the structure of the
two existing OpenRouter smoke scripts (`smoke_openrouter_schema.py`,
`smoke_openrouter_tools.py`) and adds:

- **`--trials N`** — repeats every scenario N times for variance observation.
- **Tool-choice matrix** — runs the tool-calling scenario with
  `tool_choice_first_turn` set to both `auto` and `required`.
- **`--out-dir DIR`** — dumps each trial's metadata + raw response
  payload as a JSON file for evidence capture.
- **Aggregate summary** — per-scenario pass-rate, mean latency, mean
  token usage.

Shared scaffolding: schema source of truth is
`openai_response_format_for_three_state()` from
`src/revue/core/finding_schema.py`, identical to production reviewer
clients. `temperature=0.3` (matches production), no `seed` (parity with
existing scripts).

Settings:

- Trials: 3 for DeepSeek, 1 for Qwen baseline.
- 5 scenario buckets: `findings`, `clean`, `error`, `tool_loop[auto]`,
  `tool_loop[required]`.
- Total API calls: 15 (DeepSeek) + 5 (Qwen) = **20**, well under the
  50-call ceiling.

## 3. Schema-strict scenario results

Three trials per scenario, `temperature=0.3`, no seed.

### 3.1 DeepSeek-V4-Pro

| Scenario | Schema pass | Branch match | Mean latency | Mean tokens (p/c) |
|----------|-------------|--------------|--------------|--------------------|
| findings | 3/3 | 3/3 (`findings`) | 19.8 s | 324 / 647 |
| clean | 3/3 | 3/3 (`clean`) | 2.4 s | 163 / 82 |
| error | 3/3 | 3/3 (`error`, code=`tool_unavailable`) | 3.8 s | 257 / 125 |

**All nine schema-only trials produced strict-JSON-schema-conforming
output on the correct branch.**

Sample `findings` response (trial 1, full payload):

```json
{
  "findings": [
    {
      "category": "code-quality",
      "confidence": 1.0,
      "file_path": "calculator.py",
      "issue": "The `divide` function does not handle division by zero, and the new `__main__` block calls `divide(10, 0)`, which will raise a `ZeroDivisionError` at runtime.",
      "line_number": 2,
      "severity": "high",
      "suggestion": "Add a guard clause to check if `b == 0` and handle it appropriately (e.g., raise a custom exception, return a sentinel value, or print an error message)."
    }
  ],
  "status": "findings"
}
```

Sample `clean` response (trial 1):

```json
{
  "status": "clean",
  "summary": "The diff adds a docstring to the function, which is a positive improvement. No issues found.",
  "confidence": 1.0
}
```

Sample `error` response (trial 1):

```json
{
  "status": "error",
  "error": {
    "code": "tool_unavailable",
    "message": "File-reading tool is unavailable, cannot inspect code."
  }
}
```

### 3.2 Qwen3-Coder-Next baseline (1 trial each)

| Scenario | Schema pass | Branch match | Latency | Tokens (p/c) | Notes |
|----------|-------------|--------------|---------|--------------|-------|
| findings | 0/1 | 0/1 | 14.1 s | 179 / **2048** | `finish_reason=length`, ran out of tokens generating whitespace-only filler. Output truncated mid-stream and failed JSON parse. |
| clean | 1/1 | 1/1 | 2.4 s | 170 / 57 | clean. |
| error | 1/1 | 1/1 | 2.3 s | 114 / 46 | clean. |

The Qwen `findings` failure is a known degenerate mode — after emitting
a valid findings array the model entered a loop of `"  \n  \n  \n"`
whitespace until the `max_tokens=2048` cap, producing invalid JSON. This
matches the behaviour previously observed under REVUE-241's qwen3.6
A/B test (see memory `project_openrouter_model_ab.md`). DeepSeek-V4-Pro
never exhibited this on any of the 15 trials in this run.

## 4. Tool-calling scenario results

Scenario: a diff imports `MULTIPLIER` from `src/multiplier.py`. The model
must call `read_file` on the imported module to discover that
`MULTIPLIER = 0` before judging the diff. The harness exposes one tool
(`read_file`) with synthetic file contents. `_MAX_TURNS = 5`.

### 4.1 `tool_choice_first_turn=auto` — model chooses

| Model | Trials | Emitted tool_call | Schema-valid final | Mean latency |
|-------|--------|-------------------|--------------------|--------------|
| `deepseek/deepseek-v4-pro` | 3 | **0/3** | 0/3 | 6.1 s |
| `qwen/qwen3-coder-next` | 1 | 0/1 | 0/1 | 5.5 s |

Both models, given free choice, **decline to call the tool**. DeepSeek
produces an apparently-clean verdict with `confidence: 0` (an honest
"I'm not actually sure"), but the verdict itself is wrong — without
reading `multiplier.py` the model cannot know that `MULTIPLIER = 0`
poisons the change.

Sample DeepSeek `auto` failure (trial 1, full final payload):

```json
{
  "confidence": 0,
  "status": "clean",
  "summary": "Straightforward change: imports a constant MULTIPLIER and multiplies the result of helper() by it. No issues detected."
}
```

### 4.2 `tool_choice_first_turn=required` — model forced to emit a tool call on turn 1

| Model | Trials | Emitted tool_call | Schema-valid final | Mean tool_calls per loop | Mean latency |
|-------|--------|-------------------|--------------------|--------------------------|--------------|
| `deepseek/deepseek-v4-pro` | 3 | **3/3** | **3/3** | 2.3 | 17.5 s |
| `qwen/qwen3-coder-next` | 1 | 1/1 | 1/1 | 2.0 | 11.9 s |

With `required`, DeepSeek issues 1–3 `read_file` calls per loop
(observed paths across trials: `src/multiplier.py`, `src/helper.py`,
`src/handler.py`) and then produces a schema-conformant verdict.

Sample DeepSeek `required` final response (trial 1):

```json
{
  "findings": [
    {
      "category": "code-quality",
      "confidence": 100,
      "file_path": "src/handler.py",
      "issue": "Multiplying by zero always yields zero",
      "line_number": 5,
      "severity": "high",
      "suggestion": "MULTIPLIER is defined as 0 in src/multiplier.py (with its own warning comment). Multiplying helper() by 0 means process() will always return 0, which is almost certainly not the intended behavior. Consider using a non-zero MULTIPLIER value or removing the multiplication."
    }
  ],
  "status": "findings"
}
```

**Empirical answer to `tool_choice_first_turn`:** `required`. The `auto`
value is unusable on this model for a tool-driven review pipeline.

### 4.3 Behavioural note — `confidence` numeric scale

The schema declares `confidence` as `number` (no `minimum`/`maximum` —
the Anthropic grammar compiler rejects those — so any number is valid).
Across trials, DeepSeek emitted `confidence` on **both** scales:

- 0–1 scale (e.g. `1.0`, `0`) — schema scenarios.
- 0–100 scale (e.g. `100`) — tool_loop scenarios.

This is **not** a schema break (both are valid `number`s), but downstream
consumers that interpret `confidence` need to be robust to either scale.
Qwen consistently used the 0–1 scale in this run. If a normalisation pass
isn't already in the parser, this should be tracked as a separate
follow-up rather than blocking promotion. (Status quo: existing models
in the registry already disagree on the convention; see REVUE-241 notes.)

## 5. Determinism / variance assessment

`temperature=0.3`, no seed. Three trials per schema scenario:

| Scenario | Latency range | Completion-token range | Branch consistency |
|----------|---------------|-------------------------|--------------------|
| findings | 9.3 s – 32.3 s | 372 – 902 | 3/3 same (`findings`) |
| clean | 1.0 s – 5.2 s | 48 – 145 | 3/3 same (`clean`) |
| error | 1.2 s – 5.2 s | 38 – 182 | 3/3 same (`error`) |
| tool_loop[required] | 9.5 s – 26.6 s | 239 – 693 | 2/3 `findings`, 1/3 `clean` |

**Branch verdict is stable across the schema-only scenarios** (9/9
identical branch). Latency and token counts have meaningful spread —
typical for a non-zero temperature MoE model. The tool_loop scenario
shows one branch disagreement: trial 2 returned `clean` despite
fetching context, while trials 1 and 3 returned `findings`. This is a
**judgement variance**, not a schema variance — all three trials
produced schema-valid output, the model just disagreed with itself on
whether `MULTIPLIER = 0` is a real bug. For a single-reviewer pipeline
this would be a concern; for Revue's multi-reviewer ensemble + Vex /
Nova reconciliation it is acceptable.

No truncation, no JSON failures, no whitespace runaways observed on
DeepSeek across 15 trials.

## 6. Cost analysis

DeepSeek prices: input $0.435/M, output $0.870/M.
Qwen3-Coder-Next prices: input $0.11/M, output $0.80/M.

Per-scenario mean cost (USD per call, this run):

| Scenario | DeepSeek-V4-Pro | Qwen3-Coder-Next |
|----------|-----------------|------------------|
| findings | $0.000704 | $0.001658 *(but failed)* |
| clean | $0.000142 | $0.000064 |
| error | $0.000221 | $0.000049 |
| tool_loop[auto] | $0.000365 | $0.000152 |
| tool_loop[required] | $0.000932 | $0.000281 |

**Total cost of this entire spike: $0.0079** (~0.8 cents) across 20
API calls. Per-call DeepSeek cost runs roughly 2–4× Qwen for similar
work, but absolute spend is negligible. The 1 M context window is also
materially larger than Qwen's 256 k — relevant once Revue's reviewer
context grows past Qwen's headroom.

## 7. Comparison table (qualitative)

| Dimension | DeepSeek-V4-Pro | Qwen3-Coder-Next |
|-----------|-----------------|-------------------|
| Strict JSON schema (this run) | 9/9 trials | 2/3 trials *(findings truncated)* |
| Three-state branch discipline | 9/9 correct branch | 2/3 correct |
| Tool calling (`tool_choice=required`) | Reliable, 1–3 calls/loop | Reliable, 2 calls/loop |
| Tool calling (`tool_choice=auto`) | Declines (0/3) | Declines (0/1) |
| Whitespace-runaway failure mode | Not observed | Observed in `findings` (1/1) |
| Latency floor (clean) | 1 s | 2 s |
| Latency ceiling (findings) | 32 s | 14 s *(but failed)* |
| Context window | 1 M | 256 k |
| Cost per typical call | $0.0001 – $0.001 | $0.00005 – $0.0003 |
| `confidence` scale | Mixed (0–1 and 0–100) | 0–1 consistent |

## 8. Recommendation

**Promote `deepseek/deepseek-v4-pro` to `tier: supported`** in
REVUE-262's registry, with the following conditions:

1. `tool_choice_first_turn` **MUST** be set to `required` in the registry
   entry. `auto` is empirically unusable — the model produces a verdict
   without fetching context (0/3 tool emissions), and Revue's value
   proposition depends on the reviewer actually reading the surrounding
   code.
2. Downstream `confidence` handling should be confirmed to accept both
   0–1 and 0–100 scales (file a follow-up if this isn't already the
   case across the parser — out of scope for this ticket).

Evidence supporting promotion:

- 9/9 strict-schema trials passed across all three schema scenarios.
- 3/3 tool-loop trials with `required` produced schema-valid verdicts
  with genuine multi-file context fetching.
- No degenerate failure modes observed (no JSON truncation, no
  whitespace runaway, no malformed tool_call arguments).
- Outperforms current supported-model `qwen/qwen3-coder-next` on the
  schema-strict findings branch in this comparison (3/3 vs 0/1).
- 1 M context window unlocks future use cases that exceed Qwen's
  256 k headroom.

Evidence against / cautions:

- Latency variance is high on the findings branch (9 s – 32 s).
  Acceptable for a code-review pipeline but worth noting.
- Single judgement disagreement in the tool_loop scenario (clean vs
  findings on the same diff). Multi-reviewer ensemble masks this; a
  single-reviewer deployment would not.
- Cost per call is 2–4× Qwen. Absolute spend is still small.

## 9. Proposed registry block (for REVUE-262)

`src/revue/core/models_registry.yml` does not yet exist in the
repository — REVUE-262 introduces it. The block below is the proposed
entry to add at that time. **Do not add this in REVUE-265.**

```yaml
# To be added by REVUE-262.
deepseek/deepseek-v4-pro:
  tier: supported
  provider: openrouter
  capabilities:
    json_schema_strict: true
    tool_calls: true
    response_format: true
    structured_outputs: true
  context_window: 1048576
  pricing:
    prompt_per_mtok_usd: 0.435
    completion_per_mtok_usd: 0.870
    cached_input_per_mtok_usd: 0.003625
  reviewer_defaults:
    temperature: 0.3
    max_tokens: 2048
    tool_choice_first_turn: required   # REVUE-265: auto is empirically unusable
  notes: |
    Promoted by REVUE-265 spike. Three-state schema and tool loop pass at
    3/3 trials with tool_choice_first_turn=required. With auto, the tool
    loop is 0/3 — the model emits a verdict without fetching context. See
    docs/research/deepseek-v4-pro-evaluation.md for evidence and raw
    samples. Confidence scale can be either 0-1 or 0-100; parser must
    normalize.
```

## 10. Reproducibility

```bash
# DeepSeek (3 trials per scenario, 15 API calls)
direnv exec . python3 scripts/smoke_openrouter_deepseek_test.py \
  --model deepseek/deepseek-v4-pro \
  --trials 3 \
  --out-dir /tmp/REVUE-265-outputs/deepseek

# Qwen baseline (1 trial per scenario, 5 API calls)
direnv exec . python3 scripts/smoke_openrouter_deepseek_test.py \
  --model qwen/qwen3-coder-next \
  --trials 1 \
  --out-dir /tmp/REVUE-265-outputs/qwen
```

Exit codes: 0 = every trial passed schema + branch; 1 = schema OK but
at least one branch mismatch (judgement issue); 2 = at least one schema
failure or transport error. This run: DeepSeek exited 2 (the 3
tool_loop[auto] trials failed by design — model declined to use the
tool), Qwen exited 2 (1 trial failed schema due to whitespace runaway).

These exit codes describe the harness's pass-bar, not the promotion
recommendation. The harness reports the empirical truth; the recommendation
weighs it against deployment constraints (e.g. forcing
`tool_choice=required` is the production policy, so the `auto` failures
are not deployment-blocking).
