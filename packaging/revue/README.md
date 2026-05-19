# revue-local

Run the [Revue](https://revue.io) AI code-review pipeline locally as a [Claude Code](https://claude.com/claude-code) skill.

- **No platform API credentials needed.** Reviews run against your local diff.
- **DeepSeek-V4-Pro by default** via OpenRouter — typically ~79–88% lower TCO than Anthropic Sonnet for the same review.
- **Skill prompts ship inside the wheel** — verifiable, tamper-evident, signed via Sigstore.

## Install

```bash
pip install revue-local
revue-local install-skill
```

The second command verifies the wheel signature, then drops `SKILL.md` and the orchestrator into
`~/.claude/skills/revue-local/` so Claude Code picks it up automatically on next launch.

## Verify a release

```bash
revue-local verify
```

Fetches the published manifest, compares against the locally-installed version, and prints whether the
running bundle is the one signed for that release.

## Run a review

Inside any project, ask Claude Code:

```
/revue-local
```

The skill diffs the current branch against `main`, runs the multi-agent pipeline (DeepSeek by default),
and prints findings inline. Nothing is posted to GitHub / GitLab / Bitbucket.

## Documentation

- Concepts: <https://revue.io/docs/revue-local>
- Packaging internals: `docs/distribution/revue-local-packaging.md` in this repo

## Licence

Apache-2.0 — see [`LICENSE`](../LICENSE) at the repo root.
