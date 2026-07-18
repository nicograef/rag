# rag dev interface. `make check` before claiming work done.

# Load .env (DB credentials) if present — needed for db-shell.
-include .env
export

# ── Setup ──

.PHONY: setup

setup:         ## Install dependencies into .venv (uv sync)
	uv sync

# ── Pipeline (offline ingestion) ──

.PHONY: fetch convert chunk embed load

fetch:         ## Download Wikipedia article extracts into data/raw/
	uv run python -m rag.fetch

convert:       ## Convert fetched article extracts into Markdown under data/corpus/
	uv run python -m rag.convert

chunk:         ## Chunk corpus Markdown into JSONL records under data/chunks/
	uv run python -m rag.chunk

embed:         ## Embed chunk records into vector JSONL under data/embeddings/
	uv run python -m rag.embed

load:          ## Load chunks + embeddings into Postgres/pgvector (needs `make db`)
	uv run python -m rag.load

# ── Online path (question answering) ──

.PHONY: query ask

query:         ## Verify retrieval: make query Q="<question>" (top-k similarity search)
	uv run python -m rag.retrieve "$(Q)"

ask:           ## Ask a question: make ask Q="<question>" (retrieve → assemble → generate)
	uv run python -m rag.ask "$(Q)"

# ── Web app (learner UI) ──

.PHONY: serve

serve:         ## Start the learner web app (FastAPI + UI) on API_HOST:API_PORT
	uv run python -m rag.api

# ── Code Quality ──

.PHONY: check lint fmt typecheck test

check: lint typecheck test  ## Run all checks (lint + types + tests)

lint:          ## ruff: lint + verify formatting
	uv run ruff check .
	uv run ruff format --check .

typecheck:     ## ty: static type checking
	uv run ty check

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

# ── LLM runtime (docker-compose.yml) ──

.PHONY: llm llm-pull

llm:           ## Start the Ollama LLM service (Docker Compose)
	docker compose up -d ollama

llm-pull:      ## Pull the pinned LLM into the Ollama model volume (needs `make llm`)
	docker compose exec ollama ollama pull $$(uv run python -c "from rag.generate import MODEL_TAG; print(MODEL_TAG)")

# ── Cleanup / reset ──

.PHONY: clean reset

clean:         ## Remove the regenerable data/ artifacts (raw, corpus, chunks, embeddings)
	rm -rf data/raw data/corpus data/chunks data/embeddings

reset: clean   ## Clean slate: also drop the chunks table + remove the pinned LLM (needs `make db`, `make llm`)
	docker compose exec -T postgres psql -U $(POSTGRES_USER) -d $(POSTGRES_DB) -c "DROP TABLE IF EXISTS chunks;"
	-docker compose exec -T ollama ollama rm $$(uv run python -c "from rag.generate import MODEL_TAG; print(MODEL_TAG)")

# ── Utilities ──

.PHONY: help

help:          ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
