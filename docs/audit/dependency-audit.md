# Dependency Audit

**Date:** 2026-03-21
**Scope:** All Python runtime, optional, and dev dependencies declared in `pyproject.toml`.
**Method:** Installed versions from `pip list`, outdated check via `pip list --outdated`, import grep across `src/` and `test/`.

---

## Runtime Dependencies

| Package | Constraint | Installed | Latest | Status | Notes |
|---|---|---|---|---|---|
| fastapi | `>=0.110` | 0.128.3 | 0.135.1 | **Outdated** | Lower bound is loose; installed version is several minor releases behind. |
| uvicorn\[standard\] | `>=0.27` | 0.40.0 | 0.42.0 | **Outdated** | Minor update available. |
| pydantic | `>=2.6` | 2.10.3 | 2.12.5 | **Outdated** | Lower bound is loose; several patch/minor releases behind. |
| pydantic-settings | `>=2.2` | 2.6.1 | 2.13.1 | **Outdated** | Lower bound is loose. |
| python-dotenv | `>=1.0` | 1.1.0 | 1.2.2 | **Outdated** | Minor update available. |
| Jinja2 | `>=3.1` | 3.1.6 | 3.1.6 | Up-to-date | |
| PyYAML | `>=6.0` | 6.0.2 | 6.0.3 | **Outdated** | Patch update. |
| httpx | `>=0.27` | 0.28.1 | 0.28.1 | Up-to-date | |
| structlog | `>=24.0` | 25.5.0 | 25.5.0 | Up-to-date | |
| prometheus-client | `>=0.20` | 0.21.1 | 0.24.1 | **Outdated** | Several minor releases behind. |
| weasyprint | `>=68.0,<69` | 68.1 | 68.1 | Up-to-date | Tightly pinned to 68.x; intentional for pydyf compat. |
| pydyf | `>=0.11,<0.12` | 0.11.0 | 0.12.1 | **Pinned** | Locked to 0.11.x for WeasyPrint 68 compat; 0.12.1 exists but is excluded by upper bound. |
| cryptography | `>=46.0.5` | 46.0.5 | 46.0.5 | Up-to-date | Tight lower bound is appropriate for security-critical package. |
| pyhanko | `>=0.26,<0.28` | 0.27.1 | 0.34.1 | **Outdated** | Significantly behind (0.34.1 available); upper bound `<0.28` blocks upgrade. |
| pyhanko-certvalidator | `>=0.26,<0.28` | 0.26.8 | 0.30.1 | **Outdated** | Upper bound `<0.28` blocks upgrade to 0.30.x. |

### Constraint Assessment

- **Too loose:** `fastapi>=0.110`, `pydantic>=2.6`, `pydantic-settings>=2.2`, `uvicorn>=0.27`, `httpx>=0.27` -- these floor-only constraints allow any future major version. Consider adding upper bounds (e.g., `<1.0` for fastapi) to guard against breaking changes.
- **Appropriately pinned:** `weasyprint>=68.0,<69` and `pydyf>=0.11,<0.12` are intentionally locked together for compatibility. `cryptography>=46.0.5` is tight, which is good for a security-critical library.
- **Blocking upgrades:** `pyhanko>=0.26,<0.28` and `pyhanko-certvalidator>=0.26,<0.28` are 6+ minor versions behind latest. These upper bounds should be tested and raised.

---

## Dev Dependencies

| Package | Constraint | Installed | Latest | Status | Notes |
|---|---|---|---|---|---|
| build | `>=1.2` | 1.4.0 | 1.4.0 | Up-to-date | |
| pytest | `>=8.0` | 8.3.4 | 9.0.2 | **Outdated** | pytest 9.x is available. |
| pytest-cov | `>=5.0` | 7.0.0 | 7.0.0 | Up-to-date | |
| respx | `>=0.21` | 0.22.0 | 0.22.0 | Up-to-date | |
| ruff | `>=0.4` | 0.14.14 | 0.15.7 | **Outdated** | Minor update; ruff releases frequently. |
| mypy | `>=1.10` | 1.14.1 | 1.19.1 | **Outdated** | Several releases behind. |
| playwright | `>=1.50` | 1.58.0 | 1.58.0 | Up-to-date | |
| types-PyYAML | (unpinned) | 6.0.12.20250915 | 6.0.12.20250915 | Up-to-date | No version constraint -- acceptable for type stubs. |
| pre-commit | `>=3.7` | Not installed | -- | **Missing** | Declared in dev deps but not installed in this environment. |

---

## Optional Dependencies

| Package | Constraint | Installed | Latest | Status | Notes |
|---|---|---|---|---|---|
| redis | `>=5.0` | 5.3.1 | 7.3.0 | **Outdated** | Major version 7.x available; `>=5.0` allows it but it is not installed. Lower bound could be raised to `>=5.3`. |

---

## Unused Dependencies

