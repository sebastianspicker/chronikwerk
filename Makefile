# Centralize repeatable development, quality, packaging, and release checks.
PYTHON ?= python
NPM ?= npm

.PHONY: dev dev-setup dependency-locks dependency-locks-check dependency-locks-fresh \
	frontend-install frontend-css-build frontend-js-build frontend-build frontend-typecheck \
	frontend-css-check frontend-js-check frontend-check frontend-update browser-test \
	pages-demo-build pages-demo-check lint format typecheck complexity duplication \
	test test-fast test-int pdf-ua-check brand-check docs-check \
	code-docs-check source-length-check build clean-wheel-smoke \
	production-image-smoke verify-core verify clean

dev:
	docker compose -f docker-compose.dev.yml up --build

dev-setup:
	@echo "Setting up development environment..."
	$(PYTHON) -m pip install --require-hashes --only-binary=:all: -r requirements/tools.lock
	$(PYTHON) -m pip install --require-hashes --only-binary=:all: -r requirements/dev.lock
	$(PYTHON) -m pip install --no-deps --no-build-isolation -e .
	$(NPM) ci --ignore-scripts
	@echo "Creating .env from example if not exists..."
	@if [ ! -f .env ]; then cp .env.example .env && echo "Created .env - please edit with your settings"; fi
	@echo "Development setup complete!"

lint:
	$(PYTHON) -m ruff check .

brand-check:
	$(PYTHON) scripts/ci/check_brand_identity.py

format:
	$(PYTHON) -m ruff format .

typecheck:
	$(PYTHON) -m mypy . --config-file pyproject.toml

complexity:
	$(PYTHON) -m lizard -w -C 10 -L 80 \
		-x "src/chronikwerk/web/static/admin/admin.js" \
		src/chronikwerk scripts frontend

duplication:
	$(NPM) run duplication:production
	$(NPM) run duplication:all

frontend-install:
	$(NPM) ci --ignore-scripts

frontend-typecheck:
	$(NPM) run typecheck

frontend-css-build:
	$(NPM) run build:admin-css

frontend-js-build:
	$(NPM) run build:admin-js

frontend-build: frontend-css-build frontend-js-build

frontend-css-check: frontend-css-build
	@cmp -s build/admin/admin.css src/chronikwerk/web/static/admin/admin.css || \
		(echo "Generated admin.css is stale; run 'make frontend-update'." && exit 1)

frontend-js-check: frontend-js-build
	@cmp -s build/typescript/admin.js src/chronikwerk/web/static/admin/admin.js || \
		(echo "Generated admin.js is stale; run 'make frontend-update'." && exit 1)

frontend-check: frontend-typecheck frontend-css-check frontend-js-check

frontend-update: frontend-build
	cp build/typescript/admin.js src/chronikwerk/web/static/admin/admin.js
	cp build/admin/admin.css src/chronikwerk/web/static/admin/admin.css

browser-test:
	$(NPM) run test:browser

pages-demo-build: frontend-css-check
	$(PYTHON) scripts/ci/build_pages_demo.py

pages-demo-check: pages-demo-build
	node --check demo/site/assets/demo.js
	$(PYTHON) scripts/ci/check_pages_demo.py

test:
	$(PYTHON) -m coverage run -m pytest -q
	$(PYTHON) -m coverage report

test-fast:
	$(PYTHON) -m pytest -q tests/unit

test-int:
	$(PYTHON) -m pytest -q tests/integration

pdf-ua-check:
	@test -n "$(PDF_FILES)" || (echo "Set PDF_FILES to signed and unsigned fixture paths" && exit 2)
	bash scripts/ci/verify_pdf_ua.sh $(PDF_FILES)

docs-check:
	$(PYTHON) scripts/ci/check_docs.py

code-docs-check:
	$(PYTHON) scripts/ci/check_code_docs.py

source-length-check:
	$(PYTHON) scripts/ci/check_source_lengths.py

dependency-locks:
	bash scripts/ci/compile_dependency_locks.sh requirements

dependency-locks-check:
	$(PYTHON) scripts/ci/check_dependency_locks.py

dependency-locks-fresh:
	set -eu; tmp=$$(mktemp -d); trap 'rm -rf "$$tmp"' EXIT; \
	cp requirements/*.lock "$$tmp"/; \
	bash scripts/ci/compile_dependency_locks.sh "$$tmp"; \
	for name in base signing dev tools; do cmp -s "requirements/$$name.lock" "$$tmp/$$name.lock" || \
		{ echo "requirements/$$name.lock is stale; run 'make dependency-locks'."; exit 1; }; done

build: frontend-check dependency-locks-check
	$(PYTHON) -m build --no-isolation

clean-wheel-smoke: build
	set -eu; tmp=$$(mktemp -d); trap 'rm -rf "$$tmp"' EXIT; \
	$(PYTHON) -m venv "$$tmp/venv"; \
	"$$tmp/venv/bin/python" -m pip install --no-cache-dir --require-hashes --only-binary=:all: -r requirements/base.lock; \
	"$$tmp/venv/bin/python" -m pip install --no-cache-dir --no-deps dist/*.whl; \
	"$$tmp/venv/bin/python" -c 'from chronikwerk.web.app import create_app; print(create_app)'

production-image-smoke:
	bash scripts/ci/production_image_smoke.sh

verify-core: lint brand-check docs-check code-docs-check source-length-check frontend-check pages-demo-check complexity duplication typecheck test build clean-wheel-smoke dependency-locks-check

verify: verify-core production-image-smoke

clean:
	rm -rf build dist .eggs *.egg-info src/*.egg-info .pytest_cache .coverage .coverage_html htmlcov .mypy_cache
	rm -rf .ruff_cache
	find . \( -path './.venv' -o -path './venv' \) -prune -o -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . \( -path './.venv' -o -path './venv' \) -prune -o -type f -name '*.py[co]' -exec rm -f {} + 2>/dev/null || true
