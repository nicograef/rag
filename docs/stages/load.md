# Stage contract: load

> Code: [`src/rag/load/`](../../src/rag/load/__init__.py) ·
> Roadmap: [Phase 3 — Embed & load](../roadmap.md) ·
> Theory: [vector indexes](../theory/vector-indexes.md)

Fills Postgres/pgvector with the pipeline's output: chunk records joined with their
embedding vectors. Fifth and last stage of the offline ingestion pipeline — it owns
everything database-shaped (schema, index, writes), so no other stage touches the store's
shape. After a run, every source present in the input mirrors its artifacts on disk exactly;
the mirror is deliberately per-source (see [replace semantics](#replace-semantics-idempotency)).

## Invocation

```sh
make db                         # once: start Postgres 17 + pgvector (Docker Compose)
make load                       # wraps:
uv run python -m rag.load       # options: --chunks-dir data/chunks --embeddings-dir data/embeddings
```

Connection settings come from the environment — the same variables the Compose stack uses
(`.env`, loaded by the Makefile): `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`,
plus `POSTGRES_HOST` (default `localhost`) and `POSTGRES_PORT` (default `5432`). See
[`.env.example`](../../.env.example). No other configuration mechanism exists.

## Inputs

Per source (`<slug>`), both artifacts, joined by chunk `id`:

- `data/chunks/<slug>.jsonl` — the [chunk records](chunk.md#output); every field becomes a
  column.
- `data/embeddings/<slug>.jsonl` — the [embedding records](embed.md#output); contributes
  the vector.

The join is validated per source **before anything is written**: a chunk without a vector,
a vector without a chunk, a chunk record whose `slug` contradicts the artifact file's name
(the prune keys on it), embedding records that disagree on one model and dimension, or a
dimension that does not match the schema's vector column all fail the source with no
partial write.

## Owned schema

Created idempotently on every run (`CREATE ... IF NOT EXISTS`), pinned by the
[embedding-model decision](../roadmap.md#decisions):

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS chunks (
    id text PRIMARY KEY,      -- chunk id — the upsert key
    slug text, source_title text, section text, section_path text[], citation text,
    source_url text, fetched_at text,   -- filter/citation metadata, verbatim from chunk
    part jsonb NULL,
    text text,                -- what retrieval returns and the generator reads
    embedding vector(384) NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_embedding_idx
    ON chunks USING hnsw (embedding vector_cosine_ops);
```

`source_title` is the human-readable name of whatever the chunk came from
(e.g. `Arsenal F.C.`), independent of the source's domain.
One **HNSW index** with pgvector's default build parameters (`m=16`,
`ef_construction=64`) and the cosine operator class — the distance operator everywhere is
`<=>` (cosine distance), matching the model's normalized vectors. Why HNSW, and what the
parameters mean, is the [vector-indexes chapter](../theory/vector-indexes.md).

### Dimension guard

The vector dimension is fixed at `CREATE TABLE` time, so a table built for an earlier
pinned model is **not** migrated by `CREATE ... IF NOT EXISTS` — inserting this build's
`vector(384)` rows would otherwise fail on the first insert with an opaque pgvector error.
Before loading anything, load reads the existing `chunks.embedding` column's declared
dimension; if it disagrees with the pinned `EMBEDDING_DIM` (384), load raises `LoadError`
naming both dimensions and pointing at `make reset` — an actionable error instead of a
confusing insert failure deep in the run.

## Replace semantics (idempotency)

Per source, in one transaction:

1. **Upsert** every row by `id` (`INSERT ... ON CONFLICT (id) DO UPDATE`) — re-running
   with unchanged artifacts rewrites identical rows; changed chunks update in place.
2. **Prune** the source's stale rows (`DELETE ... WHERE slug = <slug> AND id NOT IN
   (current ids)`) — a chunk removed upstream disappears from the store.

The store therefore mirrors the current artifacts source by source and never accumulates
stale rows *within* a source. The mirror is deliberately per-source, not global: a source
whose artifact files are removed entirely is never visited, so its rows stay until the
table is rebuilt — the alternative, a global prune of every slug not in the input, would
silently wipe the rest of the store on a run against a partial `--chunks-dir`. A full
reprocess on corpus change is the accepted model at MVP scale; incremental ingestion is
Backlog 13.

## Failure behaviour

Per-source isolation: a source whose artifacts fail validation (or whose write fails) is
reported on stderr (`✗ <slug>: <error>`), its transaction rolls back, and the remaining
sources still load; the exit code is non-zero if any source failed. A source present in
only one of the two input directories is an error for that source. When `--chunks-dir` is
missing or empty the stage exits non-zero with a hint to run `make chunk` first; when
`--embeddings-dir` is missing or empty, with a hint to run `make embed` first; when the
existing table's vector dimension does not match the pinned model, with the dimension
guard's `make reset` hint; when the database is unreachable, with a hint to run `make db`
first (the same hint the query command prints).

## Verification

The phase's success criterion — "plausible retrieval hits for hand-written test queries"
— is reproducible through the Phase 4 [retrieve stage](retrieve.md), which superseded the
Phase 3 dev query command (same pinned model, same query shape):

```sh
make query Q="Which stadium does Arsenal play at?"   # wraps:
uv run python -m rag.retrieve "<question>"           # options: --top-k 5
```

It embeds the question with the same pinned model and prints the top-k rows ordered by
`embedding <=> query` — rank, cosine distance, citation, and a text snippet per hit.

**Spot-check (2026-07-17):** a full offline re-run of the pipeline (`make fetch` →
`make chunk` → `make embed` → `make load`) over the 20-club corpus loaded **1333 chunk
rows** into `chunks (embedding vector(384))` with the HNSW index built. `make embed` over
the 1333 chunks took ≈ 4 minutes on CPU. A `make query` retrieval spot-check returned
plausible club sections ranked by cosine distance, e.g. "Which stadium does Arsenal play
at?" ranked the `Arsenal F.C. — Stadiums` chunks first at cosine distance ≈ 0.20.
Anecdotal plausibility only — no thresholds, no metrics; the evaluation harness is
Backlog 1.

## Downstream consumers

**[retrieve](retrieve.md)** searches this table (`ORDER BY embedding <=> $query`) and
filters/cites on the metadata columns.
