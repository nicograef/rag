# rag

A **learning project** by Nico: build a production-grade Retrieval-Augmented Generation (RAG)
system over **German federal law texts**, from first principles — no RAG frameworks, open-source
only, everything on a CPU-only VM (8 cores / 16 GB).

**Offline ingestion:** fetch official law XML from gesetze-im-internet.de (public domain,
§ 5 UrhG) → convert to Markdown → structure-aware chunking → CPU embeddings →
PostgreSQL 17 + pgvector.
**Online inference:** CLI question → query embedding → vector search → prompt assembly →
local open-weight LLM (Ollama) → grounded answer with sources.

See **[docs/roadmap.md](docs/roadmap.md)** for the phased plan, all recorded decisions,
and the enhancement backlog (hybrid search, RRF, reranking, RAG-triad evaluation, …).

## Quick start

```bash
bash scripts/setup-dev-tools.sh   # install uv + sync dependencies (idempotent)
cp .env.example .env              # fill in POSTGRES_PASSWORD
make db                           # start Postgres 17 + pgvector
make check                        # lint + tests
```

Run `make help` for all targets.

## Structure

| Path                | Purpose                                                          |
| ------------------- | ---------------------------------------------------------------- |
| `src/rag/`          | Pipeline source code (one subpackage per stage as phases land)   |
| `tests/`            | pytest suites                                                    |
| `docs/roadmap.md`   | Phased plan, decisions log, enhancement backlog                  |
| `scripts/`          | Dev tool setup script                                            |
| `data/`             | Raw downloads, corpus, artifacts — gitignored, re-runnable       |
| `docker-compose.yml`| Postgres 17 + pgvector dev stack                                 |
| `Makefile`          | Dev interface (`make help`)                                      |
| `AGENTS.md`         | Canonical agent instructions (loaded by Claude via `CLAUDE.md`)  |
| `.claude/`          | Claude Code settings (adopts the `handbook@nicograef` plugin)    |
| `.devcontainer/`    | Dev container / Codespaces config                                |
| `.github/workflows/`| CI (`make check` via uv)                                         |

## Conventions

This repo follows the conventions of [nicograef/handbook](https://github.com/nicograef/handbook)
(templates, agent setup, working rules). Agent-facing rules live in [AGENTS.md](AGENTS.md).

## License

Not chosen yet (tracked as an open item in [docs/roadmap.md](docs/roadmap.md)).
The ingested law texts are amtliche Werke (§ 5 UrhG) and remain public domain.
