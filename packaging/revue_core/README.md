# revue_core

Core orchestration library for Revue, extracted for use by independent integrations.

This package contains the shared models, pipeline, and analysis utilities used across Revue integrations.

## Embedded JWT public key & Nuitka compilation (REVUE-334)

`revue_core.security.jwt_keys` embeds the production JWT **public** key
(`JWT_PUBLIC_KEY_PEM`) so the CLI can verify licence signatures offline. The
verify sites in the `revue` wheel (`activate.py`, `validate.py`) read it via the
`get_jwt_public_key()` accessor rather than the constant directly.

### Why the accessor exists — and why it is *not* a security fix

REVUE-334 asked whether Nuitka constant-folds the embedded key into the compiled
verify bodies across the wheel boundary, so that rotating the key and rebuilding
only `revue_core` would leave the `revue` wheel verifying against a stale, folded
copy.

An empirical experiment settled it. Both packages compile **per file** with
`nuitka --module`, so every `.py` is an *independent* compilation unit. When
`activate.py` is compiled, the imported `jwt_keys` module is opaque to Nuitka, so
`_jwt_keys.JWT_PUBLIC_KEY_PEM` necessarily becomes a runtime attribute lookup.
Recompiling only `jwt_keys` with a substitute key is observed by the *unchanged*
caller binary — i.e. **there is no cross-module folding to defend against** under
this build mode.

Minimal repro (two modules, one reading the other's constant, compiled
independently with `nuitka --module`, then only the producer recompiled with a
new value): the unchanged consumer `.so` reads the new value for both the direct
read and the accessor. No fold occurs.

Conclusion: the embedded **public** key sitting verbatim inside `jwt_keys`'s own
`.so` is by design and harmless (it is public). The `get_jwt_public_key()`
accessor and the verify-site AST guard (`test_jwt_accessor_binding.py`) are
**defensive clarity / future-proofing**, not a fix for a live vulnerability.

### The invariant that actually holds the guarantee

The no-folding property depends on the build staying per-module. A switch to
whole-program `--standalone` / `--onefile` mode could, in principle, make
cross-module folding possible within a single compilation unit. That build mode
is pinned by `test_nuitka_module_mode_prevents_folding.py`; if it ever changes,
re-evaluate whether the accessor has become load-bearing.
