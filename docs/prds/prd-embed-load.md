# PRD: Embed & Load (Roadmap Phase 3)

Feature-level PRD for [roadmap Phase 3](../roadmap.md) — the **embed** and **load** stages
of the offline ingestion pipeline. The product big picture (audience, pillars, stage
taxonomy) lives in the [playbook PRD](prd-rag-playbook.md); this document only decides how
Phase 3 lands within it.

## Problem Statement

The pipeline currently stops at chunk records on disk: the corpus is fetched, converted,
and chunked, but nothing is searchable. A learner who has run the first three stages has
retrieval-ready records and no way to retrieve anything — no vectors, no vector store, no
similarity query to try. The two concepts this phase exists to teach (dense embeddings and
approximate nearest neighbor search with HNSW) have no code or theory chapter yet, and the
embedding model — the decision that pins vector dimensions, normalization, and the distance
operator for everything downstream — is still open. Phase 4 (question answering) cannot
start until a filled, indexed vector store exists.

## Solution

Land the two remaining offline stages exactly as the playbook's stage taxonomy defines
them, keeping the data flow inspectable between stages:

- **embed** turns each law's chunk records into vectors using an open-license,
  multilingual, CPU-capable sentence-transformers model, and writes one embeddings
  artifact per law — a JSON-lines file a learner can inspect with standard tools, carrying
  each chunk's identity, its vector, and the model name and dimension that produced it.
- **load** reads chunk records and embeddings, owns the database schema (a chunks table
  with text, metadata, and vector columns, plus the HNSW index), and fills
  Postgres/pgvector idempotently: re-running replaces each law's rows so the store always
  mirrors the current artifacts.

The embedding model is chosen at the start of implementation through documented research
against current model cards, and recorded as a dated decision in the roadmap — pinning
model, embedding normalization, and pgvector distance operator together. A minimal
developer query command embeds a hand-written question with the same model and prints the
top-k chunks with citations and distances, making the phase's success criterion —
"plausible §§ for hand-written test queries" — reproducible without front-running Phase
4's retrieve stage. Two theory chapters land with the code: dense embeddings, and vector
indexes (exact kNN vs ANN, HNSW, with IVF and quantization as theory-only contrasts).

## User Stories

1. As a learner, I want an embed command that turns the chunked corpus into vectors on my
   CPU-only machine, so that I can build a vector store without cloud APIs or a GPU.
2. As a learner, I want the embeddings artifact to be a plain-text file between the embed
   and load stages, so that I can inspect real vectors and their metadata before they
   disappear into the database.
3. As a learner, I want a load command that creates the schema, fills the store, and
   builds the HNSW index in one idempotent step, so that I can rebuild the database from
   artifacts at any time without manual SQL.
4. As a learner, I want a documented query command that embeds my question and shows the
   nearest chunks with citations and distances, so that I can verify retrieval quality
   hands-on before the full question-answering loop exists.
5. As a learner, I want the embedding-model choice recorded as a dated decision with its
   selection criteria, so that I can judge whether it is still a good choice and swap in
   my own.
6. As a learner, I want theory chapters on embeddings and vector indexes cross-linked with
   the stage code, so that I understand why vectors and HNSW work before reading how they
   are used.
7. As the maintainer, I want re-running embed and load to be safe and idempotent, so that
   the pipeline stays re-runnable from a clean checkout after corpus or chunking changes.
8. As the maintainer, I want the default test suite to run without the model download or a
   database, so that checks stay fast while real-model and real-database behavior remain
   covered by opt-in integration tests.

## Implementation Decisions

**Stage boundaries and artifact**

- embed and load are two separate stage modules with their own commands, matching the
  playbook taxonomy; no combined shortcut command.
- The artifact between them is one JSON-lines file per law in the gitignored data area:
  one record per chunk with the chunk id, the vector, and the producing model's name and
  dimension. The artifact is self-describing; load validates that model and dimension are
  consistent before writing anything.
- Both stages follow the established CLI conventions of the earlier stages: per-law
  failure isolation (one law failing is reported and does not stop the others), non-zero
  exit on any failure, and a clear hint when the required input directory is missing.

**Model selection (first implementation slice)**

