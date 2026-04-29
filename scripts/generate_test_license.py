#!/usr/bin/env python3
"""Generate a test REVUE_LICENSE_KEY and insert it into the Fly.io production database.

Gated by TOTP verification (1Password) or macOS Keychain before connecting to Fly.io.
See scripts/README.md for full setup instructions and security notes.

Usage:
    python3 scripts/generate_test_license.py --label github-e2e
    python3 scripts/generate_test_license.py --auth keychain --dry-run
"""

from __future__ import annotations

import argparse
import os
import secrets
import subprocess
import sys

FLY_APP = "revue-io"
DB_PATH = "/data/revue.db"
OP_ITEM_DEFAULT = "Revue E2E License Generator"
KEYCHAIN_SERVICE = "revue-license-generator"
KEYCHAIN_ACCOUNT = "revue-license-generator"

TIER_LIMITS: dict[str, str] = {
    "free": "25",
    "indie": "100",
    "pro": "None",
}


# --- Auth ---

def _op_available() -> bool:
    return subprocess.run(
        ["which", "op"], capture_output=True
    ).returncode == 0


def _auth_1password(item: str) -> None:
    """Gate on 1Password authentication by fetching the item's TOTP.

    Successful execution of `op item get --otp` is the security gate:
    it requires an active, biometric-or-password-authenticated op session.
    The fetched code is displayed so the user can confirm the right item
    is being used, but re-entry is not required.
    """
    result = subprocess.run(
        ["op", "item", "get", item, "--otp"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        err = result.stderr.strip()
        print(f"1Password error: {err}", file=sys.stderr)
        print("Run 'op signin' and ensure the item exists.", file=sys.stderr)
        sys.exit(1)

    otp = result.stdout.strip()
    if not otp:
        print("1Password returned an empty OTP — is a TOTP field configured on the item?", file=sys.stderr)
        sys.exit(1)

    print(f"✓ 1Password authenticated (current TOTP: {otp})")


def _auth_keychain() -> None:
    """Retrieve passphrase from macOS Keychain (triggers Touch ID if configured) and verify."""
    result = subprocess.run(
        [
            "security", "find-generic-password",
            "-a", KEYCHAIN_ACCOUNT,
            "-s", KEYCHAIN_SERVICE,
            "-w",  # print password to stdout
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print("Keychain item not found. Set it up with:", file=sys.stderr)
        print(
            f'  security add-generic-password -a {KEYCHAIN_ACCOUNT} -s {KEYCHAIN_SERVICE} -w "your-passphrase"',
            file=sys.stderr,
        )
        sys.exit(1)

    expected = result.stdout.strip()
    entered = getpass.getpass("Enter the revue-license-generator passphrase: ").strip()
    if entered != expected:
        print("Passphrase mismatch. Aborted.", file=sys.stderr)
        sys.exit(1)

    print("✓ Keychain passphrase verified")


def authorize(method: str, op_item: str) -> None:
    if method == "1password":
        _auth_1password(op_item)
    elif method == "keychain":
        _auth_keychain()
    else:
        # auto: prefer 1Password, fall back to keychain
        if _op_available():
            _auth_1password(op_item)
        else:
            print("op CLI not found — falling back to macOS Keychain")
            _auth_keychain()


# --- DB insertion ---

def build_inline_python(key: str, email: str, workspace: str, tier: str) -> str:
    limit = TIER_LIMITS[tier]
    # Must use only double-quoted strings here — this code is wrapped in single quotes
    # when passed to the container shell via: flyctl ssh console -C 'python3 -c "..."'
    lines = [
        "import sqlite3",
        f'conn = sqlite3.connect("{DB_PATH}")',
        'conn.row_factory = sqlite3.Row',
        'conn.execute("PRAGMA foreign_keys=ON")',
        f'email = "{email}"',
        f'key = "{key}"',
        f'workspace = "{workspace}"',
        f'tier = "{tier}"',
        f'limit = {limit}',
        'row = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()',
        'uid = row["id"] if row else conn.execute("INSERT INTO users (email, password_hash) VALUES (?,?)", (email, "test-hash")).lastrowid',
        'wid = conn.execute("INSERT INTO workspaces (user_id, name) VALUES (?,?)", (uid, workspace)).lastrowid',
        'conn.execute("INSERT INTO license_keys (workspace_id, key, tier, reviews_limit) VALUES (?,?,?,?)", (wid, key, tier, limit))',
        'conn.commit()',
        'conn.close()',
        'print("inserted: " + key)',
    ]
    return "; ".join(lines)


# --- Main ---

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a test REVUE_LICENSE_KEY on Fly.io (requires authorization)"
    )
    parser.add_argument(
        "--label",
        default="e2e-test",
        help="Label suffix for test user email (default: e2e-test)",
    )
    parser.add_argument(
        "--tier",
        default="pro",
        choices=list(TIER_LIMITS),
        help="License tier (default: pro — unlimited reviews)",
    )
    parser.add_argument(
        "--auth",
        default="auto",
        choices=["auto", "1password", "keychain"],
        help="Auth method: auto (1Password if available, else keychain), 1password, keychain",
    )
    parser.add_argument(
        "--op-item",
        default=os.environ.get("OP_ITEM", OP_ITEM_DEFAULT),
        help=f"1Password item name (default: '{OP_ITEM_DEFAULT}' or $OP_ITEM)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Verify auth then print the key without inserting into the DB",
    )
    args = parser.parse_args()

    # Auth gate — always runs, even on dry-run
    authorize(args.auth, args.op_item)

    key = f"lic_{secrets.token_hex(16)}"
    email = f"test-{args.label}@revue-test.local"
    workspace = f"test-{args.label}"

    inline = build_inline_python(key, email, workspace, args.tier)
    shell_cmd = f"python3 -c '{inline}'"
    flyctl_args = ["flyctl", "ssh", "console", "-a", FLY_APP, "-C", shell_cmd]

    if args.dry_run:
        print()
        print("Would run:")
        print(f"  {' '.join(flyctl_args[:6])} '<python>'")
        print()
        print(f"REVUE_LICENSE_KEY={key}")
        return

    print(f"Connecting to {FLY_APP} …", flush=True)
    result = subprocess.run(flyctl_args, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"flyctl error:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)

    output = result.stdout.strip()
    if "inserted:" not in output:
        print(f"Unexpected output from container:\n{output}", file=sys.stderr)
        sys.exit(1)

    print(f"✓ License key inserted (tier={args.tier})")
    print()
    print(f"REVUE_LICENSE_KEY={key}")
    print()
    print("Add this to your CI repository secrets. The key is active immediately.")


if __name__ == "__main__":
    main()
