SHELL := /bin/bash

default: help

help: ## Wyświetla tę pomoc
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Synchronizuje środowisko wraz z zależnościami deweloperskimi
	uv sync --group dev

upgrade-requirements: ## Podnosi wersje zależności i zapisuje blokadę
	uv lock --upgrade

build-requirements: ## Generuje pliki requirements
	uv export --frozen --no-hashes --no-annotate --no-dev --no-emit-project -o requirements/base.txt
	uv export --frozen --no-hashes --no-annotate --only-group dev -o requirements/development.txt

hooks: ## Instaluje hooki gita obsługiwane przez pre-commit
	uv run pre-commit install --allow-missing-config
	uv run pre-commit install --hook-type commit-msg --allow-missing-config

lint: ## Uruchamia wszystkie hooki pre-commit na całym drzewie
	uv run pre-commit run --all-files

test: ## Uruchamia testy wraz z pokryciem
	uv run pytest

coverage-report: ## Uruchamia testy i otwiera raport pokrycia w HTML
	-uv run pytest --cov-report html
	open .tmp/coverage/index.html

serve: ## Uruchamia serwer MCP z kopii roboczej przez stdio
	uvx --from . ksef-dev10x-guru

build: ## Buduje sdist i wheel do katalogu dist/
	uv build

clean: ## Usuwa artefakty budowania, pamięci podręczne i wyniki pokrycia
	rm -rf dist .pytest_cache .ruff_cache .coverage htmlcov .tmp

.PHONY: help install upgrade-requirements build-requirements hooks lint test coverage-report serve build clean
