# Dependency locks

Chronikwerk keeps version ranges in `pyproject.toml` for package consumers and commits resolved,
SHA-256-hashed environments under `requirements/` for deployment and repository automation. The
four locks cover the base service, the signing-capable service, development, and build and audit
tools. They are generated with Python 3.14 and the exact `pip-tools` version declared in
`requirements/tools.in`.

Bootstrap the lock tool in a disposable environment, verify the committed tool lock, then refresh
all locks:

```bash
python3.14 -m venv .lock-venv
.lock-venv/bin/python -m pip install --only-binary=:all: --require-hashes -r requirements/tools.lock
PATH="$PWD/.lock-venv/bin:$PATH" make dependency-locks
rm -rf .lock-venv
```

Normal regeneration retains compatible committed pins. Use
`LOCK_UPGRADE=1 make dependency-locks` in the locked tool environment to select newer
versions deliberately. `make dependency-locks-fresh` regenerates from the committed
pins in a temporary directory and compares the results.

Review every resolved version change. A routine source edit should not refresh dependency locks.
Dependency installs require wheels with `--only-binary=:all:` so a dependency cannot
fetch an unlocked build backend for a source distribution. CI installs each lock
with `--require-hashes`, installs Chronikwerk itself with `--no-deps` and
`--no-build-isolation`, and builds distributions with `python -m build --no-isolation` after the
locked build backend is present.

The base and signing locks are audited separately so signing-only dependencies cannot disappear
inside the base result. Re-run the separate base and signing steps in `.github/workflows/security.yml`
after changing either production dependency set.

Docker pins the Python image tag and registry digest. Debian packages remain dynamic because the
image build installs the current packages from the pinned image's configured Debian repositories;
its contents can change between rebuilds. Hash-locked Python dependencies and image
digests therefore do not make the resulting images byte-reproducible.
