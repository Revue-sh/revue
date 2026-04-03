# Revue.io

AI-powered code review for GitLab and Bitbucket. Revue.io uses multi-agent orchestration to provide security, performance, architecture, and code-quality analysis on every pull request.

## Quick Start

See [docs/quickstart-gitlab.md](docs/quickstart-gitlab.md) or [docs/quickstart-github.md](docs/quickstart-github.md).

## Configuration

Revue.io is configured via a `.revue.yml` file in your project root. Key sections:

- **`ai`**: Provider, model, and API key settings
- **`review`**: Diff limits, confidence thresholds, ignore patterns
- **`noise_filters`**: Control false-positive suppression, including `allowed_patterns` and `disallowed_patterns` for teaching the reviewer about intentional design decisions
- **`agents`**: Team selection and custom agent directories
- **`output`**: Output format and comment style

For the full schema reference, see [docs/configuration.md](docs/configuration.md) and [docs/revue-yml-reference.md](docs/revue-yml-reference.md).

## Documentation

- [Configuration Reference](docs/configuration.md)
- [YAML Schema Reference](docs/revue-yml-reference.md)
- [Product Requirements](docs/prd.md)
- [Architecture](docs/architecture-comment-resolution.md)
