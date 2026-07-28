# Public Alpha Status

## Candidate

| Field | Current value |
| --- | --- |
| Version | `0.3.0a1` |
| Proposed tag | `v0.3.0-alpha.1` |
| Publication state | Unreleased |
| Readiness | Not ready for publication |
| Evidence date | 2026-07-24 |

Chronikwerk is available for local evaluation with non-production data. No source
checkout, package, image, or screenshot should be treated as release evidence until it
is produced from and verified against the same reviewed tag.

## Implemented surface

The candidate currently provides:

- authenticated single and batch Zammad webhook ingestion;
- bounded process-local admission and background processing;
- one localized archival PDF layout and JSON audit sidecar;
- optional PAdES signing and RFC3161 timestamping;
- atomic filesystem storage with path confinement and recovery handling;
- Zammad tag transitions and processing notes;
- optional authenticated process-local job history;
- a disabled-by-default administration application for operational state, retries,
  and staged non-secret configuration; and
- Python, systemd, Compose, and container deployment surfaces.

The runtime remains single-process. Admission, job history, sessions, replay
deduplication, and ticket locks are volatile. A `202 Accepted` response confirms
admission, not archival completion.

## Recorded local validation for the unfrozen checkout

The following checks pass on Python 3.14.6 and the current locked frontend toolchain:

- 648 Python tests;
- 88.66 percent branch-aware coverage against an 85 percent minimum;
- Ruff lint;
- mypy across 191 source files;
- TypeScript type checking and compiled administration asset comparison;
- Python source distribution and wheel builds;
- four Chromium administration scenarios;
- production and full-corpus duplication checks with zero clones;
- configuration-contract tests;
- brand, documentation-link, screenshot-manifest, and repository smoke checks; and
- whitespace validation with `git diff --check`.

The Python suite emits one upstream Starlette/httpx deprecation warning.

## Open validation gates

Publication still requires:

- formatting alignment for the files reported by `ruff format --check`;
- code-purpose documentation for the public functions reported by
  `scripts/ci/check_code_docs.py`;
- compliance with the function limits reported by `make complexity`;
- Firefox, WebKit, and narrow WebKit browser runs with the pinned binaries;
- production-image and Docker end-to-end checks;
- representative signed and unsigned PDF/UA validation;
- manual screen-reader, keyboard, contrast, 400 percent zoom, and populated-data
  review; and
- a complete verification run against the exact proposed tag.

## Release blockers and residual risk

- The package and repository identity migration has not been frozen into a reviewed
  source state.
- Python dependency resolution is range-based and does not provide a reviewed,
  hash-pinned release lock.
- Docker base images and operating-system packages are not immutable.
- Container registry publication is intentionally not configured.
- Replay-resistant delivery-ID signing is opt-in.
- Deep storage health checks are unauthenticated filesystem writes and must remain on a
  trusted operator path.
- Delivery IDs do not have a documented size and character bound.
- FastAPI `/docs`, `/redoc`, and `/openapi.json` remain unauthenticated; the interactive
  pages load external browser assets.
- The final security-reporting destination, copyright holder, dependency-license
  inventory, image SBOM, and attribution review are unresolved.

## Maintained compiled assets

The following compiled files are intentional project artifacts:

- `src/chronikwerk/static/admin/admin.js`, built from `frontend/admin.ts`;
- `src/chronikwerk/static/admin/admin.css`, assembled from
  `frontend/admin/css/*.css`; and
- `docs/screenshots/*.png` with `docs/screenshots/manifest.json`.

The build and documentation checks compare these files with their source inputs.
Build directories, caches, browser reports, local configuration, credentials, local
databases, logs, archive output, and development-tool state are not release artifacts.

## Publication gate

Do not publish this candidate until the source state is frozen, the blockers above are
resolved or explicitly accepted, every required automated and manual gate passes
against the same tag, and the resulting packages, image, release notes, screenshots,
and checksums receive final review.
