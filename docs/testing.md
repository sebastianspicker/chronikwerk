# Testing

The local verification gate is:

```bash
make verify
```

For focused Python checks, run:

```bash
ruff check .
pytest
```

For shell script changes, run ShellCheck on the touched scripts, for example:

```bash
shellcheck scripts/ops/mount-cifs.sh
```

After static checks pass, probe a user-facing surface:

```bash
PYTHONPATH=src python -m zammad_pdf_archiver.cli --help
PYTHONPATH=src python -m zammad_pdf_archiver.cli --version
```
