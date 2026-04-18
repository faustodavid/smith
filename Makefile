.PHONY: lint typecheck test-unit test-contract test-integration test skill-validate check install install-global

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

skill-validate:
	python3 scripts/validate_skill_quality.py --mode all

check: lint typecheck test skill-validate

install:
	python3 -m pip install -e ".[dev]"

install-global:
	python3 scripts/install.py
