SHELL := /bin/bash

default: help

help: ## Show help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Sync the virtualenv with dev dependencies
	uv sync --group dev

upgrade-requirements: ## Upgrade and lock requirements
	uv lock --upgrade

build-requirements: ## Build the requirements files
	uv export --frozen --no-hashes --no-annotate --no-dev --no-emit-project -o requirements/base.txt
	uv export --frozen --no-hashes --no-annotate --only-group dev -o requirements/development.txt

hooks: ## Install the pre-commit git hooks
	uv run pre-commit install
	uv run pre-commit install --hook-type commit-msg

lint: ## Run every pre-commit hook over the whole tree
	uv run pre-commit run --all-files

test: ## Run the test suite with coverage
	uv run pytest

coverage-report: ## Run tests and open the HTML coverage report
	-uv run pytest --cov-report html
	open .tmp/coverage/index.html

serve: ## Run the MCP server from the working copy over stdio
	uvx --from . ksef-mcp

build: ## Build the sdist and wheel into dist/
	uv build

clean: ## Remove build artefacts, caches and coverage output
	rm -rf dist .pytest_cache .ruff_cache .coverage htmlcov .tmp

.PHONY: help install upgrade-requirements build-requirements hooks lint test coverage-report serve build clean
