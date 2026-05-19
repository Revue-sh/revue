import base64
import os
import urllib.request
from typing import cast

from .helpers import FP_RE, _blank_slots, _http_get, _write


class BitbucketClient:
    _BASE = "https://api.bitbucket.org/2.0"
    _REPO = "cbscd/revue"
    _PRS = [133, 131, 130, 129, 119, 113, 112]

    def _auth(self) -> dict:
        creds = base64.b64encode(
            f"{os.environ['BITBUCKET_USERNAME']}:{os.environ['BITBUCKET_API_TOKEN']}".encode()
        ).decode()
        return {"Authorization": f"Basic {creds}"}

    def get(self, path: str) -> dict:
        return cast(dict, _http_get(f"{self._BASE}{path}", self._auth()))

    def get_raw(self, path: str) -> str:
        req = urllib.request.Request(f"{self._BASE}{path}", headers=self._auth())
        with urllib.request.urlopen(req) as resp:
            return resp.read().decode("utf-8", errors="replace")

    def file_diff(self, pr: int, file_path: str) -> str:
        try:
            full_diff = self.get_raw(f"/repositories/{self._REPO}/pullrequests/{pr}/diff")
            lines = full_diff.splitlines(keepends=True)
            capture = False
            result = []
            for line in lines:
                if line.startswith("diff --git"):
                    capture = file_path in line
                    if capture:
                        result = [line]
                elif capture:
                    result.append(line)
            return "".join(result)[:3000]
        except Exception:
            return ""

    def extract(self, target: int = 12) -> None:
        print("\n── Bitbucket ──")
        seen_files: dict[str, int] = {}
        fixtures = []

        for pr in self._PRS:
            if len(fixtures) >= target:
                break
            page = self.get(f"/repositories/{self._REPO}/pullrequests/{pr}/comments?pagelen=100")
            for c in page.get("values", []):
                if len(fixtures) >= target:
                    break
                body = c.get("content", {}).get("raw", "")
                if not FP_RE.search(body):
                    continue
                inline = c.get("inline", {})
                file_path = inline.get("path", "")
                to_line = inline.get("to")
                from_line = inline.get("from")
                if not file_path or not to_line:
                    continue
                key = f"{pr}:{file_path}"
                if seen_files.get(key, 0) >= 2:
                    continue
                seen_files[key] = seen_files.get(key, 0) + 1
                fixtures.append({
                    "platform": "bitbucket",
                    "source_pr": pr,
                    "file_path": file_path,
                    "diff_snippet": self.file_diff(pr, file_path),
                    "posted_line_to": to_line,
                    "posted_line_from": from_line,
                    "comment_body_excerpt": body[:300],
                    **_blank_slots(),
                })

        for i, f in enumerate(fixtures, 1):
            _write("bitbucket", i, f)
        print(f"  extracted {len(fixtures)} Bitbucket fixtures")
