# Local CI Simulation Guide

Run the exact same Revue review that CI runs — locally, before pushing — to catch issues in minutes instead of waiting for CI iterations.

**Lesson learned (2026-04-03):** REVUE-95 required 6+ CI runs to diagnose a broken prompt. All of them could have been caught in one local run.

---

## Prerequisites

> ⚠️ **Security:** Never commit `~/.zshenv` to version control. It contains API keys and tokens.
> Consider using a password manager or macOS Keychain to store secrets, and `.gitignore` any local env files.

> 📝 **Note:** Commands in this guide pass credentials as arguments or in URLs, which may be visible
> in shell history (`~/.zsh_history`) and process listings (`ps aux`). This matches CI behaviour exactly.
> Clear your history after sensitive sessions if needed: `history -c` (zsh).

Ensure these are set in `~/.zshenv`:

```bash
export AI_API_KEY="YOUR_OPENROUTER_API_KEY_HERE"
export AI_PROVIDER="openrouter"
export AI_MODEL="deepseek/deepseek-v4-pro"
export REVUE_TIER_OVERRIDE="pro"
export BITBUCKET_USERNAME="your-username"
export BITBUCKET_API_TOKEN="your-bb-token"
```

Verify your key works:
```bash
source ~/.zshenv && curl -s https://openrouter.ai/api/v1/models \
  -H "Authorization: Bearer ${AI_API_KEY}" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('✅ Key valid' if d.get('data') else '❌ ' + str(d))"
```

---

## Step-by-step

### 1. Generate the diff (exactly as CI does)

```bash
cd Projects/revue.io
source ~/.zshenv

# Fetch main and diff only the PR commits — NOT your full local history
git fetch "https://x-token-auth:${BITBUCKET_API_TOKEN}@bitbucket.org/cbscd/revue.git" main
git diff FETCH_HEAD...HEAD > /tmp/revue_pr.diff

echo "Diff size: $(wc -l < /tmp/revue_pr.diff) lines"
```

> ⚠️ **Do NOT use `git diff origin/main...HEAD`** unless your local `origin/main` is up to date.
> Always fetch fresh from Bitbucket to match what CI sees.

### 2. Fetch the PR description

```bash
PR_ID="36"   # replace with your PR number

curl -sf "https://api.bitbucket.org/2.0/repositories/cbscd/revue/pullrequests/${PR_ID}" \
  -H "Authorization: Bearer ${BITBUCKET_API_TOKEN}" \
  | python3 -c "import json,sys; print(json.load(sys.stdin).get('description',''))" \
  > /tmp/revue_pr_description.txt

echo "PR description: $(wc -c < /tmp/revue_pr_description.txt) bytes"
```

### 3. Run the review

```bash
source ~/.zshenv
cd Projects/revue.io
export APP_ENV=staging
export PYTHONPATH="$(pwd)/src"

python3 -u src/revue/cli.py review \
  --diff /tmp/revue_pr.diff \
  --platform bitbucket \
  --pr-id "${PR_ID}" \
  --workspace "cbscd" \
  --repo-slug "revue" \
  --bb-username "${BITBUCKET_USERNAME}" \
  --bb-token "${BITBUCKET_API_TOKEN}" \
  --provider "${AI_PROVIDER:-openrouter}" \
  --model "${AI_MODEL:-deepseek/deepseek-v4-pro}" \
  --config .revue.yml \
  --comment-style per-issue \
  --pr-description-file /tmp/revue_pr_description.txt
```

---

### Option B: Run with a local Ollama model (zero API cost)

