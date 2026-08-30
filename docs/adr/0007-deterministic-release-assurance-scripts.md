# ADR 0007: Deterministic release-assurance scripts

## Status

Accepted (2026-08-02)

## Context

Release evidence must be reproducible enough to diagnose failures while still
distinguishing repository-owned checks from production, browser, and external
service validation. The repository includes dependency auditing, production
image smoke coverage, and administration screenshots. These scripts invoke
subprocesses and local services, so their inputs and cleanup behavior are part
of the assurance contract.

## Decision

This decision governs the repository skeleton gate in
`scripts/ci/smoke-test.sh`; keep that gate synchronous, fail-fast, and limited
to release-critical path existence checks before more expensive CI lanes run.

Keep release-assurance workflows synchronous and command-line driven. The
security workflow records each `pip-audit` command status and JSON report, then
passes both to the repository policy script. The policy treats missing,
malformed, incomplete, or indeterminate severity data as failures and audits
the signing environment separately after signing dependencies are installed.

The checked-in administration screenshots are static documentation assets.
They preserve the release-era visual reference without a maintained browser
automation runner. These images are
deterministic documentation previews, not evidence of live-browser behavior.

Repository tests disable real socket connections by default. Unit and
integration coverage therefore relies on injected clients or transport mocks.
Any browser,
cross-browser, production proxy, real Zammad, signing, TSA, or manual
accessibility evidence remains a separate release gate for the exact candidate.

## Consequences

- Local failures identify the audit, artifact, or screenshot phase
  without silently continuing after an incomplete result.
- Synthetic test inputs keep repository checks independent of operator
  configuration and real ticket data.
- Passing repository checks does not claim production-network, external
  service, cross-browser, or manual conformance closure.
