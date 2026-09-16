# agenttape -- development tasks.
#
# There is no build step. `make` is a convenience for the handful of commands
# you would otherwise look up in CONTRIBUTING.md.

PYTHON ?= python
SOURCES := src tests examples

.DEFAULT_GOAL := help
.PHONY: help install test test-random examples lint format types check build clean dist

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Install the package in editable mode with dev extras
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -e ".[dev]"

test: ## Run the test suite
	$(PYTHON) -m pytest tests -q

test-random: ## Run the suite with a randomised hash seed
	PYTHONHASHSEED=random $(PYTHON) -m pytest tests -q

examples: ## Run every example script
	@for script in examples/0*.py; do \
		echo "== $$script"; \
		PYTHONPATH=src $(PYTHON) "$$script" > /dev/null || exit 1; \
	done
	@echo "all examples ran"

lint: ## Check formatting and lint
	ruff format --check .
	ruff check .

format: ## Apply formatting and safe lint fixes
	ruff format .
	ruff check --fix .

types: ## Type-check the package
	mypy src/agenttape

check: lint types test ## Everything CI runs

build: clean ## Build sdist and wheel
	$(PYTHON) -m build
	$(PYTHON) -m twine check dist/*

dist: build ## Build and list the distribution contents
	@ls -la dist/

clean: ## Remove build and test artefacts
	rm -rf build dist *.egg-info src/*.egg-info
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage coverage.xml
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
