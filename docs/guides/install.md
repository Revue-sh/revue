# Installing `/revue`

## Supported platforms

Revue ships per-OS, Nuitka-compiled wheels for these platforms only:

- **macOS ARM64** (Apple Silicon — M1 and later)
- **Linux x86_64**

Every other platform — **Intel Mac and Linux ARM/Graviton (aarch64)** — is
**not supported** for local install. The one-command installer detects your
platform first and, on an unsupported one, exits with a message that names your
platform and points back to this page rather than failing deep inside `pip`.

**Windows is supported via WSL2** — see the section below.

**Workaround for unsupported platforms:** run Revue in your CI pipeline via the
**`revue-ci`** integration (GitHub, GitLab, or Bitbucket) instead of installing
locally. See the [canonical install page](https://github.com/Revue-sh/revue/blob/main/docs/guides/install.md)
for the authoritative supported-platform list.

## Windows

There is **no native Windows build** of Revue. Windows users install through
**WSL2** (Windows Subsystem for Linux 2), which is not a workaround: inside WSL2
your machine reports `linux x86_64`, so you get the same Linux x86_64
Nuitka-compiled wheel every Linux user runs — natively, at full speed.

Running the installer directly in **Git Bash, MSYS2, or Cygwin will not work**.
Those shells report a Windows platform (`MINGW64_NT-…`), and the installer stops
with a message pointing you here.

**Setup:**

1. Open **PowerShell as Administrator** (right-click → *Run as administrator*) and
   run the Revue installer:

   ```powershell
   irm https://raw.githubusercontent.com/Revue-sh/revue/main/scripts/install.ps1 -OutFile $env:TEMP\revue-install.ps1
   powershell -ExecutionPolicy Bypass -File $env:TEMP\revue-install.ps1 -Key <YOUR_LICENSE_KEY>
   ```

   `-ExecutionPolicy Bypass` is required: a downloaded `.ps1` is blocked by
   Windows' default policy, which otherwise looks like the installer failing.

2. **If you have no WSL2 yet, the installer offers to install it.** Answer `y`
   (or pass `-InstallWsl` to skip the prompt). This needs the elevated shell from
   step 1, enables Windows features, and **requires a reboot**.

   The installer then **stops and exits non-zero**, reporting that a reboot is
   needed. *That is expected, not a failure* — WSL2 is installed at that point,
   but Revue is not.

3. **After rebooting, launch your distro once** from the Start menu (e.g.
   "Ubuntu"). A freshly installed distro is uninitialised and prompts you to
   create a UNIX username and password. Do this before continuing, or the
   installer can drop into WSL's first-run setup.

4. **Install Claude Code inside WSL2.** Revue is a Claude Code skill, so the
   `claude` CLI must exist *in your WSL2 distro* — a Windows-side Claude Code
   does not count. Run `wsl`, then:

   ```bash
   curl -fsSL https://claude.ai/install.sh | bash
   ```

   Verify with `claude --version`, then `exit` back to PowerShell.

5. **Re-run the installer** from step 1. It now finds your WSL2 distro and runs
   the Linux installer inside it. A fresh distro has neither `uv` nor `pipx`, so
   expect it to install `uv` first and print its SHA-256.

6. **Run Claude Code from inside WSL2** so it picks up the `/revue` skill.

> **Why not a `| iex` one-liner?** `iex` cannot forward arguments, so
> `... | iex -Key abc` silently drops your licence key. The `scriptblock`
> wrapper that works around it has been observed failing to parse on some
> Windows PowerShell 5.1 consoles, so the two-step form above is what we
> document — it is the form verified end-to-end on Windows 11.

**Prefer to do it by hand?** Open your WSL2 shell and run the normal Linux
installer — the PowerShell script does exactly this and nothing more:

```bash
curl -fsSL https://raw.githubusercontent.com/Revue-sh/revue/main/scripts/install.sh | bash -s -- --key <YOUR_LICENSE_KEY>
```

This is the same guidance the installer itself prints if you run it from Git Bash:

> On Windows x86_64, install WSL2 with 'wsl --install', then re-run this installer inside your WSL2 shell: it reports linux x86_64, so Revue's Linux x86_64 build installs and runs there natively

> **Windows on ARM** is **not** supported, even through WSL2. WSL2 on an ARM
> machine reports `linux aarch64`, and Revue publishes no `aarch64` wheel. Use
> the **`revue-ci`** CI integration instead.

> **Raw `pip install revue` on an unsupported platform.** Because we publish no
> source distribution (that would expose the Nuitka-protected IP), a bare
> `pip install revue` on an unsupported platform fails with pip's generic
> `ERROR: No matching distribution found for revue`. That is expected, not a
> bug — pip's error text is outside our control. Use the one-command installer
> (which gives a clear, platform-named message) or the `revue-ci` CI workaround
> above.

## One-command install (recommended)

Revue offers a single curl-pipe-bash installer that sets up `/revue` in Claude Code and configures your workspace in under 5 minutes:

```bash
curl -fsSL https://raw.githubusercontent.com/Revue-sh/revue/main/scripts/install.sh | bash -s -- --key <YOUR_LICENSE_KEY>
```

This fetches the installer from GitHub, installs `revue`, sets up the `/revue` skill in Claude Code, and activates your license key in one step. Get your license key from your [Revue dashboard](https://revue.sh/signup). If you omit `--key`, run `revue activate <your-key>` afterward.

**Important:** Run this command from your project root directory so the installer writes `.revue.yml` in the correct location.

**What the installer does:**

1. ✅ Detects Claude Code at `~/.claude`
2. ✅ Installs [`uv`](https://docs.astral.sh/uv/) automatically if you have neither `uv` nor `pipx` (stock macOS has neither)
3. ✅ Installs `revue` via `uv tool install --force` (or falls back to `pipx` when that is what you have)
4. ✅ Copies the bundled skill to `~/.claude/skills/revue` — this is what provides `/revue`
5. ✅ Removes any stale `/revue` or `/revue-local` command-file shim left by an older installer (the skill is now the single source of `/revue`)
6. ✅ Auto-detects `.revue.yml` in your workspace — reuses if present, writes a default if missing
7. ✅ Verifies installation with `revue --version`

The installer is **idempotent** — re-run it anytime to upgrade in place.

### Security: Checksum Verification (Recommended)

If you want to verify the installer before running it, you can check the SHA256 checksum:

```bash
# Fetch the installer
curl -fsSL https://raw.githubusercontent.com/Revue-sh/revue/main/scripts/install.sh -o install.sh

# Verify the checksum (check revue.sh or GitHub releases for the expected hash)
shasum -a 256 -c <<< "<expected-hash>  install.sh"

# Run the verified installer
bash install.sh
```

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

This copies the skill into `~/.claude/skills/revue`. The skill **is** `/revue` — there is no separate command file to create. After Claude Code reloads, `/revue` is available.

### Step 3: Configure your workspace (optional)

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
revue doctor           # checks setup (license, config, Claude Code integration)
```

Then test it:

1. Edit a file in your project
2. Stage the change: `git add <file>`
3. In Claude Code, run `/revue`

You should see findings appear inline.

## Troubleshooting

**"Claude Code not detected"** — Install Claude Code: https://claude.ai/code

**"Neither 'uv' nor 'pipx' found"** — You only see this if you passed
`--no-bootstrap`. By default the installer installs `uv` for you. To install a
package manager yourself:
- `uv`: https://docs.astral.sh/uv/
- `pipx`: https://pipx.pypa.io/

**Verifying the `uv` installer.** When the installer bootstraps `uv`, it
downloads astral's install script to a temporary file, prints its SHA-256, and
only then executes it — so you can audit exactly what ran:

```
✓ uv installer SHA-256: 3f6a…c91d
```

**CI pipelines and fleet-managed machines should always pass a pinned digest.**
Those environments can keep a reference value current, and a mismatch there is
a signal worth failing on. Pass it and the installer refuses to execute
anything else:

```bash
curl -fsSL https://raw.githubusercontent.com/Revue-sh/revue/main/scripts/install.sh | bash -s -- --uv-installer-checksum <SHA256> --key <YOUR_LICENSE_KEY>
```

We deliberately do **not** pin a checksum inside Revue itself: astral revise
their install script whenever they cut a release, so a hardcoded hash would
break every Revue install the moment they do. Verification is therefore opt-in
for those who can keep a digest current.

**Don't want the installer touching your toolchain?** Pass `--no-bootstrap` and
it will fail fast instead of installing `uv`:

```bash
curl -fsSL https://raw.githubusercontent.com/Revue-sh/revue/main/scripts/install.sh | bash -s -- --no-bootstrap --key <YOUR_LICENSE_KEY>
```

**"revue: command not found"** — Ensure the installation completed. Try:
```bash
~/.local/bin/revue --version  # or ~/.venv/bin/revue depending on your setup
```

**`/revue` doesn't trigger** — Restart Claude Code. A newly installed skill requires a reload before `/revue` is available.

**"24h cache expired, needs revalidation"** — Run `revue activate <your-key>` to refresh the license locally.

## Post-install: wiring to your CLAUDE.md

Optionally, add instructions to your project's `CLAUDE.md` so your AI agent knows to run `/revue` before committing:

```markdown
## Pre-commit review (Revue)

Before staging a commit, invoke `/revue` on the diff against the
current base branch. The skill returns multi-agent findings with severity.

Rules:
- Resolve any Critical or High finding before committing, or request
  explicit user override.
- Re-run `/revue` after each fix to confirm the finding cleared.
- Medium and Low findings are advisory; surface them in the commit message
  if you decide not to fix them.

Why this matters: every issue Revue catches here is one fewer CI review
cycle billed against your AI subscription. See revue.sh/dashboard for
your saving.
```
