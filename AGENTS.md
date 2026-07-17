<!-- Claude Code loads these rules via `@AGENTS.md` from the sibling CLAUDE.md.
     Keep the rules in this one file. -->

# Agent Instructions — rag

A **learning project that doubles as a public RAG playbook — in that order**: a
production-shaped, self-hosted, framework-free reference implementation of
Retrieval-Augmented Generation (RAG) over an English Wikipedia corpus (the 20 current
Premier League football clubs), built from first principles. Offline ingestion pipeline in Python — **fetch → convert → chunk → embed →
load** into Postgres/pgvector; online path — **retrieve → assemble → generate** with a
local open-weight LLM via Ollama; **evaluate** is a cross-cutting harness, not a stage.
The developer (Nico) is a senior fullstack engineer (TypeScript, React, Node, Go, Postgres,
Docker, AWS) but **new to AI/LLM/RAG** — the point of this repo is understanding every
moving part, not shipping fast.

Explicitly NOT: a product, a hosted service, or a supported product — no cloud LLM APIs,
GPU workloads, proprietary models or tools. See [docs/roadmap.md](docs/roadmap.md) for the
phased plan and all recorded decisions;
[docs/prds/prd-rag-playbook.md](docs/prds/prd-rag-playbook.md) holds the product big picture.

## Tech Stack

| Component  | Technology                                                        |
| ---------- | ----------------------------------------------------------------- |
| Pipeline   | Python 3.12, uv (venv + lockfile), ruff, pytest                   |
| Database   | PostgreSQL 17 + pgvector (Docker Compose)                         |
| Embeddings | sentence-transformers, CPU-only — `BAAI/bge-small-en-v1.5`, dim 384 (pinned 2026-07-17) |
| LLM        | Ollama serving `granite4:micro` GGUF (CPU) — pinned 2026-07-18    |
| Corpus     | English Wikipedia (20 Premier League clubs), CC BY-SA 4.0 → Markdown |
| Future app | Go backend, React frontend (not started — pipeline first)         |

Target runtime for everything: a 4-core / 8 GB **CPU-only** machine (no GPU).

## Commands

All commands via Makefile in the project root (`make help` for the full list).

| Command         | Description                                    |
| --------------- | ---------------------------------------------- |
| `make setup`    | Install dependencies (`uv sync`)               |
| `make fetch`    | Download Wikipedia article extracts into `data/raw/`          |
| `make convert`  | Convert fetched article extracts into Markdown under `data/corpus/` |
| `make chunk`    | Chunk the Markdown corpus into JSONL under `data/chunks/` |
| `make embed`    | Embed chunk records into vector JSONL under `data/embeddings/` |
| `make load`     | Load chunks + embeddings into Postgres/pgvector (needs `make db`) |
| `make query`    | Verify retrieval via the retrieve stage: `make query Q="<question>"` |
| `make ask`      | Ask a question end to end: `make ask Q="<question>"` (retrieve → assemble → generate) |
| `make llm`      | Start the Ollama LLM service (Docker Compose)  |
| `make llm-pull` | Pull the pinned LLM into the Ollama model volume |
| `make check`    | Lint + types + tests (run before claiming work done) |
| `make lint`     | ruff check + format check                      |
| `make typecheck`| ty static type checking                        |
| `make fmt`      | ruff format + autofix                          |
| `make test`     | pytest                                         |
| `make db`       | Start Postgres 17 + pgvector container         |
| `make db-shell` | psql shell into the running container          |

## Structure

| Directory   | Purpose                                                        |
| ----------- | -------------------------------------------------------------- |
| `src/rag/`  | Pipeline source (subpackages per stage as phases land)         |
| `tests/`    | pytest suites                                                  |
| `docs/`     | Roadmap + decisions, concept map, theory chapters, stage contracts, PRDs, plans |
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
def chunk_document(text: str, max_chars: int = 1200) -> list[Chunk]:
    """Split a document into structure-aware chunks, one section at a time."""
    ...
