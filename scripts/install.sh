#!/usr/bin/env bash
#
# One-command installer for /revue Claude Code skill.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/Revue-sh/revue/main/scripts/install.sh | bash
#   curl -fsSL https://raw.githubusercontent.com/Revue-sh/revue/main/scripts/install.sh | bash -s -- --yes
#
# Post-MVP distributes via https://revue.sh/install.sh with signed releases.
#
# Design (REVUE-276 / E-P2A-S2, extended by REVUE-354):
# 1. Prefers `uv tool install --python 3.12 --force revue` (pins cp312 ABI to match Nuitka wheels)
# 2. Falls back to `pipx install --force --python python3.12 revue` if uv absent
# 3. Auto-detects Claude Code (~/.claude) and installs the bundled skill (which
#    is the sole source of /revue); removes any stale command-file shim
# 4. Auto-detects .revue.yml in workspace and reuses it
# 5. Aborts if Claude Code not found
#
# REVUE-354 install wizard — choose install *scope*:
#   * global  (default): installs into ~/.claude/skills, .revue.yml in $(pwd)
#   * project: installs into <project>/.claude/skills + <project>/.revue.yml
#
# Because the script is consumed via `curl ... | bash`, stdin IS the script
# content — interactive prompts therefore read from /dev/tty (the canonical
# curl-pipe-bash pattern), NOT stdin. Scope is resolved BEFORE any tty read so
# non-interactive callers (CI, sandboxes, hooks) never block.

set -euo pipefail

# Finding B: Claude Code honours CLAUDE_CONFIG_DIR to relocate its config dir.
# A global install must write where the host CLI will actually read, so respect
# that override; fall back to ~/.claude when it is unset.
#
# AC1 (REVUE-395) — resolve SAFELY under `set -u`. A bare `${HOME}/.claude` here
# crashes with "HOME: unbound variable" when HOME is entirely unset (minimal CI /
# `env -i`), BEFORE expand_tilde's guard can run; and `${HOME:-}/.claude` would
# silently become the root-relative "/.claude". So: when neither CLAUDE_CONFIG_DIR
# nor HOME is set, leave CLAUDE_HOME EMPTY and defer the error to the point a
# GLOBAL install is actually chosen (resolve_install_dirs). A project install with
# an absolute path needs no HOME; a project install with a ~/-path is owned by
# expand_tilde's "HOME is unset" message. CLAUDE_HOME is only read for global
# detection (an empty value makes those existence checks harmlessly false).
if [[ -n "${CLAUDE_CONFIG_DIR:-}" ]]; then
  readonly CLAUDE_HOME="${CLAUDE_CONFIG_DIR}"
elif [[ -n "${HOME:-}" ]]; then
  readonly CLAUDE_HOME="${HOME}/.claude"
else
  readonly CLAUDE_HOME=""
fi
readonly REVUE_YML=".revue.yml"

# ── Supported-platform policy (REVUE-360 AC1) ────────────────────────────────
# SINGLE SOURCE OF TRUTH: revue_core/platform_support.py::SUPPORTED_PLATFORMS.
# This shell copy is pinned to the Python list by
# tests/test_supported_platforms_consistency.py — edit BOTH together or CI fails.
# Format: "<uname -s, lowercased> <uname -m, lowercased+normalised>".
readonly SUPPORTED_PLATFORMS=(
  "darwin arm64"   # macOS ARM64 (Apple Silicon)
  "linux x86_64"   # Linux x86_64
)
# Mirrors revue_core.platform_support.INSTALL_PAGE_URL.
readonly INSTALL_PAGE_URL="https://github.com/Revue-sh/revue/blob/main/docs/guides/install.md"

# ANSI colour codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'  # No Colour

error() {
  printf "${RED}error:${NC} %s\n" "$@" >&2
  exit 1
}

warn() {
  printf "${YELLOW}warn:${NC} %s\n" "$@" >&2
}

info() {
  printf "${GREEN}✓${NC} %s\n" "$@"
}

