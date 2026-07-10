# 06 - Signing and Timestamping

Signing is optional. When enabled, the service applies a PAdES signature to the
rendered PDF and can request an RFC3161 timestamp.

## Required Configuration

Signing:

```bash
SIGNING__ENABLED=true
SIGNING__PFX_PATH=/run/secrets/signing.pfx
SIGNING__PFX_PASSWORD=CHANGE-ME
```

Timestamping:

```bash
SIGNING__TIMESTAMP__ENABLED=true
SIGNING__TIMESTAMP__RFC3161__TSA_URL=https://tsa.example.com
```

Optional TSA basic auth:

```bash
SIGNING__TIMESTAMP__RFC3161__USER=tsa-user
SIGNING__TIMESTAMP__RFC3161__PASSWORD=CHANGE-ME
```

## Runtime Behavior

- If signing is disabled, the unsigned PDF is stored.
- If signing is enabled but the PFX is missing or invalid, processing fails.
- If timestamping is enabled but the TSA request fails, processing fails.
- Signing and timestamp flags are recorded in the audit sidecar.

## Secret Handling

Keep PFX files and passwords outside the repository. Use deployment secret
storage, protected environment files, or read-only mounted files.

## Verification

Use a PDF signature validation tool that trusts the issuing certificate chain and
the configured TSA certificate chain. Validation depends on the operator trust
store; the app records best-effort signing metadata but does not replace an
external validation policy.
