# Plan: Roadmap Phase 3 — Embed & load (vector store)

> Source PRD: [../prds/prd-embed-load.md](../prds/prd-embed-load.md) ·
> Roadmap phase: [../roadmap.md](../roadmap.md) "Phase 3 — Embed & load (vector store)"

## Goal

Land the **embed** and **load** stages: turn every chunk record under `data/chunks/` into a
vector with an open-license, multilingual, CPU-capable sentence-transformers model, write one
inspectable embeddings artifact per law under `data/embeddings/`, and fill Postgres/pgvector
idempotently — a `chunks` table with text, metadata, and vector columns plus an HNSW index.
A minimal developer query command makes the phase's success criterion ("plausible §§ for
hand-written test queries") reproducible without front-running Phase 4's retrieve stage.

Per [AGENTS.md](../../AGENTS.md) rule 5, the phase is done only with all five deliverables:
code + tests + theory chapters (`docs/theory/embeddings.md`, `docs/theory/vector-indexes.md`)
+ stage contracts (`docs/stages/embed.md`, `docs/stages/load.md`) + README status with a
verification date. The work is broken into five **slices** ("Phase" always means the roadmap
phase).

## Architectural decisions

Durable decisions that apply across all slices:

- **Stage = subpackage, invoked via `python -m`** (same shape as fetch/convert/chunk):
  `src/rag/embed/` and `src/rag/load/`, run as `uv run python -m rag.embed` /
  `uv run python -m rag.load`, wrapped by Makefile targets `make embed` and `make load`.
  Options: embed takes `--chunks-dir data/chunks` and `--embeddings-dir data/embeddings`;
  load takes the same two directories as input. Per-law failure isolation, non-zero exit on
  any failure, and a missing-input hint ("run `make chunk` first" / "run `make embed` first")
  follow the established CLI conventions.

- **Embeddings artifact**: `data/embeddings/<slug>.jsonl` — one JSON record per chunk, one
  file per law (mirrors `data/chunks/<slug>.jsonl`). Record fields, in order:

  | Field       | Type            | Meaning                                              |
  | ----------- | --------------- | ---------------------------------------------------- |
  | `id`        | string          | The chunk `id` this vector belongs to                |
  | `model`     | string          | Hugging Face model id that produced the vector       |
  | `dim`       | integer         | Vector dimension count                               |
  | `embedding` | list of floats  | The vector itself                                    |

  Every record is self-describing (`model` + `dim` on each line); load validates that all
  records agree on one model and dimension before writing anything. The artifact is plain
  JSON-lines — inspectable with standard tools, per the playbook's inspectability pillar.

- **Embedder interface**: the embed stage hides the model behind a minimal `Embedder`
  interface — texts in, vectors out, plus the model id and dimension it produces. The one
  real implementation wraps sentence-transformers with batch encoding on CPU; tests inject a
  trivial deterministic fake through the same interface. sentence-transformers is imported
  lazily inside the real implementation so the default test suite never loads torch.

- **Model configuration**: the chosen model id, normalization flag, and pgvector distance
  operator are pinned as constants in the embed/load code, mirroring the dated roadmap
  decision from Slice 1 (single source of truth for the reasoning; the code states the
  values). No model CLI flag — one pinned model, per the PRD.

- **Database schema** (owned by load; created idempotently on every run):
  `chunks` table — `id text PRIMARY KEY`, the chunk-record metadata columns
  (`slug text`, `law text`, `unit text`, `section_path text[]`, `citation text`,
  `source_url text`, `fetched_at text`, `part jsonb NULL`), `text text`, and
  `embedding vector(<dim>) NOT NULL` with the dimension fixed by the Slice-1 decision.
  One **HNSW index** on `embedding` using the operator class matching the pinned distance
  operator. pgvector's default HNSW build parameters unless the Slice-1 research records a
  reason to deviate.

- **Idempotent per-law replace semantics**: load upserts by chunk `id` (`ON CONFLICT (id) DO
  UPDATE`) and, per law (`slug`), deletes rows whose ids are absent from the current
  artifacts — the store always mirrors the pipeline output and never accumulates stale rows.
  Full reprocess on corpus change is acceptable; incremental ingestion stays Backlog 13.

