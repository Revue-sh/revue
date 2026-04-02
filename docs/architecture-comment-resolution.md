# Comment Resolution Architecture (REVUE-98)

**Status:** In Development  
**Epic:** REVUE-87 (Revue as Peer Reviewer Initiative)  
**Story:** REVUE-98 (Auto-resolve fixed comments)  
**Updated:** 2026-04-01

---

## Overview

Revue automatically resolves PR/MR comments when developers fix issues, enabling a conversational peer-review experience. This document defines the architecture for tracking comment states and implementing auto-resolution.

---

## Design Decisions

### 1. File-Based Storage (Not Database)

**Decision:** Store comment state in `.revue/` folder within customer's repository using TOML format.

**Rationale:**
- **Privacy-first:** Customer data never leaves their infrastructure
- **Zero hosting cost:** No database to maintain
- **Natural multi-tenancy:** Each repo = isolated state
- **Git-based backup:** Version control handles data retention
- **Team-shared:** All developers see same comment state
- **Offline-capable:** No network dependency for reading state
- **Simpler implementation:** File I/O vs SQL queries

**Why NOT Postgres:**
- Our existing Postgres database is for **internal metrics** (aggregate learning, false positive tracking)
- Mixing operational data (comment states) with analytics data violates separation of concerns
- Database hosting creates privacy/compliance burden (SOC2, GDPR)
- Customers would need to trust us with their code snippets

**Why TOML over JSON:**
- 30% fewer tokens when Revue reads state (lower AI costs)
- More human-readable (developers can manually inspect/edit)
- Supports comments (developers can annotate)
- Native datetime types (no ISO string parsing)

---

## File Structure

### Repository Layout

```
customer-repo/
├── .revue/                         # Revue state folder
│   ├── comments/
│   │   ├── PR-123.toml            # Comment state for PR #123
│   │   ├── PR-124.toml            # Comment state for PR #124
│   │   └── ...
│   ├── summary.toml               # Repo-wide aggregate stats (optional)
│   └── config.toml                # Repo-specific settings
├── .gitignore                     # Includes .revue/ (optional)
└── ...
```

### Comment State File (`.revue/comments/PR-{number}.toml`)

```toml
# Revue comment tracking for PR #123
# Updated: 2026-04-01T15:30:00Z

pr_number = 123
platform = "bitbucket"  # or "github", "gitlab"
repo_owner = "acme-corp"
repo_name = "api-service"
created_at = 2026-04-01T10:00:00Z
updated_at = 2026-04-01T15:30:00Z

# Summary counts (calculated)
[summary]
total_issues = 10
fixed_count = 7
discussed_count = 2
remaining_count = 1
progress_percentage = 90

# Individual comments
[[comments]]
platform_comment_id = "abc123"
platform_thread_id = "thread_456"  # GitHub/GitLab only
file_path = "src/api/handlers.py"
line_number = 42
comment_body = "Consider extracting this to a utility function"
finding_id = 12345  # Link to internal metrics DB (optional)
state = "auto_resolved"
created_at = 2026-04-01T10:05:00Z
updated_at = 2026-04-01T14:20:00Z

  # State transition history
  [[comments.transitions]]
  from_state = "unresolved"
  to_state = "auto_resolved"
  reason = "Code changed at line 42 in commit abc789"
  timestamp = 2026-04-01T14:20:00Z
  
  [[comments.transitions]]
  from_state = "auto_resolved"
  to_state = "manually_resolved_with_reply"
  reason = "Developer reopened and explained"
  developer_reply = "Fixed differently than suggested - using existing helper"
  timestamp = 2026-04-01T15:30:00Z

[[comments]]
platform_comment_id = "def456"
file_path = "src/api/middleware.py"
line_number = 15
comment_body = "Missing error handling for edge case"
state = "dismissed_with_reason"
created_at = 2026-04-01T10:10:00Z
updated_at = 2026-04-01T11:00:00Z

  [[comments.transitions]]
  from_state = "unresolved"
  to_state = "dismissed_with_reason"
  reason = "Developer dismissed with explanation"
  developer_reply = "Won't fix - this edge case is handled upstream"
  timestamp = 2026-04-01T11:00:00Z
```

---

## Comment State Machine

### States

```
unresolved                          → Initial state when comment posted
auto_resolved                       → Revue detected code change and resolved
manually_resolved_with_reply        → Developer resolved with explanation
manually_resolved_no_reply          → Developer resolved without explanation
dismissed_with_reason               → Developer explicitly won't fix (with reason)
ignored                             → Comment sits unresolved for >7 days (no action)
```

### State Transitions

