from __future__ import annotations

import os

from .helpers import FP_RE, _blank_slots, _http_get, _write


class GitLabClient:
    _BASE = "https://gitlab.com/api/v4"
    _PROJECT = "urukia-group%2Frevue-test-gitlab"
    _MRS = [23, 14, 13, 11, 10, 9, 8]

    def get(self, path: str) -> list | dict:
        return _http_get(f"{self._BASE}{path}", {"PRIVATE-TOKEN": os.environ["GITLAB_TOKEN"]})

    def file_diff(self, mr_iid: int, file_path: str) -> str:
        try:
            diffs = self.get(f"/projects/{self._PROJECT}/merge_requests/{mr_iid}/diffs?per_page=100")
            if isinstance(diffs, dict):
                diffs = diffs.get("diffs", [])
            for d in diffs:
                if d.get("new_path") == file_path or d.get("old_path") == file_path:
                    return d.get("diff", "")
        except Exception:
            pass
        return ""

    def extract(self, target: int = 12) -> None:
        print("\n── GitLab ──")
        seen_files: dict[str, int] = {}
        fixtures = []

        for mr in self._MRS:
            if len(fixtures) >= target:
                break
            discussions = self.get(f"/projects/{self._PROJECT}/merge_requests/{mr}/discussions?per_page=100")
            for disc in discussions:
                if len(fixtures) >= target:
                    break
                for note in disc.get("notes", []):
                    if len(fixtures) >= target:
                        break
                    body = note.get("body", "")
                    if not FP_RE.search(body):
                        continue
                    pos = note.get("position", {})
                    if not pos or pos.get("position_type") != "text":
                        continue
                    file_path = pos.get("new_path", "")
                    new_line = pos.get("new_line")
                    if not file_path or not new_line:
                        continue
                    key = f"{mr}:{file_path}"
                    if seen_files.get(key, 0) >= 2:
                        continue
                    seen_files[key] = seen_files.get(key, 0) + 1
                    fixtures.append({
                        "platform": "gitlab",
                        "source_mr": mr,
                        "file_path": file_path,
                        "diff_snippet": self.file_diff(mr, file_path)[:3000],
                        "posted_line": new_line,
                        "posted_base_sha": pos.get("base_sha", ""),
                        "posted_head_sha": pos.get("head_sha", ""),
                        "posted_start_sha": pos.get("start_sha", ""),
                        "comment_body_excerpt": body[:300],
                        **_blank_slots(),
                    })

        for i, f in enumerate(fixtures, 1):
            _write("gitlab", i, f)
        print(f"  extracted {len(fixtures)} GitLab fixtures")
