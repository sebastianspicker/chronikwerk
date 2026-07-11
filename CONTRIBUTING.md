# Contributing

Thanks for contributing to **zammad-ticket-archiver**.

## Development workflow

1. Create a fork and a feature branch.
2. Implement the change (keep PRs small and focused).
3. Run the checks appropriate to the change:
   - `make test-fast` while iterating
   - `make verify-core` for non-container changes
   - `make verify` for release or deployment changes
4. Open a pull request with:
   - a clear problem statement / intent
   - any operational impact documented in `docs/08-operations.md` and/or `docs/09-security.md`

## Code style

- Python: `>=3.12` (see `pyproject.toml`)
- Linting: `ruff`
- Typing: `mypy` (required by `make verify-core`)
- Browser accessibility: pinned Playwright and axe via `make test-browser`
- PDF/UA validation: pinned veraPDF via `make pdf-ua-check PDF_FILES="..."`

Never commit real `.env` files, local YAML overrides, signing material, generated archive
PDFs, audit sidecars, credentials, admin revision state, local reports, or tool caches.
Use placeholders in examples and keep operational evidence in ignored local archive or
evidence lanes.

## Releases

Goal: reproducible releases (sdist/wheel) and optionally Docker images.

See `docs/release-checklist.md` for the step-by-step release procedure.

1. Local checks:
   - `make verify`
   - `make test-browser`
   - `make pdf-ua-check PDF_FILES="unsigned.pdf signed.pdf"`
2. Version + changelog:
   - update `CHANGELOG.md`
   - update version in `pyproject.toml`
3. Tag:
   - `git tag vX.Y.Z`
   - `git push origin vX.Y.Z`
4. CI artifacts:
   - CI builds `sdist` + `wheel` as workflow artifacts.
   - Docker builds an image on pushes to `main` and tags `v*`.
     - Pushing to GHCR is optional and only happens if secrets are configured.
