# Git hooks

Version-controlled pre-commit / pre-push hooks for this repo.

## Enable

```sh
git config core.hooksPath .githooks
```

Re-run after every fresh clone (the setting lives in `.git/config`, not in the
tracked tree).

## What they do

### pre-commit

1. **Blocks direct commits to `main` / `develop`.** Feature branches only.
2. **Keeps `packaging/revue/src/revue_skill/vendored/` in sync** with its
   source-of-truth files in `packaging/revue_core/`, `scripts/positioning/`,
   and `_revue/`. When you stage a change to any of those, the hook re-runs
   `vendor_sources.py --clean` and fails the commit if the regenerated
   `vendored/` output differs from what you've staged — prompting you to
   `git add` the regenerated files and commit again.

### pre-push

Blocks direct pushes to `main` / `develop` on the Bitbucket primary remote.
Mirror remotes (`github`, `gitlab`) are still allowed to push `main` for sync.
