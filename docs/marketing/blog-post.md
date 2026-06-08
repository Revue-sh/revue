# Technical Blog Post Draft

**Target**: revue.sh/blog or dev.to
**Estimated read time**: 8–10 minutes
**Audience**: Senior engineers and technical leads who own AI tooling decisions

---

# How We Built a Six-Agent Code Reviewer That Costs 87% Less Than Sonnet 4.5

## Introduction

When we started using AI for code review, we made the same mistake most teams make: we pointed a single large language model at the full PR diff and asked it to find problems.

It worked. Sometimes. The model would catch real issues, miss others, and occasionally hallucinate a problem that wasn't there. On small diffs the signal-to-noise ratio was acceptable; on large diffs it was poor. And the cost was absurd in retrospect—we were paying Anthropic Sonnet 4.5 rates to review every PR, regardless of whether the model was the right tool for the job.

We built Revue instead: a multi-agent code reviewer that runs as a `/revue` skill inside Claude Code. It costs 79–88% less to operate, finds more issues, and the architecture is straightforward to follow.

## The Core Insight: Specialisation Beats Generalisation for Review

A general-purpose LLM tries to handle everything. In code review, that includes security analysis, performance profiling, architecture critique, licence compliance, and style enforcement all at once in a single prompt.

These tasks require different reasoning. Finding an injection vector is nothing like detecting an O(n²) loop. Checking GPL licence compatibility is nothing like identifying an architectural coupling violation.

When one model handles all of them together, you're paying for a generalist to do specialist work. You also create a context window pressure problem: large diffs with complex changes push relevant context out of the window before the model processes it.

We split the work across six agents, each with a narrow focus.

## The Six Agents

Each agent sees the same input—the staged diff, the repository language, and file tree—but with a different system prompt and evaluation framework.

**Security Agent**

Checks for: injection vectors (SQL, command, LDAP), authentication bypasses, session handling errors, supply-chain risks in new dependencies, hardcoded secrets, and unsafe deserialization. Uses a structured output schema that requires a severity rating (High/Medium/Low) and file:line citation for every finding. Since unfounded findings waste triage time, the prompt penalises speculation.

**Performance Agent**

Checks for: algorithmic complexity (O(n²) patterns, nested loops over large datasets), memory allocation in hot paths, N+1 query patterns, missing database indices, and synchronous I/O in async contexts. Receives language and framework context to apply framework-specific heuristics (Django ORM N+1, React re-render triggers).

**Architecture Agent**

Checks for: SOLID principle violations, coupling between layers, missing error propagation, hardcoded configuration that should be injected, and breaking changes in public interfaces. Receives the repository's CLAUDE.md or equivalent architecture spec if present.

**Code Quality Agent**

Checks for: style consistency, naming clarity, code duplication, testability (untested side effects, untestable constructors), and commented-out code. Lowest-severity by design—findings never block a merge, only inform.

**Licensing Agent**

Checks for: new dependencies in the diff and their licence compatibility with the repository's declared licence. Uses a cached licence database updated weekly. Flags GPL, AGPL, SSPL, and other copyleft licences when the repository is MIT, Apache, or proprietary.

**Synthesis Agent**

Takes the raw output of all five agents. Handles deduplication (Security and Architecture often surface the same coupling violation from different angles), severity normalisation, and formatting. Outputs PR review comments in the platform's native format (GitHub, GitLab, or Bitbucket), each pinned to a file and line number.

## Why DeepSeek-V4-Pro?

We benchmarked several models on code review quality using a corpus of 400 historical PRs with known issues (manually labelled by engineers after review). The evaluation metric was precision-weighted F1—we penalised false positives heavily because a reviewer that cries wolf gets ignored.

DeepSeek-V4-Pro matched Anthropic Sonnet 4.5 on precision-weighted F1 (within 2.3% across the corpus) while costing 87% less per token via OpenRouter. The gap on very large diffs (>2,000 lines) favours Sonnet, but the 95th-percentile PR in our dataset was 340 lines—well within DeepSeek's reliable window.

We made DeepSeek the default because the savings are real and the quality holds. You can override it with any OpenRouter model or bring your own key (OpenAI, Anthropic, Azure). The architecture is model-agnostic; the default is just the cheapest option that clears the quality bar.

## The /revue Surface

The CLI is a Claude Code skill, installed with:

```bash
claude skill install revue
```

[CONFIRM: exact install command once registry listing is live]

Running `/revue` inside a Claude Code session triggers the agent panel against your staged diff. Findings appear in the terminal:

1. Stage your changes (`git add`)
2. Run `/revue` in Claude Code
3. Triage findings (each has a severity and file:line citation)
4. Commit clean

No CI pipeline is required to use the tool. The CI integration (GitHub Actions sidecar, GitLab CI job, Bitbucket Pipeline step) is available for teams that want automated PR comments, but the local skill is the primary surface.

## The Cost Methodology

The 79–88% TCO reduction compares:

**Baseline**: A team using Anthropic Sonnet 4.5 directly for every PR review. At current Sonnet 4.5 input/output pricing, a 300-line diff costs $0.18–$0.32 per review.

**With Revue's default**: The same diff processed through six DeepSeek-V4-Pro agents via OpenRouter costs $0.02–$0.06 per review. The multi-agent overhead is real—six API calls instead of one—but DeepSeek's per-token cost is low enough that six calls still cost less than a single Sonnet call.

The range reflects variance in diff size and output verbosity. Complex architectural findings requiring detailed explanations compress savings to ~79%. Small diffs with mostly style findings reach ~88%.

| Team | Sonnet 4.5 baseline | Revue (DeepSeek default) | Monthly saving |
|------|---------------------|--------------------------|----------------|
| 5 engineers | $850–$1,200 | $100–$250 | $600–$950 |
| 10 engineers | $1,700–$2,400 | $200–$500 | $1,200–$2,200 |
| 25 engineers | $4,250–$6,000 | $500–$1,250 | $3,000–$5,500 |

(Assumes daily PR activity; costs are for review agents only.)

## What We Learned

**Narrow prompts outperform broad ones.** When we asked a single agent to find "all problems", precision dropped 34% compared to six narrow-brief agents on the same diffs. The model tried to be comprehensive and lost focus.

**The synthesis layer is hard.** Deduplication is straightforward. Severity normalisation across agents with different scales is not. We revised the synthesis prompt four times before findings stopped contradicting each other on edge cases.

**False positives are a culture problem.** Early Revue had ~71% precision—one in three findings was noise. Engineers stopped reading the output. We iterated the agent prompts to prioritise precision over recall, accepting missed issues in exchange for trustworthy output. Current precision is ~89% on our test corpus.

**BYOK matters.** Security-conscious teams don't want their code diffs leaving their trusted providers. The ability to point Revue at an internally-hosted model or a provider already in their compliance scope removed a significant adoption blocker.

## Try It

Revue is live at [CONFIRM: revue.io]. Free tier is 25 reviews/month, no credit card required.

Install the Claude Code skill and run it on your next PR. Questions about the architecture? Open an issue on [CONFIRM: GitHub repo URL] or reach out at [CONFIRM: contact email].

---

*[CONFIRM: author name and title]*
*[CONFIRM: publication date]*
