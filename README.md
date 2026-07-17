# RAG Playbook

A production-shaped, self-hosted, framework-free reference implementation of
Retrieval-Augmented Generation (RAG) over **German federal law**, for developers who want to
learn RAG hands-on — by reading and running a real system, stage by stage, with the theory
next to the code. A **learning project** by [Nico](https://github.com/nicograef) that
doubles as a public playbook — in that order.

What it is **not**: a product, a hosted service, or supported software — and it never claims
to be state of the art. It teaches durable building blocks and records every concrete choice
(models, parameters, trade-offs) as a dated decision you can re-evaluate.

The constraints are the features:

- **Open-source only** — every tool, library, and model; no cloud accounts, no paid APIs.
- **CPU-only** — designed for an 8-core / 16 GB VM; nothing may require a GPU or more.
- **No RAG frameworks** — no LangChain, LlamaIndex, or Haystack; every primitive is built by
  hand from plain libraries, so reading the code teaches RAG, not a framework.
- **Real public-domain corpus** — German federal law with genuine structure, not a toy blog post.
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
| 4 — Online PoC                       | retrieve, assemble, generate            | ⬜     |
| 5+ — Enhancement backlog             | — (incl. the cross-cutting evaluate harness) | ⬜  |

Quick start last verified from a clean checkout: **2026-07-14** — every step below as
written: dev setup, `make db` including the image pull, `make check`, the full pipeline
on the live corpus (`make fetch` through `make load`, 1,225 chunks — the first
`make embed` including the model download), and a `make query` retrieval spot-check
(recorded in the [load contract](docs/stages/load.md#verification)).

## Quick start

What runs today (Phases 0–3): the dev setup, the checks, the database, and the whole
offline ingestion pipeline.

```bash
bash scripts/setup-dev-tools.sh   # install uv + sync Python dependencies (idempotent)
cp .env.example .env              # then fill in POSTGRES_PASSWORD
make db                           # start Postgres 17 + pgvector (Docker Compose)
make check                        # lint + types + tests
make fetch                        # download the law XML (~0.4 MB) into data/raw/
make convert                      # convert it into Markdown under data/corpus/
make chunk                        # slice the corpus into JSONL chunks under data/chunks/
make embed                        # embed the chunks (first run downloads the model, ~4.6 GB)
make load                         # fill the chunks table + HNSW index in Postgres
make query Q="Wie müssen elektronische Kassen gesichert werden?"   # verify retrieval
```

Run `make help` for all targets. Requirements: Linux/macOS with `curl`, Docker with the
Compose plugin, and Python 3.12 (uv installs one if missing).

**First-run costs** (deps and model measured 2026-07-14, image/corpus 2026-07-11/12):
~1.2 GB of Python dependencies (PyTorch dominates even as the CPU-only build — torch is
pinned to the PyTorch CPU wheel index in `pyproject.toml`, which avoids the ~4 GB of
CUDA libraries the default PyPI wheels bundle; the dev tools alone are ~65 MB), a
one-time ~160 MB (compressed) pull of the
`pgvector/pgvector:pg17` image, ~0.4 MB zipped (~1.8 MB extracted) of law XML for the
four-law MVP corpus, and a one-time **~4.6 GB download of the pinned embedding model**
(`BAAI/bge-m3`) into `~/.cache/huggingface/` on the first `make embed` — the 2.27 GB
weights land twice, as `pytorch_model.bin` plus the safetensors conversion (details in
the [model decision](docs/roadmap.md#decisions)). **Phase 4 adds more** — open-weight
LLM weights via Ollama. Those costs are documented here when their phases land; until
the status table above marks a phase ✅, its downloads and commands don't exist yet.

## Pipeline overview

The data flow is the table of contents. **Offline ingestion** builds the store; each stage
is a single-responsibility module whose input and output are inspectable artifacts — files
on disk or database state:

| Stage       | Responsibility                  | Input → output artifact                          |
| ----------- | ------------------------------- | ------------------------------------------------ |
| **[fetch](docs/stages/fetch.md)**     | acquire the source      | source → raw files (official law XML) |
| **[convert](docs/stages/convert.md)** | make the source workable | raw files → clean Markdown corpus    |
| **[chunk](docs/stages/chunk.md)**     | slice into retrieval units | corpus → chunk records with metadata  |
| **[embed](docs/stages/embed.md)**     | turn text into vectors  | chunk records → vectors (JSONL per law) |
| **[load](docs/stages/load.md)**       | own the database (incl. schema and indexes) | chunk records + vectors → database |

The **online path** answers a question in one process. Its contract is a documented entry
point plus step-level logs of every intermediate (query, retrieved chunks with scores,
assembled prompt, answer):

| Stage        | Responsibility                              | Entry point + step logs        |
| ------------ | ------------------------------------------- | ------------------------------ |
| **retrieve** | question → ranked chunks                    | logs query + chunks with scores |
| **assemble** | question + ranked chunks → prompt           | logs the assembled prompt      |
| **generate** | prompt → grounded answer with citations     | logs the final answer          |

**evaluate** is a cross-cutting harness, not a pipeline stage: a checked-in gold-question
set plus a pinned configuration in, a dated metrics report out.

Each stage's precise contract (`docs/stages/<stage>.md`) and its theory chapter
(`docs/theory/<building-block>.md`) land with the phase that implements it — the five
offline contracts are linked above. The Phase 1 chapter,
[corpus & parsing](docs/theory/corpus-and-parsing.md), explains why corpus choice,
licensing, and lossless parsing are RAG decisions; the Phase 2 chapter,
[chunking](docs/theory/chunking.md), explains why chunk size matters and why
structure-aware chunking beats fixed-size splitting for law texts; the Phase 3 chapters,
[embeddings](docs/theory/embeddings.md) and
[vector indexes](docs/theory/vector-indexes.md), explain how meaning becomes geometry and
how HNSW searches it fast. See the status table above for what exists today.
The [concept map](docs/concepts.md) indexes every RAG concept the playbook tracks — a
one-line definition each, plus where it lives: a phase, a backlog item, a theory chapter,
or a recorded reason it is deliberately out of scope.

## The corpus — and swapping it

German federal law (XML from gesetze-im-internet.de) is a deliberate feature: real structure
(law → Buch/Abschnitt → § → Absatz) makes structure-aware chunking and citations a genuine
lesson instead of a toy exercise, and the norm texts are amtliche Werke (§ 5 UrhG) — public
domain. The full argument is the [corpus & parsing](docs/theory/corpus-and-parsing.md)
chapter. The corpus is German-language; that limitation is acknowledged and offset by the
swap path.

**Swapping in your own corpus** — the honest blast radius: reimplement **fetch** and
**convert** for your source, and adapt the chunker's structural logic and citation fields to
your documents' structure. The chunk-record contract uses corpus-neutral field names, and
each stage contract states exactly which fields downstream stages require — so the boundary
is explicit, not discovered. The first two contracts ([fetch](docs/stages/fetch.md),
[convert](docs/stages/convert.md)) are landed; the rest arrive with their phases (status
table above).

## Project status & support

This is a learning project first; the maintainer sets the pacing and scope. Each landed
phase is complete on its own — code, tests, theory chapter, runnable stage — but the
playbook as a whole is a work in progress. There is **no support, no SLA, and no
contribution program**. Between phases, external dependencies (the law-XML endpoint, model
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
| `docs/prds/`         | PRDs: product big picture (playbook) and per-phase feature PRDs  |
| `docs/plans/`        | Implementation plans for reviewed changes                        |
| `scripts/`           | Dev tool setup script                                            |
| `data/`              | Raw downloads (`data/raw/`), corpus (`data/corpus/`), chunks (`data/chunks/`), embeddings (`data/embeddings/`) — gitignored, re-runnable |
| `laws.toml`          | Corpus config: one entry per law to fetch                        |
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
The ingested law texts are amtliche Werke (§ 5 UrhG) and remain public domain.
