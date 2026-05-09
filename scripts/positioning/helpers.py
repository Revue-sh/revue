import json
import re
import urllib.request
from pathlib import Path

OUT_DIR = Path(__file__).parent.parent.parent / "src/revue/tests/fixtures/positioning"
FP_RE = re.compile(r"\[//\]: # \(revue:fp:[a-f0-9]+\)")


def _http_get(url: str, headers: dict) -> list | dict:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def _write(platform: str, idx: int, data: dict) -> None:
    path = OUT_DIR / platform / f"fixture_{idx:02d}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    print(f"  wrote {path.relative_to(Path.cwd())}")


def _blank_slots() -> dict:
    return {
        "reported_line": None,
        "replacement_line_count": 1,
        "expected_position": None,
        "expected_api_params": None,
    }
