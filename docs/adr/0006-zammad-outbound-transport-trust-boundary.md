# ADR 0006: Zammad outbound transport trust boundary

## Status

Accepted (2026-08-02)

## Context

Chronikwerk retrieves ticket data from a configured Zammad service after an
authenticated webhook is admitted. The Zammad origin and API token are
operator-supplied, while DNS resolution, HTTP routing, and TLS verification
are runtime concerns. Local and injected test fixtures also need to exercise
failure paths without weakening the production configuration contract.

## Decision

Use `ZammadConnection` as the immutable configured boundary. Its origin is
canonicalized to a credential-free origin with a scheme, host, and optional
port only. Production configuration accepts HTTPS origins, and the client uses
the fixed `/api/v1` root. The API token is held as a `SecretStr`, must be
non-empty, and must not contain whitespace.

Configured Zammad clients always enable TLS certificate verification. The
direct compatibility constructor rejects unsafe TLS or HTTP options unless a
private injected runtime is supplied. Private-network and insecure-HTTP
allowances remain explicit transport settings for reviewed internal or test
deployments; they are not defaults.

Before each outbound request, the client resolves the configured host off the
event loop. With the default public-network policy, every resolved address is
checked and the request is pinned to a validated address while retaining the
original HTTP Host header and TLS SNI identity. Literal loopback, private,
link-local, unspecified, reserved, and multicast addresses are rejected unless
the explicit private-network override applies. DNS resolution failures and
timeouts fail closed or are classified as transient transport failures.

The injected runtime is private to the client implementation. It may provide a
test HTTP client, retry timing, or explicit private-fixture allowance, but it
does not expand the public production configuration surface.

## Consequences

- Zammad credentials cannot be embedded in the configured origin and remain
  separate from request routing.
- Default production requests use HTTPS, certificate verification, validated
  DNS addresses, and `trust_env=false` unless operators explicitly opt in.
- Internal deployments and tests must declare their private-network or
  insecure-HTTP exception instead of relying on an implicit local bypass.
- Production egress controls remain necessary because proxy configuration and
  DNS rebinding can affect the effective network path after application checks.
