# Installing `/revue-local`

## One-command install (recommended)

Revue offers a single curl-pipe-bash installer that sets up `/revue-local` in Claude Code and configures your workspace in under 5 minutes:

```bash
curl -fsSL https://raw.githubusercontent.com/cbscd/revue/main/scripts/install.sh | bash
```

Pre-MVP: fetches from GitHub. Post-MVP: distributes from `https://revue.sh/install.sh` with signed releases.

**Important:** Run this command from your project root directory so the installer writes `.revue.yml` in the correct location.

**What the installer does:**

1. ✅ Detects Claude Code at `~/.claude`
2. ✅ Installs `revue` via `uv tool install --force` (or falls back to `pipx`)
3. ✅ Copies the bundled skill to `~/.claude/skills/revue`
4. ✅ Writes slash command shim at `~/.claude/commands/revue-local.md`
5. ✅ Auto-detects `.revue.yml` in your workspace — reuses if present, writes a default if missing
6. ✅ Verifies installation with `revue --version`

The installer is **idempotent** — re-run it anytime to upgrade in place.

### Security: Checksum Verification (Recommended)

If you want to verify the installer before running it, you can check the SHA256 checksum:

```bash
# Fetch the installer
curl -fsSL https://raw.githubusercontent.com/cbscd/revue/main/scripts/install.sh -o install.sh

# Verify the checksum (check revue.sh or GitHub releases for the expected hash)
shasum -a 256 -c <<< "<expected-hash>  install.sh"

# Run the verified installer
bash install.sh
```

Post-MVP releases will include signed artifacts for signature verification.

## Manual install (if needed)

If you prefer not to pipe a script, or need to install on a machine without internet:

### Step 1: Install the revue package

Choose your tool:

```bash
# Option A: uv (recommended — faster, handles toolchain)
uv tool install revue

# Option B: pipx (Python app installer)
pipx install revue
```

### Step 2: Install the bundled skill

```bash
revue install-skill
```

This copies the skill into `~/.claude/skills/revue`.

### Step 3: Write the slash command

Create `~/.claude/commands/revue-local.md`:

```markdown
---
name: revue-local
description: Multi-agent code review — run locally in Claude Code before committing
---

# /revue-local

Run a multi-agent code review on your current staged diff using Revue. No platform APIs, no Anthropic spend — runs locally inside Claude Code.

## Basic usage

1. Stage your changes: `git add <files>`
2. In Claude Code, run `/revue-local`
3. Review findings by severity
4. Fix Critical/High findings before committing

See https://revue.sh/docs for full documentation.
```

### Step 4: Configure your workspace (optional)

The one-command installer writes a default `.revue.yml` if one is missing. If you're installing manually and want the same default, create `.revue.yml` in your project root:

```yaml
version: "1"

ai:
  provider: openrouter
  model: deepseek/deepseek-v4-pro
  api_key_env: REVUE_API_KEY
```

Set your credentials: `export REVUE_API_KEY=<your-openrouter-api-key>` (or whichever provider you configure).

See `docs/guides/revue-yml-reference.md` for all options.

## Verifying installation

After install, confirm everything is wired:

```bash
revue --version        # prints installed version
revue doctor           # checks setup (licence, config, Claude Code integration)
```

Then test it:

1. Edit a file in your project
2. Stage the change: `git add <file>`
3. In Claude Code, run `/revue-local`

You should see findings appear inline.

## Troubleshooting

**"Claude Code not detected"** — Install Claude Code: https://claude.ai/code

**"Neither 'uv' nor 'pipx' found"** — Install one:
- `uv`: https://docs.astral.sh/uv/
- `pipx`: https://pipx.pypa.io/

**"revue: command not found"** — Ensure the installation completed. Try:
```bash
~/.local/bin/revue --version  # or ~/.venv/bin/revue depending on your setup
```

**Slash command doesn't trigger** — Restart Claude Code. New slash commands require a reload.

**"24h cache expired, needs revalidation"** — Run `revue-local activate <your-key>` to refresh the licence locally.

## Post-install: wiring to your CLAUDE.md

Optionally, add instructions to your project's `CLAUDE.md` so your AI agent knows to run `/revue-local` before committing:

```markdown
## Pre-commit review (Revue)

Before staging a commit, invoke `/revue-local` on the diff against the
current base branch. The skill returns multi-agent findings with severity.

Rules:
- Resolve any Critical or High finding before committing, or request
  explicit user override.
- Re-run `/revue-local` after each fix to confirm the finding cleared.
- Medium and Low findings are advisory; surface them in the commit message
  if you decide not to fix them.

Why this matters: every issue Revue catches here is one fewer CI review
cycle billed against your AI subscription. See revue.sh/dashboard for
your saving.
```

See `docs/planning/product-brief-revue-local-distribution.md` (§8.1) for the canonical wiring block.
