.PHONY: dev lint format typecheck test test-fast test-unit test-int test-nfr test-all test-e2e smoke docs-check docker-smoke qa codacy-local build coverage-test config-check clean-wheel-smoke production-image-smoke verify-core verify ci dev-setup clean

dev:
	docker compose -f docker-compose.dev.yml up --build

dev-setup:
	@echo "Setting up development environment..."
	pip install -e ".[dev]"
	pip install pre-commit
	pre-commit install
	@echo "Creating .env from example if not exists..."
	@if [ ! -f .env ]; then cp .env.example .env && echo "Created .env - please edit with your settings"; fi
	@echo "Development setup complete!"

lint:
	python -m ruff check .

smoke:
	bash scripts/ci/smoke-test.sh

format:
	python -m ruff format .

typecheck:
	python -m mypy src test

test:
	python -m pytest -q

test-fast:
	python -m pytest -q test/static test/unit

test-unit:
	python -m pytest -q test/unit

test-int:
	python -m pytest -q test/integration

test-nfr:
	python -m pytest -q test/nfr

test-all:
	python -m pytest -q

coverage-test:
	python -m pytest -q test/static test/unit test/integration test/nfr \
		--cov=src/zammad_pdf_archiver --cov-report=term-missing --cov-fail-under=85

config-check:
	python -m pytest -q test/unit/test_config_schema_sync.py test/unit/test_env_example_sanity.py

test-e2e:
	python scripts/e2e/docker_api_smoke.py \
		--compose-file infra/e2e/docker-compose.yml \
		--dataset infra/e2e/dataset.json

docs-check:
	@for p in README.md $$(find docs -name '*.md' -type f | sort); do \
		test -f $$p || (echo "Missing docs: $$p" && exit 1); \
	done; \
	echo "docs-check: OK"

docker-smoke:
	docker build -t zammad-pdf-archiver:local .

qa: lint smoke
	python -m ruff check src --select C901
	python -m mypy . --config-file pyproject.toml
	$(MAKE) coverage-test

codacy-local:
	bash scripts/ci/run_local_codacy.sh

build:
	python -m build

clean-wheel-smoke: build
	tmp=$$(mktemp -d); trap 'rm -rf "$$tmp"' EXIT; \
	python -m venv "$$tmp/venv"; \
	"$$tmp/venv/bin/python" -m pip install --no-cache-dir dist/*.whl; \
	"$$tmp/venv/bin/python" -c 'from zammad_pdf_archiver.app.server import create_app; print(create_app)'

production-image-smoke:
	bash scripts/ci/production_image_smoke.sh

verify-core: lint
	python -m ruff check src --select C901
	python -m mypy . --config-file pyproject.toml
	$(MAKE) coverage-test
	$(MAKE) config-check
	$(MAKE) smoke docs-check build clean-wheel-smoke

verify: verify-core production-image-smoke test-e2e

ci: verify

clean:
	rm -rf build dist .eggs *.egg-info .pytest_cache .coverage .coverage_html htmlcov .mypy_cache
	rm -rf .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.py[co]' -delete 2>/dev/null || true
