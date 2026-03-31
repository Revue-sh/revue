# Post-MVP Enhancement Ideas

## Auto-resolve Bitbucket Comments

**Priority:** Post-MVP  
**Value:** Developer experience improvement  
**Effort:** Medium (3-5 points)

**Problem:**
Currently, Revue posts inline comments for every finding but doesn't auto-resolve them when issues are fixed in subsequent commits. This creates noise in PRs with many findings (e.g., 105 comments in PR #22).

**Proposed Solution:**
- Track finding "fingerprints" (hash of file path + line + issue text)
- On subsequent review runs, compare new findings against previous comments
- Auto-resolve Bitbucket comments for findings that no longer appear
- Keep comments open for new/changed findings

**Implementation Notes:**
- Store comment IDs mapped to finding fingerprints (in-memory or lightweight cache)
- Use Bitbucket API: `PUT /repositories/{workspace}/{repo}/pullrequests/{pr_id}/comments/{comment_id}` with `{"resolved": true}`
- Handle edge cases: line number changes (use fuzzy matching), file renames

**Benefits:**
- Cleaner PR review experience
- Developers can focus on remaining/new issues
- Automatic confirmation that fixes worked

**Related:**
- Could extend to GitHub, GitLab platforms
- Could add comment thread tracking (replies, discussions)

**Epic Candidate:** Post-MVP Quality of Life Improvements
