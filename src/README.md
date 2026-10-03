# `src/`

The Python package is a modular monolith assembled by `chronikwerk/composition.py`.

- `archiving/`: archive workflow, outcomes, Zammad-to-snapshot mapping, tag state machine, notes, and failure policy.
- `zammad/`: Zammad transport, DTOs, and ticket gateway.
- `documents/`: snapshot models, sanitization, PDF templates, rendering, signing, and TSA.
- `storage/`: safe paths, root-confined filesystem access, audit records, and transactions.
- `configuration/`: validated settings, loading, redaction, and managed revisions.
- `operations/`: process-local scheduling and shutdown, admission, dedupe, ticket guards, history, logging, and metrics.
- `web/`: FastAPI routes, middleware, administration UI, templates, and generated assets.
- `composition.py`: converts configuration into narrow runtime options and builds all runtime objects.
- `failures.py`, `outbound.py`, `redaction.py`, `timestamps.py`, `concurrency.py`, `i18n.py`, `_version.py`: shared leaf modules that import no other package module.
- `runtime.py` and `asgi.py`: command-line and ASGI entry points.

The dependency rules are documented in the
[architecture reference](../docs/01-architecture.md) and enforced by
`tests/unit/configuration/test_architecture.py`. Do not recreate the removed historical package
families or import underscore-prefixed implementation modules across package boundaries.
