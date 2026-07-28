# Centralize repeatable development, quality, packaging, and release checks.
PYTHON ?= python
NPM ?= npm
NPX ?= npx

.PHONY: dev lint format typecheck complexity duplication frontend-install frontend-typecheck frontend-build frontend-check frontend-update test test-fast test-unit test-int test-contracts test-all test-e2e browser-setup test-browser pdf-ua-check smoke brand-check docs-screenshots docs-check code-docs-check docker-smoke qa build coverage-test config-check clean-wheel-smoke production-image-smoke verify-core verify ci dev-setup clean

dev:
	docker compose -f docker-compose.dev.yml up --build

dev-setup:
	@echo "Setting up development environment..."
	$(PYTHON) -m pip install -e ".[dev]"
	$(PYTHON) -m pip install pre-commit
	$(PYTHON) -m pre_commit install
	$(MAKE) frontend-install
	@echo "Creating .env from example if not exists..."
	@if [ ! -f .env ]; then cp .env.example .env && echo "Created .env - please edit with your settings"; fi
	@echo "Development setup complete!"

lint:
	$(PYTHON) -m ruff check .

smoke:
	bash scripts/ci/smoke-test.sh

brand-check:
	$(PYTHON) scripts/ci/check_brand_identity.py

format:
	$(PYTHON) -m ruff format .

typecheck:
	$(PYTHON) -m mypy src tests

complexity:
	$(PYTHON) -m lizard -w -C 10 -L 80 \
		-x "src/chronikwerk/static/admin/admin.js" \
		src/chronikwerk scripts frontend

duplication:
	$(NPM) run duplication:production
	$(NPM) run duplication:all

frontend-install:
	$(NPM) ci --ignore-scripts

frontend-typecheck:
	$(NPM) run typecheck

frontend-build:
	$(NPM) run build:admin

frontend-check: frontend-typecheck frontend-build
	@cmp -s build/typescript/admin.js src/chronikwerk/static/admin/admin.js || \
		(echo "Generated admin.js is stale; run 'make frontend-update'." && exit 1)
	@cmp -s build/admin/admin.css src/chronikwerk/static/admin/admin.css || \
		(echo "Generated admin.css is stale; run 'make frontend-update'." && exit 1)

frontend-update: frontend-build
	cp build/typescript/admin.js src/chronikwerk/static/admin/admin.js
	cp build/admin/admin.css src/chronikwerk/static/admin/admin.css

test:
	$(PYTHON) -m pytest -q

test-fast:
	$(PYTHON) -m pytest -q tests/static tests/unit

test-unit:
	$(PYTHON) -m pytest -q tests/unit

test-int:
	$(PYTHON) -m pytest -q tests/integration

test-contracts:
	$(PYTHON) -m pytest -q tests/contracts

test-all:
	$(PYTHON) -m pytest -q

coverage-test:
	$(PYTHON) -m pytest -q tests/static tests/unit tests/integration tests/contracts \
		--cov=src/chronikwerk --cov-report=term-missing --cov-fail-under=85

config-check:
	$(PYTHON) -m pytest -q tests/unit/test_config_schema_sync.py tests/unit/test_env_example_sanity.py

test-e2e:
	$(PYTHON) scripts/e2e/docker_api_smoke.py \
		--compose-file infra/e2e/docker-compose.yml \
		--dataset infra/e2e/dataset.json

browser-setup: frontend-install
	$(NPX) playwright install chromium firefox webkit

test-browser:
	$(NPM) run test:browser

pdf-ua-check:
	@test -n "$(PDF_FILES)" || (echo "Set PDF_FILES to signed and unsigned fixture paths" && exit 2)
	bash scripts/ci/verify_pdf_ua.sh $(PDF_FILES)

docs-check:
	$(PYTHON) scripts/ci/check_docs.py

code-docs-check:
	$(PYTHON) scripts/ci/check_code_docs.py

docs-screenshots:
	$(PYTHON) scripts/docs/render_admin_screenshots.py $(if $(CAPTURED_AT),--captured-at $(CAPTURED_AT))

docker-smoke:
	docker build -t chronikwerk:local .

qa: lint smoke brand-check docs-check code-docs-check frontend-check complexity duplication
	$(PYTHON) -m mypy . --config-file pyproject.toml
	$(MAKE) coverage-test

build: frontend-check
	$(PYTHON) -m build

clean-wheel-smoke: build
	tmp=$$(mktemp -d); trap 'rm -rf "$$tmp"' EXIT; \
	$(PYTHON) -m venv "$$tmp/venv"; \
	"$$tmp/venv/bin/python" -m pip install --no-cache-dir dist/*.whl; \
	"$$tmp/venv/bin/python" -c 'from chronikwerk.app.server import create_app; print(create_app)'

production-image-smoke:
	bash scripts/ci/production_image_smoke.sh

verify-core: lint brand-check docs-check code-docs-check frontend-check complexity duplication
	$(PYTHON) -m mypy . --config-file pyproject.toml
	$(MAKE) coverage-test
	$(MAKE) config-check
	$(MAKE) smoke docs-check build clean-wheel-smoke

verify: verify-core production-image-smoke test-e2e

ci: verify

clean:
	rm -rf build dist .eggs *.egg-info src/*.egg-info .pytest_cache .coverage .coverage_html htmlcov .mypy_cache
	rm -rf .ruff_cache
	find . \( -path './.venv' -o -path './venv' \) -prune -o -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . \( -path './.venv' -o -path './venv' \) -prune -o -type f -name '*.py[co]' -exec rm -f {} + 2>/dev/null || true
