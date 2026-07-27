# 03 - Data Model

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
and identifiers). Attachment binary content is intentionally not persisted by
the storage layer; the metadata is rendered in the PDF only.

## Path Fields

Path placement is derived from ticket custom fields:

- `ticket.custom_fields.archive_path`
- optional `ticket.custom_fields.archive_user_mode` (defaults to `owner`)
- `ticket.custom_fields.archive_user` when mode is `fixed`

See [04 - Path Policy](04-path-policy.md).

## Audit Sidecar

For every archived PDF, the service writes a JSON sidecar next to the PDF:

```text
Ticket-123_20260207T120000Z.pdf
Ticket-123_20260207T120000Z.pdf.json
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