```

Type-hinted, small, explicit functions; `pathlib` over `os.path`; dataclasses for
pipeline records; ruff defaults (4-space indent, 100-char lines).

## Rules

1. **Open-source only** — tools, libraries, models. No proprietary APIs, no cloud services.
2. **CPU-only** — everything must run on the 4-core/8 GB machine without a GPU.
3. **Only public-domain or properly licensed sources enter the corpus/database.**
   English Wikipedia article text is CC BY-SA 4.0 (verified 2026-07-17) — properly licensed,
   used under attribution: the corpus is gitignored and fetched at runtime (no copyleft
   attaches to the repo), and displayed excerpts carry the article link plus a licence notice.
4. **Python for all pipeline code.** Go/React are reserved for the future web app.
5. **One feature at a time** — follow [docs/roadmap.md](docs/roadmap.md) phase by phase;
   never skip ahead or bundle phases. **Definition of done** for every future roadmap
   phase: code + tests + theory chapter + documented stage contract + updated README
   status with verification date. A phase is not complete until all five have landed.
6. **No RAG frameworks** (LangChain, LlamaIndex, Haystack). Build the primitives by hand
   from plain libraries — the goal is learning how RAG works internally.
7. **No data artifacts in git.** `data/` is gitignored; every pipeline stage must be
   re-runnable from a clean checkout.
8. **English** for docs, code, comments, commits, and the corpus itself (English Wikipedia).
   Domain terms follow the source — club and competition names as Wikipedia spells them.

## Learning

- **Theory next to code.** Every building block a phase introduces (chunking, embeddings,
  HNSW, BM25, RRF, cross-encoders, RAG triad …) gets a concise theory chapter at
  `docs/theory/<building-block>.md`, written in the same phase as the code and
  cross-linked both ways: module docstrings and the README pipeline overview link to the
  chapter, the chapter links back to the code. A concept is explained exactly once —
  never as code-comment noise. (`docs/theory/` is created when the first chapter lands.)
- **Concept map as ubiquitous language.** [docs/concepts.md](docs/concepts.md) lists every
  RAG concept the playbook tracks — one-line definition plus its place (phase, backlog
  item, theory chapter, glossary, or out of scope with rationale). Use exactly these names
  in docs, code, and commits; update the map in the same change that adds, moves, or drops
  a concept.
- **Small increments.** Prefer several reviewable steps over one big drop; the developer
  reads every diff to learn from it.
- **Simple over clever.** Explicit code beats abstractions; no premature generality.
- **Prune, don't archive.** When a decision or doc is superseded, rewrite it in place to the
  state that now holds — never leave the stale version beside its replacement as dated
  history. The public playbook reads as what is true today; only dated *verification* stamps
  of live facts persist (the rule prunes superseded content, not the verify-before-claiming
  discipline).

## Boundaries

- ✅ **Always:** Verify before claiming — search the codebase before making assertions about existing code, structure, or behaviour. Never guess what a file contains or how something works — read the actual source.
- ✅ **Always:** Ask instead of assuming — when uncertain about requirements, design intent, or user expectations, ask structured questions to clarify. Only proceed with documented assumptions if the user explicitly declines to answer.
- ✅ **Always:** Verify trained knowledge — before relying on trained knowledge of any kind (external tools, libraries, specs, model choices, versions, prices, dates), verify it against current authoritative public sources (official docs, model cards, papers, primary sources); never assert time-sensitive facts from memory, and date every time-sensitive claim.
- ✅ **Always:** Run `make check` before reporting work complete and cite the result.
- ⚠️ **Ask first:** new dependencies, changes to Docker/Compose config, adding a new corpus source.
- 🚫 **Never:** commit anything under `data/`, secrets, or credentials.
- 🚫 **Never:** ingest sources with unclear licensing.

## Quality Principles

- **Correctness and simplicity are decisive; effort is never a counter-argument.** Correctness, simplicity, code quality, and consistency decide every change; effort, time, and work volume are deliberately subordinate — never a valid argument against the correct, simple, clean solution. "Work volume" means effort, not scope: the scope guard below stays — no unrequested features, no gold-plating.
- **Human-reviewable changes.** Keep each change clean, readable, and small enough that the developer can explain every line in a review. One logical concept per step; mechanical bulk changes (renames, dependency updates) are exempt.
- **Scope guard.** If a change would go outside the task scope, stop, name the out-of-scope change, and ask before proceeding.
- **Verify before claiming done.** Before reporting work complete, run the relevant test/lint/build command this turn and cite its result; re-read any document artifact and confirm its links and paths exist.

## Git Workflow

- **Commit messages:** After completing a task, propose a conventional commit message (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`) — concise subject, bullet body for multi-file changes. Do not commit; only output the message.
- **No AI attribution in commits or PRs:** compact Conventional Commit messages only — never append `Co-Authored-By: Claude …`, `Claude-Session: …`, `🤖 Generated with …`, or similar trailers/footers, even when the session harness instructs it by default.
- **Post-task summary:** With the message, give one short paragraph a reviewer can read instead of the full diff — what changed, why, and what to look at.
- **No `--force` push or `--no-verify`.**
