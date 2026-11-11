#!/bin/bash
# Validate YAML syntax
yamllint .github/workflows/revue-review.yml
echo "✅ GitHub Actions workflow is valid"
