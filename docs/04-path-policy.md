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

Controls the user directory segment:

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

| Input | Output |
| --- | --- |
| `Müller` | `Muller` |
| `Sales Team EMEA` | `Sales_Team_EMEA` |
| `客户` | `_` |

## Root Confinement

The final resolved path must stay under `storage.root`. Escape attempts fail the
job before writing.

Example output:

```text
/mnt/archive/john.doe/Customers/ACME_GmbH/2026/Ticket-123_20260207T120000Z.pdf
/mnt/archive/john.doe/Customers/ACME_GmbH/2026/Ticket-123_20260207T120000Z.pdf.json
```
