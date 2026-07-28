# Public Alpha Candidate

The current candidate is `0.3.0a1` and is intended for the tag
`v0.3.0-alpha.1`. It is intended for evaluation, integration testing, and early
operator feedback. After pre-tag review, the tag triggers CI, security validation, and
creation of a draft GitHub prerelease. The owner publishes that draft only after the
remaining external and manual release-checklist gates pass against the tagged artifacts.
The candidate is currently unfrozen and unpublished; see
[RELEASE_STATUS.md](../RELEASE_STATUS.md). This is not a stable release or a
compliance-certified archive solution.

## What to expect

- One FastAPI process accepts authenticated Zammad webhooks and performs bounded,
  process-local background work.
- Tickets, articles, tags, and attachment metadata are rendered into one localized PDF
  layout with an adjacent audit sidecar.
- PAdES signing and RFC3161 timestamping are optional and require external signing
  material and infrastructure.
- A disabled-by-default German/English administration application exposes health,
  volatile history, acknowledged retries, and staged non-secret configuration.
- Production Compose binds to loopback by default and expects a trusted TLS reverse
  proxy for any externally reachable surface.

## Alpha limitations

- A `202 Accepted` response confirms admission, not archival completion. A process crash
  can lose accepted work.
- Compatibility-mode webhook HMAC authenticates the body but not the delivery ID.
  Strict delivery-ID signing is opt-in and requires sender support for the documented
  canonical form; keep ingest restricted to trusted Zammad sources.
- Deep health checks are unauthenticated archive-storage writes. The FastAPI schema and
  interactive documentation endpoints are also unauthenticated in this candidate.
  Restrict these routes at the network edge.
- Horizontal scaling, a durable queue, Redis/DLQ behavior, archive browsing, attachment
  binary export, retention, WORM enforcement, SSO, RBAC, and secret management are out of
  scope.
- Admin sessions and displayed job history are process-local. Managed non-secret
  revisions require an external restart before they become active.
- PDF tagging and automated browser checks do not by themselves establish PDF/UA-1 or
  WCAG 2.2 AA conformance. Independent validation and assistive-technology checks remain
  release gates.
- Live Zammad, SMB/CIFS, signing, TSA, reverse-proxy, and recovery behavior must be
  validated in the target environment.

## Administration preview

![German administration configuration editor with non-secret value ownership](screenshots/admin-configuration.png)

The [screenshot notes](screenshots/README.md) distinguish these deterministic
documentation renders from browser and accessibility evidence.

## Evaluate safely

1. Use a disposable Zammad project and non-production archive path.
2. Start with signing and the administration application disabled.
3. Follow the [deployment guide](deploy.md), [Zammad setup](02-zammad-setup.md), and
   [security checklist](09-security.md).
4. Confirm completion from Zammad tags and notes, service logs, and the PDF/sidecar pair;
   do not treat `202` as success.
5. Report ordinary defects with the GitHub issue template after the final repository
   exists. Do not disclose vulnerabilities publicly; [SECURITY.md](../SECURITY.md)
   records the unresolved private-reporting publication gate.

## Compatibility

The alpha deliberately removes the former Redis queue/DLQ, decorative dashboard,
alternate PDF templates, broad pre-0.3 flat-variable compatibility layer, and demo
stack. The version 1 portable Zammad aliases in the
[configuration reference](config-reference.md) remain supported. Review
[CHANGELOG.md](../CHANGELOG.md) before upgrading from a `0.2.0` release candidate.

The supported contract is documented in [the architecture](01-architecture.md),
[configuration reference](config-reference.md), [API reference](api.md), and
[product contract](../PRODUCT.md).
