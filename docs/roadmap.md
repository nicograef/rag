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

## Phase 1 — Fetch & convert (corpus acquisition) ✅

Stages: **fetch**, **convert**

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

## Phase 2 — Structure-aware chunking ✅

Stage: **chunk**

Goal: split the Markdown corpus into retrieval units without destroying legal structure.

- Chunk by **§ (Paragraph)** as the natural semantic unit; split oversized §§ by Absatz
  with overlap; merge tiny ones.
- Each chunk carries **metadata**: law, § number, heading path (Buch/Abschnitt), source
  URL, fetch date — the basis for later filtering and citations.
- Learn here: why chunk size matters, recursive character splitting as the baseline,
  and why structure-aware beats fixed-size for law texts.
- Later candidates: see backlog item 6 (advanced chunking strategies).

## Phase 3 — Embed & load (vector store) ⬜

Stages: **embed**, **load**

Goal: embeddings for every chunk, stored and indexed in Postgres.

- Research and pick an **open-license, multilingual, CPU-capable** embedding model from
  Hugging Face via sentence-transformers (candidates to evaluate: multilingual-e5 family,
  jina-embeddings-de, bge-m3 — decide by German retrieval quality, dimension count, and
  CPU latency; verify against current model cards, not memory).
- Schema: `chunks` table (text, metadata columns, `vector` column); **HNSW index**
  (speed/recall trade-off — learn how it works). The theory chapter frames HNSW within
  approximate nearest neighbor search and contrasts IVF and vector quantization (both
  theory-only — see the [concept map](concepts.md)).
- The dated model decision pins **embedding normalization** and the pgvector distance
  operator together with the model choice (verified against the model card).
- Batch loader: idempotent re-runs (upsert by chunk identity), embedding in batches on CPU.
- Verify: `SELECT ... ORDER BY embedding <=> query` returns plausible §§ for hand-written
  test queries.

## Phase 4 — Online PoC (CLI question answering) ⬜

Stages: **retrieve**, **assemble**, **generate**

Goal: close the loop — ask a legal question in the terminal, get a grounded answer.

- Runtime: **Ollama** (localhost HTTP API) serving an open-weight GGUF model that fits
  8-core/16 GB CPU (candidate size: 7–8B quantized; pick during the phase). Runs as a
  second Compose service next to Postgres, models in a named volume (see Docker decision
  below).
- Flow: CLI prompt → embed the question (same model as Phase 3) → top-k vector search →
  **prompt assembly** (system instructions + retrieved chunks with citations + question)
  → Ollama → print answer + sources to the terminal.
- The system instructions carry **grounding and abstention directives** ("answer only from
  the provided §§; say so if they don't contain the answer") — the prompt-level half of
  hallucination prevention.
- The phase's generation theory chapter explains CPU inference (prefill vs decode, KV
  caching, prompt-prefix reuse — why a stable prompt layout is cheap), GGUF weight
  quantization, and the cost/benefit of chain-of-thought for a small model.
- Log every step (query, retrieved chunks with scores, final prompt, answer) so failures
  are debuggable — the seed of observability.
- This is the MVP: offline ingestion + online inference, end to end.

## Phase 5+ — Enhancement backlog (one at a time) ⬜

Ordered roughly by learning value; each item is its own phase with its own plan. A backlog
phase may add or split a stage by amending the PRD's stage taxonomy in the same change; the
evaluation item lands the cross-cutting **evaluate** harness, not a pipeline stage. The
[concept map](concepts.md) indexes every concept these items cover — and the concepts
deliberately left out.

1. **Evaluation first (RAG triad):** a small gold-question set, each question labeled with
   its expected §§; measure context relevance, faithfulness/groundedness, and answer
   relevance with a local open-weight LLM judge via Ollama (LLM-as-a-judge,
   reference-free — built by hand, no RAGAS), plus deterministic rank metrics
   against the labeled §§ (Recall@K, Precision@K, MRR, NDCG) — so every later enhancement
   is measurable.
2. **Hybrid search:** Postgres full-text (BM25-style sparse retrieval) alongside dense
   vectors; fuse with **Reciprocal Rank Fusion (RRF)**. Theory contrasts lexical sparse
   retrieval with learned sparse embeddings (SPLADE) and RRF with score
   normalization / weighted fusion.
3. **Metadata filtering:** scoped retrieval — filter vector/hybrid search by chunk metadata
   (law, § number, heading path) via SQL predicates combined with pgvector; learn pre- vs
   post-filtering and how filters interact with HNSW recall.
4. **Query transformation:** query rewriting, query expansion (synonyms and domain
   abbreviations), multi-query retrieval (variants fused via RRF), HyDE, query
   decomposition — including sequential multi-hop retrieval — keyword extraction for hard
   facts (§ numbers, exact terms), and a lightweight query router (explicit § citations go
   to exact metadata lookup). Theory covers step-back prompting as the contrast.
