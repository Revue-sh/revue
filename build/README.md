# Revue Build Pipeline

Compiles `revue/core/` modules to native `.so` binaries using [Nuitka](https://nuitka.net/) for IP protection. The CLI entry point (`cli.py`) and agent/team definitions (`.md`/`.yaml`) remain as plain text.

## Why Nuitka / IP Protection

Revue's commercial value lives in its orchestration logic — the multi-agent pipeline that routes diffs, runs parallel AI reviewers, deduplicates and consolidates findings, and posts structured inline comments. This logic is entirely inside `revue/core/`. Shipping it as plain `.py` files would allow any customer to read, copy, or modify the orchestration without restriction.

Nuitka compiles Python source to native C extensions (`.so` on Linux/macOS, `.pyd` on Windows). The resulting binaries:

- Cannot be decompiled back to readable Python
- Are functionally identical to the original source — no behaviour change
- Run at native speed (minor performance bonus)

### What is compiled and why

| Path | Compiled? | Reason |
|---|---|---|
| `revue/core/*.py` | **Yes** | Orchestration, pipeline, AI routing, agent runner, metrics — core IP |
| `revue/cli.py` | No | Entry point only; contains no business logic |
| `revue/__init__.py` | No | Package marker |
| `revue/core/__init__.py` | No | Package marker — Nuitka requires a directory, not `__init__.py`, for package compilation |
| `revue/agents/*.md` | No | Customer-visible agent prompts; intentionally readable and extensible |
| `revue/teams/*.yaml` | No | Customer team configurations; must remain editable |

### Platform matrix

Wheels are built per-platform because compiled `.so` files are not portable across OS/architecture combinations:

| Platform | Runner |
|---|---|
| `manylinux_2_17_x86_64` | Bitbucket Cloud (Linux x86) |
| `manylinux_2_17_aarch64` | Self-hosted (`linux.arm64`) |
| `macosx_14_0_arm64` | Self-hosted (`macos.arm64`) |

Each platform produces a separate `.whl` that pip selects automatically based on the installer's environment.

## Prerequisites

```bash
pip install nuitka ordered-set zstandard
```

A C compiler is also required (`gcc` on Linux, Xcode CLI tools on macOS).

## Local Build

```bash
# 1. Compile core/ modules to .so
python build/build_nuitka.py

# 2. Assemble a platform-specific wheel
python build/build_wheel.py
```

### Output Locations

| Path | Contents |
|---|---|
| `dist/nuitka/` | Raw Nuitka compilation output |
| `dist/revue_compiled/` | Assembled package tree (`.so` + plain files) |
| `dist/wheels/` | Platform-specific `.whl` file |

### Install the Wheel

```bash
pip install dist/wheels/revue-*.whl
```

## Docker Image (Enterprise)

```bash
docker build -f build/Dockerfile.build -t revue-io/revue:latest .
docker run --rm revue-io/revue:latest revue --help
```

The Docker build uses a multi-stage approach: Nuitka compilation in the builder stage, then a slim runtime image with only the wheel installed.

## CI/CD Pipeline

The Bitbucket Pipelines configuration (`bitbucket-pipelines.yml`) runs a parallel build matrix on every push to `main`, after the test step passes.

### Build Matrix

| Step | Platform | Runner |
|---|---|---|
| Build Linux x86_64 | `manylinux_2_17_x86_64` | Bitbucket Cloud |
| Build Linux ARM64 | `manylinux_2_17_aarch64` | Self-hosted `linux.arm64` |
| Build macOS ARM64 | `macosx_14_0_arm64` | Self-hosted `macos.arm64` |

### Pipeline Flow

```
push to main
  └─ Run Tests
       └─ parallel:
            ├─ Build Linux x86_64   → dist/wheels/*.whl
            ├─ Build Linux ARM64    → dist/wheels/*.whl
            └─ Build macOS ARM64    → dist/wheels/*.whl
                 └─ Collect Artifacts (registry push TBD)
```

Each build step:
1. Installs Nuitka and build dependencies
2. Installs project dependencies via `pip install -e src/`
3. Runs `build_nuitka.py` to compile core modules
4. Runs `build_wheel.py` to produce the wheel

Artifacts from all parallel steps are collected in a final step for registry publishing (currently stubbed).
