# Revue Review Comparison Knowledge Base

A structured dataset of before/after reviews used to tune Revue's prompts,
agent behaviour, and context filtering over time.

---

## Purpose

Each comparison entry captures:
- The same diff reviewed **without** PR description context (baseline)
- The same diff reviewed **with** PR description context (contextual)
- A structured analysis of the differences

Over time this dataset answers:
- Which false positives appear consistently? → candidates for `Out of Scope` patterns or prompt tuning
- Which true positives get suppressed with context? → risk of over-silencing
- Which agents produce the most noise? → agent prompt candidates for refinement
- Are there project-specific patterns Revue should always know? → `.revue.yml` additions

---

## Running a Comparison

```bash
# 1. Generate the diff
git diff main...your-branch > /tmp/review.diff

# 2. Baseline — no context
python3 src/revue/cli.py review \
  --diff /tmp/review.diff \
  --provider anthropic --model claude-sonnet-4-5 \
  --config .revue.yml \
  --output json \
  > docs/review-comparisons/REVUE-XX/baseline.json

# 3. Contextual — with PR description file
python3 src/revue/cli.py review \
  --diff /tmp/review.diff \
  --provider anthropic --model claude-sonnet-4-5 \
  --config .revue.yml \
  --output json \
  --pr-description-file /tmp/pr_description.txt \
  > docs/review-comparisons/REVUE-XX/contextual.json

# 4. Analyse (produces ANALYSIS.md in the comparison dir)
python3 scripts/compare_reviews.py docs/review-comparisons/REVUE-XX/
```

---

## Entry Structure

```
docs/review-comparisons/
  REVUE-XX/
    baseline.json        # Review output without PR context
    contextual.json      # Review output with PR context
    pr_description.txt   # The PR description used for context
    ANALYSIS.md          # Human + structured comparison
```

---

## ANALYSIS.md Template

```markdown
# Review Comparison: REVUE-XX — <PR title>
**Date:** YYYY-MM-DD  
**Diff size:** N lines, M files  
**Model:** claude-sonnet-4-5  
**Tier:** free / pro  

## Summary

| Metric | Baseline | Contextual | Delta |
|--------|----------|------------|-------|
| Total findings | N | N | -N% |
| High/Medium | N | N | -N |
| Low/Info | N | N | -N |
| False positives | N | N | -N |
| Missed real issues | N | N | +/-N |

## False Positives in Baseline (suppressed by context)

List findings that appeared without context but were correctly suppressed with it.
For each: which section of the PR description suppressed it?

## True Positives Preserved

Findings that appeared in both, confirming context didn't over-silence.

## Regressions (suppressed that shouldn't have been)

Findings present in baseline but missing from contextual that were real issues.

## Observations & Actions

- Prompt tuning candidates
- `.revue.yml` patterns to add
- Agent-specific noise patterns
- Out of Scope section effectiveness
```

---

## Growing the Knowledge Base

After each significant PR:
1. Run the comparison
2. Fill in `ANALYSIS.md`
3. Commit to `docs/review-comparisons/`
4. When patterns emerge across 3+ entries → open a ticket to act on them

---

## Epic E8 — Review Intelligence & Knowledge Base

| Ticket | Description | Status |
|--------|-------------|--------|
| REVUE-87 | Epic: E8 — Review Intelligence & Knowledge Base | 🔲 |
| REVUE-88 | Postgres container on NAS (192.168.0.36) | 🔲 |
| REVUE-89 | Normalised Postgres schema (see SCHEMA.md) | 🔲 |
| REVUE-90 | run-comparison.sh writes to Postgres | 🔲 |
| REVUE-91 | reviews.py query CLI | 🔲 |
| REVUE-92 | Human rating flow | 🔲 |
| REVUE-93 | Auto-heuristic quality scorer | 🔲 |
| REVUE-94 | .revue.yml allowed_patterns / disallowed_patterns | 🔲 |

See `SCHEMA.md` for the full normalised Postgres schema and key queries.
