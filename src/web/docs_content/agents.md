# How Revue reviews code

Revue uses a multi-agent architecture. Each specialist reviews the diff from a different angle, then a consolidation step deduplicates and prioritises findings into a single output.

---

## Orchestration

An orchestrator analyses the diff, classifies its complexity and risk level, and routes it to the appropriate specialist team. For small diffs it may use a fast-review team; for security-sensitive changes it routes to a security-focused team.

The orchestrator does not post findings directly. It coordinates.

---

## Security

**Role:** Identifies security vulnerabilities and risks.

**Checks:**

- SQL injection, XSS, CSRF, path traversal, SSRF
- Hardcoded secrets and API keys
- Insecure deserialization
- Authentication and authorisation flaws
- Dependency injection bypass patterns
- Cryptographic weaknesses (weak algorithms, insecure random)

**Example finding:**
> `auth.py:42` — Hardcoded secret key detected. Severity: critical.
> Use an environment variable instead: `SECRET_KEY = os.environ["SECRET_KEY"]`

---

## Performance

**Role:** Identifies performance bottlenecks and inefficiencies.

**Checks:**

- N+1 query patterns
- Missing database indexes for queried columns
- Inefficient loops (O(n²) that could be O(n))
- Unnecessary blocking calls in async code
- Memory leaks and excessive allocations
- Caching opportunities

**Example finding:**
> `models.py:88` — N+1 query pattern detected. Each iteration calls `get_user_by_id()`. Fetch all users in a single query before the loop.

---

## Code Quality

**Role:** Reviews code quality, maintainability, and best practices.

**Checks:**

- Long functions and classes (complexity)
- Missing error handling and edge cases
- Code duplication (DRY violations)
- Unclear variable/function naming
- Missing docstrings on public APIs
- Dead code
- Test coverage gaps

**Example finding:**
> `utils.py:15` — Function `process_data` has 12 parameters. Consider extracting a dataclass or config object.

---

## Architecture

**Role:** Reviews structural and architectural concerns.

**Checks:**

- Circular dependencies
- Layering violations (e.g. business logic in routes)
- Tight coupling between modules
- Missing abstractions / abstraction leaks
- API contract changes (breaking changes)
- Inappropriate use of global state

**Example finding:**
> `routes/api.py:55` — Direct database call in route handler. Move to a service/repository layer to keep routes thin.

---

## Synthesis

**Role:** Deduplicates and prioritises findings from all specialist reviewers.

The synthesis step receives findings from all specialists, deduplicates them, and produces a single ranked list ordered by severity and confidence. It also generates the summary comment posted to the PR.

---

## Fix Suggestions

**Role:** Generates code fixes for self-contained findings.

After synthesis, the resolver evaluates each finding and classifies it as:

- **Fixable**: a safe, scoped code fix can be generated
- **Needs human review**: too complex, too risky, or requires context not available in the diff

For fixable findings, it generates a fix and posts it as a platform-native suggestion:

- **GitHub:** Suggested Change (1-click accept in the PR UI)
- **GitLab:** Apply Suggestion
- **Bitbucket:** Inline comment with code block

Only suggestions above the configured `min_confidence` threshold are posted (default: 70).

---

## Built-in Teams

These are starting-point configurations. You can customise any team or create your own in `.revue.yml`.

| Team | Specialists included | Best for |
|---|---|---|
| `team-full-review` | All 6 specialists + resolver | General-purpose: all checks |
| `team-quick` | Code quality + synthesis only | Fast reviews of small changes |
| `team-security-focus` | Security, code quality, synthesis + resolver | Security-sensitive changes |
| `team-performance` | Performance, code quality, synthesis | Database / algorithm changes |
| `team-swift-ios` | Security, code quality + iOS-specific rules | Swift / iOS projects |
| `team-kotlin-android` | Security, code quality + Android-specific rules | Kotlin / Android projects |
| `team-python` | Security, code quality, architecture, synthesis | Python codebases |
| `team-typescript` | Security, code quality, architecture, synthesis | TypeScript / Node.js codebases |

Configure your team in `.revue.yml`:

```yaml
agents:
  team: team-security-focus
```
