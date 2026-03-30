# Revue Build Pipeline

Compiles `revue/core/` modules to native `.so` binaries using [Nuitka](https://nuitka.net/) for IP protection. The CLI entry point (`cli.py`) and agent/team definitions (`.md`/`.yaml`) remain as plain text.

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
