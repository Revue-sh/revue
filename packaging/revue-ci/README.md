# revue-ci

CI/CLI entry point for [Revue](https://revue.sh) — the AI-powered multi-agent code review pipeline.

`revue-ci` is the command-line tool that runs Revue against pull requests on
GitHub, GitLab, and Bitbucket from inside a CI job (or a local terminal). It
is a thin entry-point wrapper around [`revue_core`](https://pypi.org/project/revue-core/),
which contains the shared orchestration logic.

## Install

```sh
pip install revue-ci
```

This pulls `revue_core` as a dependency. No build-time compilation; pure Python.

## Use

```sh
revue-ci review --diff /tmp/pr.diff --platform github --pr-id 123 \
    --workspace my-org --repo-slug my-repo --config .revue.yml
```

See `revue-ci --help` for the full flag set, and the
[main project README](https://github.com/Token-Labs-Ltd/revue) for end-to-end CI
setup examples for each platform.

## Related packages

| Package | Purpose |
|---|---|
| [`revue_core`](https://pypi.org/project/revue-core/) | Shared library — pipeline, agents, comment routing |
| `revue-ci` (this) | CLI / CI entry point |
| [`revue`](https://pypi.org/project/revue/) | Claude Code skill wheel — local-only review path |
