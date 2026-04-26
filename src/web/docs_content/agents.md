# Agent Catalogue

Revue uses a multi-agent architecture. Each specialist agent reviews the diff from a different angle, then Nova consolidates their findings into a single prioritised output.

---

## Cleo — Orchestrator & Router

**Role:** Analyses the diff and decides which specialist agents to run, and in what team configuration.

Cleo reads the diff, classifies its complexity and risk level, and selects the appropriate team. For small diffs, it may route to `team-quick`; for security-sensitive changes it routes to `team-security-focus`.

**Cleo does not post findings directly** — it routes and coordinates.

---

## Zara — Security Analyst

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

## Kai — Performance Expert

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

## Maya — Code Quality Expert

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

## Leo — Architecture Reviewer

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

## Nova — Consolidator

**Role:** Deduplicates and prioritises findings from all specialist agents.

Nova receives findings from Zara, Kai, Maya, and Leo, deduplicates them, and produces a single ranked list ordered by severity and confidence. It also generates the summary comment posted to the PR.

**Nova does not post independent findings** — it synthesises and ranks.

---

## Sage — The Resolver

**Role:** Generates code fixes for self-contained findings.

After Nova consolidates findings, Sage classifies each one as:
- **Fixable** — a safe, scoped code fix can be generated
- **Needs human** — too complex, too risky, or requires context Sage doesn't have

For fixable findings, Sage generates a fix and posts it as a platform-native suggestion:
- **GitHub:** Suggested Change (1-click accept in the PR UI)
- **GitLab:** Apply Suggestion
- **Bitbucket:** Inline comment with code block

Sage only posts suggestions above the configured `min_confidence` threshold (default: 70).

---

## Built-in Teams

| Team | Agents | Best for |
|---|---|---|
| `team-full-review` | Zara, Kai, Maya, Leo, Nova, Sage | General-purpose — all checks |
| `team-quick` | Maya, Nova | Fast reviews of small changes |
| `team-security-focus` | Zara, Maya, Nova, Sage | Security-sensitive changes |
| `team-performance` | Kai, Maya, Nova | Database / algorithm changes |
| `team-swift-ios` | Zara, Maya + iOS-specific rules | Swift / iOS projects |
| `team-kotlin-android` | Zara, Maya + Android-specific rules | Kotlin / Android projects |
| `team-python` | Zara, Maya, Leo, Nova | Python codebases |
| `team-typescript` | Zara, Maya, Leo, Nova | TypeScript / Node.js codebases |

Configure your team in `.revue.yml`:

```yaml
agents:
  team: team-security-focus
```
