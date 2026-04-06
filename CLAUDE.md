# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Revue.io is an AI-powered multi-agent code review CLI for GitHub, GitLab, and Bitbucket. It orchestrates specialized agents (security, architecture, performance, code quality) to review diffs and post findings as PR/MR comments.

## Commands

### Install (development)
```bash
pip install -e src/
```

### Run tests
```bash
# Unit tests (fast, no DB required)
cd src && PYTHONPATH=$(pwd) pytest revue/tests/ -q

# Single test file
cd src && PYTHONPATH=$(pwd) pytest revue/tests/core/test_pipeline.py -v

# Single test
cd src && PYTHONPATH=$(pwd) pytest revue/tests/core/test_pipeline.py::test_name -v

# Integration tests (require PostgreSQL)
pytest tests/ -q -m integration

# Skip integration tests
pytest tests/ -q -k "not integration"
```

The `conftest.py` at repo root adds `src/` to `sys.path` automatically.

### CLI
```bash
# Review a diff locally
revue review --diff /path/to.diff [--config .revue.yml] [--output markdown|json|text] [--dry-run]

# Review and post comments to VCS
revue review --diff /path/to.diff --platform github --pr-id 123

# Init / validate config
revue init [--force]
revue validate --config .revue.yml
```

### CI test command (from bitbucket-pipelines.yml)
```bash
pip install openai anthropic httpx pyyaml tomli-w pytest --quiet
cd src && PYTHONPATH=$(pwd) python3 -m pytest revue/tests/ -q
```

## Architecture

### Pipeline flow
```
CLI (cli.py)
  → ReviewPipeline (core/pipeline.py)      # parse → filter → route → review → consolidate
      → DiffParser (core/diff_parser.py)   # .diff → FileChange[]
      → CleoRouter (core/cleo_router.py)   # select which agents to run
      → AgentRunner (core/agent_runner.py) # run agents in parallel
      → NovaConsolidator (core/nova_consolidator.py)  # merge findings
  → CommentService (comments/service.py)   # post to VCS
      → PlatformAdapter (comments/platform_adapter.py)  # GitHub/GitLab/Bitbucket
```

### Agents (`src/revue/agents/`)
Named agents with YAML/Markdown prompt definitions:
- **Cleo** – router: decides which agents run on which files
- **Leo** – architecture expert
- **Maya** – code quality expert
- **Kai** – performance expert
- **Zara** – security expert
- **Nova** – consolidator: merges findings from all agents

Pre-built teams in `src/revue/teams/` (e.g., `team-full-review.yml`, `team-security-focus.yml`).

### Tier-based routing
- **Free tier** → single-pass review (orchestrator + code-quality-expert only)
- **Paid tiers** → full orchestration (Cleo routing, all parallel agents, Nova consolidation)

### Layered architecture (strictly enforced)
```
CLI / Presentation
    ↓ (no layer skipping)
Service Layer  (core/pipeline.py, comments/service.py)
    ↓
Repository Layer  (db/repositories/)
    ↓
Infrastructure  (PostgreSQL, external AI APIs)
```

**Rules from ARCHITECTURE.md:**
- No raw SQL outside `db/repositories/`
- Domain models (`core/models.py`) are separate from DB schemas
- Dependencies injected via constructor — never instantiated inside service methods
- Unit tests mock repositories; integration tests use real DB

### Persistence
MVP uses local `.revue/` JSON folder (not PostgreSQL). The `JsonReviewRepository` and `PostgresReviewRepository` implement the same interface — swapping is a one-line constructor change.

### Domain models (`src/revue/core/models.py`)
Key types: `FileChange`, `AIReview`, `Finding`, `Review`. All are dataclasses.

## Configuration

`.revue.yml` in project root. Key fields:
```yaml
ai:
  provider: anthropic        # anthropic | openai | azure | openrouter | custom
  model: claude-sonnet-4-5-20250929
  api_key_env: ANTHROPIC_API_KEY

review:
  max_diff_lines: 10000
  min_confidence: 70          # findings below this are filtered out
  agent_timeout_seconds: 90

agents:
  team: team-full-review      # selects team YAML from src/revue/teams/
```

Full reference: `docs/revue-yml-reference.md`.

## Key files to read first for any significant change

| Task | Read first |
|------|-----------|
| Pipeline / orchestration | `src/revue/core/pipeline.py`, `core/cleo_router.py` |
| Agent prompts / behavior | `src/revue/agents/*.md`, `agents/*.yaml` |
| Comment posting / threading | `src/revue/comments/service.py`, `comments/platform_adapter.py` |
| VCS integration | `src/revue/core/vcs_adapter.py` |
| Domain types | `src/revue/core/models.py` |
| AI provider abstraction | `src/revue/core/ai_client.py` |
| Architectural rules | `ARCHITECTURE.md` |
| Current sprint context | `docs/HANDOFF.md` |

## Testing conventions

- Unit tests live in `src/revue/tests/` and mock all external dependencies (repositories, AI clients, VCS)
- Integration tests live in `tests/` and are gated with `@pytest.mark.integration`
- Mock repositories extend the real repository class (LSP-compliant) — see `ARCHITECTURE.md` for pattern