# Supported-platform guard (REVUE-360 AC1). Runs FIRST in main(), before any
# package manager or directory is touched, so an unsupported platform fails fast
# with a clear, actionable message instead of pip's opaque "no matching
# distribution" error. Normalises the amd64 alias to x86_64 (mirrors
# revue_core.platform_support.normalise_machine); every other arch is compared
# verbatim against SUPPORTED_PLATFORMS.
check_supported_platform() {
  local raw_sys raw_mach sys mach key supported
  raw_sys="$(uname -s 2>/dev/null || echo unknown)"
  raw_mach="$(uname -m 2>/dev/null || echo unknown)"
  # Lowercase and strip ALL whitespace — parity with the Python side's
  # .strip().lower(); OS/arch tokens never contain internal spaces, so this is
  # safe and defends against any stray whitespace in uname output.
  sys="$(printf '%s' "$raw_sys" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
  mach="$(printf '%s' "$raw_mach" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
  case "$mach" in
    amd64) mach="x86_64" ;;
  esac

  key="${sys} ${mach}"
  for supported in "${SUPPORTED_PLATFORMS[@]}"; do
    if [[ "$key" == "$supported" ]]; then
      return 0
    fi
  done

  # Unsupported: name the platform, link the install page, state the workaround.
  printf "${RED}error:${NC} Revue does not publish a wheel for your platform: %s %s\n" \
    "$raw_sys" "$raw_mach" >&2
  # Message components (labels + workaround) are pinned to
  # revue_core.platform_support by tests/test_supported_platforms_consistency.py
  # so this hand-written copy can never silently drift from the policy module.
  printf "Supported platforms: macOS ARM64, Linux x86_64.\n" >&2
  printf "See %s for the supported-platform list.\n" "$INSTALL_PAGE_URL" >&2
  printf "Workaround: run Revue in your CI pipeline via the revue-ci integration (github/gitlab/bitbucket) instead.\n" >&2
  exit 1
}

# Detect Claude Code presence.
#
# REVUE-354 finding #4: gate on the `claude` host CLI being present, NOT on the
# ~/.claude directory. A fresh machine doing a *project* install legitimately has
# no ~/.claude yet, so requiring that directory broke project scope. The host CLI
# is the real prerequisite; global scope still creates/uses ~/.claude downstream.
detect_claude_code() {
  if command_exists claude; then
    return 0
  fi
  return 1
}

# Detect if a command is available on PATH
command_exists() {
  command -v "$1" >/dev/null 2>&1
}

# Ensure a directory exists AND is writable, without leaving partial state on
# failure (finding A). Returns non-zero (and creates nothing it can avoid) when
# the dir cannot be created or written.
#
# `mkdir -p` on an ALREADY-EXISTING directory succeeds even when that dir denies
# file creation (e.g. an unwritable global $(pwd)). So existence alone does not
# prove writability — we additionally create and remove a probe file. This is
# what catches the "package + skill + command shim all succeed, then .revue.yml
# write fails" partial-install gap (finding A.2).
ensure_writable_dir() {
  local d="$1"
  mkdir -p "$d" 2>/dev/null || return 1
  local probe
  probe="$(mktemp "${d}/.revue-probe.XXXXXX" 2>/dev/null)" || return 1
  rm -f "$probe" 2>/dev/null || true
  return 0
}

# Install revue via uv tool install --force
# --python 3.12: the published Nuitka wheels are cp312-only; uv will download
# Python 3.12 automatically if it is not already present on the system.
install_via_uv() {
  info "Found uv — installing revue via 'uv tool install --python 3.12 --force revue'"
  uv tool install --python 3.12 --force revue
}

# Install revue via pipx install --force
# --python python3.12: the published Nuitka wheels are cp312-only; python3.12
# must be available on PATH (install via https://docs.astral.sh/uv/ if missing).
install_via_pipx() {
  info "uv not found — falling back to pipx (install --force --python python3.12 revue)"
  pipx install --force --python python3.12 revue
}

# Run revue install-skill to copy the bundled skill into the scope-appropriate
# skills dir. --overwrite (REVUE-354) so quick-update refreshes a stale skill.
install_skill() {
  local skills_dir="$1"
  mkdir -p "$skills_dir"
  info "Installing bundled skill into ${skills_dir}..."
  revue install-skill --target-dir "$skills_dir" --overwrite
}

