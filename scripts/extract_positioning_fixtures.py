#!/usr/bin/env python3
"""Extract raw positioning fixtures from real PRs on all three platforms.

Outputs one JSON file per fixture into:
  src/revue/tests/fixtures/positioning/{github,gitlab,bitbucket}/

Usage:
  python scripts/extract_positioning_fixtures.py
"""
import os
import subprocess

from positioning import BitbucketClient, GitHubClient, GitLabClient
from positioning.protocol import PositioningExtractor

_CLIENTS: dict[str, PositioningExtractor] = {
    "github": GitHubClient(),
    "gitlab": GitLabClient(),
    "bitbucket": BitbucketClient(),
}


def _load_env() -> None:
    result = subprocess.run(["zsh", "-c", "source ~/.zshenv && env"], capture_output=True, text=True)
    for line in result.stdout.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k, v)


if __name__ == "__main__":
    _load_env()
    for client in _CLIENTS.values():
        client.extract()
    print("\nDone. Review files in src/revue/tests/fixtures/positioning/")
    print("Fill in reported_line, expected_position, and expected_api_params for each.")
