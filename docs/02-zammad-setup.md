# 02 - Zammad Setup

This guide covers Zammad-side setup for sending archive requests to the service.

## Prerequisites

- Zammad admin access.
- Reachable Chronikwerk endpoint, for example
  `https://archiver.example.com/ingest`.
- `ZAMMAD__WEBHOOK_HMAC_SECRET` configured in Chronikwerk.

## Custom Fields

Create ticket fields matching the configured Chronikwerk field names.

| Technical name | Required | Notes |
| --- | --- | --- |
| `archive_path` | yes | Target path under `storage.root`; prefer controlled values. |
| `archive_user_mode` | no | Defaults to `owner`; may be `owner`, `current_agent`, or `fixed`. |
| `archive_user` | conditional | Required only for `archive_user_mode=fixed`. |
| `archive_request` | optional | Useful for Zammad-side workflow conditions. |

Admin path: `Admin -> Objects -> Ticket`.

## Core Workflow Rules

Recommended validation:

1. When `archive_request=true`, require `archive_path`. Set
   `archive_user_mode` only when the default `owner` behavior is not sufficient.
2. When `archive_user_mode=fixed`, require `archive_user`.

Admin path: `Admin -> Core Workflows`.

## Archive Macro

Create a macro that:

- adds the trigger tag, default `pdf:sign`
- optionally sets `archive_request=true`

Admin path: `Admin -> Manage -> Macros`.

## Webhook

Create a Zammad webhook targeting:

```text
https://archiver.example.com/ingest
```

Configure the same HMAC secret as `ZAMMAD__WEBHOOK_HMAC_SECRET`.

## Trigger

Create a trigger that sends the webhook when the archive macro updates a
ticket. Include enough payload data for the service to resolve `ticket.id`.

## Smoke Test

1. Fill `archive_path`; optionally choose a non-default `archive_user_mode`.
2. Apply the archive macro.
3. Confirm Zammad receives HTTP `202`.
4. Confirm temporary `pdf:processing`.
5. Confirm final `pdf:signed` or `pdf:error`.
6. Confirm the internal Chronikwerk note.

`pdf:signed` is the retained workflow success tag and does not prove that optional PAdES
signing ran. Verify signatures from the PDF and audit sidecar.

## Common Issues

### `403 forbidden`

HMAC secret mismatch, wrong signature header, or body transformation by a proxy.

### `400 missing_delivery_id`

The service requires `X-Zammad-Delivery`, but Zammad did not send it.

### Trigger does not fire

Check macro tag changes, trigger conditions, and workflow validation rules.

### Ticket ends in `pdf:error`

Zammad setup may be correct; inspect storage, signing, TSA, and service logs.