| Package | Declared In | Direct Import Found? | Verdict |
|---|---|---|---|
| pydyf | runtime | No direct import in `src/` | **Indirect use only.** Referenced in a comment in `render_pdf.py` as a WeasyPrint transitive dependency pinned for compatibility. Not imported directly -- it is a version-lock companion for WeasyPrint. Keeping it is intentional to prevent pip from resolving an incompatible version. |
| pyhanko-certvalidator | runtime | No direct import in `src/` | **Indirect use only.** Used transitively by pyhanko. Listed explicitly to pin a compatible version range. Found in `scripts/ops/verify-pdf.py` only. Keeping it is intentional. |
| playwright | dev | No import in `test/` | **Used in scripts only.** Imported in `scripts/demo/capture_screenshots.py`, not in the test suite. Consider moving to a separate optional dependency group (e.g., `[scripts]`) rather than `[dev]`. |

All other declared dependencies have confirmed direct imports in `src/` or `test/`.

---

## Security Audit Pipeline Review

**File:** `.github/workflows/security.yml`

### Strengths
- Runs on push to `main`, all PRs, weekly schedule, and manual dispatch -- good coverage.
- Uses `pip-audit` with OSV data source and JSON output for machine-parseable results.
- Custom severity policy: fails on CRITICAL, HIGH, and unknown-severity vulns (fail-closed).
- Fetches full CVSS vectors from OSV API for accurate severity classification.
- Retry logic (4 attempts with backoff) for transient OSV API failures.
- Actions are pinned to full commit SHAs (supply-chain hardening).
- `persist-credentials: false` on checkout.
- Minimal `permissions: contents: read`.

### Weaknesses / Recommendations
- The `|| true` after `pip-audit` suppresses its exit code entirely. If `pip-audit` itself crashes (not just "found vulnerabilities"), the pipeline silently succeeds with an empty JSON file. Consider checking the exit code more granularly: exit code 1 = vulns found (expected), other codes = tool failure (should fail the job).
- The `pip-audit` command audits `.` (the project) but does not specify `--require-hashes` or `--strict` mode.
- No SBOM (Software Bill of Materials) generation step. Consider adding `cyclonedx-bom` or `pip-audit --format=cyclonedx-json` for compliance.
- The inline Python enforcement script is long (~170 lines). Consider extracting it to a standalone script under `scripts/ci/` for testability.

---

## Dependabot Configuration Review

**File:** `.github/dependabot.yml`

### Configuration
- **github-actions ecosystem:** weekly, prefix `ci` -- good.
- **pip ecosystem:** weekly, prefix `deps`, limit 10 open PRs -- good.

### Strengths
- Covers both GitHub Actions and pip dependencies.
- Weekly cadence is reasonable.
- PR limit of 10 prevents flooding.
- Commit message prefixes enable conventional-commit filtering.

### Weaknesses / Recommendations
- No `ignore` rules are configured. This is fine if all PRs are reviewed, but could lead to noise from frequent ruff/mypy releases.
- No `groups` configuration. Consider grouping minor/patch updates (e.g., all dev deps in one PR) to reduce review burden.
- Docker ecosystem is not monitored. If the project uses a base image in `Dockerfile`, add a `docker` ecosystem entry.
- No `reviewers` or `assignees` configured -- PRs may go unnoticed without notifications.

---

## Recommendations

### High Priority
1. **Raise pyhanko upper bounds.** `pyhanko>=0.26,<0.28` is 6 minor versions behind (0.34.1). Test compatibility with 0.34.x and update to `<0.35`. Same for `pyhanko-certvalidator` (0.30.1 available, currently capped at `<0.28`).
2. **Update cryptography promptly when new versions appear.** Currently at 46.0.5 (latest). This package frequently receives security patches -- the tight lower bound is correct.
3. **Add upper bounds to loose constraints.** For `fastapi`, `pydantic`, `httpx`, and `uvicorn`, add `<1.0` or similar caps to prevent unexpected breaking changes from future major releases.

### Medium Priority
4. **Update dev tooling.** pytest 9.x, mypy 1.19.x, and ruff 0.15.x are available. These are low-risk upgrades that improve lint coverage and test features.
5. **Evaluate WeasyPrint/pydyf pin.** The `<69` / `<0.12` pins may become stale. Track WeasyPrint 69+ releases and test upgrading when available.
6. **Harden pip-audit failure handling.** Replace `|| true` with exit-code-aware logic so tool crashes are not silently swallowed.
7. **Move playwright to `[scripts]` optional group.** It is not used in tests, only in `scripts/demo/`.

### Low Priority
8. **Add Dependabot groups.** Group dev dependency updates to reduce PR noise.
9. **Add Docker ecosystem to Dependabot** if a Dockerfile base image is used.
10. **Consider generating an SBOM** in the security workflow for supply-chain transparency.
11. **Install pre-commit in dev environments.** It is declared as a dev dependency but was not found installed locally.
