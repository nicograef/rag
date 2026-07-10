# Roadmap

A professional RAG system is an **information pipeline with clear interfaces**, split into
an **offline ingestion workflow** (building the store) and an **online retrieval workflow**
(answering questions), plus **evaluation & monitoring**. This roadmap builds that system one
increment at a time — each phase lands alone, gets reviewed, and must work before the next
one starts (see rule 5 in [AGENTS.md](../AGENTS.md)).

Status legend: ✅ done · 🔨 in progress · ⬜ open

## Phase 0 — Scaffold ✅

Repo skeleton: uv project, ruff + pytest, Makefile, Postgres 17 + pgvector Compose stack,
devcontainer, CI, handbook conventions (AGENTS.md/CLAUDE.md, plugin adoption).

## Phase 1 — Fetch & convert (corpus acquisition) ⬜

Goal: a config-driven fetcher that turns official law XML into clean Markdown on disk.

- Download official XML from gesetze-im-internet.de per law
  (`https://www.gesetze-im-internet.de/<slug>/xml.zip`). These texts are amtliche Werke
  (§ 5 UrhG) — public domain.
- MVP corpus (config-driven, extensible): `ao_1977` (AO), `ustg_1980` (UStG),
  `kassensichv` (KassenSichV), `gg` (GG).
- Parse the `gii-norm` XML and emit one Markdown file per law under `data/corpus/`,
  preserving structure: law → Buch/Abschnitt → § → Absatz, plus a metadata header
  (law abbreviation, full title, fetch date, source URL).
- No Docling here: the official XML is already structured — parsing it ourselves is
  lossless and teaches real document parsing. Docling enters later for messy PDFs
  (see backlog).
- Verify: re-runnable from clean checkout; deterministic output; unit tests on the
  XML → Markdown converter with a small fixture.

## Phase 2 — Structure-aware chunking ⬜

Goal: split the Markdown corpus into retrieval units without destroying legal structure.

- Chunk by **§ (Paragraph)** as the natural semantic unit; split oversized §§ by Absatz
  with overlap; merge tiny ones.
- Each chunk carries **metadata**: law, § number, heading path (Buch/Abschnitt), source
  URL, fetch date — the basis for later filtering and citations.
- Learn here: why chunk size matters, recursive character splitting as the baseline,
  and why structure-aware beats fixed-size for law texts.
- Later candidates (backlog): semantic chunking, hierarchical parent-child chunks.

## Phase 3 — Embed & load (vector store) ⬜

Goal: embeddings for every chunk, stored and indexed in Postgres.

- Research and pick an **open-license, multilingual, CPU-capable** embedding model from
  Hugging Face via sentence-transformers (candidates to evaluate: multilingual-e5 family,
  jina-embeddings-de, bge-m3 — decide by German retrieval quality, dimension count, and
  CPU latency; verify against current model cards, not memory).
- Schema: `chunks` table (text, metadata columns, `vector` column); **HNSW index**
  (speed/recall trade-off — learn how it works).
- Batch loader: idempotent re-runs (upsert by chunk identity), embedding in batches on CPU.
- Verify: `SELECT ... ORDER BY embedding <=> query` returns plausible §§ for hand-written
  test queries.

## Phase 4 — Online PoC (CLI question answering) ⬜

Goal: close the loop — ask a legal question in the terminal, get a grounded answer.

- Runtime: **Ollama** (localhost HTTP API) serving an open-weight GGUF model that fits
  8-core/16 GB CPU (candidate size: 7–8B quantized; pick during the phase).
- Flow: CLI prompt → embed the question (same model as Phase 3) → top-k vector search →
  **prompt assembly** (system instructions + retrieved chunks with citations + question)
  → Ollama → print answer + sources to the terminal.
- Log every step (query, retrieved chunks with scores, final prompt, answer) so failures
  are debuggable — the seed of observability.
- This is the MVP: offline ingestion + online inference, end to end.

## Phase 5+ — Enhancement backlog (one at a time) ⬜

Ordered roughly by learning value; each item is its own phase with its own plan:

1. **Evaluation first (RAG triad):** a small gold-question set; measure context relevance,
   faithfulness/groundedness, answer relevance — so every later enhancement is measurable.
2. **Hybrid search:** Postgres full-text (BM25-style sparse retrieval) alongside dense
   vectors; fuse with **Reciprocal Rank Fusion (RRF)**.
3. **Query transformation:** HyDE, query decomposition, keyword extraction for hard facts
   (§ numbers, exact terms).
4. **Cross-encoder reranking** of top-k results (open-source model, CPU).
5. **Prompt/context-window management:** lost-in-the-middle ordering, token budgets.
6. **Observability & tracing:** step-level traces (open-source, e.g. Arize Phoenix),
   silent-failure detection.
7. **Guardrails:** input validation (prompt injection), output checks (groundedness, PII).
8. **Docling ingestion path** for messy PDF sources (layout analysis, tables) — a second
   connector proving the pipeline's interfaces.
9. **Drift detection:** embedding drift monitoring after model or corpus updates.
10. **Chat web app:** Go backend + React frontend on top of the proven pipeline.

## Decisions

Recorded from the clarification rounds at project start (2026-07-10):

| Decision            | Choice                                                        |
| ------------------- | ------------------------------------------------------------- |
| Python tooling      | uv (venv, lockfile, `uv run`)                                 |
| Corpus acquisition  | Official gesetze-im-internet.de XML → Markdown via Python     |
| MVP corpus          | AO, UStG, KassenSichV, GG                                     |
| Docs language       | English (German only for corpus + domain terms)               |
| LLM runtime (PoC)   | Ollama                                                        |
| Data in git         | None — `data/` fully gitignored, pipeline re-runnable         |

> **Assumption:** no RAG frameworks (LangChain/LlamaIndex/Haystack) — primitives are built
> by hand from plain libraries, because the goal is learning how RAG works internally.
>
> **Assumption:** the embedding model is chosen in Phase 3 after researching current model
> cards (open license, multilingual, CPU-capable) — not fixed now.
>
> **Open item:** repository license — not chosen yet.
