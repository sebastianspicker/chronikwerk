# ADR 0009: Composition-owned runtime state

## Status

Accepted (2026-10-03). Refines [ADR 0008](0008-modular-monolith-boundaries.md).

## Context

After ADR 0008 the process still kept mutable state in module globals: job history, ticket
guards (in-flight locks and delivery dedupe), and shutdown tracking, each with a test-only reset
hook. Jobs travelled as dictionaries with magic payload keys, so a job without a ticket id was
representable. `documents` imported `zammad` for the Zammad-to-snapshot mapping, which tied PDF
production to a wire format. Two scrubbers existed, one in `archiving` and a weaker copy in
`configuration`, and tag rules and failure hints were split between `zammad` and `archiving`.

## Decision

- `composition.py` builds the job-processing state: `JobHistory`, `TicketGuards`,
  `TicketSchedulingService`, the shared Zammad client, and the archive processor. `create_app`
  receives them explicitly (`scheduler`, `history`, `cleanup`); without a scheduler the app is
  read-only. Request-serving state stays per application in `web` (admin sessions, the managed
  configuration store, rate-limit buckets, the deep-health lock). Remaining module-level state is
  deliberate and not job state: Prometheus metrics (process-wide by design, so admission gauges
  reflect the last-updated admission), the PDF signer cache, and the WeasyPrint render lock.
- `TicketSchedulingService` owns tracked tasks and shutdown. `aclose()` stops accepting work
  synchronously, closes admission, drains for `admission.shutdown_timeout_seconds`, then cancels
  and awaits the finalizers. Jobs are typed `TicketJob` envelopes that always carry a ticket id.
- `archiving` owns the Zammad-to-snapshot mapping, the tag state machine, error policy, and
  notes. `documents` depends on no feature package, and `archiving` no longer imports
  `configuration`.
- Shared leaf modules that import no other package module: `failures`, `outbound` (including
  bounded HTTP response helpers), `timestamps`, `redaction` (one `scrub_secrets_in_text`),
  `concurrency` (cancellation-safe thread offloading), `i18n`, and `_version`. `configuration/redaction.py` only redacts settings dumps.
- `tests/unit/configuration/test_architecture.py` is the authoritative dependency map.

## Consequences

- Tests build isolated instances; no reset hooks or cross-test leakage.
- Error notes and logs share one scrubber and redact more secret shapes; error hints name the
  configured trigger tag.
- `settings_not_configured` and `skipped_no_ticket_id` no longer exist, and dedupe no longer
  pauses while shutting down.
- Volatile single-process semantics are unchanged: the state is explicit, not durable.
