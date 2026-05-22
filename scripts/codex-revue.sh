#!/usr/bin/env bash
# Run headless Codex (`codex exec`) with this project's plain env vars loaded
# from .envrc — skipping any 1Password (`op read ...`) lookups so no
# interactive auth is triggered.
#
# Use this in place of `direnv exec . codex exec ...` when the skill you want
# to run only needs plain values (BITBUCKET_USERNAME, POSTGRES_*, AI_*, etc.)
# and not GITHUB_TOKEN / GITLAB_TOKEN / REVUE_ANTHROPIC_API_KEY / OPENROUTER_API_KEY.
#
# Usage:
#   scripts/codex-revue.sh '<prompt>'
#   scripts/codex-revue.sh --some-codex-flag '<prompt>'
#
# Optional: CODEX_REVUE_REQUIRE="VAR1 VAR2" — fail fast if those vars aren't
# set after .envrc loading. Example:
#   CODEX_REVUE_REQUIRE="BITBUCKET_USERNAME JIRA_API_TOKEN" \
#     scripts/codex-revue.sh '<prompt>'

set -euo pipefail

warn() { printf 'codex-revue: %s\n' "$*" >&2; }
die()  { printf 'codex-revue: error — %s\n' "$*" >&2; exit 2; }

# ---------------------------------------------------------------------------
# Resolve repo root and main repo .envrc

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" \
  || REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

ENVRC="$REPO_ROOT/.envrc"
if [ ! -f "$ENVRC" ]; then
  COMMON_DIR="$(git rev-parse --git-common-dir 2>/dev/null || true)"
  if [ -n "$COMMON_DIR" ]; then
    # --git-common-dir can be relative to REPO_ROOT; absolutize before use.
    [[ "$COMMON_DIR" = /* ]] || COMMON_DIR="$REPO_ROOT/$COMMON_DIR"
    if [ -d "$COMMON_DIR" ]; then
      MAIN_REPO="$(cd "$COMMON_DIR/.." && pwd)"
      [ -f "$MAIN_REPO/.envrc" ] && ENVRC="$MAIN_REPO/.envrc"
    fi
  fi
fi

# ---------------------------------------------------------------------------
# Safe .envrc parser — does NOT `source` the file.
#
# Accepts:    export KEY=value
#             export KEY="value"
#             export KEY='value'
# Rejects (with a one-line warning):
#   - line continuations (`\` at EOL)
#   - command substitution `$(...)` or backticks (any kind)
#   - other shell metacharacters that could trigger evaluation
#
# 1Password lookups (`$(op ...)`, `op://`) are skipped silently — that's the
# whole point of this wrapper.

load_safe_envrc() {
  local envrc="$1"
  if [ ! -f "$envrc" ]; then
    warn "warning — .envrc not found at $envrc; proceeding without project env"
    return 0
  fi

  local line key value loaded_count=0
  while IFS= read -r line || [ -n "$line" ]; do
    # Trim leading whitespace
    line="${line#"${line%%[![:space:]]*}"}"

    # Skip blanks and comments
    case "$line" in ''|'#'*) continue ;; esac

    # Refuse multi-line exports (line continuation).
    if [[ "$line" == *\\ ]]; then
      warn "warning — skipping multi-line export (line continuation unsupported): ${line:0:60}..."
      continue
    fi

    # Match: export KEY=...
    if [[ ! "$line" =~ ^export[[:space:]]+([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
      continue
    fi
    key="${BASH_REMATCH[1]}"
    value="${BASH_REMATCH[2]}"

    # Reject command substitution (matches actual syntax, not substring).
    # 1Password-backed exports skip silently; anything else warns.
    if [[ "$value" == *'$('* ]] || [[ "$value" == *'`'* ]]; then
      if [[ "$value" == *'$(op '* ]] || [[ "$value" == *'op://'* ]]; then
        continue
      fi
      warn "warning — skipping $key (value contains command substitution)"
      continue
    fi

    # Strip a single layer of outer quotes (double or single) if matched.
    if [[ "$value" =~ ^\"(.*)\"$ ]]; then
      value="${BASH_REMATCH[1]}"
    elif [[ "$value" =~ ^\'(.*)\'$ ]]; then
      value="${BASH_REMATCH[1]}"
    fi

    export "$key=$value"
    loaded_count=$((loaded_count + 1))
  done < "$envrc"

  if [ "$loaded_count" -eq 0 ]; then
    warn "warning — $envrc parsed but produced 0 exports (filter may be too strict)"
  fi
}

load_safe_envrc "$ENVRC"

# Optional required-vars assertion. Caller can set CODEX_REVUE_REQUIRE to a
# space-separated list of var names that must be present after loading.
if [ -n "${CODEX_REVUE_REQUIRE:-}" ]; then
  missing=()
  for var in $CODEX_REVUE_REQUIRE; do
    if [ -z "${!var:-}" ]; then
      missing+=("$var")
    fi
  done
  if [ "${#missing[@]}" -gt 0 ]; then
    die "required env var(s) not set after loading $ENVRC: ${missing[*]}"
  fi
fi

# ---------------------------------------------------------------------------
# Suppress direnv re-activation in Codex's child shells.
#
# Codex spawns shell commands as `/bin/zsh -lc ...`. Login zsh sources ~/.zshrc,
# which runs `eval "$(direnv hook zsh)"`, which then re-activates .envrc and
# fires every `op read` — opening the 1Password UI in each child shell.
#
# Point ZDOTDIR at an empty directory so child zsh skips .zshrc entirely; pair
# with shell_environment_policy.inherit=all so child shells still inherit the
# vars we loaded above (BITBUCKET_USERNAME et al).
#
# Also unset DIRENV_* state from the parent so any direnv that does get reached
# doesn't try to "diff" against a phantom previous activation and re-run hooks.

EMPTY_ZDOTDIR="${TMPDIR:-/tmp}/codex-revue-zdotdir"
mkdir -p "$EMPTY_ZDOTDIR"
export ZDOTDIR="$EMPTY_ZDOTDIR"
unset DIRENV_DIR DIRENV_FILE DIRENV_WATCHES DIRENV_DIFF || true

# ---------------------------------------------------------------------------
exec codex exec \
  --sandbox workspace-write \
  -c sandbox_workspace_write.network_access=true \
  -c shell_environment_policy.inherit=all \
  --ephemeral \
  --skip-git-repo-check \
  "$@"
