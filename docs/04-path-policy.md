# 04 - Path Policy

Archive paths are parsed from ticket fields, sanitized, and confined under
`storage.root`.

## Input Fields

### `archive_path`

Accepted formats:

- string with `>` separators, for example `Customers > ACME GmbH > 2026`
- list of strings, for example `["Customers", "ACME GmbH", "2026"]`

Empty fragments are ignored, but at least one segment must remain.

### `archive_user_mode`

Optionally controls the user directory segment. A missing value defaults to `owner`:

- `owner`: `ticket.owner.login`
- `current_agent`: webhook user login, falling back to `ticket.updated_by.login`
- `fixed`: `archive_user`

## Validation

Each segment must:

- be a string
- be non-empty after trimming
- not be `.` or `..`
- not contain `/`, `\`, or NUL
- be at most 64 characters

The archive path is limited to 10 segments. Violations are permanent processing
failures.

## Sanitization

After validation, segments are sanitized deterministically:

- Unicode normalized with NFKD
- combining marks removed
- whitespace collapsed to `_`
- only `A-Za-z0-9._-` kept
- other characters replaced with `_`
- repeated underscores collapsed

Examples:

| Input | Readable sanitized prefix |
| --- | --- |
| `Müller` | `Muller` |
| `Sales Team EMEA` | `Sales_Team_EMEA` |
| `客户` | `_` |

Sanitization is lossy, so an archive component whose sanitized form differs
from its raw input receives a `-<32 hex characters>` suffix derived from the
SHA-256 digest of the raw UTF-8 input. This prevents distinct usernames, path
segments, or ticket numbers such as `alice+hr` and `alice?hr` from mapping to
the same archive location. Already-safe components remain unchanged. The
readable prefix is truncated when necessary so the stored component stays
within the 64-character path limit.

## Root Confinement

The final resolved path must stay under `storage.root`. Escape attempts fail the
job before writing.

Example output:

```text
/mnt/archive/john.doe/Customers/ACME_GmbH/2026/Ticket-123_20260207T120000Z.pdf
/mnt/archive/john.doe/Customers/ACME_GmbH/2026/Ticket-123_20260207T120000Z.pdf.json
```

The example uses already-safe stored components; lossy inputs include the
disambiguation suffix described above.
