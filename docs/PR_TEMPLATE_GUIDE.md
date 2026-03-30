# Pull Request Template Guide

## Overview
All PRs must follow the template defined in `.bitbucket/pull_request_template.md` to ensure consistency, completeness, and traceability.

## Creating PRs

### Option 1: Via Script (Preferred)
```bash
# 1. Create filled PR description based on template
cat > /tmp/pr-description-REVUE-XX.md << 'EOF'
# [Paste and fill template from .bitbucket/pull_request_template.md]
EOF

# 2. Run script
./scripts/create-pr.sh REVUE-XX "feat(scope)[REVUE-XX]: title" main /tmp/pr-description-REVUE-XX.md
```

### Option 2: Via Web UI
1. Push branch: `git push -u origin feat/REVUE-XX-description`
2. Follow link from git output: `https://bitbucket.org/cbscd/revue/pull-requests/new?source=...`
3. Template auto-populates — fill in sections marked with `<!--`

### Option 3: Manual API Call
```bash
# Required env vars: BITBUCKET_USERNAME, BITBUCKET_API_TOKEN, BITBUCKET_WORKSPACE, BITBUCKET_REPO_SLUG
DESCRIPTION=$(cat /tmp/pr-description-REVUE-XX.md | jq -Rs .)
TITLE=$(jq -Rs . <<< "feat(scope)[REVUE-XX]: title")
curl -X POST \
  -u "${BITBUCKET_USERNAME}:${BITBUCKET_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{
    \"title\": $TITLE,
    \"description\": $DESCRIPTION,
    \"source\": {\"branch\": {\"name\": \"feat/REVUE-XX-branch\"}},
    \"destination\": {\"branch\": {\"name\": \"main\"}},
    \"close_source_branch\": true
  }" \
  "https://api.bitbucket.org/2.0/repositories/${BITBUCKET_WORKSPACE}/${BITBUCKET_REPO_SLUG}/pullrequests"
```

## Template Sections

### Required
- **🎯 Ticket**: Link to Jira ticket
- **📝 Summary**: 1-2 sentence overview
- **🔧 Changes**: Bulleted list of key changes
- **✅ Acceptance Criteria**: Copy from Jira, mark completed
- **🧪 Testing**: Test commands + results
- **📊 Impact**: Business + technical impact
- **📋 Checklist (Author)**: Pre-merge verification

### Optional (use "N/A" or "None" if not applicable)
- **🚨 Breaking Changes**
- **📚 Documentation**
- **🔗 Dependencies**
- **🖼️ Screenshots / Videos** (for UI changes)
- **🚀 Deployment Notes**

## Quality Standards
- All checkboxes must be checked before requesting review
- Tests must pass locally before pushing
- Commit message format: `type(scope)[REVUE-XX]: description`
- PR title must match commit message format
- Self-review completed (read your own diff!)

## Examples
See `/tmp/pr-description-REVUE-81.md` for a complete filled example.

## Automation
- `.bitbucket/pull_request_template.md`: Auto-populated in web UI
- `scripts/create-pr.sh`: Command-line PR creation
- Bitbucket CI: Runs tests + AI review on every PR
- Jira webhook: Auto-links PR to ticket
