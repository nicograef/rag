# rag dev interface. `make check` before claiming work done.

# Load .env (DB credentials) if present — needed for db-shell.
-include .env
export

# ── Setup ──

.PHONY: setup

setup:         ## Install dependencies into .venv (uv sync)
	uv sync

# ── Code Quality ──

.PHONY: check lint fmt test

check: lint test  ## Run all checks (lint + tests)

lint:          ## ruff: lint + verify formatting
	uv run ruff check .
	uv run ruff format --check .

fmt:           ## ruff: format + autofix lint findings
	uv run ruff format .
	uv run ruff check --fix .

test:          ## Run pytest
	uv run pytest

# ── Database (docker-compose.yml) ──

.PHONY: db db-shell down

db:            ## Start Postgres 17 + pgvector container
	docker compose up -d postgres

db-shell:      ## Open psql shell in running PostgreSQL container
	docker compose exec postgres psql -U $(POSTGRES_USER) -d $(POSTGRES_DB)

down:          ## Stop all containers
	docker compose down

# ── Utilities ──

.PHONY: help

help:          ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
