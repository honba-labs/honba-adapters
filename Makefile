.PHONY: default dev build test test-live lint fmt typecheck check clean

VENV_PYTHON := $(wildcard ../.venv/bin/python)
ifneq ($(VENV_PYTHON),)
PYTHON ?= $(VENV_PYTHON)
else
PYTHON ?= python3
endif

# Default target is dev
default: dev

dev:
	$(PYTHON) -m pip install -e ".[dev]"

test:
	$(PYTHON) -m pytest tests/unit tests/contract -v

test-live:
	$(PYTHON) -m pytest -m live -v

lint:
	ruff check src/ tests/ scripts/

fmt:
	ruff format src/ tests/ scripts/

typecheck:
	mypy src/honba_adapters

check: lint typecheck test

clean:
	rm -rf build/ dist/ *.egg-info src/*.egg-info src/*/*.egg-info .pytest_cache .ruff_cache .mypy_cache
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
