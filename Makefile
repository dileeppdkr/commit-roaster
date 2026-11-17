PYTHON ?= python3
VENV   := .venv
BIN    := $(VENV)/bin

.PHONY: help install lint format typecheck test check clean

help:  ## Show available targets
	@grep -E '^[a-z]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

$(BIN)/python:
	$(PYTHON) -m venv $(VENV)

install: $(BIN)/python  ## Create the venv and install the package with dev tools
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -e ".[dev]"
	$(BIN)/pre-commit install

lint:  ## Lint and check formatting
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .

format:  ## Auto-fix lint issues and format code
	$(BIN)/ruff check --fix .
	$(BIN)/ruff format .

typecheck:  ## Run mypy in strict mode
	$(BIN)/mypy

test:  ## Run tests with coverage
	$(BIN)/pytest

check: lint typecheck test  ## Everything CI runs

clean:  ## Remove caches and build output
	rm -rf build dist .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