5. **Cross-encoder reranking** of top-k results (open-source model, CPU). Theory maps the
   bi-encoder → late-interaction (ColBERT) → cross-encoder spectrum.
6. **Advanced chunking strategies:** semantic chunking, hierarchical parent-child chunks
   (Absatz-level children retrieved, §-level parents assembled), contextual chunk
   enrichment (prepend heading-path/law context; optionally local-LLM chunk summaries),
   and late chunking if the Phase 3 embedding model exposes token embeddings — each
   compared against the Phase 2 baseline via the evaluate harness.
7. **Prompt/context-window management:** lost-in-the-middle ordering, token budgets
   counted with the served model's own tokenizer. Theory covers the long-context-vs-RAG
   debate.
8. **Observability & tracing:** step-level traces (open-source, e.g. Arize Phoenix),
   silent-failure detection.
9. **Guardrails:** input validation (prompt injection), retrieval rails (score thresholds
   drop weak chunks before assembly), output checks (a CoVe-style groundedness self-check
   with the local LLM, and PII). Theory covers the full rails taxonomy, including dialog
   rails and ingestion-time PII handling (this corpus contains none).
10. **Iterative / agentic retrieval loop:** a hand-built plan → retrieve → reflect loop
    where the local LLM decides follow-up searches and when it can answer, with CRAG-style
    self-correction (corpus-internal — no web-search fallback). Theory places Self-RAG on
    the same spectrum.
11. **Graph-augmented retrieval (GraphRAG):** extract the explicit §-to-§ and law-to-law
    citation links into a lightweight graph (plain Postgres tables, no graph database) and
    expand retrieval hits with their graph neighbors. Theory contrasts full
    LLM-entity-extraction GraphRAG and its CPU cost.
12. **Docling ingestion path** for messy PDF sources — a second connector proving the
    pipeline's interfaces: layout analysis, reading order, tables, page-level chunking with
    page-number citations, and a dated scoping decision on figures/images. Theory covers
    classic OCR + layout analysis vs end-to-end document-intelligence VLMs.
13. **Incremental ingestion:** detect changed laws (XML builddate or content hash) and
    reprocess only those through convert → chunk → embed → load, skipping unchanged chunks
    at the upsert boundary.
14. **Drift detection:** embedding drift monitoring after model or corpus updates —
    amended laws are this corpus's real drift trigger; the gold-set metrics from item 1
    are the signal.
15. **Chat web app:** Go backend + React frontend on top of the proven pipeline.

## Decisions

Recorded as they are made, starting with the clarification rounds at project start (2026-07-10).
Convention: a decision that binds future work or makes a deliberate exception gets a dated entry
with its reasoning — context, choice, weighed alternatives, consequences — like the Docker block
below; trivial choices stay one-line table rows.

| Decision            | Choice                                                        |
| ------------------- | ------------------------------------------------------------- |
| Python tooling      | uv (venv, lockfile, `uv run`)                                 |
| Corpus acquisition  | Official gesetze-im-internet.de XML → Markdown via Python     |
| MVP corpus          | AO, UStG, KassenSichV, GG                                     |
| Docs language       | English (German only for corpus + domain terms)               |
| LLM runtime (PoC)   | Ollama                                                        |
| Data in git         | None — `data/` fully gitignored, pipeline re-runnable         |
| Repository license  | MIT                                                           |
| Docker usage        | Stateful infrastructure only (see below)                      |
| Interim runtime     | GitHub Codespace (16 vCPU / 32 GB) until a VPS is available; the 8-core/16 GB CPU-only VM stays the design floor — nothing may require more |

> **Assumption:** no RAG frameworks (LangChain/LlamaIndex/Haystack) — primitives are built
> by hand from plain libraries, because the goal is learning how RAG works internally.
>
**Docker usage** (decided 2026-07-10): containers earn their keep for versioned, stateful,
long-running infrastructure and for deployment — not for code under active development.

- **Postgres + pgvector:** Docker Compose (pinned version, one-command reset).
- **Ollama:** second Compose service when Phase 4 lands, models in a named volume —
  the whole serving side becomes one `docker compose up`. No GPU passthrough concerns
  on a CPU-only target.
- **Python pipeline:** native via uv, never containerized for development — fast iteration,
  plain debugging, shared Hugging Face model cache; uv's lockfile + pinned Python already
  provide the reproducibility.
- **Future Go/React app:** dockerized at deployment time, following the handbook's
  Compose/nginx templates.

> **Assumption:** the embedding model is chosen in Phase 3 after researching current model
> cards (open license, multilingual, CPU-capable) — not fixed now.

**Playbook repositioning** (decided 2026-07-11): the repository is a learning project that
doubles as a public **RAG playbook** — a production-shaped, self-hosted, framework-free
reference implementation a learner can clone and run — in that order.