```
                      ┌─────────────┐
                      │ unresolved  │
                      └──────┬──────┘
                             │
            ┌────────────────┼────────────────┐
            │                │                │
            ▼                ▼                ▼
    ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
    │ auto_resolved│  │ manually_    │  │ dismissed_   │
    │              │  │ resolved_*   │  │ with_reason  │
    └──────────────┘  └──────────────┘  └──────────────┘
```

---

## Component Architecture

### High-Level Design

```
┌─────────────────────────────────────────────────────────────┐
│ CommentResolutionService (Business Logic)                   │
│ • Orchestrates auto-resolution workflow                     │
│ • Detects code changes, developer replies, manual resolution│
│ • Updates summary comment on platform                       │
└───────────┬─────────────────────────────┬───────────────────┘
            │                             │
            ▼                             ▼
┌─────────────────────────┐   ┌─────────────────────────────┐
│ CommentFileStore        │   │ PlatformAdapter             │
│ • Read/write TOML files │   │ • Post/resolve comments     │
│ • Manage .revue/ folder │   │ • Read developer replies    │
│ • Calculate summaries   │   │ • Platform-specific APIs    │
└─────────────────────────┘   └──────┬──────────────────────┘
                                     │
                      ┌──────────────┼──────────────┐
                      │              │              │
                      ▼              ▼              ▼
           ┌──────────────┐  ┌──────────┐  ┌──────────┐
           │ Bitbucket    │  │ GitHub   │  │ GitLab   │
           │ Adapter      │  │ Adapter  │  │ Adapter  │
           └──────────────┘  └──────────┘  └──────────┘
```

### Modules

**`src/revue/comments/`**
- `models.py` - Domain models (PRComment, CommentState, SummaryComment)
- `file_store.py` - TOML file I/O (replaces repository.py)
- `platform_adapter.py` - Bitbucket/GitHub/GitLab API clients
- `service.py` - Auto-resolution orchestration

---

## Data Flow

### 1. Initial PR Scan

```
1. Revue reviews PR → generates findings
2. For each finding → posts comment to platform API
3. Store comment metadata in .revue/comments/PR-{number}.toml
4. Create summary comment (first comment on PR)
5. Commit .revue/ folder to repo (or store locally if gitignored)
```

### 2. PR Update (Developer Pushes New Commit)

```
1. Revue detects PR update (webhook or poll)
2. Read .revue/comments/PR-{number}.toml
3. For each unresolved comment:
   a. Check if manually resolved on platform
   b. Check for developer replies (dismissals)
   c. Check if code changed at comment location (diff analysis)
   d. Transition state if applicable
4. Update .revue/comments/PR-{number}.toml with new states
5. Update summary comment on platform
6. Commit updated .revue/ folder
```

### 3. Auto-Resolution Logic

```python
def process_comment(comment):
    # Priority 1: Manual resolution on platform
    if platform.is_resolved(comment.platform_id):
        if platform.has_reply(comment.platform_id):
            transition_to("manually_resolved_with_reply")
        else:
            transition_to("manually_resolved_no_reply")
        return
    
    # Priority 2: Developer dismissal
    replies = platform.get_replies(comment.platform_id)
    if any_dismissal_keyword(replies):
        transition_to("dismissed_with_reason")
        auto_resolve_on_platform()  # or post acknowledgment if API blocked
        return
    
    # Priority 3: Code changed
    if code_changed_at_line(comment.file_path, comment.line_number):
        transition_to("auto_resolved")
        auto_resolve_on_platform()
        return
```

---

## Platform-Specific Behavior

### Bitbucket

- **API Resolution:** ✅ Supported via `PUT /comments/{id}` with `resolved: true`
- **Thread ID:** Not applicable (flat comment structure)
- **Acknowledgment:** Not needed (API resolution works)

### GitHub

- **API Resolution:** ❌ NOT supported via Personal Access Token
  - GitHub requires GitHub App for programmatic resolution
  - Limitation: PATs can post/read comments but NOT resolve threads
- **Thread ID:** Pull Request Review ID (review threads, not individual comments)
- **Acknowledgment:** ✅ Post reply comment: "✅ Revue detected this was fixed in commit {sha}"

### GitLab

- **API Resolution:** ✅ Supported via `PUT /discussions/{id}?resolved=true`
- **Thread ID:** Discussion ID (GitLab groups comments into discussions)
- **Acknowledgment:** Not needed (API resolution works)

---

## Privacy & Compliance

### What We Store (Internal Metrics DB)

**Separate from operational data.** Our Postgres database stores:
- Anonymized review metrics (false positive rates, quality scores)
- Aggregate findings (no code snippets)
- Model training data (hashed identifiers only)

