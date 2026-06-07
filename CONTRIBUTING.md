# Contributing

Thanks for contributing to **zammad-ticket-archiver**.

## Development workflow

1. Create a fork and a feature branch.
2. Implement the change (keep PRs small and focused).
3. Run local checks:
   - `make lint`
   - `make test`
   - `make qa`
4. Open a pull request with:
   - a clear problem statement / intent
   - any operational impact documented in `docs/08-operations.md` and/or `docs/09-security.md`
   - the exact commands run and their results

## Code style

- Python: `>=3.12` (see `pyproject.toml`)
- Linting: `ruff`
- Typing: `mypy` (optional but recommended for non-trivial changes)

## Documentation and local artifacts

- Keep public docs focused on current behavior.
- Do not stage local audit, plan, ledger, status, or archive packets unless a
  maintainer explicitly promotes them to public documentation.
- Use `docs/README.md` as the public docs router.
- Before release or docs cleanup work, check ignored local artifacts with
  `git check-ignore -v <path>`.

## Releases

Goal: reproducible releases (sdist/wheel) and optionally Docker images.

1. Local checks:
   - `make qa`
   - `make verify`
2. Release readiness:
   - confirm `README.md`, `docs/`, `.env.example`, `config/config.example.yaml`, and `config/config.schema.json` describe the current public behavior
   - confirm no internal audit, plan, ledger, status, or archive material is staged for commit
   - confirm `.gitignore` still excludes local-only docs and generated artifacts
   - run any deployment-specific smoke check needed for the changed surface, such as `make test-e2e` for Docker/API behavior
3. Version + changelog:
   - update `CHANGELOG.md`
   - update version in `pyproject.toml`
4. Tag:
   - `git tag vX.Y.Z`
   - `git push origin vX.Y.Z`
5. CI artifacts:
   - CI builds `sdist` + `wheel` as workflow artifacts.
   - Docker builds an image on pushes to `main` and tags `v*`.
     - Pushing to GHCR is optional and only happens if secrets are configured.
