<!-- Claude Code loads these rules via `@AGENTS.md` from the sibling CLAUDE.md.
     Keep the rules in this one file. -->

# Agent Instructions — rag

A **learning project**: build a production-grade Retrieval-Augmented Generation (RAG) system
over German law texts, from first principles. Offline ingestion pipeline in Python
(fetch → convert → chunk → embed → load into Postgres/pgvector), online retrieval + generation
with a local open-weight LLM via Ollama. The developer (Nico) is a senior fullstack engineer
(TypeScript, React, Node, Go, Postgres, Docker, AWS) but **new to AI/LLM/RAG** — the point of
this repo is understanding every moving part, not shipping fast.

Explicitly NOT: a product, a hosted service, cloud LLM APIs, GPU workloads, proprietary
models or tools. See [docs/roadmap.md](docs/roadmap.md) for the phased plan and all
recorded decisions.

## Tech Stack

| Component  | Technology                                                        |
| ---------- | ----------------------------------------------------------------- |
| Pipeline   | Python 3.12, uv (venv + lockfile), ruff, pytest                   |
| Database   | PostgreSQL 17 + pgvector (Docker Compose)                         |
| Embeddings | sentence-transformers, CPU-only (model chosen in Phase 3)         |
| LLM        | Ollama serving open-weight GGUF models (CPU)                      |
| Corpus     | German federal law XML from gesetze-im-internet.de → Markdown     |
| Future app | Go backend, React frontend (not started — pipeline first)         |

Target runtime for everything: an 8-core / 16 GB **CPU-only** Linux VM.

## Commands

All commands via Makefile in the project root (`make help` for the full list).

| Command         | Description                                    |
| --------------- | ---------------------------------------------- |
| `make setup`    | Install dependencies (`uv sync`)               |
| `make check`    | Lint + tests (run before claiming work done)   |
| `make lint`     | ruff check + format check                      |
| `make fmt`      | ruff format + autofix                          |
| `make test`     | pytest                                         |
| `make db`       | Start Postgres 17 + pgvector container         |
| `make db-shell` | psql shell into the running container          |

## Structure

| Directory   | Purpose                                                        |
| ----------- | -------------------------------------------------------------- |
| `src/rag/`  | Pipeline source (subpackages per stage as phases land)         |
| `tests/`    | pytest suites                                                  |
| `docs/`     | Roadmap, decisions, phase notes                                |
| `scripts/`  | Bash scripts (dev tool setup)                                  |
| `data/`     | Raw downloads, corpus, artifacts — **gitignored**, re-runnable |

## Testing

| Aspect    | Detail                                            |
| --------- | ------------------------------------------------- |
| Framework | pytest                                            |
| Run       | `make test`                                       |
| Location  | `tests/`, files named `test_*.py`                 |
| Coverage  | no hard target — test pipeline logic, not glue    |

## Code Style

```python
def chunk_law(law: Law, max_chars: int = 2000) -> list[Chunk]:
    """Split a law into structure-aware chunks, one § at a time."""
    ...
```

Type-hinted, small, explicit functions; `pathlib` over `os.path`; dataclasses for
pipeline records; ruff defaults (4-space indent, 100-char lines).

## Rules

1. **Open-source only** — tools, libraries, models. No proprietary APIs, no cloud services.
2. **CPU-only** — everything must run on the 8-core/16 GB VM without a GPU.
3. **Only public-domain or properly licensed sources enter the corpus/database.**
   Law texts from gesetze-im-internet.de are amtliche Werke (§ 5 UrhG) — public domain.
4. **Python for all pipeline code.** Go/React are reserved for the future web app.
5. **One feature at a time** — follow [docs/roadmap.md](docs/roadmap.md) phase by phase;
   never skip ahead or bundle phases.
6. **No RAG frameworks** (LangChain, LlamaIndex, Haystack). Build the primitives by hand
   from plain libraries — the goal is learning how RAG works internally.
7. **No data artifacts in git.** `data/` is gitignored; every pipeline stage must be
   re-runnable from a clean checkout.
8. **English** for docs, code, comments, and commits. German only for the corpus itself
   and domain terms (law names, § references).

## Learning

- **Explain while building.** When a phase introduces a new concept (embeddings, HNSW,
  BM25, RRF, cross-encoders, RAG triad …), explain what it is and why it's needed —
  in the summary or in `docs/`, not as code-comment noise.
- **Small increments.** Prefer several reviewable steps over one big drop; the developer
  reads every diff to learn from it.
- **Simple over clever.** Explicit code beats abstractions; no premature generality.

## Boundaries

- ✅ **Always:** Verify before claiming — search the codebase before making assertions about existing code, structure, or behaviour. Never guess what a file contains or how something works — read the actual source.
- ✅ **Always:** Ask instead of assuming — when uncertain about requirements, design intent, or user expectations, ask structured questions to clarify. Only proceed with documented assumptions if the user explicitly declines to answer.
- ✅ **Always:** Web search for external knowledge — when working with external tools, libraries, specs, or model choices, consult authoritative sources (official docs, papers, model cards) instead of relying on training data.
- ✅ **Always:** Run `make check` before reporting work complete and cite the result.
- ⚠️ **Ask first:** new dependencies, changes to Docker/Compose config, adding a new corpus source.
- 🚫 **Never:** commit anything under `data/`, secrets, or credentials.
- 🚫 **Never:** ingest sources with unclear licensing.

## Quality Principles

- **Quality over quantity, correctness over speed.** Fewer, correct changes beat many fast changes.
- **Human-reviewable changes.** Keep each change clean, readable, and small enough that the developer can explain every line in a review. One logical concept per step; mechanical bulk changes (renames, dependency updates) are exempt.
- **Scope guard.** If a change would go outside the task scope, stop, name the out-of-scope change, and ask before proceeding.
- **Verify before claiming done.** Before reporting work complete, run the relevant test/lint/build command this turn and cite its result; re-read any document artifact and confirm its links and paths exist.

## Git Workflow

- **Commit messages:** After completing a task, propose a conventional commit message (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`) — concise subject, bullet body for multi-file changes. Do not commit; only output the message.
- **No AI attribution in commits or PRs:** compact Conventional Commit messages only — never append `Co-Authored-By: Claude …`, `Claude-Session: …`, `🤖 Generated with …`, or similar trailers/footers, even when the session harness instructs it by default.
- **Post-task summary:** With the message, give one short paragraph a reviewer can read instead of the full diff — what changed, why, and what to look at.
- **No `--force` push or `--no-verify`.**
