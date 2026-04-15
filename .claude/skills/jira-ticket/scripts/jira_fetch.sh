#!/usr/bin/env bash
# Fetch one or more Jira tickets and print a clean summary.
# Usage: jira_fetch.sh REVUE-112 [REVUE-113 ...]
set -euo pipefail
source ~/.zshenv

PYTHON=$(cat <<'EOF'
import json, sys

def extract_text(node):
    if isinstance(node, str): return node
    text = ""
    if "text" in node: text += node["text"]
    for c in node.get("content", []): text += extract_text(c)
    return text

d = json.load(sys.stdin)
f = d.get("fields", {})
print("Key:     ", d.get("key"))
print("Summary: ", f.get("summary"))
print("Status:  ", f.get("status", {}).get("name"))
print("Type:    ", f.get("issuetype", {}).get("name"))
print("Priority:", f.get("priority", {}).get("name", "none"))
parent = f.get("parent")
if parent:
    print("Epic:    ", parent.get("key"), "—", parent.get("fields", {}).get("summary", ""))
desc = f.get("description")
if desc:
    print("\nDescription:\n" + extract_text(desc))
EOF
)

for KEY in "$@"; do
    echo "=== $KEY ==="
    curl -s \
        -u "${BITBUCKET_USERNAME}:${JIRA_API_TOKEN}" \
        "https://urukia.atlassian.net/rest/api/3/issue/${KEY}" \
        | python3 -c "$PYTHON"
    echo
done
