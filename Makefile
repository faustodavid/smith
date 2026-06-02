.PHONY: lint format typecheck test-unit test-contract test-integration test skill-validate check install install-global

lint:
	uv run --extra dev ruff check .

format:
	uv run --extra dev ruff format --check .

typecheck:
	uv run --extra dev mypy src

test-unit:
	uv run --extra dev pytest tests/unit -q

test-contract:
	uv run --extra dev pytest tests/contract -q

test-integration:
	uv run --extra dev pytest tests/integration -q --run-integration

test:
	uv run --extra dev pytest tests/unit tests/contract --cov=src/smith --cov-report=term-missing -q
	uv run --extra dev python scripts/check_targeted_coverage.py

skill-validate:
	uv run --extra dev python scripts/validate_skill_quality.py --mode all

check: lint format typecheck test skill-validate

install:
	uv pip install -e ".[dev]"

install-global:
	uv run --extra dev python scripts/install.py
