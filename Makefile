.PHONY: dev lint format typecheck test test-fast test-unit test-int test-nfr test-all test-e2e smoke docs-check docker-smoke qa build verify ci dev-setup clean

define pytest_guarded
	@set -e; python -m pytest -q $(1) || (test $$? -eq 5 && echo 'No tests collected (bootstrap stage)' && exit 0)
endef

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
	$(call pytest_guarded,)

test-fast:
	$(call pytest_guarded,test/static test/unit)

test-unit:
	$(call pytest_guarded,test/unit)

test-int:
	$(call pytest_guarded,test/integration)

test-nfr:
	$(call pytest_guarded,test/nfr)

test-all:
	$(call pytest_guarded,)

test-e2e:
	python scripts/e2e/docker_api_smoke.py

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
	python -m pytest -q test/static test/unit test/integration test/nfr

build:
	python -m build

verify: qa build

ci: lint typecheck test

clean:
	rm -rf build dist .eggs *.egg-info .pytest_cache .coverage .coverage_html htmlcov .mypy_cache
	rm -rf .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.py[co]' -delete 2>/dev/null || true
