# ADR 0005: Admin control plane and accessible archive documents

## Status

Accepted (2026-07-11)

## Context

The authoritative baseline is the current single-process FastAPI service. It has no
interactive frontend, durable queue, archive browser, or secret-management UI. Its only
presentational surface is the Jinja and WeasyPrint PDF pipeline. Operators otherwise use
raw operational endpoints, logs, Zammad, and storage.

## Decision

Add a server-rendered FastAPI and Jinja administration application for service status,
process-local job history, safe retries, and staged non-secret configuration. It is
disabled by default and uses one externally managed high-entropy access token,
process-local sessions, per-session CSRF protection, strict response headers, and secure
cookies. Sessions and job history remain explicitly volatile.

Managed configuration is an allowlisted non-secret overlay. Environment values retain
highest precedence and remain read-only in the UI. Changes are validated, revisioned,
written atomically, and require an external restart. The UI neither manages secrets nor
restarts the service.

German (`de-DE`) and English (`en-GB`) catalogs are shared by the admin and PDF surfaces.
The admin target is WCAG 2.2 AA. Archive PDFs use semantic HTML, complete article counts,
localized metadata, a production-guaranteed DejaVu Sans stack, and WeasyPrint's
`pdf/ua-1` variant. Independent veraPDF validation and human assistive-technology checks
remain release gates because renderer tagging alone does not prove conformance.

## Consequences

- Existing ingest, retry, history, health, and metrics contracts remain compatible.
- Admin-disabled deployments return 404 for all admin routes.
- A `202 Accepted` retry response never claims that archiving completed.
- No archive indexing, PDF preview, Redis, DLQ, RBAC, SSO, live reload, or UI-controlled
  restart is introduced.
- The admin feature remains off until browser, accessibility, PDF/UA, deployment, and
  documentation gates pass.