- **Database connection**: from the existing `.env` (`POSTGRES_USER`, `POSTGRES_PASSWORD`,
  `POSTGRES_DB` — the variables the Compose stack already uses), plus `POSTGRES_HOST`
  defaulting to `localhost` and `POSTGRES_PORT` defaulting to `5432`. No new configuration
  mechanism, no connection string in git.

- **Dev query command**: `uv run python -m rag.query "<question>"` (Makefile target
  `make query Q="..."`), printing the top-k chunks (default 5) with citation, distance, and
  a text snippet. A thin verification tool composing the embed stage's model and one
  similarity query — explicitly not the Phase 4 retrieve stage; it is expected to be
  superseded there.

- **New runtime dependencies** (approved in the PRD): `sentence-transformers` (Slice 1, for
  the decision measurements), `psycopg` and `pgvector` (Slice 3, first code that needs
  them).

- **Test tiers**: the default suite (`make test`, CI) uses the fake embedder and no
  database — deterministic golden-file tests, exactly like chunk's. Real-model and
  real-database behavior live in **opt-in integration tests** marked `integration`
  (registered pytest marker), which skip cleanly with an explanatory reason when the model
  cache or the database is unavailable. `make test` runs everything and skips what the
  environment lacks; nothing in `make check` requires a download or a running container.

## Key models

- **`Embedder`** — the minimal interface (texts → vectors; exposes model id and dimension);
  one sentence-transformers implementation, one deterministic test fake.
- **Embedding record** — the artifact line described above (`id`, `model`, `dim`,
  `embedding`); the interface load consumes together with the chunk records.

## Inventory

- `docs/stages/chunk.md — "Output" / "Downstream consumers"` — the input contract: the chunk
  record schema, `id` as the upsert key, and which fields become filter/citation metadata.
- `src/rag/chunk/__init__.py — Chunk, main()` — the record fields to mirror in the DB schema
  and the per-law CLI isolation pattern (stderr `✗ <slug>: <error>`, non-zero exit).
- `src/rag/chunk/__main__.py` — the `python -m` entry-point shape every stage repeats.
- `tests/test_chunk.py` — golden-file + determinism test pattern; `tests/fixtures/chunks/` —
  pinned chunk JSONL reusable as embed's input fixtures.
- `Makefile — fetch / convert / chunk targets` — pattern for the new `embed`, `load`, and
  `query` targets; the `db` / `db-shell` targets and `-include .env` show how DB env vars
  reach commands.
- `docker-compose.yml — postgres service` — the pgvector/pg17 container load connects to;
  healthcheck shows readiness handling.
- `.env.example` — gains `POSTGRES_HOST` / `POSTGRES_PORT` (documented defaults).
- `pyproject.toml — [project] dependencies, [dependency-groups] dev, [tool.pytest.ini_options]`
  — where the new dependencies and the `integration` marker land.
- `docs/roadmap.md — "Phase 3" + "Decisions"` — the decision-entry format (dated block with
  context/choice/alternatives/consequences) the model decision follows.
- `docs/concepts.md — "Vectorization & storage" rows` — Vector embedding, Dense embedding,
  Embedding normalization, Vector database, ANN, HNSW, IVF, Vector quantization: their
  `Place` column links to the theory chapters once those exist (Slice 5).
- `docs/prds/prd-rag-playbook.md — "Architecture: stage = module"` — the taxonomy and
  determinism promises (embed: tolerance, not bitwise) the slices implement.
- `README.md — status table, quick start, pipeline overview, structure table` and
  `AGENTS.md — Commands table` — updated in Slice 5.

## Resolved decisions

Clarified with the maintainer during the PRD round:

- **Model decision in-phase**: the PRD fixes criteria and candidates; the dated decision
  lands in the roadmap as Slice 1, pinning model + normalization + distance operator
  together.
- **Two stages with a JSONL artifact** between them (approach A) — no combined command, no
  binary matrix format.
- **Verification via a small dev query command**, not psql-only spot-checks and not a
  gold-question integration test (that is Backlog 1).
- **Dependencies approved**: sentence-transformers, psycopg, pgvector adapter.
- **Fake + opt-in integration testing**: default suite needs no model download and no DB;
  reference-vector (tolerance) and Postgres tests are opt-in and skip cleanly.

