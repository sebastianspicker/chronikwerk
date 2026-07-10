#!/usr/bin/env bash
# Run the tracked local Codacy policy without creating repository artifacts.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

if ! command -v codacy-analysis >/dev/null 2>&1; then
    echo "codacy-analysis is required; install @codacy/analysis-cli first." >&2
    exit 2
fi

remove_result=false
if [[ -n "${CODACY_OUTPUT:-}" ]]; then
    result_file="$CODACY_OUTPUT"
    mkdir -p "$(dirname "$result_file")"
else
    result_file="$(mktemp "${TMPDIR:-/tmp}/zammad-ticket-archiver-codacy.XXXXXX")"
    remove_result=true
fi
# Invoked through the EXIT trap below.
# shellcheck disable=SC2329
cleanup() {
    if [[ "$remove_result" == true ]]; then
        rm -f "$result_file"
    fi
}
trap cleanup EXIT

files=()
while IFS= read -r file; do
    case "$file" in
        .env|.env.*|*.pem|*.key|*.p12|*.pfx|.codacy/*|docs/archive/*|deprecated/*)
            continue
            ;;
    esac
    [[ -f "$file" ]] && files+=("$file")
done < <(git ls-files -co --exclude-standard)

if (( ${#files[@]} == 0 )); then
    echo "No tracked or untracked files are available for Codacy analysis." >&2
    exit 2
fi

set +e
codacy-analysis analyze \
    --config-file .codacy/codacy.config.json \
    --output-format json \
    --output "$result_file" \
    --no-log \
    --fail-if-missing \
    "$@" \
    --files "${files[@]}"
status=$?
set -e

if [[ ! -s "$result_file" ]]; then
    echo "Codacy did not produce a JSON result." >&2
    exit 2
fi

if ! jq -e '.issues and .toolResults and .errors' "$result_file" >/dev/null; then
    echo "Codacy produced an invalid result document." >&2
    exit 2
fi

jq '{issueCount: (.issues | length), toolResults: [.toolResults[] | {toolId, status, issueCount}], errors}' "$result_file"
exit "$status"
