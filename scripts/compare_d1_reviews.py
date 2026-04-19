#!/usr/bin/env python3
"""
compare_d1_reviews.py — Compare Revue review quality pre- vs post-D1 prompt restructure.

Usage:
    python3 scripts/compare_d1_reviews.py docs/review-comparisons/REVUE-152/

Reads pre-d1-{small,medium,large}.json and post-d1-{small,medium,large}.json,
produces ANALYSIS.md with per-size finding counts, deltas, severity breakdown,
and an overall PASS/FAIL verdict.

Equivalence threshold: |Δ findings| ≤ 2 per diff size (AC1–AC3).
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

SIZES = ["small", "medium", "large"]
THRESHOLD = 2  # AC1-AC3: |Δ findings| ≤ 2


def load_findings(path: Path) -> list[dict]:
    """Load all findings from a Revue --output json file.

    Strips [revue] log lines that the CLI writes to stdout alongside JSON.
    """
    text = path.read_text()
    json_lines = [line for line in text.splitlines() if not line.startswith("[revue]")]
    raw = json.loads("\n".join(json_lines))
    findings: list[dict] = []

    if isinstance(raw, list):
        for entry in raw:
            findings.extend(_parse_review_text(entry.get("review", "")))
    elif isinstance(raw, dict):
        if "findings" in raw:
            return raw["findings"] if isinstance(raw["findings"], list) else []
        for entry in raw.get("results", []):
            findings.extend(_parse_review_text(entry.get("review", "")))

    return findings


def _parse_review_text(text: str) -> list[dict]:
    if not text:
        return []
    try:
        clean = text.strip()
        if clean.startswith("```"):
            clean = "\n".join(clean.split("\n")[1:])
        if clean.endswith("```"):
            clean = "\n".join(clean.split("\n")[:-1])
        clean = clean.strip()
        data = json.loads(clean)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("findings", [])
    except Exception:
        pass
    return []


def count_by_severity(findings: list[dict]) -> Counter:
    c: Counter = Counter()
    for f in findings:
        sev = f.get("severity", "info").lower()
        c[sev] += 1
    return c


def analyse(comparison_dir: Path) -> None:
    rows = []
    all_pass = True

    for size in SIZES:
        pre_path = comparison_dir / f"pre-d1-{size}.json"
        post_path = comparison_dir / f"post-d1-{size}.json"

        if not pre_path.exists():
            print(f"ERROR: {pre_path} not found — run scripts/run-d1-regression.sh first", file=sys.stderr)
            sys.exit(1)
        if not post_path.exists():
            print(f"ERROR: {post_path} not found — run scripts/run-d1-regression.sh first", file=sys.stderr)
            sys.exit(1)

        pre = load_findings(pre_path)
        post = load_findings(post_path)
        delta = len(post) - len(pre)
        verdict = "PASS" if abs(delta) <= THRESHOLD else "FAIL"
        if verdict == "FAIL":
            all_pass = False

        rows.append({
            "size": size,
            "pre": len(pre),
            "post": len(post),
            "delta": delta,
            "verdict": verdict,
            "pre_sev": count_by_severity(pre),
            "post_sev": count_by_severity(post),
        })

    overall = "PASS" if all_pass else "FAIL"

    lines = [
        "# D1 Regression Analysis — REVUE-152",
        "",
        f"**Overall verdict: {overall}**  ",
        f"**Threshold:** |Δ findings| ≤ {THRESHOLD} per diff size  ",
        f"**Model:** claude-haiku-4-5-20251001  ",
        "",
        "---",
        "",
        "## Finding Counts",
        "",
        "| Diff size | Pre-D1 | Post-D1 | Δ | Verdict |",
        "|-----------|--------|---------|---|---------|",
    ]

    for r in rows:
        delta_str = f"{r['delta']:+d}"
        lines.append(
            f"| {r['size']} | {r['pre']} | {r['post']} | {delta_str} | {r['verdict']} |"
        )

    lines += [
        "",
        "---",
        "",
        "## Severity Distribution",
        "",
    ]

    for r in rows:
        lines.append(f"### {r['size'].capitalize()} diff")
        lines.append("")
        lines.append("| Severity | Pre-D1 | Post-D1 |")
        lines.append("|----------|--------|---------|")
        all_sevs = sorted(set(r["pre_sev"].keys()) | set(r["post_sev"].keys()))
        for sev in all_sevs:
            lines.append(f"| {sev} | {r['pre_sev'][sev]} | {r['post_sev'][sev]} |")
        lines.append("")

    lines += [
        "---",
        "",
        "## Notes",
        "",
        "_Any delta ≤ 2 is within the LLM non-determinism budget at temperature 0._",
        "_Delta > 2 indicates a meaningful change in review quality and must be investigated._",
    ]

    out = comparison_dir / "ANALYSIS.md"
    out.write_text("\n".join(lines) + "\n")
    print(f"✅ ANALYSIS.md written to {out}")
    print(f"   Overall verdict: {overall}")
    for r in rows:
        print(f"   {r['size']:6s}: pre={r['pre']} post={r['post']} Δ={r['delta']:+d} → {r['verdict']}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 scripts/compare_d1_reviews.py <comparison-dir>", file=sys.stderr)
        sys.exit(1)
    analyse(Path(sys.argv[1]))
