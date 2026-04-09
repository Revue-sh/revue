#!/usr/bin/env python3
"""
Clean a GitLab CI job log for AI consumption.

Strips ANSI escapes, GitLab section markers, timestamp prefixes,
and filters out noise (apt-get, pip, httpcore DEBUG, duplicate
rate-limit blocks, progress bars). Keeps the signal: [revue] lines,
meaningful errors/warnings, and pipeline outcomes.

Usage:
    python scripts/clean_ci_log.py <input_file> [-o output_file]
    cat raw.log | python scripts/clean_ci_log.py -

Output goes to stdout by default, or to -o file.
"""

import argparse
import re
import sys

# ── ANSI escape pattern ──────────────────────────────────────────────
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\[[0-9;]*|\[0K|\[0;m")

# ── GitLab timestamp + stream prefix ─────────────────────────────────
# e.g. "2026-04-07T20:17:20.679272Z 00O " or "01E "
TIMESTAMP_PREFIX_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z)\s+\d{2}[OE]\+?\s?"
)

# ── Section markers ──────────────────────────────────────────────────
SECTION_RE = re.compile(r"section_(start|end):\d+:\w+")

# ── Max useful line length ───────────────────────────────────────────
MAX_LINE_LEN = 500

# ── Lines to drop entirely (compiled once) ───────────────────────────
DROP_PATTERNS = [
    # httpcore lifecycle noise
    re.compile(r"DEBUG httpcore\.(http11|connection)\s"),
    # anthropic SDK debug (header dumps, retry internals)
    re.compile(r"DEBUG anthropic\._base_client"),
    # httpx INFO lines (just "HTTP Request: POST … 200 OK")
    re.compile(r"INFO httpx\s"),
    # apt-get / dpkg noise
    re.compile(r"(Selecting previously unselected|Preparing to unpack|Unpacking |Setting up |Processing triggers)"),
    re.compile(r"\(Reading database"),
    re.compile(r"^debconf:"),
    # pip install noise
    re.compile(r"(Downloading |Installing collected|Successfully installed|Collecting |Using cached )"),
    re.compile(r"^\s*$"),  # blank lines
    # docker / executor setup
    re.compile(r"Using (Docker executor|effective pull policy|docker image sha256)"),
    re.compile(r"Pulling docker image"),
    # git setup boilerplate (not revue git ops)
    re.compile(r"(Initialized empty Git|Created fresh repository|Skipping Git submodules|git remote set-url)"),
    re.compile(r"Gitaly correlation ID"),
    # runner info
    re.compile(r"Running (with gitlab-runner|on runner-)"),
    re.compile(r"^\s+on\s+\S+\.runners-manager"),
    re.compile(r"feature flags:"),
    # cache download/extract (low signal)
    re.compile(r"(Downloading cache from|Successfully extracted cache|Checking cache for)"),
    # Python traceback frames inside SDK (keep the exception line itself)
    re.compile(r"^\s+File \"/usr/local/lib/python"),
    re.compile(r"^\s+(response\.raise_for_status|raise HTTPStatusError)"),
    # httpx.HTTPStatusError duplicate line (the [revue] ❌ line is kept)
    re.compile(r"^httpx\.HTTPStatusError:"),
    # Bare "Traceback" and MDN links (the [revue] error line is sufficient)
    re.compile(r"^Traceback \(most recent call last\):$"),
    re.compile(r"^For more information check: https://developer\.mozilla"),
    # anthropic SDK retry info (the [revue] ❌ line already shows rate-limit)
    re.compile(r"INFO anthropic\._base_client Retrying request"),
    # Cleanup boilerplate
    re.compile(r"Cleaning up project directory"),
    # Resolving secrets
    re.compile(r"Resolving secrets"),
]


def strip_ansi(line: str) -> str:
    return ANSI_RE.sub("", line)


def extract_timestamp(line: str) -> tuple[str | None, str]:
    """Return (timestamp_or_None, rest_of_line)."""
    m = TIMESTAMP_PREFIX_RE.match(line)
    if m:
        ts = m.group(1)
        # Shorten to seconds: 2026-04-07T20:17:20
        ts_short = ts[:19]
        return ts_short, line[m.end():]
    return None, line


def should_drop(text: str) -> bool:
    for pat in DROP_PATTERNS:
        if pat.search(text):
            return True
    return False


def deduplicate_rate_limits(lines: list[str]) -> list[str]:
    """Collapse repeated rate-limit blocks into one occurrence + count."""
    out: list[str] = []
    seen_429_requests: set[str] = set()
    i = 0
    while i < len(lines):
        line = lines[i]
        # Detect rate-limit error blocks by the [revue] ❌ RATE LIMIT line
        if "RATE LIMIT ERROR" in line:
            # Extract request_id from the Reason line (next line)
            block = [line]
            j = i + 1
            while j < len(lines) and (
                lines[j].strip().startswith("Reason")
                or lines[j].strip().startswith("Action")
                or lines[j].strip().startswith("retry_on_rate_limit")
                or lines[j].strip() == ""
            ):
                block.append(lines[j])
                j += 1

            # Use the Reason line as dedup key
            reason_lines = [b for b in block if "Reason" in b]
            req_id_match = re.search(r"request_id.*?(req_\w+)", reason_lines[0]) if reason_lines else None
            dedup_key = req_id_match.group(1) if req_id_match else None

            if dedup_key and dedup_key in seen_429_requests:
                # Skip duplicate block
                i = j
                continue
            if dedup_key:
                seen_429_requests.add(dedup_key)

            out.extend(block)
            i = j
        else:
            out.append(line)
            i += 1

    if len(seen_429_requests) > 1:
        # Annotate: tell the reader how many unique 429s there were
        for idx, line in enumerate(out):
            if "RATE LIMIT ERROR" in line:
                out.insert(idx, f"  [{len(seen_429_requests)} agents hit rate limits — showing each once]")
                break

    return out


def clean_log(raw: str) -> str:
    # Normalize line endings
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")

    cleaned: list[str] = []
    prev_ts: str | None = None

    for line in raw.split("\n"):
        # 1. Strip ANSI
        line = strip_ansi(line)

        # 2. Extract / shorten timestamp
        ts, text = extract_timestamp(line)

        # 4. Drop section markers (may appear after timestamp stripping)
        if SECTION_RE.search(text):
            continue

        # 5. Drop noise
        if should_drop(text):
            continue

        # 6. Drop very long lines entirely (API payloads, diff bodies in logs).
        #    Meaningful log output (errors, [revue] status, warnings) is always
        #    well under MAX_LINE_LEN.  Anything longer is an SDK request/response
        #    body dump that's useless even when truncated.
        if len(text) > MAX_LINE_LEN:
            continue

        # 7. Format output: only show timestamp when it changes (by second)
        if ts and ts != prev_ts:
            cleaned.append(f"[{ts}] {text}")
            prev_ts = ts
        elif ts:
            cleaned.append(f"  {text}")
        else:
            # Lines without timestamp (rare) — keep as-is
            if text.strip():
                cleaned.append(text)

    # 6. Deduplicate rate-limit blocks
    cleaned = deduplicate_rate_limits(cleaned)

    return "\n".join(cleaned)


def main():
    parser = argparse.ArgumentParser(
        description="Clean a GitLab CI log for AI consumption."
    )
    parser.add_argument(
        "input",
        help='Path to raw log file, or "-" for stdin',
    )
    parser.add_argument(
        "-o", "--output",
        help="Output file (default: stdout)",
        default=None,
    )
    args = parser.parse_args()

    if args.input == "-":
        raw = sys.stdin.read()
    else:
        with open(args.input, "r", errors="replace") as f:
            raw = f.read()

    result = clean_log(raw)

    if args.output:
        with open(args.output, "w") as f:
            f.write(result)
        print(f"Wrote {len(result)} bytes → {args.output}", file=sys.stderr)
    else:
        print(result)


if __name__ == "__main__":
    main()
