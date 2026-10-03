#!/usr/bin/env bash
# Compile every Python environment lock with the repository-pinned resolver.
set -euo pipefail

if [[ "$(python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" != "3.14" ]]; then
  echo "dependency locks must be compiled with Python 3.14" >&2
  exit 2
fi
expected_pip_tools="$(sed -n 's/^pip-tools==//p' requirements/tools.in)"
actual_pip_tools="$(python -c 'from importlib.metadata import version; print(version("pip-tools"))')"
if [[ -z "$expected_pip_tools" || "$actual_pip_tools" != "$expected_pip_tools" ]]; then
  echo "dependency locks require the exact pip-tools pin from requirements/tools.in" >&2
  exit 2
fi

output_dir="${1:-requirements}"
mkdir -p "$output_dir"
upgrade_args=()
if [[ "${LOCK_UPGRADE:-0}" == "1" ]]; then
  upgrade_args+=(--upgrade)
fi

compile() {
  local output_name="$1"
  shift
  CUSTOM_COMPILE_COMMAND="make dependency-locks" \
    python -m piptools compile \
      --generate-hashes \
      --allow-unsafe \
      --no-build-isolation \
      --resolver=backtracking \
      --strip-extras \
      "${upgrade_args[@]}" \
      --output-file="$output_dir/$output_name.lock" \
      "$@"
}

compile_dev() {
  python scripts/ci/dev_lock_requirements.py | \
    CUSTOM_COMPILE_COMMAND="make dependency-locks" \
      python -m piptools compile \
        --generate-hashes \
        --allow-unsafe \
        --no-build-isolation \
        --resolver=backtracking \
        --strip-extras \
        "${upgrade_args[@]}" \
        --output-file="$output_dir/dev.lock" \
        -
}

compile base pyproject.toml
compile signing --extra=signing pyproject.toml
compile_dev
compile tools requirements/tools.in
