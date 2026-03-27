# Sage — Fix Suggestion Confidence Threshold

## What is the confidence threshold?

When Sage analyses a code review finding, it assigns a **confidence score** (0–100) representing how certain it is that the suggested fix is correct and safe to apply with one click.

The **confidence threshold** is the minimum score a finding must reach before Sage posts it as a 1-click suggestion. Findings that score below the threshold are posted as "Needs human" comments instead — Sage explains what it found but leaves the fix to the developer.

---

## What is the default threshold and why?

**The default threshold is 70.**

The original product specification recommended 90 as the default. During implementation, the pattern library was built with a range of confidence levels reflecting real-world certainty:

| Pattern type | Confidence | Example |
|---|---|---|
| Hardcoded secret | 90 | `api_key = "sk-abc123"` → replace with `os.getenv()` |
| SQL injection | 85 | String-concatenated query → parameterised query |
| Unused import | 80 | `import foo  # unused` → remove line |
| Typo in string | 75 | Comment-flagged typo → corrected string |
| Missing null check | 70 | `if not username` pattern |

Setting the threshold at 90 would have silenced the bottom four categories entirely — Sage would classify those findings as "Needs human" regardless of how clear the fix is. That defeats the purpose of having those patterns.

**The decision:** ship with 70 as the default so all patterns are reachable, and make the threshold configurable so teams can tighten it to suit their risk tolerance.

---

## What this means for you as a customer

| Your threshold | What Sage suggests | Best for |
|---|---|---|
| **70** (default) | All pattern types, including medium-confidence fixes like null checks and typos | Teams who want maximum automation coverage |
| **80** | Security and SQL patterns + unused imports only | Balanced — automation on high-value fixes, human judgement on lower-certainty ones |
| **90** | Security patterns (hardcoded secrets) only | Teams with strict review policies who want Sage to suggest only the most obvious fixes |
| **95+** | Nothing (no patterns reach this threshold) | Effectively disables Sage suggestions |

There is no "wrong" choice — it depends on how much you trust automated suggestions in your codebase.

---

## How to configure it

In your project's `.revue.yml`:

```yaml
review:
  min_confidence: 70   # default — change to 80 or 90 to be more conservative
```

You can also set it per environment via the CI variable `REVUE_MIN_CONFIDENCE` (takes precedence over `.revue.yml`).

---

## How Sage communicates confidence to developers

Every Sage suggestion includes the confidence score in the comment body:

```
🧠 Sage — Suggested Fix (confidence: 85%)

Replace string-concatenated SQL query with parameterised query.
```

Every "Needs human" comment explains why Sage held back:

```
🧠 Sage — Needs human review

This architectural finding requires context beyond the diff.
Suggested next step: review the service boundary design with your team.
```

This means developers always know whether Sage is confident or cautious — they are never left guessing.

---

## FAQ

**Can I set different thresholds per agent?**
Not in v1.0. Per-agent thresholds are a planned v1.5 feature.

**Will Sage ever suggest a fix it isn't sure about?**
No. If a fix scores below the threshold, Sage posts a "Needs human" comment. It will never post an uncertain suggestion silently.

**What happens if I set the threshold to 0?**
Sage would suggest fixes for any pattern match regardless of confidence. This is not recommended — some patterns at low confidence have a higher false-positive rate.
