#!/usr/bin/env python3
"""Post Revue findings to GitLab MR as discussions."""
import json
import os
import urllib.request
import urllib.error

API_URL = os.environ["CI_API_V4_URL"]
PROJECT_ID = os.environ["CI_PROJECT_ID"]
MR_IID = os.environ["CI_MERGE_REQUEST_IID"]
TOKEN = os.environ["CI_JOB_TOKEN"]

with open("review.json") as f:
    review = json.load(f)

headers = {"PRIVATE-TOKEN": TOKEN, "Content-Type": "application/json"}


def post(url: str, payload: dict) -> None:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    urllib.request.urlopen(req)


# Post summary as MR note
if summary := review.get("summary"):
    try:
        post(
            f"{API_URL}/projects/{PROJECT_ID}/merge_requests/{MR_IID}/notes",
            {"body": summary},
        )
        print("Posted summary comment")
    except urllib.error.URLError as e:
        print(f"Failed to post summary: {e}")

# Post inline discussions (max 50 to avoid API spam)
findings = review.get("findings", [])[:50]
posted = 0

for finding in findings:
    position = {
        "base_sha": os.environ.get("CI_MERGE_REQUEST_DIFF_BASE_SHA"),
        "head_sha": os.environ["CI_COMMIT_SHA"],
        "position_type": "text",
        "new_path": finding["file_path"],
        "new_line": finding["line_number"],
    }
    body = f"**{finding['severity']}**: {finding['message']}"
    if context := finding.get("context"):
        body += f"\n\n{context}"

    try:
        post(
            f"{API_URL}/projects/{PROJECT_ID}/merge_requests/{MR_IID}/discussions",
            {"body": body, "position": position},
        )
        posted += 1
    except urllib.error.URLError as e:
        print(f"Failed to post inline comment for {finding.get('file_path')}: {e}")

print(f"Posted {posted}/{len(findings)} inline comments")
