# Release Readiness Audit

Date: 2026-06-30

## Verdict

Not release-ready yet.

The public documentation surface is cleaner and local-only files are excluded
from normal Git status, but the working tree contains a very large runtime and
test diff that still needs full behavioral review and release-gate proof before
publication.

## Confirmed State

- Current diff size: 204 files changed, about 3,936 insertions and 18,783
  deletions.
- Deleted public-facing historical material includes old product-planning docs,
  demo screenshots, demo manifests, and demo/admin docs.
- Local-only archive and tool state are ignored and do not appear as commit
  candidates.
- Public term scan for local tool/process wording currently has no matches in
  the checked public paths.
- Markdown docs existence and link checks passed in the current tree.
- Whitespace diff check passes when excluding `.env.example`, which is blocked
  by the local permission profile.
- Ruff and C901 checks pass under the local Python 3.12 virtual environment.

## Release Blockers

- The security workflow references `scripts/ci/enforce_pip_audit_policy.py`,
  but that file is currently untracked. Track it or change the workflow before
  release.
- Several untracked support/test files may be required by the modified test
  suite. Decide explicitly whether each is release content or local scratch.
- The diff removes or rewrites major runtime capabilities, including admin UI,
  queue-related modules, config schema support, demo/dev scripts, templates, and
  many tests. Each removal needs a release note or a restore decision.
- Full release gates have not been run after the cleanup because the local
  virtual environment is missing or has broken test/type/build tooling.

## Public-Surface Findings

- Public docs now describe the compact active HTTP surface instead of removed
  demo/admin material.
- The pull request checklist no longer names local process-note files.
- Ignore rules now use generic local-only patterns instead of publishing
  machine-specific tool names.
- Historical changelog entries still mention previously available admin routes;
  this is acceptable as release history, but new release notes must explain any
  intentional removal.

## Required Checks Before Release

Run these after deciding the final file set:

```bash
git status --short --untracked-files=all
git diff --check
make docs-check
python -m ruff check .
python -m ruff check src --select C901
python -m mypy src test
python -m pytest -q
python -m build
```

Current local verification results:

```text
make docs-check: PASS
Markdown local-link check: PASS
git diff --check -- ':!.env.example': PASS
.venv/bin/python -m ruff check .: PASS
.venv/bin/python -m ruff check src --select C901: PASS
.venv/bin/python -m pytest --version: BLOCKED (broken Pygments install)
.venv/bin/python -m mypy --version: BLOCKED (mypy package not executable)
.venv/bin/python -m build --version: BLOCKED (build module missing)
python -m ruff check .: BLOCKED (system Python 3.14 lacks Ruff)
```

If the security workflow remains unchanged, also run:

```bash
pip-audit -f json -s osv --desc off --aliases on -o pip-audit.json . || true
python scripts/ci/enforce_pip_audit_policy.py
```

## Commit Gate

Before committing, verify:

- `git diff --cached --name-only` contains no local-only archive, generated
  screenshots, local tool state, env files, key material, or scratch reports.
- Public docs and workflow files contain no local tool/process wording.
- Every untracked file is either staged intentionally or ignored intentionally.
- `CHANGELOG.md` documents any removed public capability.
