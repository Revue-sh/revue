import json
import subprocess

from .helpers import FP_RE, _blank_slots, _write


class GitHubClient:
    _REPO = "repos/cbscd/revue-test-github"
    _PRS = [11, 9, 8, 7, 6, 5, 4]

    def get(self, path: str) -> list | dict:
        result = subprocess.run(
            ["gh", "api", path, "--paginate"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"gh api {path}: {result.stderr.strip()}")
        return json.loads(result.stdout)

    def extract(self, target: int = 12) -> None:
        print("\n── GitHub ──")
        seen_files: dict[str, int] = {}
        fixtures = []

        for pr in self._PRS:
            if len(fixtures) >= target:
                break
            comments = self.get(f"{self._REPO}/pulls/{pr}/comments")
            for c in comments:
                if len(fixtures) >= target:
                    break
                body = c.get("body", "")
                if not FP_RE.search(body):
                    continue
                file_path = c.get("path", "")
                line = c.get("line") or c.get("original_line")
                diff_hunk = c.get("diff_hunk", "")
                if not file_path or not line or not diff_hunk:
                    continue
                key = f"{pr}:{file_path}"
                if seen_files.get(key, 0) >= 2:
                    continue
                seen_files[key] = seen_files.get(key, 0) + 1
                fixtures.append({
                    "platform": "github",
                    "source_pr": pr,
                    "file_path": file_path,
                    "diff_snippet": diff_hunk,
                    "posted_line": line,
                    "posted_side": c.get("side", "RIGHT"),
                    "comment_body_excerpt": body[:300],
                    **_blank_slots(),
                })

        for i, f in enumerate(fixtures, 1):
            _write("github", i, f)
        print(f"  extracted {len(fixtures)} GitHub fixtures")
