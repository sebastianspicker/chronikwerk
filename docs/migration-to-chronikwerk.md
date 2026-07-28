# Migrating to Chronikwerk

Chronikwerk is the public name for this `0.3.0a1` release. Its deployment names are
`chronikwerk`, `chronikwerk-admin`, `/opt/chronikwerk`, `/etc/chronikwerk`, and
`/var/lib/chronikwerk/admin`.

## Manual migration only

There is no automatic migration. Stop the existing service before copying any state.
Copy only the administrator revision state to the new state directory, preserving
ownership and restrictive permissions, then update the deployment checkout, environment
file, Compose service, and systemd unit to their Chronikwerk names. Start the new service
only after the copy and configuration review complete.

The prior public deployment name was `zammad-pdf-archiver`; its systemd and environment
file names used `zammad-archiver`, and the checkout and configuration directories used
`zammad-ticket-archiver` and `/etc/zammad-archiver`. Do not run both deployments against
the same state directory.

Zammad configuration, `ZAMMAD__*`, `STORAGE__*`, and `ADMIN__*` environment variables,
archive fields, `pdf:*` tags, API routes, and schema names remain unchanged.
