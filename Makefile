.PHONY: lint typecheck test check install

lint:
	ruff check .

typecheck:
	mypy src

test:
	pytest --cov=src/smith --cov-report=term-missing -q
	python3 scripts/check_targeted_coverage.py

check: lint typecheck test

install:
	python3 -m pip install -e ".[dev]"
