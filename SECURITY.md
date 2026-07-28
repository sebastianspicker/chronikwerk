# Security policy

## Supported versions

| Version | Support |
| --- | --- |
| `0.3.0a1` | Best-effort fixes for the unfrozen public-alpha candidate. |
| `0.2.x` and older | No regular security maintenance. |

The candidate is not a stable or compliance-certified release. If an alpha is published,
use the newest published candidate, update dependencies, and apply the controls in
[docs/09-security.md](docs/09-security.md).

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability.

Use GitHub's `Security > Advisories > New draft security advisory` form when private
vulnerability reporting is enabled for the repository. Include:

- affected version or revision;
- reproduction steps;
- expected security impact;
- a minimal proof of concept when practical;
- relevant configuration with all credentials and private data removed.

Do not attach credentials, ticket content, archive documents, unredacted production logs,
signing material, or personal data.

Private vulnerability reporting has not yet been verified for this unfrozen candidate. No
fallback private channel is currently documented. If the private form is unavailable, do
not disclose vulnerability details in a public issue.

## Security scope

The repository security reference covers:

- webhook authentication and delivery-ID handling;
- request-size and rate limits;
- Zammad transport validation;
- archive path confinement and symlink rejection;
- credential redaction;
- optional route authentication;
- administration sessions and CSRF protection;
- container and filesystem boundaries;
- residual risks from process-local state.

See [docs/09-security.md](docs/09-security.md) for the implemented controls and deployment
requirements.