## Open questions / Risks

- **Chunk size is re-validated in Slice 1.** Phase 2 chose 2000 chars as a token proxy
  (~400–500 tokens). If the chosen model's effective input limit is smaller, the mismatch is
  recorded in the decision entry and the chunk `max_chars` is adjusted there (a chunk-stage
  parameter change with re-pinned golden files) — the hand-off plan-chunk.md announced, not
  rework debt.
- **`ty` vs torch/sentence-transformers**: the typechecker may lack stubs for the ML stack.
  The lazy import inside the real `Embedder` implementation confines any suppression to one
  module; `make check` must stay green without weakening repo-wide settings.
- **CI has no model and no database**, so integration tests always skip there — real-model
  and real-DB verification are documented local runs, recorded as dated spot-checks in the
  stage contracts. Acceptable for a learning playbook; the evaluation harness (Backlog 1)
  later hardens quality claims.
- **Memory headroom**: model + Postgres must fit the 16 GB floor with room for Phase 4's
  LLM. Slice 1 records the measured footprint against the playbook's risk item; if the
  preferred model is too heavy, the decision entry documents the fallback candidate chosen
  instead.

---

## Slice 1: Embedding-model decision (research + dated roadmap entry)

**User stories**: 5 (dated model decision with criteria).

### Context

- `docs/roadmap.md — "Decisions"` — the dated decision-block format to follow; the entry
  lands under it.
- `docs/prds/prd-embed-load.md — "Model selection"` — criteria: open license, German
  retrieval quality, dimension count, CPU latency, memory fit; candidates: multilingual-e5
  family, jina-embeddings-de, bge-m3.
- `pyproject.toml` — gains `sentence-transformers` so the measurements run against the real
  library.
- `docs/plans/plan-chunk.md — "Open questions / Risks"` — the announced chunk-size
  re-validation this slice performs.

### What to build

No stage code — the decision the whole phase hangs on. Research current model cards and
license terms for the candidates (live sources, never memory; every claim dated), compare
German retrieval quality using published benchmark results (e.g. MTEB German tasks),
dimension count, and model size. Add `sentence-transformers`, download the shortlisted
model(s) once, and measure on this machine: embedding throughput on a real chunk sample and
peak memory. Verify the model's input token limit against the Phase 2 chunk size (2000
chars) using the model's own tokenizer on real chunks. Record the dated decision in the
roadmap: the chosen model, whether vectors are normalized, the pgvector distance operator —
pinned together, with alternatives weighed, measured numbers, the 16 GB-floor assessment,
and one line on whether the model exposes token embeddings (the Backlog 6 late-chunking
condition).

### Acceptance criteria

- [ ] A dated decision entry in `docs/roadmap.md` pins model + normalization + distance
      operator together, with license verification, benchmark citations, measured CPU
      throughput and memory from this machine, and the late-chunking note.
- [ ] The Phase 2 chunk size is confirmed against the chosen model's tokenizer on real
      chunks (or the required `max_chars` adjustment is recorded in the entry and applied to
      the chunk stage with re-pinned golden files before Slice 2 starts).
- [ ] `sentence-transformers` is added and `make check` is green — no stage code yet.

---

## Slice 2: Embed stage — chunks JSONL → embeddings JSONL

**User stories**: 1 (embed on a CPU-only machine), 2 (inspectable artifact), 8 (default
tests without model/DB).

### Context

- `src/rag/embed/` — new subpackage with `__main__.py`; created here.
- `docs/stages/chunk.md — "Output"` — the input records; only `id` and `text` are consumed.
- `tests/fixtures/chunks/` — pinned chunk JSONL from Phase 2, reusable as input fixtures.
- `tests/test_chunk.py` — the golden-file pattern the embed tests mirror.
- `docs/stages/embed.md` — the stage contract; created here.
- `Makefile` — new `embed` target.

### What to build

