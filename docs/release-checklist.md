# Release Checklist

This project uses SemVer and Keep-a-Changelog style `CHANGELOG.md`.

## Release Modes

| Mode | Package version | Git tag |
| --- | --- | --- |
| stable | `0.2.0` | `v0.2.0` |
| release candidate | `0.2.0rcN` | `v0.2.0-rc.N` |

## Preconditions

- You are on `main`.
- The working tree is clean.
- CI is green for the commit being released.
- `make verify` is the first local release validation command: it enforces
  branch-aware coverage at 85%, static/unit/integration/NFR checks, docs/config
  checks, build and clean-wheel import, production-image unsigned-render smoke,
  and dedicated Docker E2E.
- On hosts without Docker, use `make verify-core` for non-container diagnostics;
  it is not release sign-off because image and E2E checks remain mandatory.
- Security dependency auditing remains a separate required fail-closed workflow
  covering base and signing dependency environments.
- RC publication is tag-only (`v*-rc.*`): reusable CI and security workflows
  must pass for the tagged SHA, and the release job downloads their `dist`
  artifact rather than rebuilding. Tag/version normalization and the exact
  nonempty `CHANGELOG.md` section are mandatory checks.
- Security workflow is green.
- Target version and tag format are decided.

## Version and Changelog

1. Update `project.version` in `pyproject.toml`.
2. Move `CHANGELOG.md` entries from `[Unreleased]` into the release section.
3. Leave an empty `[Unreleased]` section for future work.

## Local Validation

Run the repository-owned aggregate gates first:

```bash
make verify-core
make verify
```

The commands below document the constituent non-container checks for diagnosis:

```bash
python -m ruff check .
python -m ruff check src --select C901
python -m mypy . --config-file pyproject.toml
python -m pytest -q
make docs-check
python -m build
```

## Wheel Smoke Test

```bash
python -m venv /tmp/zpa-release-venv
. /tmp/zpa-release-venv/bin/activate
python -m pip install -U pip
python -m pip install dist/*.whl
python - <<'PY'
from zammad_pdf_archiver.app.server import create_app
from zammad_pdf_archiver.config.settings import Settings

settings = Settings.from_mapping({
    "zammad": {"base_url": "https://example.invalid", "api_token": "x"},
    "storage": {"root": "/tmp"},
})
app = create_app(settings)
assert app.title == "zammad-pdf-archiver"
print("wheel-import-ok", app.version)
PY
```

## Docker Smoke Test

Release evidence uses the production `Dockerfile`, not the development image.
The dedicated API fixture builds that image, starts a minimal mock Zammad and
archive volume, signs ingest bodies with SHA-256 HMAC, checks authenticated
history, tags/notes, retry acceptance (`202`), PDF headers, sidecars, and
checksums, then tears the stack down:

```bash
make test-e2e
```

```bash
docker build -t zammad-pdf-archiver:local .
docker run --rm -p 8080:8080 \
  -e ZAMMAD__BASE_URL=https://example.invalid \
  -e ZAMMAD__API_TOKEN=x \
  -e ZAMMAD__WEBHOOK_HMAC_SECRET=x \
  -e STORAGE__ROOT=/tmp \
  zammad-pdf-archiver:local
```

In another terminal:

```bash
python - <<'PY'
import urllib.request

print(urllib.request.urlopen("http://127.0.0.1:8080/healthz", timeout=2).read().decode())
PY
```

## Production Safety Checks

- Verify `/metrics` is protected when enabled.
- Verify `STORAGE__ROOT` is writable by the service identity.
- Execute one real archive run and confirm PDF plus sidecar.
- Confirm signing material and TSA settings in the target environment.
- Confirm logs and internal ticket notes do not expose secrets.
- Confirm admin routes return 404 when `admin.enabled=false`.
- Run keyboard-only and axe checks in German and English across Chromium, Firefox, and
  WebKit at desktop, 768px, 390px, and 400% zoom.
- Verify secure-cookie behavior behind the production TLS proxy and confirm no external
  browser asset requests.
- Run veraPDF 1.30.1 with the `ua1` profile on unsigned and signed representative PDFs;
  confirm title, language, A4 size, tagging, bookmarks, and DejaVu font embedding.
  The pinned local gate is `make pdf-ua-check PDF_FILES="unsigned.pdf signed.pdf"`.
- Complete VoiceOver/Safari, NVDA/Firefox, PDF reading-order, outline, contrast, reflow,
  and non-color-state manual checks. Renderer tagging alone is not conformance proof.

## Tag

```bash
git tag vX.Y.Z
git push origin vX.Y.Z
```

For release candidates:

```bash
git tag vX.Y.Z-rc.N
git push origin vX.Y.Z-rc.N
```

## GitHub Release

1. Verify CI artifacts for the tag.
2. Create or verify the GitHub release.
3. Use the matching `CHANGELOG.md` section as release notes.

## Post-Release

- Add a fresh `[Unreleased]` section if needed.
- Update deployment manifests or image tags maintained outside this repo.
