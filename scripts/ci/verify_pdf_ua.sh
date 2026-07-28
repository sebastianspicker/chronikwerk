#!/usr/bin/env bash
# Run the pinned veraPDF profile so CI and local validation apply the same PDF/UA rules.
set -euo pipefail

readonly required_version="1.30.1"
readonly verapdf_bin="${VERAPDF_BIN:-verapdf}"

if [[ "$#" -lt 1 ]]; then
  echo "usage: $0 PDF [PDF ...]" >&2
  exit 2
fi

if ! command -v "$verapdf_bin" >/dev/null 2>&1; then
  echo "veraPDF ${required_version} is required (set VERAPDF_BIN to its CLI path)" >&2
  exit 2
fi

version_output="$($verapdf_bin --version 2>&1)"
if [[ "$version_output" != *"${required_version}"* ]]; then
  echo "veraPDF ${required_version} is required; found: ${version_output}" >&2
  exit 2
fi

for pdf in "$@"; do
  if [[ ! -f "$pdf" ]]; then
    echo "PDF fixture not found: $pdf" >&2
    exit 2
  fi
done

"$verapdf_bin" --format text --flavour ua1 --fail-on-test-fail "$@"
