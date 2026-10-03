# Public alpha candidate

The current candidate is `0.3.0a1`, intended for the tag `v0.3.0-alpha.1`. It is for evaluation,
integration testing, and early operator feedback — not production use, and not a
compliance-certified archive solution.

When the pre-tag review passes, the tag runs CI, security validation, and a draft GitHub
prerelease. The release owner publishes that draft only after the remaining external and manual
[release-checklist](release-checklist.md) gates pass against the tagged artifacts. The candidate
is still unfrozen and unpublished; see [RELEASE_STATUS.md](../RELEASE_STATUS.md).

## What to expect

- One FastAPI process accepts authenticated Zammad webhooks and performs bounded, process-local
  background work.
- Tickets, articles, tags, and attachment metadata render into one localized PDF layout with an
  adjacent audit sidecar.
- PAdES signing and RFC 3161 timestamping are optional and need external signing material and
  infrastructure.
- A disabled-by-default German/English administration application exposes health, volatile history,
  acknowledged retries, and staged non-secret configuration.
- The production Compose file binds to loopback by default and expects a trusted TLS reverse proxy
  for anything externally reachable.

## Alpha limitations

- A `202 Accepted` response confirms admission, not archival completion. A process crash can lose
  accepted work.
- Compatibility-mode webhook HMAC authenticates the body but not the delivery ID. Strict
  delivery-ID signing is opt-in and needs sender-side support for the documented canonical form;
  keep ingest restricted to trusted Zammad sources.
- Deep health checks are unauthenticated archive-storage writes, and the FastAPI schema and
  interactive documentation endpoints are unauthenticated in this candidate. Restrict all of these
  at the network edge.
- Horizontal scaling, a durable queue, Redis/DLQ behavior, archive browsing, attachment binary
  export, retention, WORM enforcement, SSO, RBAC, and secret management are out of scope.
- Admin sessions and displayed job history are process-local. Managed non-secret revisions require
  an external restart before they take effect.
- PDF tagging and automated browser checks do not by themselves establish PDF/UA-1 or WCAG 2.2 AA
  conformance. Independent validation and assistive-technology checks remain release gates.
- Live Zammad, SMB/CIFS, signing, TSA, reverse-proxy, and recovery behavior still need validation in
  your target environment.

## Administration preview

The [screenshot tour](screenshots/README.md) shows the administration application in both locales.
These are deterministic documentation renders, not browser or accessibility evidence.

The repository also includes a
[mock-only static administration demo](https://sebastianspicker.github.io/zammad-ticket-archiver/). It uses
synthetic browser-local state and is not a deployed service, integration test, or release artifact.
See [admin-frontend.md](admin-frontend.md) for the demonstration boundary.

## Evaluate safely

1. Use a disposable Zammad project and a non-production archive path.
2. Start with signing and the administration application disabled.
3. Follow the [deployment guide](deploy.md), [Zammad setup](02-zammad-setup.md), and
   [security checklist](09-security.md).
4. Confirm completion from Zammad tags and notes, the service logs, and the PDF/sidecar pair — do
   not treat `202` as success.
5. Report ordinary defects with the GitHub issue template. Do not disclose vulnerabilities
   publicly; [SECURITY.md](../SECURITY.md) records the unresolved private-reporting gate.

## Compatibility

The alpha removes the former Redis queue/DLQ, the decorative dashboard, alternate PDF
templates, the broad pre-0.3 flat-variable compatibility layer, and the old operational demo stack.
The replacement static Pages demo has no backend capability, and the version 1 portable Zammad
aliases in the [configuration reference](config-reference.md) remain supported. Review
[CHANGELOG.md](../CHANGELOG.md) before upgrading from a `0.2.0` release candidate.

The supported contract is documented in the [architecture](01-architecture.md),
[configuration reference](config-reference.md), [API reference](api.md), and
[product contract](../PRODUCT.md).