# Remove any stale command-file shim left by a prior installer.
#
# The shipped skill (~/.claude/skills/revue) already registers `/revue`; an
# additional ~/.claude/commands/revue.md shim is a redundant duplicate that
# collides with the skill on `/revue`. Earlier installer versions wrote that
# shim (and an even-earlier one wrote revue-local.md). This installer writes no
# command file; it only cleans up the stale shims so upgraded installs don't keep
# a dangling /revue-local or a duplicate /revue. Best-effort: never abort the
# install if a stale file cannot be removed.
remove_stale_slash_command() {
  local commands_dir="$1"
  local stale
  for stale in "${commands_dir}/revue.md" "${commands_dir}/revue-local.md"; do
    if [[ -f "$stale" ]]; then
      if rm -f "$stale" 2>/dev/null; then
        info "Removed stale slash command shim: ${stale}"
      else
        info "Could not remove stale slash command shim (continuing): ${stale}"
      fi
    fi
  done
}

# Detect existing .revue.yml in workspace, or write a default if missing
handle_revue_yml() {
  local workspace_dir="$1"
  local target="${workspace_dir}/${REVUE_YML}"

  if [[ -f "$target" ]]; then
    info "Found existing ${REVUE_YML} in ${workspace_dir} — reusing configuration"
    return 0
  fi

  cat > "$target" <<'REVUE_YML_DEFAULT'
# Default Revue configuration generated by the installer.
# See https://revue.sh/docs/revue-yml-reference for all options.
version: "1"

ai:
  provider: openrouter
  model: deepseek/deepseek-v4-pro
  api_key_env: REVUE_API_KEY
REVUE_YML_DEFAULT
  info "Wrote default ${REVUE_YML} at ${target}"
}

# ---------------------------------------------------------------------------
# REVUE-354 wizard — scope + path resolution.
#
# Globals set by resolve_scope():
#   INSTALL_SCOPE  — "global" | "project"
#   PROJECT_DIR    — resolved (tilde-expanded) project dir when scope=project
#
# Globals used downstream:
#   COMMANDS_DIR / SKILLS_DIR / REVUE_YML_DIR
# ---------------------------------------------------------------------------
INSTALL_SCOPE=""
PROJECT_DIR=""

# Whether the interactive controlling terminal (fd 3) is live. Set by open_tty.
TTY_OPEN=""

# Open /dev/tty on fd 3 for interactive prompts. Returns non-zero if the
# controlling terminal is unavailable (CI, sandboxes, hooks). We probe
# /dev/tty openability rather than `[ -t 0 ]` because `curl | bash` makes
# stdin the piped script, not a terminal. On success sets TTY_OPEN=1 so callers
# don't have to re-probe (and don't rely on non-portable /dev/fd/3 checks).
#
# fd 3 is opened READ-WRITE (`<>`), not read-only (`<`): finding #6 writes the
# interactive prompts to fd 3 (`printf ... >&3`) as well as reading answers from
# it (`read <&3`). A read-only fd would make every prompt write fail with EBADF
# ("Bad file descriptor") and, under `set -e`, abort the installer. The access
# mode is fixed at open() time, so it must be `<>` here regardless of the tty
# device being writable.
open_tty() {
  if exec 3<>/dev/tty 2>/dev/null; then
    TTY_OPEN="1"
    return 0
  fi
  return 1
}

close_tty() {
  exec 3<&- 2>/dev/null || true
}