**NOT stored in our database:**
- Comment state (lives in `.revue/` folder)
- Developer replies (lives in `.revue/` folder)
- Code snippets (only in customer's repo)

### What Customers Store (`.revue/` folder)

- Comment platform IDs (for API lookups)
- File paths and line numbers
- State transition history
- Developer reply text (if dismissed)

**Customers control:**
- Whether to commit `.revue/` to Git (we add to `.gitignore` by default)
- Data retention (Git history or local-only)
- Access permissions (repo-level access controls)

---

## Configuration

### `.revue/config.toml`

```toml
# Revue configuration for this repository

[comment_resolution]
enabled = true
auto_resolve = true  # Auto-resolve when code changes
dismissal_keywords = [
    "won't fix",
    "wontfix",
    "not fixing",
    "keeping as-is",
    "intentional"
]

[storage]
commit_state = false  # If true, commit .revue/ to Git; if false, keep local only

[privacy]
track_developer_replies = true  # Store reply text in .revue/ for learning
anonymize_transitions = false   # Hash developer names in transition logs
```

---

## Deployment Models

### Local Development

```bash
# Revue CLI runs on developer machine
revue review PR-123
# → Reads .revue/comments/PR-123.toml from local repo
# → Posts comments to Bitbucket/GitHub/GitLab via API
# → Updates .revue/comments/PR-123.toml locally
```

### CI/CD Pipeline

```yaml
# Bitbucket Pipelines / GitHub Actions / GitLab CI
- step:
    name: Revue Code Review
    script:
      - revue review $BITBUCKET_PR_ID
      # → Reads .revue/ from checked-out repo
      # → Updates .revue/ and commits back (if configured)
```

### Self-Hosted Server

```bash
# Revue webhook listener (optional)
revue serve --webhook
# → Listens for PR events
# → Clones repo, runs review, commits .revue/ back
```

---

## Testing Strategy

### Unit Tests

- `test_file_store.py` - TOML read/write operations
- `test_state_machine.py` - State transition logic
- `test_dismissal_detection.py` - Keyword matching
- `test_platform_adapters.py` - Mock API calls

### Integration Tests

- `test_comment_resolution_flow.py` - End-to-end resolution workflow
- `test_bitbucket_integration.py` - Real Bitbucket API calls (test repo)
- `test_github_integration.py` - Real GitHub API calls (test repo)
- `test_gitlab_integration.py` - Real GitLab API calls (test repo)

### Test Data

```
tests/fixtures/
├── sample-pr-123.toml     # Example comment state file
├── expected-summary.toml  # Expected summary calculations
└── dismissal-replies.json # Sample developer replies for keyword testing
```

---

## Performance Considerations

### File I/O Optimization

- **Lazy loading:** Only read TOML when comment state needed
- **Caching:** Cache parsed TOML in memory during single review run
- **Batch writes:** Update all comments in PR, then write once

### Token Optimization

**TOML vs JSON:**
- JSON: ~50 tokens per comment
- TOML: ~35 tokens per comment (30% savings)
- **Savings at scale:** 1000 comments = 15,000 fewer tokens = $0.30 saved per review (claude-sonnet-4-5)

---

## Future Enhancements (Out of Scope for REVUE-98)

### Phase 2: Advanced Features

- **Multi-commit tracking:** Detect fixes across multiple commits
- **Diff analysis:** AST-based change detection (not just line-based)
- **Conversational mode:** Revue replies to developer questions
- **Real-time webhooks:** Instant resolution (no polling)

### Phase 3: Enterprise Features

- **Centralized dashboard:** Aggregate `.revue/` data across repos
- **Team analytics:** Per-developer resolution metrics
- **Custom workflows:** Configurable auto-resolution rules
- **Audit logs:** Compliance-ready state transition history

---

## Migration Path (From Postgres Prototype)

We initially prototyped with Postgres but pivoted to file-based storage for privacy/simplicity.

**Reusable components:**
- ✅ Domain models (`PRComment`, `CommentState`, `SummaryComment`)
- ✅ Platform adapters (`BitbucketAdapter`, `GitHubAdapter`, `GitLabAdapter`)
- ✅ Service logic (auto-resolution orchestration)
- ✅ Dismissal keyword detection

**Replaced components:**
- ❌ `CommentRepository` (SQL) → `CommentFileStore` (TOML)
- ❌ Database migrations → File schema versioning
- ❌ Connection pooling → File locking

---

## References

- [TOML Specification](https://toml.io/en/)
- [Bitbucket API - Comments](https://developer.atlassian.com/cloud/bitbucket/rest/api-group-pullrequests/#api-repositories-workspace-repo-slug-pullrequests-pull-request-id-comments-post)
- [GitHub API - Review Comments](https://docs.github.com/en/rest/pulls/comments)
- [GitLab API - Discussions](https://docs.gitlab.com/ee/api/discussions.html)