The embed stage end to end: read every `data/chunks/<slug>.jsonl`, embed each record's
`text` with the `Embedder` in batches, and write `data/embeddings/<slug>.jsonl` with the
artifact record (`id`, `model`, `dim`, `embedding`), preserving input order. The real
`Embedder` wraps the pinned sentence-transformers model (lazy import, batch encode,
normalization per the Slice-1 decision); per-law progress goes to stdout, per-law failures
to stderr with the established isolation semantics. The stage contract documents invocation,
input (which chunk fields it consumes), the artifact schema, guarantees (order-preserving;
reproducible within tolerance — never bitwise, per the playbook PRD), failure behaviour, and
downstream consumers (load; Phase 4 retrieve uses the same model).

Tests: golden-file tests with the deterministic fake embedder (chunk fixtures → pinned
embeddings JSONL, byte-exact, run-twice determinism); CLI failure isolation; and one opt-in
`integration` test that loads the real model, embeds a small fixture, and asserts cosine
similarity within tolerance against small checked-in reference vectors (skips with a reason
when the model cache is absent).

### Acceptance criteria

- [ ] `make embed` turns every `data/chunks/<slug>.jsonl` into
      `data/embeddings/<slug>.jsonl`, one self-describing record per chunk, order preserved;
      missing/empty chunks dir exits non-zero with a "run `make chunk` first" hint.
- [ ] Golden-file tests with the fake embedder pass byte-exactly and run without torch, a
      model download, or network; the real-model reference-vector test passes locally within
      tolerance and skips cleanly elsewhere.
- [ ] `docs/stages/embed.md` documents invocation, input, artifact schema, guarantees,
      failure behaviour, and downstream consumers.
- [ ] `make check` is green.

---

## Slice 3: Load stage — chunks + embeddings → Postgres/pgvector

**User stories**: 3 (one idempotent load command), 7 (safe re-runs), 8 (default tests
without model/DB).

### Context

- `src/rag/load/` — new subpackage with `__main__.py`; created here.
- Architectural decisions — Database schema, Idempotent per-law replace semantics, Database
  connection.
- `docker-compose.yml — postgres service` and `Makefile — db target` — the running database
  the integration tests and real runs use.
- `.env.example` — gains the documented `POSTGRES_HOST` / `POSTGRES_PORT` defaults.
- `docs/stages/load.md` — the stage contract; created here.
- `Makefile` — new `load` target.

### What to build

The load stage end to end: read chunk records and embeddings per law, join them by `id`
(a chunk without a vector, a vector without a chunk, or model/dim disagreement across
records is a per-law error — nothing partial is written for that law), create the schema and
HNSW index idempotently, and write with per-law replace semantics: upsert every row by `id`,
then delete the law's rows whose ids are gone. `psycopg` and the `pgvector` adapter land
here. The stage contract documents invocation, inputs (both artifacts and the join), the
schema it owns, the replace semantics, connection configuration, failure behaviour, and the
downstream consumer (Phase 4 retrieve).

Tests: default tests cover the pure logic — chunk/embedding joining and the validation
failures — with no database; opt-in `integration` tests run against the Compose Postgres
(skip with a reason when unreachable) and assert: schema and index exist after a run, row
count matches the artifacts, re-running is idempotent (same row count, updated rows), a
removed id disappears (stale-row pruning), and a wrong-dimension artifact is rejected.

### Acceptance criteria

- [ ] `make load` (with `make db` up and artifacts present) creates the `chunks` table and
      HNSW index and fills one row per chunk, joined correctly by `id`; missing inputs exit
      non-zero with a "run `make embed` first" hint.
- [ ] Re-running `make load` is idempotent; removing a chunk upstream and re-running prunes
      its row; per-law validation errors (missing vector, orphan vector, model/dim mismatch)
      fail that law without partial writes.
- [ ] Default tests pass without a database; integration tests pass against the Compose
      Postgres locally and skip cleanly elsewhere.
- [ ] `docs/stages/load.md` documents invocation, inputs, the owned schema, replace
      semantics, connection config, failure behaviour, and downstream consumers.
- [ ] `make check` is green.

---

## Slice 4: Dev query command + full-corpus verification

**User stories**: 4 (hands-on retrieval verification).

### Context

- `src/rag/query/` — new subpackage with `__main__.py`; created here.
- The embed stage's `Embedder` (same pinned model embeds the question) and the load stage's
  connection configuration — the two modules this thin tool composes.
