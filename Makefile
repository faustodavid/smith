.PHONY: lint typecheck test-unit test-contract test-integration test check install

lint:
	ruff check .

typecheck:
	mypy src

test-unit:
	pytest tests/unit -q

test-contract:
	pytest tests/contract -q

test-integration:
	pytest tests/integration -q --run-integration

test:
	pytest tests/unit tests/contract --cov=src/smith --cov-report=term-missing -q
	python3 scripts/check_targeted_coverage.py

check: lint typecheck test

install:
	python3 -m pip install -e ".[dev]"
