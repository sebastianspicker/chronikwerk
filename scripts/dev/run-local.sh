#!/bin/sh
set -eu

# Make executable:
# chmod +x scripts/dev/run-local.sh
#
# Purpose:
# Local developer entrypoint running FastAPI service.

usage() {
  cat >&2 <<'EOF'
Usage:
  scripts/dev/run-local.sh [--reload] [--dry-run]

Environment:
  SERVER_HOST (default: 0.0.0.0)
  SERVER_PORT (default: 8080)

Notes:
  - Loads the normal Settings loader, so `.env` in the repo root is supported.
EOF
}

reload=0
dry_run=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --reload)
      reload=1
      shift
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

script_dir="$(CDPATH='' cd "$(dirname "$0")" && pwd)"
repo_root="$(CDPATH='' cd "${script_dir}/../.." && pwd)"

host="${SERVER_HOST:-0.0.0.0}"
port="${SERVER_PORT:-8080}"

echo "Repo: ${repo_root}"
echo "Command:"
printf '%s' "python -m uvicorn zammad_pdf_archiver.asgi:app --host ${host} --port ${port}"
if [ "${reload}" -eq 1 ]; then
  printf '%s' " --reload"
fi
echo

if [ "${dry_run}" -eq 1 ]; then
  exit 0
fi

cd "${repo_root}"
export PYTHONPATH="${repo_root}/src"
if [ "${reload}" -eq 1 ]; then
  exec python -m uvicorn zammad_pdf_archiver.asgi:app --host "${host}" --port "${port}" --reload
fi
exec python -m uvicorn zammad_pdf_archiver.asgi:app --host "${host}" --port "${port}"
