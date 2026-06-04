#!/usr/bin/env bash
# commit_compass.sh — persist docs/planning/mvp-compass.md to main, ORIGIN ONLY.
#
# Foreground, deterministic. Invoked by bitbucket-merge-pr Step 5b-2 AFTER the
# background agent has already edited the compass. Never run this backgrounded:
# it does git commit/push on the shared main checkout and must serialise with
# any concurrent PR merges (a detached push would hit non-fast-forward races).
#
# Usage: commit_compass.sh "<short message describing the compass change>"
#
# Behaviour:
#   1. Resolve the single reusable ticket by label `compass-auto`
#      (reuse → In Progress; none → create one; >1 → FAIL, never guess).
#   2. git add + commit the compass on main as chore(compass)[KEY]: <msg>.
#   3. Push to origin (Bitbucket) ONLY, surviving a moving main via
#      fetch + rebase + retry on non-fast-forward.
#   4. Transition the ticket to Done ONLY after a confirmed push.
#      Any failure → ticket left In Progress, non-zero exit, loud error.
#
# Requires the local protected-branch hooks to be disabled (maintainer setup) —
# it commits and pushes main directly. If a hook blocks it, the commit/push
# fails and the ticket is left In Progress (fail-safe), never --no-verify.
set -euo pipefail
source ~/.zshenv

MSG="${1:?Usage: commit_compass.sh \"<short message describing the compass change>\"}"

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"
COMPASS="docs/planning/mvp-compass.md"
LABEL="compass-auto"
JIRA="$REPO_ROOT/.claude/skills/jira-ticket/scripts"

# --- Preconditions --------------------------------------------------------
BRANCH="$(git branch --show-current)"
if [ "$BRANCH" != "main" ]; then
  echo "❌ commit-compass: must run on main (currently on '$BRANCH'). Aborting." >&2
  exit 1
fi
if [ ! -f "$COMPASS" ]; then
  echo "❌ commit-compass: $COMPASS not found. Aborting." >&2
  exit 1
fi
# Make origin/main current so the no-op and stranded-commit checks are accurate.
git fetch origin >/dev/null 2>&1 || true
UNPUSHED="$(git rev-list --count origin/main..HEAD 2>/dev/null || echo 0)"

if git diff --quiet -- "$COMPASS" && git diff --cached --quiet -- "$COMPASS"; then
  if [ "${UNPUSHED:-0}" -gt 0 ]; then
    echo "❌ commit-compass: no pending compass edit, but local main is $UNPUSHED commit(s) ahead of origin/main." >&2
    echo "   A prior run likely committed but failed to push. Push/investigate manually before re-running —" >&2
    echo "   refusing to report success on a stranded commit." >&2
    exit 1
  fi
  echo "ℹ️ commit-compass: no changes in $COMPASS — nothing to persist."
  exit 0
fi

# --- 1. Resolve the reusable compass ticket by label ----------------------
# Capture the search separately so a transient search failure ABORTS rather
# than silently falling through to "zero → create a duplicate ticket".
if ! SEARCH_OUT="$("$JIRA/jira_search.sh" "project = REVUE AND labels = $LABEL" 50)"; then
  echo "❌ commit-compass: Jira search for label '$LABEL' failed — refusing to create a ticket blindly. Aborting." >&2
  exit 1
fi
KEYS="$(printf '%s\n' "$SEARCH_OUT" | grep -oE '^REVUE-[0-9]+' || true)"
COUNT="$(printf '%s' "$KEYS" | grep -c . || true)"

if [ "$COUNT" -gt 1 ]; then
  echo "❌ commit-compass: $COUNT issues carry label '$LABEL' — ambiguous, refusing to guess." >&2
  printf '   %s\n' $KEYS >&2
  echo "   Consolidate to exactly one reusable ticket, then retry." >&2
  exit 1
elif [ "$COUNT" -eq 1 ]; then
  KEY="$KEYS"
  echo "↻ Reusing compass ticket $KEY"
else
  echo "＋ No '$LABEL' ticket exists — creating the single reusable one."
  if ! CREATE_OUT="$("$JIRA/jira_create.sh" \
    "chore(compass): recurring mvp-compass.md persistence (reusable — do not close permanently)" \
    "REVUE-269" "$LABEL" \
    "Reusable ticket for automated compass commits via the commit-compass skill. Cycled In Progress → Done on every post-merge compass update so the backlog is not polluted with per-update tickets. Do not delete; do not file new tickets for compass commits.")"; then
    echo "❌ commit-compass: jira_create failed:" >&2
    printf '%s\n' "$CREATE_OUT" >&2
    exit 1
  fi
  KEY="$(printf '%s\n' "$CREATE_OUT" | sed -n 's/^Created: *//p')"
  if [ -z "$KEY" ]; then
    echo "❌ commit-compass: jira_create returned no key:" >&2
    printf '%s\n' "$CREATE_OUT" >&2
    exit 1
  fi
  echo "  created $KEY"
fi

"$JIRA/jira_transition.sh" "$KEY" in-progress >/dev/null 2>&1 || true

# --- 2. Commit the compass on main ----------------------------------------
git add -- "$COMPASS"
# Scope the commit to the compass path only — a `git commit` with no pathspec
# would sweep any other staged change on main into a chore(compass) commit.
if ! git commit -m "chore(compass)[$KEY]: $MSG" -- "$COMPASS"; then
  echo "❌ commit-compass: git commit failed (is the main-commit hook active?). $KEY left In Progress." >&2
  exit 1
fi
LOCAL_SHA="$(git rev-parse --short HEAD)"

# --- 3. Push to origin ONLY, surviving a moving main ----------------------
PUSHED=0
for attempt in 1 2 3; do
  if git push origin main 2>/tmp/compass-push.err; then
    PUSHED=1; break
  fi
  echo "… push rejected (attempt $attempt/3) — rebasing onto origin/main and retrying" >&2
  git fetch origin >/dev/null 2>&1 || true
  if ! git rebase origin/main 2>/tmp/compass-rebase.err; then
    git rebase --abort 2>/dev/null || true
    echo "❌ commit-compass: rebase onto origin/main failed (conflict). $KEY left In Progress; commit is local ($LOCAL_SHA)." >&2
    cat /tmp/compass-rebase.err >&2 2>/dev/null || true
    exit 1
  fi
done

if [ "$PUSHED" -ne 1 ]; then
  echo "❌ commit-compass: push to origin failed after 3 attempts. $KEY left In Progress; commit is local ($(git rev-parse --short HEAD))." >&2
  cat /tmp/compass-push.err >&2 2>/dev/null || true
  exit 1
fi
echo "✅ Pushed compass commit $(git rev-parse --short HEAD) to origin/main"

# --- 4. Done ONLY after a confirmed push ----------------------------------
# The push already landed (durable success). A failed Jira transition here is a
# manual-followup nit, NOT a "did not land" failure — so do NOT exit non-zero
# (a non-zero exit is reserved strictly for "the compass did not reach origin").
if "$JIRA/jira_transition.sh" "$KEY" done >/dev/null 2>&1; then
  echo "✅ $KEY → Done"
else
  echo "⚠️ commit-compass: compass IS persisted to origin, but $KEY could not be set Done — transition it manually. (Commit/push succeeded; not a failure.)" >&2
fi

echo "✅ commit-compass: compass persisted to origin/main; $KEY cycled. (github/gitlab reconcile on the next sync_remotes.)"
