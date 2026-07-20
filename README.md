# RAG Playbook

A production-shaped, self-hosted, framework-free reference implementation of
Retrieval-Augmented Generation (RAG) over **English Wikipedia** (the 20 current Premier
League football clubs), for developers who want to learn RAG hands-on — by reading and
running a real system, stage by stage, with the theory next to the code. A **learning project** by [Nico](https://github.com/nicograef) that
doubles as a public playbook — in that order.

What it is **not**: a product, a hosted service, or supported software — and it never claims
to be state of the art. It teaches durable building blocks and records every concrete choice
(models, parameters, trade-offs) as a dated decision you can re-evaluate.

The constraints are the features:

- **Open-source only** — every tool, library, and model; no cloud accounts, no paid APIs.
- **CPU-only** — designed for a 4-core / 8 GB VM (the design floor, justified by the model
  footprints in the roadmap's [hardware-floor decision](docs/roadmap.md#decisions)); nothing
  may require a GPU or more.
- **No RAG frameworks** — no LangChain, LlamaIndex, or Haystack; every primitive is built by
  hand from plain libraries, so reading the code teaches RAG, not a framework.
- **Real, properly-licensed corpus** — English Wikipedia (Premier League clubs), CC BY-SA 4.0
  with attribution, with genuine section structure, not a toy blog post.
- **Re-runnable from a clean checkout** — no data artifacts in git; every stage rebuilds its output.

## Status

The playbook grows chapter by chapter and is explicitly incomplete. This table is what you
can run today — no claim in this file promises more than it shows. Phases and decisions live
in the [roadmap](docs/roadmap.md).

| Phase                                | Stages                                  | Status |
| ------------------------------------ | --------------------------------------- | ------ |
| 0 — Scaffold                         | — (repo, tooling, database)             | ✅     |
| 1 — Fetch & convert                  | fetch, convert                          | ✅     |
| 2 — Structure-aware chunking         | chunk                                   | ✅     |
| 3 — Embed & load                     | embed, load                             | ✅     |
| 4 — Online PoC                       | retrieve, assemble, generate            | ✅     |
| 5+ — Enhancement backlog             | — (incl. the cross-cutting evaluate harness) | ⬜  |

> **Whole loop verified 2026-07-18:** the pipeline runs end to end on the **English Wikipedia**
> corpus (the 20 current Premier League clubs, `clubs.toml`) — **fetch**, **convert**, **chunk**,
> **embed** (`BAAI/bge-small-en-v1.5`, dim 384), **load** (`vector(384)`), and the online path
> (**assemble**, **generate** on `granite4:micro`). A full `make fetch` → `make load` run indexed
> 1,333 chunks, and `make ask "Which stadium does Arsenal play at?"` answers *"Arsenal plays at
> the Emirates Stadium"* with a numbered `Sources:` block and a CC BY-SA notice — re-verified from
> a clean checkout, CPU-only, on an 8-core/5.7 GB machine (a tighter RAM budget than the
> 4-core/8 GB design floor).

## Quick start

What runs today (Phases 0–4): the dev setup, the checks, the database, the whole
offline ingestion pipeline, the online question-answering loop, and a minimal
learner web app (`make serve`) that puts it in a browser.

```bash
bash scripts/setup-dev-tools.sh   # install uv + sync Python dependencies (idempotent)
cp .env.example .env              # then fill in POSTGRES_PASSWORD
make db                           # start Postgres 17 + pgvector (Docker Compose)
make check                        # lint + types + tests
make fetch                        # download the 20 club articles into data/raw/
make convert                      # convert them into Markdown under data/corpus/
make chunk                        # slice the corpus into JSONL chunks under data/chunks/
make embed                        # embed the chunks (first run downloads the model, ~130 MB)
make load                         # fill the chunks table + HNSW index in Postgres
make query Q="Which stadium does Arsenal play at?"   # retrieval-only check (the retrieve stage)
make llm                          # start Ollama (Docker Compose)
make llm-pull                     # pull the pinned LLM (~2.1 GB) into the model volume
make ask Q="Which stadium does Arsenal play at?"   # grounded answer with citations (CPU: ~1 min)
make serve                        # open the web UI at http://127.0.0.1:8000 (question box + retrieval view)
```

Run `make help` for all targets. Requirements: Linux/macOS with `curl`, Docker with the
Compose plugin, and Python 3.12 (uv installs one if missing).

**First-run costs** (deps and model measured 2026-07-14, image 2026-07-11):
~1.2 GB of Python dependencies (PyTorch dominates even as the CPU-only build — torch is
pinned to the PyTorch CPU wheel index in `pyproject.toml`, which avoids the ~4 GB of
CUDA libraries the default PyPI wheels bundle; the dev tools alone are ~65 MB), a
one-time ~160 MB (compressed) pull of the
`pgvector/pgvector:pg17` image, the Wikipedia extracts for the 20-club corpus (fetched at
runtime, gitignored; a few MB of text), and a one-time **~130 MB download of the pinned
embedding model**
(`BAAI/bge-small-en-v1.5`) into `~/.cache/huggingface/` on the first `make embed` (measured
2026-07-17; details in the [model decision](docs/roadmap.md#decisions)). The online path adds (measured
2026-07-18): a one-time pull of the pinned `ollama/ollama:0.32.1` image (≈ 8 GB on
disk) and a one-time **~2.1 GB download of the pinned LLM** (`granite4:micro`,
4-bit GGUF) into the named Docker volume on the first `make llm-pull`.

## Pipeline overview

The data flow is the table of contents. **Offline ingestion** builds the store; each stage
is a single-responsibility module whose input and output are inspectable artifacts — files
on disk or database state:

| Stage       | Responsibility                  | Input → output artifact                          |
| ----------- | ------------------------------- | ------------------------------------------------ |
| **[fetch](docs/stages/fetch.md)**     | acquire the source      | source → raw files (Wikipedia article extracts) |
| **[convert](docs/stages/convert.md)** | make the source workable | raw files → clean Markdown corpus    |
| **[chunk](docs/stages/chunk.md)**     | slice into retrieval units | corpus → chunk records with metadata  |
| **[embed](docs/stages/embed.md)**     | turn text into vectors  | chunk records → vectors (JSONL per article) |
| **[load](docs/stages/load.md)**       | own the database (incl. schema and indexes) | chunk records + vectors → database |

The **online path** answers a question in one process — `make ask` wraps
`python -m rag.ask`, which composes the three stages and logs every intermediate (query,
retrieved chunks with scores, assembled prompt size, generation stats) to stderr while
the answer streams to stdout:

| Stage        | Responsibility                              | Entry point + step logs        |
| ------------ | ------------------------------------------- | ------------------------------ |
| **[retrieve](docs/stages/retrieve.md)** | question → ranked chunks         | logs query + chunks with scores |
| **[assemble](docs/stages/assemble.md)** | question + ranked chunks → prompt | logs the assembled prompt      |
| **[generate](docs/stages/generate.md)** | prompt → grounded answer with citations | logs the final answer    |

**evaluate** is a cross-cutting harness, not a pipeline stage: a checked-in gold-question
set plus a pinned configuration in, a dated metrics report out.

Each stage's precise contract (`docs/stages/<stage>.md`) and its theory chapter
(`docs/theory/<building-block>.md`) land with the phase that implements it — the five
offline contracts are linked above. The Phase 1 chapter,
[corpus & parsing](docs/theory/corpus-and-parsing.md), explains why corpus choice,
licensing, and lossless parsing are RAG decisions; the Phase 2 chapter,
[chunking](docs/theory/chunking.md), explains why chunk size matters and why
structure-aware chunking beats fixed-size splitting on structured text; the Phase 3 chapters,
[embeddings](docs/theory/embeddings.md) and
[vector indexes](docs/theory/vector-indexes.md), explain how meaning becomes geometry and
how HNSW searches it fast; the Phase 4 chapter,
[LLM generation](docs/theory/llm-generation.md), explains how a quantized model turns a
prompt into streamed tokens on a CPU — prefill vs decode, KV caching, and the prompt
techniques that keep answers grounded. See the status table above for what exists today.
The [concept map](docs/concepts.md) indexes every RAG concept the playbook tracks — a
one-line definition each, plus where it lives: a phase, a backlog item, a theory chapter,
or a recorded reason it is deliberately out of scope.

## The corpus — and swapping it

English Wikipedia — the 20 current Premier League clubs (`clubs.toml`), fetched as plain-text
extracts from the MediaWiki API — is a deliberate feature: each article carries a real heading
hierarchy (article → `== section ==` → `=== subsection ===`) that makes structure-aware
chunking and citations a genuine lesson instead of a toy exercise. Be honest about the scale:
two or three heading levels of encyclopedic prose is a **lighter** version of that lesson than
deeply nested reference works (legal codes, technical manuals) would teach — real structure,
not rich structure. Wikipedia text is **CC BY-SA 4.0 with attribution**, not public domain, so
it clears rule 3 on the "properly licensed" clause; the full argument, and why that obligation
stays cheap here, is the [corpus & parsing](docs/theory/corpus-and-parsing.md) chapter.

**Swapping in your own corpus** — the honest blast radius: reimplement **fetch** and
**convert** for your source, and adapt the chunker's structural logic and citation fields to
your documents' structure. The chunk-record contract uses corpus-neutral field names
(`source_title`, `section`, `section_path`, `citation`), and each stage contract states exactly
which fields downstream stages require — so the boundary is explicit, not discovered. The
first two contracts ([fetch](docs/stages/fetch.md), [convert](docs/stages/convert.md)) are
landed; the rest arrive with their phases (status table above).

## Project status & support

This is a learning project first; the maintainer sets the pacing and scope. Each landed
phase is complete on its own — code, tests, theory chapter, runnable stage — but the
playbook as a whole is a work in progress. There is **no support, no SLA, and no
contribution program**. Between phases, external dependencies (the Wikipedia API, model
hosting) can rot; each phase re-verifies the quick start and records the date.

## Structure

| Path                 | Purpose                                                          |
| -------------------- | ---------------------------------------------------------------- |
| `src/rag/`           | Pipeline source code (one subpackage per stage as phases land)   |
| `tests/`             | pytest suites                                                    |
| `docs/roadmap.md`    | Phased plan, dated decisions log, enhancement backlog            |
| `docs/concepts.md`   | Concept map: every tracked RAG concept, defined once, with its place |
| `docs/stages/`       | Stage contracts: input/output artifacts, invocation, guarantees  |
| `docs/theory/`       | Theory chapters: one building block each, landed with its phase  |
| `docs/prds/`         | PRDs: the product big picture                                    |
| `docs/plans/`        | Implementation plans for reviewed changes                        |
| `scripts/`           | Dev tool setup script                                            |
| `data/`              | Raw downloads (`data/raw/`), corpus (`data/corpus/`), chunks (`data/chunks/`), embeddings (`data/embeddings/`) — gitignored, re-runnable |
| `clubs.toml`         | Corpus config: one entry per Wikipedia article to fetch          |
| `docker-compose.yml` | Postgres 17 + pgvector dev stack                                 |
| `Makefile`           | Dev interface (`make help`)                                      |
| `AGENTS.md`          | Contributor/agent instructions (loaded by Claude via `CLAUDE.md`)|
| `.claude/`           | Claude Code settings (adopts the `handbook@nicograef` plugin)    |
| `.devcontainer/`     | Dev container / Codespaces config                                |
| `.github/workflows/` | CI (`make check` via uv)                                         |

Conventions follow [nicograef/handbook](https://github.com/nicograef/handbook); everything
contributor- and agent-facing lives in [AGENTS.md](AGENTS.md).

## License

MIT — see [LICENSE](LICENSE).
The ingested Wikipedia article text is **CC BY-SA 4.0** (not public domain; verified
2026-07-17). The corpus is gitignored and fetched at runtime — never redistributed in git —
so no copyleft attaches to this repo; displayed excerpts carry attribution (the article link)
and a licence notice, shown by `make ask`.