# Expand a leading tilde in a path WITHOUT eval (REVUE-354 finding #5).
#
# This runs on `curl ... | bash` input, so user-supplied text must NEVER reach
# `eval`. We handle the safe forms by hand:
#   * `~`        → $HOME
#   * `~/path`   → $HOME/path
#   * `~user`    → that user's home via `getent passwd` (or `dscl` on macOS)
#   * `~user/p`  → that user's home + /p
# If a `~user` form is given but the user cannot be resolved (no getent/dscl, or
# unknown user), we print an actionable error and exit — never guess, never eval.
# Result is printed on stdout; callers capture it via command substitution.
expand_tilde() {
  local raw="$1"

  # No leading tilde → return verbatim.
  if [[ "$raw" != "~"* ]]; then
    printf '%s' "$raw"
    return 0
  fi

  # Bare `~` or `~/...` → $HOME-prefixed.
  if [[ "$raw" == "~" || "$raw" == "~/"* ]]; then
    # AC1 (REVUE-395): guard an empty/unset HOME. Without this, `~/x` would
    # expand to the root-relative `/x` silently and install into the wrong place.
    if [[ -z "${HOME:-}" ]]; then
      printf "${RED}error:${NC} Cannot expand '~' — HOME is unset. Provide an absolute path instead.\n" >&2
      return 1
    fi
    printf '%s' "${raw/#\~/$HOME}"
    return 0
  fi

  # `~user` or `~user/path` → resolve the named user's home dir.
  local rest="${raw#\~}"          # strip leading ~
  local user="${rest%%/*}"         # user = up to first slash
  local tail=""
  if [[ "$rest" == */* ]]; then
    tail="/${rest#*/}"             # remainder including leading slash
  fi

  local user_home=""
  if command_exists getent; then
    user_home="$(getent passwd "$user" 2>/dev/null | cut -d: -f6)"
  elif command_exists dscl; then
    # macOS has no getent; query Directory Services without eval.
    # AC2 (REVUE-395): strip the "NFSHomeDirectory: " label and keep the REST of
    # the line — `awk '{print $2}'` truncated home dirs containing spaces (e.g.
    # "/Users/john doe" → "/Users/john").
    user_home="$(dscl . -read "/Users/${user}" NFSHomeDirectory 2>/dev/null | sed -n 's/^NFSHomeDirectory:[[:space:]]*//p')"
  fi

  if [[ -z "$user_home" ]]; then
    # Print to stderr and return non-zero. We do NOT call error()/exit here:
    # this function runs inside command substitution, where exit only kills the
    # subshell and would not reliably abort the parent under `set -e`. The caller
    # checks the return status and aborts in the parent shell instead.
    printf "${RED}error:${NC} Cannot resolve home directory for '~%s'. Provide an absolute path or a ~/-relative path instead.\n" "$user" >&2
    return 1
  fi

  printf '%s' "${user_home}${tail}"
}

# Resolve INSTALL_SCOPE and PROJECT_DIR. Precedence (highest first):
#   1. --yes/-y                       → global
#   2. REVUE_INSTALL_SCOPE set        → use it (global|project)
#   3. REVUE_INSTALL_NONINTERACTIVE=1 → global (legacy compatibility)
#   4. REVUE_INSTALL_PATH set         → project
#   5. /dev/tty unavailable           → global fallback (AC7), exit 0
#   6. otherwise                      → interactive prompts via fd 3
resolve_scope() {
  local yes_flag="$1"

  if [[ "$yes_flag" == "1" ]]; then
    INSTALL_SCOPE="global"
  elif [[ -n "${REVUE_INSTALL_SCOPE:-}" ]]; then
    INSTALL_SCOPE="${REVUE_INSTALL_SCOPE}"
  elif [[ "${REVUE_INSTALL_NONINTERACTIVE:-}" == "1" ]]; then
    INSTALL_SCOPE="global"
  elif [[ -n "${REVUE_INSTALL_PATH:-}" ]]; then
    INSTALL_SCOPE="project"
  elif ! open_tty; then
    # AC7: no controlling terminal — fall back to global with a one-line note.
    warn "No /dev/tty available — falling back to global install scope (~/.claude)."
    INSTALL_SCOPE="global"
  else
    # Interactive path (tty is open). Finding #6: write prompts to the
    # controlling terminal (fd 3), not stderr, so they stay visible even under
    # `curl ... | bash 2>logfile`.
    #
    # Finding #1: detect an existing GLOBAL install FIRST and offer a quick
    # update; resolve scope SECOND. Doing it in this order (inside the only
    # branch where the tty is open) means a Quick update no longer discards a
    # project path — because we never prompt for scope/path when the user
    # accepts the quick update.
    if [[ -d "${CLAUDE_HOME}/skills/revue" ]]; then
      local update_answer=""
      printf "Existing global install detected. [Q]uick update (default) or [M]odify scope? [Q] " >&3
      IFS= read -r update_answer <&3 || update_answer=""
      case "${update_answer}" in
        [Mm]*) : ;;                      # Modify → fall through to scope prompt.
        *)     INSTALL_SCOPE="global" ;; # Quick → reuse the existing (global) scope.
      esac
    fi

    if [[ -z "$INSTALL_SCOPE" ]]; then
      # AC1: interactive scope prompt; blank answer → Global default.
      local scope_answer=""
      printf "Install scope: [G]lobal (~/.claude) or [P]roject (enter path)? [G] " >&3
      IFS= read -r scope_answer <&3 || scope_answer=""
      case "${scope_answer}" in
        [Pp]*) INSTALL_SCOPE="project" ;;
        *)     INSTALL_SCOPE="global" ;;
      esac
    fi
  fi

  if [[ "$INSTALL_SCOPE" != "global" && "$INSTALL_SCOPE" != "project" ]]; then
    error "Invalid install scope: '${INSTALL_SCOPE}' (expected 'global' or 'project')."
  fi

  # Findings #3 + C: when scope resolves to global but the caller ALSO set
  # project-oriented env vars, surface a one-line warning per ignored var that
  # NAMES the cause — never silently drop them. These are warnings, not errors:
  # global is a legitimate explicit choice.
  #
  # Precedence note (matters for correct attribution): REVUE_INSTALL_SCOPE is
  # checked BEFORE REVUE_INSTALL_NONINTERACTIVE, so NONINTERACTIVE never
  # overrides an explicit SCOPE=project. Only --yes can override SCOPE=project.
  # Therefore the real override cases are:
  #   * --yes over REVUE_INSTALL_SCOPE=project
  #   * --yes over REVUE_INSTALL_PATH
  #   * REVUE_INSTALL_NONINTERACTIVE=1 over REVUE_INSTALL_PATH
  #   * an explicit REVUE_INSTALL_SCOPE=global alongside REVUE_INSTALL_PATH
  if [[ "$INSTALL_SCOPE" == "global" ]]; then
    # AC1 (REVUE-395): a global install writes to CLAUDE_HOME, which is empty only
    # when neither CLAUDE_CONFIG_DIR nor HOME is set. Validate it HERE — at scope
    # resolution, the single owner of scope viability — so main() can rely on a
    # global scope always having a usable target and never re-checks. Fail fast
    # rather than crashing on `set -u` or defaulting to the root-relative /.claude.
    if [[ -z "$CLAUDE_HOME" ]]; then
      error "Cannot determine the global config directory — neither CLAUDE_CONFIG_DIR nor HOME is set. Set one, or choose a project install with an absolute path."
    fi

    # Determine what forced global, for an accurate cause string.
    local forced_by=""
    if [[ "$yes_flag" == "1" ]]; then
      forced_by="--yes"
    elif [[ "${REVUE_INSTALL_SCOPE:-}" == "global" ]]; then
      forced_by="REVUE_INSTALL_SCOPE=global"
    elif [[ "${REVUE_INSTALL_NONINTERACTIVE:-}" == "1" ]]; then
      forced_by="REVUE_INSTALL_NONINTERACTIVE=1"
    fi

    if [[ "${REVUE_INSTALL_SCOPE:-}" == "project" && "$yes_flag" == "1" ]]; then
      warn "--yes forces global scope; REVUE_INSTALL_SCOPE=project ignored."
    fi
    if [[ -n "${REVUE_INSTALL_PATH:-}" ]]; then
      # Keep the literal "REVUE_INSTALL_PATH ignored" substring (finding #3 test).
      if [[ -n "$forced_by" ]]; then
        warn "${forced_by} forces global scope; REVUE_INSTALL_PATH ignored."
      else
        # Defensive fallback: in practice forced_by is always set when PATH is
        # present and scope is global (any PATH-set caller routes to project
        # unless --yes / SCOPE=global / NONINTERACTIVE forces global — each sets
        # forced_by). Kept as a safety net so the var is never silently dropped.
        warn "REVUE_INSTALL_PATH ignored: install scope is global."
      fi
    fi
  fi

  if [[ "$INSTALL_SCOPE" == "project" ]]; then
    resolve_project_dir
  fi
}

