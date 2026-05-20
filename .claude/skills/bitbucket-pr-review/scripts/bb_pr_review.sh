#!/usr/bin/env bash
# Fetch PR info, CI pipeline status, comments, and optionally pipeline step logs.
#
# Usage:
#   bb_pr_review.sh [pr_number] [--logs [step_pattern]] [workspace/repo]
#
#   pr_number    — optional; auto-detected from current git branch if omitted
#   --logs       — also fetch and print the log for the matching pipeline step
#   step_pattern — substring to match step name (default: "Revue AI Code Review")
#   workspace/repo — defaults to cbscd/revue
#
# Examples:
#   bb_pr_review.sh                        # auto-detect PR, no logs
#   bb_pr_review.sh 82                     # PR #82, no logs
#   bb_pr_review.sh 82 --logs              # PR #82 + Revue step log
#   bb_pr_review.sh 82 --logs "Run Tests"  # PR #82 + Run Tests step log

set -euo pipefail
source ~/.zshenv

# ── Argument parsing ──────────────────────────────────────────────────────────
PR=""
FETCH_LOGS=false
LOG_STEP_PATTERN="Revue AI Code Review"
REPO="cbscd/revue"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --logs)
      FETCH_LOGS=true
      # If the next arg exists and doesn't start with - and isn't a repo slug, treat as step pattern
      if [[ $# -gt 1 && "$2" != --* && "$2" != */* ]]; then
        LOG_STEP_PATTERN="$2"
        shift
      fi
      ;;
    */*)
      REPO="$1"
      ;;
    [0-9]*)
      PR="$1"
      ;;
    *)
      echo "ERROR: unrecognised argument: $1" >&2
      exit 1
      ;;
  esac
  shift
done

BASE="https://api.bitbucket.org/2.0/repositories/${REPO}"
AUTH_ARGS=(-u "${BITBUCKET_USERNAME}:${BITBUCKET_API_TOKEN}")

# ── Resolve PR number ─────────────────────────────────────────────────────────
if [[ -z "$PR" ]]; then
  BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
  if [[ -z "$BRANCH" ]]; then
    echo "ERROR: no PR number given and not in a git repo" >&2
    exit 1
  fi
  ENCODED_BRANCH=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$BRANCH")
  PR=$(curl -sf "${AUTH_ARGS[@]}" \
    "${BASE}/pullrequests?state=OPEN&q=source.branch.name%3D%22${ENCODED_BRANCH}%22&pagelen=1" \
    | python3 -c "
import sys, json
prs = json.load(sys.stdin).get('values', [])
print(prs[0]['id'] if prs else '')
" 2>/dev/null || echo "")
  if [[ -z "$PR" ]]; then
    echo "ERROR: no open PR found for branch '${BRANCH}' in ${REPO}" >&2
    exit 1
  fi
fi

# ── PR info ───────────────────────────────────────────────────────────────────
echo "=== PR INFO ==="
curl -sf "${AUTH_ARGS[@]}" "${BASE}/pullrequests/${PR}" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('PR #' + str(d['id']) + ': ' + d['title'])
print('State : ' + d['state'])
print('Branch: ' + d['source']['branch']['name'] + ' → ' + d['destination']['branch']['name'])
print('URL   : ' + d['links']['html']['href'])
"

# ── Pipeline / build statuses ─────────────────────────────────────────────────
echo ""
echo "=== PIPELINE ==="
PIPELINE_BUILD_NUMBER=$(curl -sf "${AUTH_ARGS[@]}" "${BASE}/pullrequests/${PR}/statuses" | python3 -c "
import sys, json
vals = json.load(sys.stdin).get('values', [])
if not vals:
    print('')
else:
    for s in vals:
        icon = {'SUCCESSFUL': '✅', 'FAILED': '❌', 'INPROGRESS': '⏳', 'STOPPED': '⏹️'}.get(s.get('state',''), '❓')
        print(icon + ' ' + s.get('state','?') + '  ' + s.get('name','') + '  ' + s.get('url',''))
    # Emit build number on last line prefixed with BUILD: so the shell can parse it
    url = vals[0].get('url', '')
    build_num = url.rstrip('/').split('/')[-1] if '/pipelines/results/' in url else ''
    print('BUILD:' + build_num)
")

# Print all but the last BUILD: line
echo "$PIPELINE_BUILD_NUMBER" | grep -v '^BUILD:' || true
BUILD_NUM=$(echo "$PIPELINE_BUILD_NUMBER" | grep '^BUILD:' | cut -d: -f2 || echo "")

# ── Comments ──────────────────────────────────────────────────────────────────
echo ""
echo "=== COMMENTS ==="
curl -sf "${AUTH_ARGS[@]}" "${BASE}/pullrequests/${PR}/comments?pagelen=100" | python3 -c "
import sys, json, re

vals = json.load(sys.stdin).get('values', [])

SEVERITY_RE = re.compile(r'\[(HIGH|MEDIUM|LOW|INFO)\]')
ICONS = {'HIGH': '🔴', 'MEDIUM': '🟡', 'LOW': '🔵', 'INFO': 'ℹ️'}

# Separate root comments from replies
roots = [c for c in vals if not c.get('parent')]
replies = {}
for c in vals:
    if c.get('parent'):
        pid = c['parent']['id']
        replies.setdefault(pid, []).append(c)

counts = {'HIGH': 0, 'MEDIUM': 0, 'LOW': 0, 'INFO': 0, 'OTHER': 0}
general = []
inline = []

for c in roots:
    raw = (c.get('content') or {}).get('raw', '')
    severities = SEVERITY_RE.findall(raw)
    for s in severities:
        counts[s] += 1
    if not severities:
        counts['OTHER'] += 1

    cid    = c['id']
    author = (c.get('user') or {}).get('display_name', 'unknown')
    date   = (c.get('created_on') or '')[:10]
    preview = raw[:200].replace('\n', ' ')
    rep_count = len(replies.get(cid, []))
    rep_label = f' [{rep_count} repl{\"y\" if rep_count == 1 else \"ies\"}]' if rep_count else ''
    sev_icons = ' '.join(ICONS.get(s,'') + ' [' + s + ']' for s in dict.fromkeys(severities)) or ''

    if c.get('inline'):
        loc = c['inline'].get('path','?') + ':' + str(c['inline'].get('to','?'))
        inline.append((cid, author, date, loc, sev_icons, preview, rep_label, cid in replies))
    else:
        general.append((cid, author, date, sev_icons, preview, rep_label))

total = len(roots)
print(f'Total: {total} top-level comment(s) ({len(inline)} inline, {len(general)} general)')
print(f'Severity: 🔴 {counts[\"HIGH\"]} HIGH  🟡 {counts[\"MEDIUM\"]} MEDIUM  🔵 {counts[\"LOW\"]} LOW  ℹ️ {counts[\"INFO\"]} INFO')
print()

if inline:
    print('--- Inline ---')
    for (cid, author, date, loc, sev, preview, rep_label, has_replies) in inline:
        print(f'[#{cid}] {author} ({date}) {loc}{rep_label}')
        if sev:
            print(f'  Severity: {sev}')
        print(f'  {preview}')
        print()

if general:
    print('--- General ---')
    for (cid, author, date, sev, preview, rep_label) in general:
        print(f'[#{cid}] {author} ({date}){rep_label}')
        if sev:
            print(f'  Severity: {sev}')
        print(f'  {preview}')
        print()

# Unresolved inline (no replies)
unresolved = [(cid, loc, sev, preview) for (cid,_,_,loc,sev,preview,_,has_replies) in inline if not has_replies]
if unresolved:
    print('--- Unresolved inline (no reply yet) ---')
    for (cid, loc, sev, preview) in unresolved:
        print(f'  [#{cid}] {loc} {sev}  {preview[:100]}')
"

# ── Pipeline step log (optional) ───────────────────────────────────────────────
if [[ "$FETCH_LOGS" == "true" ]]; then
  echo ""
  echo "=== LOG: ${LOG_STEP_PATTERN} ==="

  if [[ -z "$BUILD_NUM" ]]; then
    echo "ERROR: could not determine pipeline build number — no log available." >&2
  else
    # Find pipeline UUID by build number (API only supports sort=-created_on)
    PIPELINE_UUID=$(curl -sf "${AUTH_ARGS[@]}" \
      "${BASE}/pipelines/?sort=-created_on&pagelen=50" \
      | python3 -c "
import sys, json
vals = json.load(sys.stdin).get('values', [])
target = int('${BUILD_NUM}')
for p in vals:
    if p.get('build_number') == target:
        print(p['uuid'])
        break
" 2>/dev/null || echo "")

    if [[ -z "$PIPELINE_UUID" ]]; then
      echo "ERROR: pipeline #${BUILD_NUM} not found in recent pipelines." >&2
    else
      # URL-encode the UUID (wrap { } → %7B %7D)
      ENC_PIPELINE=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$PIPELINE_UUID")

      # Find the step UUID whose name contains the pattern
      STEP_UUID=$(curl -sf "${AUTH_ARGS[@]}" \
        "${BASE}/pipelines/${ENC_PIPELINE}/steps/" \
        | python3 -c "
import sys, json
steps = json.load(sys.stdin).get('values', [])
pattern = '${LOG_STEP_PATTERN}'.lower()
for s in steps:
    if pattern in s.get('name','').lower():
        print(s['uuid'])
        break
" 2>/dev/null || echo "")

      if [[ -z "$STEP_UUID" ]]; then
        echo "ERROR: no step matching '${LOG_STEP_PATTERN}' found in pipeline #${BUILD_NUM}." >&2
        echo "Available steps:"
        curl -sf "${AUTH_ARGS[@]}" "${BASE}/pipelines/${ENC_PIPELINE}/steps/" \
          | python3 -c "
import sys, json
for s in json.load(sys.stdin).get('values', []):
    print('  -', s.get('name'))
"
      else
        ENC_STEP=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$STEP_UUID")
        curl -sfL "${AUTH_ARGS[@]}" \
          "${BASE}/pipelines/${ENC_PIPELINE}/steps/${ENC_STEP}/log"
      fi
    fi
  fi
fi
