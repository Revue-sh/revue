#!/usr/bin/env python3
"""Parse and validate [revue:body] logs from CI output against REVUE-209 ACs."""

import re
import sys
from pathlib import Path
from collections import defaultdict

def extract_revue_body_logs(log_file: str) -> list[dict]:
    """Extract [revue:body] DEBUG log lines from CI output (actual runtime logs, not source code)."""
    # Match actual DEBUG output lines with resolved values (not format strings with %s placeholders)
    pattern = r'DEBUG\s+\w+\s+\[revue:body\]\s+(.+?)(?=\n|$)'
    logs = []

    try:
        with open(log_file, 'r', errors='ignore') as f:
            for line in f:
                match = re.search(pattern, line)
                if match:
                    log_content = match.group(1).strip()
                    # Skip source code lines (which have format string %s or .py file references in wrong format)
                    if '%s' in log_content or 'logging.debug' in line:
                        continue
                    logs.append({
                        'raw': f"[revue:body] {log_content}",
                        'content': log_content
                    })
    except FileNotFoundError:
        print(f"Error: Log file not found: {log_file}")
        sys.exit(1)

    return logs

def parse_log_entry(log: dict) -> dict:
    """Parse a single [revue:body] log entry into structured data."""
    content = log['content']

    # Parse: file:line → singleton/grouped platform=X agents=Y
    # Examples:
    # app.py:10 → singleton  platform=github  agent=zara  sev=high  has_code=False
    # src/service.py:42 → grouped(2)  platform=bitbucket  agents=zara, kai

    result = {'raw': log['raw']}

    # Extract file:line
    match = re.search(r'(\S+:\d+)', content)
    if match:
        result['location'] = match.group(1)

    # Extract routing type (singleton vs grouped)
    if '→ singleton' in content:
        result['routing'] = 'singleton'
    elif '→ grouped' in content:
        result['routing'] = 'grouped'
        match = re.search(r'grouped\((\d+)\)', content)
        if match:
            result['count'] = int(match.group(1))

    # Extract platform
    match = re.search(r'platform=(\w+)', content)
    if match:
        result['platform'] = match.group(1)

    # Extract agents
    if 'agents=' in content:
        match = re.search(r'agents=([\w, ]+)', content)
        if match:
            result['agents'] = [a.strip() for a in match.group(1).split(',')]
    elif 'agent=' in content:
        match = re.search(r'agent=(\w+)', content)
        if match:
            result['agent'] = match.group(1)

    # Extract severity
    match = re.search(r'sev=(\w+)', content)
    if match:
        result['severity'] = match.group(1)

    # Extract code replacement
    if 'has_code=' in content:
        result['has_code'] = 'True' in content

    return result

def validate_acs(logs: list[dict]) -> dict:
    """Validate logs against REVUE-209 acceptance criteria."""
    parsed = [parse_log_entry(log) for log in logs]

    validations = {
        'AC1_singleton_routing': False,  # build() called for single findings
        'AC2_grouped_routing': False,     # build_grouped() called for multi-findings
        'AC3_platform_github': False,     # Platform dispatch working for GitHub
        'AC3_platform_gitlab': False,     # Platform dispatch working for GitLab
        'AC3_platform_bitbucket': False,  # Platform dispatch working for Bitbucket
        'AC7_cli_integration': False,     # BodyBuilder integrated into _run_per_issue_dedup
        'logs_present': len(logs) > 0,
    }

    platforms_seen = set()

    for entry in parsed:
        if entry.get('routing') == 'singleton':
            validations['AC1_singleton_routing'] = True
            validations['AC7_cli_integration'] = True

        if entry.get('routing') == 'grouped':
            validations['AC2_grouped_routing'] = True
            validations['AC7_cli_integration'] = True

        if entry.get('platform'):
            platform = entry['platform']
            platforms_seen.add(platform)
            validations[f'AC3_platform_{platform}'] = True

    return {
        'parsed_entries': parsed,
        'validations': validations,
        'platforms_seen': platforms_seen,
        'total_logs': len(logs),
        'raw_logs': logs,
    }

def print_report(analysis: dict) -> None:
    """Print a human-readable validation report."""
    print("\n" + "="*70)
    print("REVUE-209 ACCEPTANCE CRITERIA VALIDATION REPORT")
    print("="*70)

    if not analysis['total_logs']:
        print("\n❌ NO [revue:body] LOGS FOUND")
        print("\nCheck:")
        print("  - Is REVUE_LOG_LEVEL=DEBUG set in Bitbucket pipeline?")
        print("  - Did the review run and post comments?")
        print("  - Are the [revue:body] logs in the provided log file?")
        return

    print(f"\n✅ Found {analysis['total_logs']} [revue:body] log entries\n")

    # Debug: show raw log lines
    print("RAW LOG LINES (for debugging):")
    print("-"*70)
    for log in analysis.get('raw_logs', [])[:3]:
        print(f"  {log['raw'][:120]}...")
    print("-"*70 + "\n")

    validations = analysis['validations']

    # AC1: singleton routing
    status = "✅" if validations['AC1_singleton_routing'] else "⚠️"
    print(f"{status} AC1 — BodyBuilder.build() called for single findings: {validations['AC1_singleton_routing']}")

    # AC2: grouped routing
    status = "✅" if validations['AC2_grouped_routing'] else "⚠️"
    print(f"{status} AC2 — BodyBuilder.build_grouped() called for multi-findings: {validations['AC2_grouped_routing']}")

    # AC3: platform dispatch
    platforms = analysis['platforms_seen']
    for plat in ['github', 'gitlab', 'bitbucket']:
        key = f'AC3_platform_{plat}'
        status = "✅" if validations[key] else "⚠️"
        print(f"{status} AC3 — Platform dispatch for {plat}: {plat in platforms}")

    # AC7: CLI integration
    status = "✅" if validations['AC7_cli_integration'] else "❌"
    print(f"{status} AC7 — BodyBuilder integrated into _run_per_issue_dedup: {validations['AC7_cli_integration']}")

    # Sample entries
    if analysis['parsed_entries']:
        print("\n" + "-"*70)
        print("Sample Log Entries (first 5):")
        print("-"*70)
        for i, entry in enumerate(analysis['parsed_entries'][:5], 1):
            print(f"\n{i}. {entry.get('location', '?')} → {entry.get('routing', '?')}")
            if entry.get('platform'):
                print(f"   Platform: {entry['platform']}")
            if entry.get('agent'):
                print(f"   Agent: {entry['agent']}")
            if entry.get('agents'):
                print(f"   Agents: {', '.join(entry['agents'])}")
            if entry.get('severity'):
                print(f"   Severity: {entry['severity']}")

    print("\n" + "="*70)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python parse_revue_logs.py <log_file>")
        sys.exit(1)

    log_file = sys.argv[1]
    logs = extract_revue_body_logs(log_file)
    analysis = validate_acs(logs)
    print_report(analysis)
