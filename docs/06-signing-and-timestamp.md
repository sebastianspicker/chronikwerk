# Signing and timestamping

Signing is optional. When enabled, the service applies a PAdES signature to the
rendered PDF and can request an RFC 3161 timestamp.

## Required configuration

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

## Runtime behavior

- If signing is disabled, the unsigned PDF is stored.
- If signing is enabled but the PFX is missing or invalid, processing fails.
- If timestamping is enabled but the TSA request fails, processing fails.
- The audit sidecar records the signing and timestamp flags. The certificate
  fingerprint comes from the exact in-memory signer used for the PDF, so mutable
  PFX material is never reread for provenance.
- Cached signers are keyed by the loaded PFX bytes and password, so a rotation is
  detected even when a deployment preserves the file's modification time. The
  cached certificate's validity window is checked on every signature, so hourly
  PFX parsing cannot sign past certificate expiry.

## Secret handling

Keep PFX files and passwords outside the repository. Use deployment secret
storage, protected environment files, or read-only mounted files. Provision the PFX as a
bounded regular file owned by the service account. Do not use a symlink or a group- or
world-writable key file; this candidate does not enforce those checks itself.

## Verification

Use a PDF signature validation tool that trusts the issuing certificate chain and
the configured TSA certificate chain. Validation depends on the operator trust
store; the app records best-effort signing metadata but does not replace an
external validation policy.