Useful for diagnosing pipeline logic (routing, won't-fix tracking, comment posting) without spending Anthropic budget.

**Prerequisites:**
1. Install [Ollama](https://ollama.com) and pull a model:
   ```bash
   ollama pull gemma4       # check exact tag with: ollama list
   ```
2. Check the model tag (tag varies by download):
   ```bash
   curl -s http://localhost:11434/api/tags | python3 -c "import json,sys; [print(m['name']) for m in json.load(sys.stdin)['models']]"
   # e.g. → gemma4:e4b
   ```

**Run command:**
```bash
source ~/.zshenv
cd Projects/revue.io
export APP_ENV=staging
export PYTHONPATH="$(pwd)/src"
export REVUE_BASE_URL="http://localhost:11434/v1"   # Ollama OpenAI-compat endpoint
export AI_API_KEY="ollama"                           # dummy value — Ollama ignores it
export REVUE_TIER_OVERRIDE="pro"

python3 -u src/revue/cli.py review \
  --diff /tmp/revue_pr.diff \
  --platform bitbucket \
  --pr-id "${PR_ID}" \
  --workspace "cbscd" \
  --repo-slug "revue" \
  --bb-username "${BITBUCKET_USERNAME}" \
  --bb-token "${BITBUCKET_API_TOKEN}" \
  --provider openai \
  --model "gemma4:e4b" \
  --config .revue.yml \
  --comment-style per-issue \
  --pr-description-file /tmp/revue_pr_description.txt
```

**Notes:**
- `REVUE_BASE_URL` overrides the OpenAI SDK base URL — the `openai` provider + `REVUE_BASE_URL` is how Ollama compatibility works.
- Local models may produce truncated or malformed JSON for large payloads (e.g. 40+ won't-fix threads). The pipeline degrades gracefully — won't-fix classify returns empty, and the review continues.
- Won't-fix tracking (classify/respond) requires `BITBUCKET_USERNAME` + `BITBUCKET_API_TOKEN` to be set. Both Bearer tokens (`ATCTT3…`) and App Passwords work.

---

## What to look for

### ✅ Healthy output
```
[revue] ── Step 2/4: AI review (orchestrated) — 4 file(s), 9 agent(s) available
[revue]   Running shared diff analysis...
[revue]   🔍 Analyzing your changes...
[revue]   
[revue]   I've detected modifications in:
[revue]     🔧 Core orchestration changes
[revue]   
[revue]   To ensure quality, I'm bringing in:
[revue]     → 🏗️ Architecture Agent for module organization review
[revue]   
[revue]   Starting review...
[revue]   4 agent(s) succeeded, 0 failed.
```

### ❌ Shared analysis fallback (bad)
```
[revue]   Shared analysis unavailable (fallback) — running all agents as fallback.
```
If you see this, check the line above it — the warning log will show the real error:
```
Shared analysis failed: <actual error here>
```

### ❌ All agents failed
```
[revue] ✗ All agents failed — review aborted.
```
Check API key validity (see Prerequisites above).

---

## Debugging tips

### See the raw LLM response
Add a quick probe script to inspect what Claude actually returns:

```bash
source ~/.zshenv && cd Projects/revue.io && export PYTHONPATH="$(pwd)/src" && python3 - <<'EOF'
from revue.core.config_loader import load_config
from revue.core.ai_client import create_ai_client
from revue.core.diff_parser import parse_diff_file
from revue.core.shared_analysis import (
    SHARED_ANALYSIS_PROMPT, _ANTHROPIC_JSON_SUFFIX,
    _build_diff_summary, _detect_languages
)

config = load_config('.revue.yml')
client = create_ai_client(config)
changes = parse_diff_file('/tmp/revue_pr.diff')[:2]
diff_summary = _build_diff_summary(changes, 100)
prompt = SHARED_ANALYSIS_PROMPT.format(diff_summary=diff_summary) + _ANTHROPIC_JSON_SUFFIX

raw = client.complete([{'role': 'user', 'content': prompt}])
print(f"Raw length: {len(raw)}")
print(f"Raw repr: {repr(raw[:500])}")
EOF
```

### Check diff size before running
```bash
wc -l /tmp/revue_pr.diff
# If > 10000 lines → diff-limit will fire, agents won't run
# Fix: ensure .revue.yml ignore_patterns excludes irrelevant files
```

---

## One-liner for quick re-runs

Once diff and PR description are generated (steps 1–2), save this as an alias or just re-run step 3. The diff and PR description files persist in `/tmp/` for the session.

```bash
# Quick alias (add to ~/.zshenv)
alias revue-local='source ~/.zshenv && cd ~/Projects/revue.io && export APP_ENV=staging PYTHONPATH="$(pwd)/src" && python3 -u src/revue/cli.py review --diff /tmp/revue_pr.diff --platform bitbucket --pr-id "${PR_ID}" --workspace cbscd --repo-slug revue --bb-username "${BITBUCKET_USERNAME}" --bb-token "${BITBUCKET_API_TOKEN}" --provider "${AI_PROVIDER:-openrouter}" --model "${AI_MODEL:-deepseek/deepseek-v4-pro}" --config .revue.yml --comment-style per-issue --pr-description-file /tmp/revue_pr_description.txt'
```

---

## Environment variable reference

| Variable | Required | Description |
|---|---|---|
| `AI_API_KEY` | ✅ | OpenRouter API key (default). Use Anthropic / OpenAI key if overriding `AI_PROVIDER`. |
| `AI_PROVIDER` | ✅ | `openrouter` (default), `anthropic`, or `openai` |
| `AI_MODEL` | ✅ | e.g. `deepseek/deepseek-v4-pro` (default), `claude-sonnet-4-5-20250929`, `gpt-4o-mini` |
| `REVUE_TIER_OVERRIDE` | ✅ (staging) | Set to `pro` for local testing |
| `BITBUCKET_USERNAME` | ✅ | Your Bitbucket username |
| `BITBUCKET_API_TOKEN` | ✅ | Bitbucket repository access token |
| `APP_ENV` | ✅ | Set to `staging` to enable tier override |
| `PYTHONPATH` | ✅ | Must include `$(pwd)/src` |
