# Testing Guide

## Running tests

```bash
# Unit tests (fast, no DB)
cd src && PYTHONPATH=$(pwd) python3.12 -m pytest revue/tests/ -q

# Single file
cd src && PYTHONPATH=$(pwd) python3.12 -m pytest revue/tests/core/test_pipeline.py -v

# Single test
cd src && PYTHONPATH=$(pwd) python3.12 -m pytest revue/tests/core/test_pipeline.py::test_name -v
```

## CI reproduction (from bitbucket-pipelines.yml)

```bash
pip install openai anthropic httpx pyyaml tomli-w pytest --quiet
cd src && PYTHONPATH=$(pwd) python3.12 -m pytest revue/tests/ -q
```

The `conftest.py` at repo root adds `src/` to `sys.path` automatically.

## Conventions

- Unit tests: `src/revue/tests/` — mock all external deps (repos, AI clients, VCS)
- Integration tests: `tests/` — gated with `@pytest.mark.integration`
- Mock repositories extend the real class (LSP-compliant)
- **TDD**: write a failing test before writing implementation. Run the full test suite after each task.
- IMPORTANT: pipeline and cross-platform stories need live CI log evidence + error-path simulation. Unit tests alone are insufficient.

## AC contract testing — mandatory before Code Review

Every AC that specifies a data schema or output format **must** have a test that asserts every field in that schema — not just the fields that seem important at the time. Silently skipping fields (e.g. asserting token counts but not `agent_name`) allows data-flow bugs to pass tests and reach production.

Before transitioning a ticket to Code Review:
1. For every output schema in the ACs (JSONL, dataclass, API response), enumerate all fields.
2. Confirm a test asserts each field by name, including optional/nullable ones.
3. Confirm the end-to-end wiring is tested — not just that the writer accepts a value, but that the caller passes it.

**Why:** REVUE-162 shipped with `agent_name=None` hardcoded in `AnthropicClient.complete()`. The test checked 5 token fields but skipped `agent_name`. The artifact schema looked valid until Daniel downloaded and inspected it manually. This must be caught in tests, not in production artifact inspection.
