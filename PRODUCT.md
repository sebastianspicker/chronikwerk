# Product

## Register

product

## Users

The service supports operations and DevOps administrators, Zammad administrators,
support leads, compliance reviewers, and security or release maintainers. They work
primarily on desktop and laptop systems, often during deployment or incident response,
and need dense, keyboard-accessible access to real service state without exposing
secrets. Help-desk agents continue to work in Zammad rather than in the admin surface.

## Product Purpose

Zammad Ticket Archiver turns authenticated Zammad webhook events into archival PDFs,
optional signatures and timestamps, audit sidecars, and visible Zammad outcomes. Its
administration application provides a coherent control plane for health, process-local
job history, safe retries, and staged non-secret configuration. Success means that
operators can distinguish accepted work from completed work, understand volatile and
staged state, recover safely, and produce complete, accessible German or English
archive documents without changing the single-process deployment model.

## Brand Personality

Dependable, institutional, precise. The interface is calm and utilitarian, uses direct
operational language, and earns trust by stating uncertainty, volatility, and restart
boundaries explicitly.

## Anti-references

Do not restore the deleted dashboard or imitate decorative SaaS dashboards. Avoid
gradients, glass panels, glowing status indicators, hover-lift cards, fake metrics,
decorative charts, raw JSON as the primary presentation, transient toasts as the only
feedback, placeholder-only labels, and ornamental motion. Do not duplicate archive
browsing, Redis, durable queues, DLQ controls, secret management, live reload, or
infrastructure restarts in the UI.

## Design Principles

1. State the operational truth, including volatility, staleness, and restart boundaries.
2. Keep expert data visible and scannable without exposing secrets.
3. Make consequential actions explicit, specific, and recoverable.
4. Prefer native semantics and familiar controls over custom interaction.
5. Localize language, dates, numbers, errors, and document metadata together.
6. Preserve the primary Zammad workflow instead of duplicating it.

## Accessibility & Inclusion

WCAG 2.2 AA is a release target for the administration application and PDF/UA-1 is a
release target for archive PDFs. German (`de-DE`) and English (`en-GB`) experiences are
complete. Keyboard operation, visible focus, 400% zoom and reflow, reduced motion,
non-color state cues, correct language metadata, semantic document structure, and
human screen-reader checks are release requirements.
