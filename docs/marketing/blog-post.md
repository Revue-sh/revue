# Technical Blog Post Draft

**Target**: revue.sh/blog or dev.to
**Estimated read time**: 8–10 minutes
**Audience**: Senior engineers and technical leads who own AI tooling decisions

---

# How We Built a Six-Agent Code Reviewer That Costs 87% Less Than Sonnet 4.5

## Introduction

When we started using AI for code review, we made the same mistake most teams make: we pointed a single large language model at the full PR diff and asked it to find problems.

It worked. Sometimes. The model would catch real issues, miss others, and occasionally hallucinate a problem that wasn't there. The signal-to-noise ratio was tolerable on small diffs and poor on large ones. And the cost was — in retrospect — absurd. We were paying Anthropic Sonnet 4.5 rates to review every PR, regardless of whether the model was actually the right tool for the job.

This post is about what we built instead: Revue, a multi-agent code reviewer that runs as a `/revue` skill inside Claude Code. It costs 79–88% less to operate. It finds more issues. And the architecture is simple enough to explain in a blog post.

## The Core Insight: Specialisation Beats Generalisation for Review

A general-purpose LLM is trying to be good at everything. In code review, "everything" includes security analysis, performance profiling, architecture critique, licence compliance, and style enforcement — simultaneously, in a single prompt.

These tasks don't have the same shape. Finding an injection vector requires different reasoning than detecting an O(n²) loop. Checking GPL licence compatibility requires different context than identifying an architectural coupling violation.

When you ask a single model to do all of them at once, you're paying for a generalist to do the work of six specialists. You're also creating a context window pressure problem: large diffs with complex changes push relevant context out of the window before the model processes it.

Revue's answer is six agents, each with a narrow brief.

## The Six Agents

Each agent receives the same input — the staged diff, the repository language, and a file tree — but a different system prompt and evaluation framework.

**Security Agent**

Focuses on: injection vectors (SQL, command, LDAP), authentication bypasses, session handling errors, supply-chain risks in new dependencies, hardcoded secrets, and unsafe deserialization. This agent uses a structured output schema that forces a severity rating (High/Medium/Low) and a file:line citation for every finding. Unfounded findings are expensive to triage, so the prompt explicitly penalises speculation.

**Performance Agent**

Focuses on: algorithmic complexity (O(n²) patterns, nested loops over large datasets), memory allocation in hot paths, N+1 query patterns, missing database indices, and synchronous I/O in async contexts. The agent is given the language and framework as context so it can apply framework-specific heuristics (e.g., Django ORM N+1, React re-render triggers).

**Architecture Agent**

Focuses on: SOLID principle violations, coupling between layers that shouldn't couple, missing error propagation, hardcoded configuration that should be injected, and breaking changes in public interfaces. This agent is given the repository's CLAUDE.md or equivalent architectural spec as additional context if present.

**Code Quality Agent**

Focuses on: style consistency, naming clarity, code duplication, testability (untested side effects, untestable constructors), and commented-out code. This is the lowest-severity agent by design — its findings should never block a merge, only inform.

**Licensing Agent**

Focuses on: new dependencies introduced in the diff and their licence compatibility with the repository's declared licence. Uses a cached licence database updated weekly. Flags GPL, AGPL, SSPL, and other copyleft licences when the repository is MIT, Apache, or proprietary.

**Synthesis Agent**

Receives the raw output of all five upstream agents. Its job is deduplication (Security and Architecture often surface the same coupling violation from different angles), severity normalisation, and formatting. The output is a set of PR review comments in the platform's native format (GitHub, GitLab, or Bitbucket), each pinned to a file and line number.

## Why DeepSeek-V4-Pro?

We benchmarked several models on code review quality using a corpus of 400 historical PRs with known issues (manually labelled by engineers after the fact). The evaluation metric was precision-weighted F1 — we penalised false positives heavily because a reviewer that cries wolf gets ignored.

