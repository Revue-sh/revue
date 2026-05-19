"""Test: vendored modules are byte-identical to source-of-truth (after rewrites).

Drift between ``src/revue/`` and ``packaging/revue/src/revue_skill/vendored/``
silently breaks the wheel. This test loads the vendor manifest, re-applies
rewrites in-memory against the canonical sources, and asserts that the
committed copy matches.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

PACKAGING_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = PACKAGING_DIR.parent.parent
SOURCES_YAML = PACKAGING_DIR / "tools" / "sources.yaml"


def _entries() -> list[dict]:
    raw = yaml.safe_load(SOURCES_YAML.read_text(encoding="utf-8"))
    out: list[dict] = []
    for section in ("skill", "vendored", "agent_prompts"):
        for item in raw.get(section, []):
            if "source_dir" in item:
                src_dir = REPO_ROOT / item["source_dir"]
                tgt_dir = PACKAGING_DIR / item["target_dir"]
                for src_file in src_dir.rglob("*"):
                    if not src_file.is_file():
                        continue
                    rel = src_file.relative_to(src_dir)
                    out.append(
                        {
                            "source": src_file,
                            "target": tgt_dir / rel,
                            "rewrites": [],
                            "id": str(rel),
                        }
                    )
            else:
                out.append(
                    {
                        "source": REPO_ROOT / item["source"],
                        "target": PACKAGING_DIR / item["target"],
                        "rewrites": item.get("rewrite_imports", []),
                        "id": item["source"],
                    }
                )
    return out


ENTRIES = _entries()


@pytest.mark.parametrize("entry", ENTRIES, ids=[e["id"] for e in ENTRIES])
def test_vendored_file_matches_source(entry: dict) -> None:
    assert entry["source"].is_file(), f"source file missing: {entry['source']}"
    assert entry["target"].is_file(), f"vendored file missing: {entry['target']}"

    expected = entry["source"].read_bytes()
    if entry["rewrites"]:
        text = expected.decode("utf-8")
        for rule in entry["rewrites"]:
            text = text.replace(rule["from"], rule["to"])
        expected = text.encode("utf-8")

    actual = entry["target"].read_bytes()
    if expected != actual:
        rel_source = entry["source"].relative_to(REPO_ROOT)
        rel_target = entry["target"].relative_to(PACKAGING_DIR)
        pytest.fail(
            f"vendored file drifted from source-of-truth.\n"
            f"  source: {rel_source}\n"
            f"  vendored: {rel_target}\n"
            f"Re-run:  python packaging/revue/tools/vendor_sources.py --clean"
        )


