.PHONY: lint typecheck test check install

lint:
	python -m ruff check .

typecheck:
	python -m mypy src

test:
	python -m pytest -q

check: lint typecheck test

install:
	pip install -e ".[dev]"
