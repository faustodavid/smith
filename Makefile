.PHONY: lint format typecheck test-unit test-contract test-integration test skill-validate check install install-global

lint:
	uv run --locked --all-extras ruff check .

format:
	uv run --locked --all-extras ruff format --check .

typecheck:
	uv run --locked --all-extras mypy src

test-unit:
	uv run --locked --all-extras pytest tests/unit -q

test-contract:
	uv run --locked --all-extras pytest tests/contract -q

test-integration:
	uv run --locked --all-extras pytest tests/integration -q --run-integration

test:
	uv run --locked --all-extras pytest tests/unit tests/contract --cov=src/smith --cov-report=term-missing -q
	uv run --locked --all-extras python scripts/check_targeted_coverage.py

skill-validate:
	uv run --locked --all-extras python scripts/validate_skill_quality.py --mode all

check: lint format typecheck test skill-validate

install:
	uv sync --locked --all-extras

install-global:
	uv run --locked --all-extras python scripts/install.py
