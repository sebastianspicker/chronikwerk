# Data model

This page describes the data objects that matter for rendering, storage, and
audit output.

## Snapshot

The renderer consumes a normalized snapshot built from Zammad ticket, article,
tag, and user data.

Example:

- [examples/ticket-snapshot.sample.json](../examples/ticket-snapshot.sample.json)

Core template fields:

- `ticket.id`
- `ticket.number`
- `ticket.title`
- `ticket.created_at`
- `ticket.updated_at`
- `ticket.customer`
- `ticket.owner`
- `ticket.tags`
- `ticket.custom_fields`
- `articles[]`
- `articles_total`
- `articles_omitted`

Each article may include attachment metadata (`filename`, `size`, content type,
and identifiers). The storage layer does not persist attachment binaries; only
the metadata is rendered in the PDF.

## Path fields

Path placement is derived from ticket custom fields:

- `ticket.custom_fields.archive_path`
- optional `ticket.custom_fields.archive_user_mode` (defaults to `owner`)
- `ticket.custom_fields.archive_user` when mode is `fixed`

See [04 - Path Policy](04-path-policy.md).

## Audit sidecar

For every archived PDF, the service writes a JSON sidecar next to the PDF:

```text
Ticket-123_2026-02-07.pdf
Ticket-123_2026-02-07.pdf.json
```

The sidecar records:

- ticket ID and number
- title
- archive timestamp
- storage path
- SHA-256 checksum
- signing/timestamp status
- service metadata
- article coverage (`total`, `included`, `omitted`, and `complete`)

The sidecar is for operational audit and integrity checks; it is not a durable
database.