# Resolve PROJECT_DIR for a project install, honouring REVUE_INSTALL_PATH or
# prompting interactively (default $(pwd)). Handles tilde expansion (AC2) and
# missing-directory behaviour (AC9).
resolve_project_dir() {
  local raw=""

  if [[ -n "${REVUE_INSTALL_PATH:-}" ]]; then
    raw="${REVUE_INSTALL_PATH}"
  elif [[ -n "$TTY_OPEN" ]]; then
    # AC2: prompt for the project dir; blank → $(pwd). (fd 3 — finding #6.)
    printf "Project directory: [%s] " "$(pwd)" >&3
    IFS= read -r raw <&3 || raw=""
    [[ -z "$raw" ]] && raw="$(pwd)"
  else
    # AC3 (REVUE-395): project scope chosen, but no REVUE_INSTALL_PATH and no
    # terminal to prompt (e.g. CI). Fall back to $(pwd) but WARN — installing
    # into wherever the job happens to be standing should not be a silent surprise.
    raw="$(pwd)"
    warn "REVUE_INSTALL_SCOPE=project but no REVUE_INSTALL_PATH set and no terminal to prompt — using current directory: ${raw}"
  fi

  if ! PROJECT_DIR="$(expand_tilde "$raw")"; then
    # expand_tilde already emitted an actionable message (e.g. unresolvable
    # ~user). Abort in the parent shell with a non-zero exit (AC9/finding #5).
    exit 1
  fi

  if [[ ! -d "$PROJECT_DIR" ]]; then
    # AC9: missing path. Interactive → offer to create + retry; otherwise hard error.
    if [[ -n "$TTY_OPEN" ]]; then
      local create_answer=""
      printf "Project directory '%s' does not exist. Create it? [Y/n] " "$PROJECT_DIR" >&3
      IFS= read -r create_answer <&3 || create_answer=""
      case "${create_answer}" in
        [Nn]*) error "Project directory does not exist: ${PROJECT_DIR}. Create it and re-run." ;;
        *)
          # `mkdir … && info` inside a case arm does NOT abort under set -e on
          # mkdir failure (the && makes it a tested command list); it would fall
          # through and fail confusingly later. Abort explicitly instead.
          if ! mkdir -p "$PROJECT_DIR"; then
            error "Could not create project directory: ${PROJECT_DIR}"
          fi
          info "Created project directory ${PROJECT_DIR}"
          ;;
      esac
    else
      error "Project directory does not exist: ${PROJECT_DIR}. Create it (mkdir -p '${PROJECT_DIR}') and re-run, or set REVUE_INSTALL_PATH to an existing directory."
    fi
  fi

  # Finding #2: detect an existing PROJECT install so it isn't silently
  # overwritten without notice. Only the project skill dir is discoverable once
  # the path is known. Interactive → inform the user that the existing install
  # will be refreshed in place (install_skill already uses --overwrite, so this
  # IS the quick update). There is no sensible "modify scope" target for an
  # already-chosen project path, so we keep it minimal and proceed.
  if [[ -n "$TTY_OPEN" && -d "${PROJECT_DIR}/.claude/skills/revue" ]]; then
    printf "Existing project install detected at %s — quick-updating in place.\n" "$PROJECT_DIR" >&3
  fi
}

