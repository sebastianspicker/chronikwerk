# Release Status

**Evidence cutoff:** 2026-07-14
**Verdict:** NOT READY for release publication

## Status Summary

The current source snapshot has no known reproducible repository-local defect
after the remediation and verification recorded below. It is still **NOT
READY** to publish because candidate identity, external integration evidence,
and release-owner approval remain unresolved.

Candidate identity observed on 2026-07-14: branch `main`, commit `33a718e`, no
tag at `HEAD`, ahead of `origin/main` by 1 commit and behind by 51 commits. Git
reported 269 working-tree paths while `.env.example` remained unreadable under
the local permission profile. Package version `0.2.0rc2` agrees with the
`0.2.0-rc.2` changelog entry and documented RC tag convention.

The documented runtime scope is single-instance and process-local. A durable
queue, multi-instance correctness, live SMB, and live Zammad behavior are not
claimed by the local verification.

## Verified Evidence

- Ruff passed across the repository; the expanded C901 complexity scan passed
  across `src/` and production scripts.
- mypy passed across 176 source files.
- Static, unit, integration, and NFR suites passed: 547 passed, 2 skipped. Both
  skips are the permission-blocked `.env.example` sanity cases.
- Branch coverage was 88.66%, above the required 85% threshold.
- The focused independent security regression suite passed 187 tests. The
  review found no remaining confirmed defect in request limits, authentication,
  admin controls, DNS pinning, upstream bounds, storage, or rollback behavior.
- Documentation and config-schema checks passed.
- A no-isolation wheel build, dependency-free wheel installation, installed
  package imports, metadata, and console entry-point declarations passed.
- Production, development, and E2E Compose files rendered successfully with an
  explicit empty environment file. The E2E harness dry-run passed.

The only test warning was the upstream Starlette `TestClient` deprecation for
its current `httpx` integration.

## Remaining Release Blockers

- Reconcile the branch with upstream, review the large dirty candidate tree,
  freeze it, and tag the intended commit.
- Run browser rendering and PDF/UA validation with the required local browser
  and validation tooling.
- Run actual Docker image/container smoke and live SMB, Zammad, signing/TSA,
  and end-to-end deployment checks in approved infrastructure.
- Obtain release-owner approval after reviewing the exact candidate artifact.

## Operational Boundaries

- Accepted background work is process-local; a crash can lose it.
- Native PDF, signing, and filesystem worker threads cannot be interrupted
  safely and can extend shutdown beyond the configured async grace period.
- `storage.root` and its host-level ancestry are administrator-controlled trust
  anchors.
- Explicit private-network and environment-proxy opt-ins expand the default
  outbound trust boundary.

## Next Gate

Reconcile and freeze one versioned candidate, then run the browser/PDF-UA and
live deployment evidence against that exact artifact before publication.