- The model decision is made at the start of implementation, not in this PRD: research
  current model cards (candidates per roadmap: multilingual-e5 family,
  jina-embeddings-de, bge-m3) and select by open license, German retrieval quality,
  dimension count, CPU latency, and memory fit within the 16 GB floor.
- The dated decision lands in the roadmap (single source of truth) and pins three things
  together: the model, whether vectors are normalized, and the pgvector distance operator.
  The memory footprint observed at decision time is recorded against the playbook's 16 GB
  risk item.
- The same model will embed questions in Phase 4's retrieve stage — the deliberate
  coupling the playbook PRD names; the decision entry restates it.

**Modules**

- The embed stage hides the embedding machinery behind a minimal embedder interface
  (texts in, vectors out) with the sentence-transformers model as its one real
  implementation, encoding in batches on CPU. Tests inject a trivial fake through the same
  interface.
- The load stage owns everything database-shaped: schema creation (chunks table with text,
  metadata columns, and a fixed-dimension vector column), the HNSW index, and idempotent
  writes keyed by chunk id with per-law replace semantics — rows whose chunk ids no longer
  exist in the artifacts are removed, so the store never drifts from the pipeline output.
- Database connection settings come from the existing environment file that the Compose
  stack already uses; no new configuration mechanism.
- The developer query command is a thin verification tool on top of the two modules: embed
  one question, run one similarity query, print the top-k results. It is explicitly not
  the retrieve stage and is expected to be superseded in Phase 4.

**Dependencies (approved)**

- sentence-transformers (brings PyTorch, CPU build), psycopg, and the pgvector Python
  adapter. The first model download is cached locally and its size documented as part of
  the README's first-run cost.

**Definition of done (per repo rule)**

- Code + tests + two theory chapters (embeddings; vector indexes covering ANN/HNSW with
  IVF and vector quantization as contrasts) + stage contracts for embed and load + README
  pipeline status updated with the verification date. The concept map already places all
  Phase 3 concepts; it changes only if a concept moves.

## Testing Decisions

- A good test exercises a stage's external contract — given this input artifact, that
  output artifact or database state — never internal helpers. This extends the pattern set
  by the existing stage test suites.
- embed with the fake embedder is a deterministic pure transform: golden-file tests assert
  exact artifact output for checked-in chunk fixtures (prior art: the chunk stage's
  golden-file tests). Real-model behavior is covered by an opt-in integration test
  asserting similarity within tolerance against small checked-in reference vectors — never
  bitwise equality, matching the playbook's determinism promises.
- load is tested at two levels: default tests cover record assembly and validation without
  a database; an opt-in integration test runs against the Compose Postgres and asserts
  schema existence, row counts, dimension enforcement, idempotent re-runs, and stale-row
  removal. Integration tests skip cleanly when the model or database is unavailable, so
  the default check command stays self-contained.
- The developer query command is verified manually with hand-written German legal
  questions; results are recorded as a dated spot-check in the load stage contract (prior
  art: the spot-check sections of the convert and chunk contracts). No gold-question
  metrics — that is the backlog's evaluation harness.

## Out of Scope

- The online path: retrieve, assemble, generate, and anything Ollama (Phase 4). The dev
  query command is a verification tool, not the retrieve stage.
- Hybrid/sparse search, metadata-filtered retrieval, query transformation, reranking
  (backlog items 2–5).
- The evaluation harness and gold-question set (backlog item 1).
- Incremental ingestion (backlog item 13): a corpus change re-embeds and reloads
  everything; acceptable at MVP corpus scale.
- IVF and vector quantization in practice — theory-chapter contrasts only.
- Model benchmarking infrastructure or multi-model support: one pinned model, selected by
  documented criteria.

## Further Notes

- Artifact size at MVP scale is a few tens of megabytes across all four laws — inspectable
  JSON-lines is deliberately chosen over compact binary formats (inspectability pillar);
  revisit only if the corpus grows far beyond the MVP.
- Embedding a few thousand chunks on an 8-core CPU is expected to take minutes, not hours;
  the embed stage prints per-law progress so a learner sees it working. Actual timings are
  recorded in the stage contract when the phase lands.
- If the chosen model exposes token embeddings and a long context, late chunking (backlog
  item 6) becomes a live option — worth one line in the model decision entry either way.