# AC10 existing-install handling now lives inside resolve_scope's interactive
# branch (finding #1) and resolve_project_dir (finding #2). For --yes and
# non-interactive callers, the quick update IS the normal install flow below
# (uv ... --force + install-skill --overwrite), so no separate prompt is needed.

# Main flow
main() {
  # Parse flags: --yes / -y forces global, skips prompts.
  #              --key <licence-key> activates the licence after install.
  local yes_flag="0"
  local licence_key=""
  local arg
  while [[ $# -gt 0 ]]; do
    arg="$1"
    case "$arg" in
      --yes|-y) yes_flag="1" ;;
      --key)
        shift
        if [[ $# -eq 0 || -z "$1" || "$1" == --* ]]; then
          error "--key requires a non-empty licence key value."
        fi
        licence_key="$1"
        ;;
      --key=*)
        licence_key="${arg#--key=}"
        if [[ -z "$licence_key" ]]; then
          error "--key= requires a non-empty licence key value."
        fi
        ;;
      *) warn "Ignoring unrecognised argument: ${arg}" ;;
    esac
    shift
  done

  # Step 0 (REVUE-360): supported-platform guard BEFORE anything else. We publish
  # per-OS wheels for macOS ARM64 + Linux x86_64 only; on any other platform pip
  # would later fail with an opaque "no matching distribution" error, so fail fast
  # here with a message that names the platform and the CI workaround.
  check_supported_platform

  # Step 1: Detect Claude Code (always required — both scopes need the CLI host).
  if ! detect_claude_code; then
    error "Claude Code not detected. Install Claude Code first: https://claude.ai/code"
  fi
  info "Claude Code host CLI detected"

  # Step 2: Resolve install scope + path (incl. existing-install handling and
  # interactive prompts). resolve_scope sets TTY_OPEN=1 iff it opened the tty.
  # Finding E: ensure the interactive tty fd (if opened) is always closed, even
  # if a later error()/exit aborts before the explicit close at the end.
  trap 'close_tty' EXIT
  resolve_scope "$yes_flag"

  # Compute scope-aware target dirs.
  local commands_dir skills_dir revue_yml_dir
  if [[ "$INSTALL_SCOPE" == "project" ]]; then
    commands_dir="${PROJECT_DIR}/.claude/commands"
    skills_dir="${PROJECT_DIR}/.claude/skills"
    revue_yml_dir="${PROJECT_DIR}"
    info "Install scope: project (${PROJECT_DIR})"
  else
    # resolve_scope already validated CLAUDE_HOME is non-empty for global scope.
    commands_dir="${CLAUDE_HOME}/commands"
    skills_dir="${CLAUDE_HOME}/skills"
    revue_yml_dir="$(pwd)"  # global .revue.yml stays in the current directory
    info "Install scope: global (${CLAUDE_HOME})"
  fi

  # Step 3 (finding A): a SINGLE pre-flight BEFORE any package install, so we
  # never leave partial state. Order matters:
  #   (1) verify a package manager exists FIRST — on a box with neither uv nor
  #       pipx we must NOT create any .claude dirs (finding A.1). command_exists
  #       has no side effects, so capture the choice and invoke it later.
  #   (2) verify writability of the target dirs we actually write — skills AND
  #       revue_yml_dir (finding A.2). revue_yml_dir is checked first and is
  #       pre-existing (for global it's $(pwd)); ensure_writable_dir probes it
  #       with a temp file, since `mkdir -p` on an existing-but-unwritable dir
  #       would falsely "succeed". The commands dir is NOT pre-checked: the
  #       installer writes no command file (the skill is the sole source of
  #       /revue), and stale-shim cleanup is best-effort.
  # Only after ALL pass do uv/pipx + skill + .revue.yml run.
  local pkg_mgr=""
  if command_exists uv; then
    pkg_mgr="uv"
  elif command_exists pipx; then
    pkg_mgr="pipx"
  else
    error "Neither 'uv' nor 'pipx' found. Install one: https://docs.astral.sh/uv or https://pipx.pypa.io"
  fi

  if ! ensure_writable_dir "$revue_yml_dir"; then
    error "Target directory is not writable: ${revue_yml_dir} (needed for ${REVUE_YML})."
  fi
  if ! ensure_writable_dir "$skills_dir"; then
    error "Target directory is not writable: ${skills_dir} (needed for the bundled skill)."
  fi

  # Step 4: Install revue package via the pre-selected manager.
  if [[ "$pkg_mgr" == "uv" ]]; then
    install_via_uv
  else
    install_via_pipx
  fi

  # Step 5: Install the bundled skill into the scope-appropriate dir. The skill
  # is the sole source of /revue — no separate command-file shim is written.
  install_skill "$skills_dir"

  # Step 6: Remove any stale command-file shim from a prior installer so an
  # upgraded install doesn't keep a dangling /revue-local or a duplicate /revue.
  remove_stale_slash_command "$commands_dir"

  # Step 7: Detect and handle .revue.yml in the scope-appropriate dir.
  handle_revue_yml "$revue_yml_dir"

  # Close the interactive tty fd (if it was opened).
  close_tty

  # Step 8: Activate licence if --key was supplied.
  if [[ -n "$licence_key" ]]; then
    info "Activating licence..."
    if revue activate "$licence_key"; then
      info "Licence activated — Revue is ready to use offline"
    else
      # Activation failure is non-fatal: the package and skill are installed.
      # The user can run `revue activate <key>` manually at any time.
      warn "Licence activation failed — run 'revue activate <your-key>' to retry"
    fi
  fi

  # Step 9: Verify installation.
  # REVUE-373: use the `version` subcommand (supported on all published wheels).
  # Guard with `|| true` so a failure here (e.g. edge-platform import error in
  # revue_core) never aborts a successful install under `set -e` — the install
  # state is already correct; this is a best-effort sanity print only.
  info "Installation complete"
  revue version || warn "version check failed — install was successful; run 'revue version' manually to diagnose"
  info "Ready to use: invoke /revue in Claude Code"
}

main "$@"
