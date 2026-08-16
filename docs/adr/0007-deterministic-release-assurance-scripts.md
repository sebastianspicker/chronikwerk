# ADR 0007: Deterministic release-assurance scripts

## Status

Accepted (2026-08-02)

## Context

Release evidence must be reproducible enough to diagnose failures while still
distinguishing repository-owned checks from production, browser, and external
service validation. The repository includes dependency auditing, production
image smoke coverage, a Docker API fixture with a mock Zammad service, and
administration screenshots. These scripts invoke subprocesses and local network
services, so their inputs and cleanup behavior are part of the assurance
contract.

## Decision

This decision governs the repository skeleton gate in
`scripts/ci/smoke-test.sh`; keep that gate synchronous, fail-fast, and limited
to release-critical path existence checks before more expensive CI lanes run.

Keep release-assurance workflows synchronous and command-line driven. The
security workflow records each `pip-audit` command status and JSON report, then
passes both to the repository policy script. The policy treats missing,
malformed, incomplete, or indeterminate severity data as failures and audits
the signing environment separately after signing dependencies are installed.

Use the production `Dockerfile` for the Docker API smoke. The runner accepts a
repository dataset, uses deterministic default local stack coordinates and synthetic
credentials, starts a Compose project with the mock Zammad service, waits for
health endpoints, exercises signed ingest and retry flows, verifies Zammad
side effects plus PDF, sidecar, and checksum artifacts, and removes containers
and volumes unless `--keep-stack` is requested. Its subprocess wrapper permits
only resolved Docker commands; startup checks ports before Compose changes
local state.

Generate administration documentation screenshots from isolated synthetic
settings and authenticated FastAPI test-client HTML. The renderer uses the
locked Playwright Chromium package with fixed routes, locales, and viewports,
embeds shipped assets, disables JavaScript, records source hashes and renderer
metadata, and atomically replaces the matching manifest. These images are
deterministic documentation previews, not evidence of live-browser behavior.

Repository tests disable real socket connections by default. Unit and
integration coverage therefore relies on injected clients or transport mocks;
the Docker fixture is the explicit local-network exception. Any browser,
cross-browser, production proxy, real Zammad, signing, TSA, or manual
accessibility evidence remains a separate release gate for the exact candidate.

## Consequences

- Local failures identify the audit, stack, artifact, or screenshot phase
  without silently continuing after an incomplete result.
- The release scripts can clean up their known resources by default and expose
  deliberate retention through `--keep-stack` only.
- Synthetic credentials and fixtures keep release evidence independent of
  operator configuration and real ticket data.
- Passing repository checks does not claim production-network, external
  service, cross-browser, or manual conformance closure.