- **Context:** the docs addressed one person — no audience statement, no landed-vs-planned
  status, no support policy — although the existing constraints (open-source only, CPU-only,
  no frameworks, real public-domain corpus) already match what a framework-free reference
  needs. The product big picture (audience, promises, pillars, stage taxonomy) is recorded
  in the [PRD](prds/prd-rag-playbook.md).
- **Choice:** reposition through a documentation-only rework — README as the learner front
  door with a status table, stage-annotated roadmap, repositioned agent instructions.
  Pipeline code untouched; phases continue per this roadmap. Alternatives weighed: keeping
  the private-learning framing (rejected — the repo is public and the tutorial/framework gap
  is real); docs tooling or a rendered site (rejected — plain Markdown suffices, see the
  PRD's "Out of Scope").
- **Consequences:** every future phase is bound by the definition of done stated in full in
  [AGENTS.md](../AGENTS.md) (rule 5); the README status table gates every
  runnable-experience claim; time-sensitive claims carry the date they were last verified;
  the playbook never claims "state of the art".

**Concept coverage map** (decided 2026-07-11): the playbook tracks the RAG concept space
explicitly instead of implicitly.

- **Context:** a comprehensive external list of RAG concepts (ingestion → chunking →
  vectorization → retrieval → query transformation → generation → guardrails → evaluation →
  advanced architectures) was audited against the repo. Most core concepts already had a
  home, but several techniques (metadata filtering, advanced chunking, agentic/corrective
  retrieval, graph-augmented retrieval, incremental ingestion, rank-based retrieval
  metrics, …) had none, and nothing recorded what is deliberately out of scope.
- **Choice:** a concept map at [concepts.md](concepts.md) — the project's ubiquitous
  language: every tracked concept with a one-line definition and its place (core phase,
  backlog item, theory chapter, glossary, or out of scope with rationale). The backlog grew
  from 10 to 15 items and was renumbered (the chat web app stays last); existing items
  gained explicit technique lists. Alternatives weighed: writing the theory chapters now
  (rejected — theory stays next to the code that lands it, per AGENTS.md); folding
  everything into backlog prose (rejected — definitions and out-of-scope rationale would
  drown the plan).
- **Consequences:** the map is the index, the roadmap wording is the commitment, and theory
  chapters remain the single place a concept is explained. When a phase or backlog change
  adds, moves, or drops a concept, the map is updated in the same change. Out-of-scope
  entries (CDC, chunk-level permissions, multimodal embeddings, human-feedback loops, …)
  are recorded with rationale so they are decisions, not omissions.

**Corpus licensing — gesetze-im-internet.de** (verified live 2026-07-12): only the
normative law texts enter the corpus; footnotes and editorial apparatus stay out.

- **Context:** Phase 1's fetch stage ingests XML from gesetze-im-internet.de, and rule 3
  in [AGENTS.md](../AGENTS.md) demands that only public-domain or properly licensed
  sources enter the corpus. The licensing facts had so far been asserted from memory and
  a mirror; this entry pins them from the live site.
- **Facts (all verified live 2026-07-12):** the site's start page states: „Die
  Rechtsnormen in deutscher Sprache stehen in allen angebotenen Formaten zur freien
  Nutzung und Weiterverwendung zur Verfügung." (<https://www.gesetze-im-internet.de/> —
  note the wording differs slightly from the often-cited „in ihrer deutschsprachigen
  Fassung" variant). The statutory basis is § 5 Abs. 1 UrhG: „Gesetze, Verordnungen,
  amtliche Erlasse und Bekanntmachungen sowie Entscheidungen und amtlich verfaßte
  Leitsätze zu Entscheidungen genießen keinen urheberrechtlichen Schutz."
  (<https://www.gesetze-im-internet.de/urhg/__5.html>). The Hinweise page (section 6,
  „Download und Weiterverwertung der Normen") documents the XML offering and its DTD
  (`/dtd/1.01/gii-norm.dtd`) and adds no further licence terms. The download URLs of all
  four MVP laws respond and contain the expected GiI-Norm XML (`make fetch` run live the
  same day).
- **Choice:** the corpus may include the **normative texts** — norm headings and body
  text of the German-language laws. **Footnotes (`<fussnoten>`) and editorial apparatus
  (`<standangabe>`, status notes) are excluded**: they are editorial additions of the
  Dokumentationsstelle, covered neither clearly by § 5 Abs. 1 UrhG („Gesetze,
  Verordnungen …") nor unambiguously by the free-reuse statement („Die Rechtsnormen …"),
  so the conservative reading wins.
- **Consequences:** convert emits normative text only; the README's licensing wording
  (amtliche Werke, § 5 UrhG, public domain) is confirmed unchanged; the
  [fetch contract](stages/fetch.md) links here as its licensing basis. If a future
  source's terms are less clear, it does not enter the corpus (rule: never ingest
  sources with unclear licensing).