- `docs/stages/load.md` — gains the dated verification spot-check section.
- `Makefile` — new `query` target (`make query Q="..."`).

### What to build

The verification tool and the phase's proof: `python -m rag.query "<question>"` embeds the
question with the pinned model and prints the top-k rows ordered by the pinned distance
operator — rank, distance, citation, and a text snippet per hit. Then run the whole pipeline
on the real corpus (`make fetch && make convert && make chunk && make embed && make load`)
and verify retrieval with hand-written German legal questions whose expected §§ are known
(e.g. a Kassen-question hitting KassenSichV §§, a Steuer-question hitting AO/UStG §§, a
Grundrechte-question hitting GG Art). Record the questions, top hits, and date as a
spot-check section in the load stage contract — the phase's "plausible §§" criterion made
concrete. No gold-set metrics, no thresholds — that is Backlog 1.

Tests: the command's assembly logic (argument handling, result formatting) with the fake
embedder and no database; querying end to end is covered by the documented manual
verification.

### Acceptance criteria

- [ ] `make query Q="..."` prints top-k hits with rank, distance, citation, and snippet,
      using the pinned model and distance operator; `--top-k` overrides the default of 5.
- [ ] The full pipeline runs clean on the real corpus, and hand-written questions return
      plausible §§; questions, hits, and date are recorded in `docs/stages/load.md`.
- [ ] Formatting/argument tests run without model or DB; `make check` is green.

---

## Slice 5: Theory chapters, cross-links, README status, phase wrap-up

**User stories**: 6 (theory chapters cross-linked with code); playbook stories 7 (front door
states landed vs. planned) and 11 (pedagogy in the definition of done).

### Context

- `docs/theory/embeddings.md` and `docs/theory/vector-indexes.md` — the phase's theory
  chapters; created here.
- `docs/concepts.md — "Vectorization & storage" rows` — link their `Place` column to the new
  chapters.
- `README.md — status table, quick start, pipeline overview, structure table`,
  `AGENTS.md — Commands table`, `docs/roadmap.md — "Phase 3"` heading — the honesty
  artifacts.
- `src/rag/embed/__init__.py` / `src/rag/load/__init__.py` — module docstrings gain the
  theory + contract cross-links.

### What to build

The pedagogy and honesty artifacts completing the definition of done. `embeddings.md`:
what a dense embedding is, how sentence-transformers models produce one, dense vs sparse
(pointing at Backlog 2), embedding normalization and its coupling to the distance operator,
and the model-decision rationale link. `vector-indexes.md`: exact kNN vs approximate nearest
neighbor and the recall/latency trade-off, how HNSW works (layered proximity graphs, greedy
search), with IVF and vector quantization as theory-only contrasts, and why a vector
database is more than an index — each concept once, cross-linked both ways (module
docstrings ↔ chapters; chapters ↔ code and stage contracts). The concept map's
vectorization rows link to the chapters.

README updates: Phase 3 status ✅ with a fresh clean-checkout verification date (the Slice-4
full run), `make embed` / `make load` join the quick start (with `make db` before load and
the first-run model-download size stated up front), the pipeline overview gains the two
stages linking contract + chapter, and the structure table gains `data/embeddings/`. The
AGENTS.md Commands and tech-stack tables record the new targets and the chosen model; the
roadmap Phase 3 heading flips to ✅.

### Acceptance criteria

- [ ] Both theory chapters exist, are concise, cover their concepts exactly once, and are
      cross-linked both ways (docstrings → chapters; chapters → code + contracts).
- [ ] `docs/concepts.md`'s Vector embedding, Dense embedding, Embedding normalization,
      Vector database, ANN, HNSW, IVF, and Vector quantization rows link to the chapters; no
      concept is added, moved, or dropped.
- [ ] README: Phase 3 row ✅ with a clean-checkout verification date; quick start includes
      `make embed` and `make load` and states the model-download cost; pipeline overview and
      structure table updated; every relative link resolves.
- [ ] AGENTS.md lists the new commands and the pinned embedding model; roadmap Phase 3
      heading marked ✅.
- [ ] Definition-of-done audit against AGENTS.md rule 5 passes — code + tests + theory
      chapters + stage contracts + README status with date all present; `make check` green.