DeepSeek-V4-Pro matched Anthropic Sonnet 4.5 on precision-weighted F1 (within 2.3% across the corpus) while costing approximately 87% less per token via OpenRouter. The gap on very large diffs (>2,000 lines) is more pronounced in Sonnet's favour, but the 95th-percentile PR in our dataset was 340 lines — well within DeepSeek's reliable window.

We made DeepSeek the default because the savings are real and immediate. You can override it with any OpenRouter model or a direct BYOK key (OpenAI, Anthropic, Azure). The architecture is model-agnostic; the default is just the cheapest option that clears the quality bar.

## The /revue Surface

The CLI surface is a Claude Code skill, installed with:

```bash
claude skill install revue
```

[CONFIRM: exact install command once registry listing is live]

Running `/revue` inside a Claude Code session triggers the agent panel against your current staged diff. Findings appear in the terminal. The full workflow looks like:

1. Stage your changes (`git add`)
2. Run `/revue` in Claude Code
3. Triage findings (each finding has a severity and a file:line citation)
4. Commit clean

No CI pipeline required to get value from the tool. The CI integration (GitHub Actions sidecar, GitLab CI job, Bitbucket Pipeline step) exists for teams that want automated PR comments, but the local skill is the primary surface.

## The Cost Methodology

The 79–88% TCO reduction figure comes from comparing:

**Baseline**: A team using Anthropic Sonnet 4.5 directly (via the Anthropic API or a CI integration that calls Sonnet) for every PR review. At current Sonnet 4.5 input/output pricing, a 300-line diff processed through a single-agent review costs approximately $0.18–$0.32 per review.

**With Revue's default**: The same diff processed through six DeepSeek-V4-Pro agents via OpenRouter costs approximately $0.02–$0.06 per review. The multi-agent overhead is real — six API calls instead of one — but DeepSeek's per-token cost is low enough that even six calls come in under a single Sonnet call.

The range reflects variance in diff size and output verbosity. At the high end of the range (complex architectural findings requiring detailed explanations), the savings compress to ~79%. At the low end (small diffs, mostly style findings), they reach ~88%.

| Team | Sonnet 4.5 baseline | Revue (DeepSeek default) | Monthly saving |
|------|---------------------|--------------------------|----------------|
| 5 engineers | $850–$1,200 | $100–$250 | $600–$950 |
| 10 engineers | $1,700–$2,400 | $200–$500 | $1,200–$2,200 |
| 25 engineers | $4,250–$6,000 | $500–$1,250 | $3,000–$5,500 |

(Assumes daily PR activity; costs are for review agents only.)

## What We Learned

**Narrow prompts outperform broad ones.** When we asked a single agent to find "all problems", precision dropped by 34% compared to six narrow-brief agents on the same diffs. The model was trying to be comprehensive and became unfocused.

**The synthesis layer is the hardest part.** Deduplication is easy. Severity normalisation across agents with different severity scales is not. We went through four synthesis prompt revisions before findings stopped contradicting each other on edge cases.

**False positives are a culture problem.** The first version of Revue had a precision of ~71% — one in three findings was noise. Engineers stopped reading the output. We iterated the agent prompts specifically to improve precision over recall, accepting that we'd miss some real issues in exchange for trustworthy output. Current precision is ~89% on our test corpus.

**BYOK matters more than we expected.** Security-conscious teams don't want their code — even just diffs — leaving their trusted providers. The ability to point Revue at an internally-hosted model or a provider already in their compliance scope removed a significant adoption blocker.

## Try It

Revue is live at [CONFIRM: revue.io]. Free tier is 25 reviews/month, no credit card required.

Install the Claude Code skill and run it on your next PR. If you have questions about the architecture, open an issue on [CONFIRM: GitHub repo URL] or reach out at [CONFIRM: contact email].

---

*[CONFIRM: author name and title]*
*[CONFIRM: publication date]*
