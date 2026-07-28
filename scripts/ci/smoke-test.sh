#!/usr/bin/env bash
# Verify the small, release-critical repository skeleton before more expensive CI lanes run.
set -euo pipefail

echo "Smoke test: repo structure sanity"

required_paths=(
  "README.md"
  "pyproject.toml"
  "docs/01-architecture.md"
  "config/config.example.yaml"
  "src/chronikwerk/templates/default/ticket.html"
  ".github/workflows/ci.yml"
)

for p in "${required_paths[@]}"; do
  if [[ ! -e "$p" ]]; then
    echo "Missing required path: $p" >&2
    exit 1
  fi
done

echo "OK."
