#!/usr/bin/env python3
"""Migrate Revue.io stories from Taiga to Jira.

Reads kanban-board.md as source of truth, creates Epics + Tasks in Jira REVUE project.
Done stories are transitioned to Done. Open stories stay in To Do.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

# ── Config ────────────────────────────────────────────────────────────────────
JIRA_BASE = "https://urukia.atlassian.net"
JIRA_EMAIL = "dsanchezcisneros@gmail.com"
JIRA_TOKEN = os.environ["JIRA_API_TOKEN"]
PROJECT_KEY = "REVUE"

AUTH = (JIRA_EMAIL, JIRA_TOKEN)
HEADERS = {"Accept": "application/json", "Content-Type": "application/json"}

STATUS_DONE_ID = "10111"   # Done transition target id
STATUS_TODO_ID = "10109"   # To Do (default)

EPIC_TYPE_ID = "10113"
TASK_TYPE_ID = "10112"

# ── Data ──────────────────────────────────────────────────────────────────────
@dataclass
class Story:
    taiga_id: int
    title: str
    epic_name: str
    done: bool
    archived: bool = False

@dataclass
class Epic:
    name: str
    stories: list[Story] = field(default_factory=list)
    jira_key: Optional[str] = None

# ── Parse kanban-board.md ─────────────────────────────────────────────────────
def parse_kanban(path: str) -> list[Epic]:
    with open(path) as f:
        content = f.read()

    epics: dict[str, Epic] = {}
    current_epic = None

    for line in content.splitlines():
        # Epic header e.g. "### Epic E1 — Core Review Engine (9/9 ✅)"
        epic_match = re.match(r"^###\s+Epic\s+(E\d+\s+—\s+.+?)(?:\s+\(.*\))?$", line)
        if epic_match:
            name = epic_match.group(1).strip()
            if name not in epics:
                epics[name] = Epic(name=name)
            current_epic = epics[name]
            continue

        # Story line e.g. "- [x] **[62]** Workspace onboarding UI..."
        # or archived   "- ~~[39]~~ Self-service..."
        if current_epic is None:
            continue

        archived_match = re.match(r"^\s*-\s+~~\[(\d+)\]~~\s+(.+)", line)
        if archived_match:
            taiga_id = int(archived_match.group(1))
            title = re.sub(r"\s+→.*$", "", archived_match.group(2)).strip()
            title = re.sub(r"\*\(.*?\)\*$", "", title).strip()
            current_epic.stories.append(Story(
                taiga_id=taiga_id, title=title,
                epic_name=current_epic.name, done=True, archived=True
            ))
            continue

        story_match = re.match(r"^\s*-\s+\[([ x])\]\s+\*\*\[(\d+)\]\*\*\s+(.+)", line)
        if story_match:
            done = story_match.group(1) == "x"
            taiga_id = int(story_match.group(2))
            raw = story_match.group(3)
            # Strip trailing notes like "*(L, ~1 week)* ✅" or "*(M)* ✅ — Note:..."
            title = re.sub(r"\s+\*\(.*", "", raw).strip()
            title = re.sub(r"\s+✅.*$", "", title).strip()
            title = re.sub(r"\s+—\s+Note:.*$", "", title).strip()
            current_epic.stories.append(Story(
                taiga_id=taiga_id, title=title,
                epic_name=current_epic.name, done=done
            ))

    return list(epics.values())

# ── Jira helpers ──────────────────────────────────────────────────────────────
def jira_post(path: str, payload: dict) -> dict:
    url = f"{JIRA_BASE}{path}"
    r = httpx.post(url, auth=AUTH, headers=HEADERS, json=payload)
    if r.status_code not in (200, 201):
        print(f"  ❌ POST {path} → {r.status_code}: {r.text[:300]}")
        return {}
    return r.json()

def jira_get(path: str) -> dict:
    url = f"{JIRA_BASE}{path}"
    r = httpx.get(url, auth=AUTH, headers=HEADERS)
    return r.json()

def transition_to_done(issue_key: str):
    """Transition an issue to Done."""
    transitions = jira_get(f"/rest/api/3/issue/{issue_key}/transitions")
    done_id = None
    for t in transitions.get("transitions", []):
        if t["to"]["id"] == STATUS_DONE_ID:
            done_id = t["id"]
            break
    if not done_id:
        print(f"  ⚠️  No Done transition found for {issue_key}")
        return
    r = httpx.post(
        f"{JIRA_BASE}/rest/api/3/issue/{issue_key}/transitions",
        auth=AUTH, headers=HEADERS,
        json={"transition": {"id": done_id}}
    )
    if r.status_code != 204:
        print(f"  ⚠️  Transition failed for {issue_key}: {r.status_code}")

def create_epic(name: str) -> str:
    """Create an Epic, return its key."""
    payload = {
        "fields": {
            "project": {"key": PROJECT_KEY},
            "issuetype": {"id": EPIC_TYPE_ID},
            "summary": name,
        }
    }
    result = jira_post("/rest/api/3/issue", payload)
    return result.get("key", "")

def create_task(story: Story, epic_key: str) -> str:
    """Create a Task under an Epic, return its key."""
    summary = f"[{story.taiga_id}] {story.title}"
    payload = {
        "fields": {
            "project": {"key": PROJECT_KEY},
            "issuetype": {"id": TASK_TYPE_ID},
            "summary": summary,
            "parent": {"key": epic_key},
        }
    }
    result = jira_post("/rest/api/3/issue", payload)
    return result.get("key", "")

# ── ID mapping file ───────────────────────────────────────────────────────────
MAPPING_PATH = os.path.join(os.path.dirname(__file__), "taiga_to_jira_mapping.json")

def load_mapping() -> dict:
    if os.path.exists(MAPPING_PATH):
        with open(MAPPING_PATH) as f:
            return json.load(f)
    return {}

def save_mapping(mapping: dict):
    with open(MAPPING_PATH, "w") as f:
        json.dump(mapping, f, indent=2)
    print(f"\n📄 Mapping saved to {MAPPING_PATH}")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    kanban_path = os.path.join(
        os.path.dirname(__file__), "..", "docs", "kanban-board.md"
    )
    print(f"📖 Parsing {kanban_path}...")
    epics = parse_kanban(kanban_path)

    total_stories = sum(len(e.stories) for e in epics)
    print(f"✅ Found {len(epics)} epics, {total_stories} stories\n")

    mapping = load_mapping()  # resume support
    created_epics = 0
    created_tasks = 0
    skipped = 0

    for epic in epics:
        print(f"\n── {epic.name} ({len(epic.stories)} stories) ──")

        epic_map_key = f"epic:{epic.name}"
        if epic_map_key in mapping:
            epic_key = mapping[epic_map_key]
            print(f"  ↩️  Epic already exists: {epic_key}")
        else:
            epic_key = create_epic(epic.name)
            if not epic_key:
                print(f"  ❌ Failed to create epic, skipping stories")
                continue
            mapping[epic_map_key] = epic_key
            save_mapping(mapping)
            print(f"  ✨ Epic created: {epic_key}")
            created_epics += 1
            time.sleep(0.2)

        for story in epic.stories:
            story_map_key = f"story:{story.taiga_id}"
            if story_map_key in mapping:
                print(f"  ↩️  [{story.taiga_id}] already migrated → {mapping[story_map_key]}")
                skipped += 1
                continue

            task_key = create_task(story, epic_key)
            if not task_key:
                print(f"  ❌ Failed to create task [{story.taiga_id}]")
                continue

            mapping[story_map_key] = task_key
            save_mapping(mapping)
            created_tasks += 1

            if story.done or story.archived:
                transition_to_done(task_key)
                status = "✅ Done" if story.done else "🗄️ Archived→Done"
            else:
                status = "📋 To Do"

            print(f"  {status}  [{story.taiga_id}] → {task_key}: {story.title[:60]}")
            time.sleep(0.15)  # be nice to the API

    print(f"\n🎉 Migration complete!")
    print(f"   Epics created:  {created_epics}")
    print(f"   Tasks created:  {created_tasks}")
    print(f"   Skipped (already done): {skipped}")
    print(f"\n🔗 Board: https://urukia.atlassian.net/jira/software/projects/REVUE/boards/101")

if __name__ == "__main__":
    main()
