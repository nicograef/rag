# Stage contract: load

> Code: [`src/rag/load/`](../../src/rag/load/__init__.py) ·
> Roadmap: [Phase 3 — Embed & load](../roadmap.md) ·
> Theory: [vector indexes](../theory/vector-indexes.md)

Fills Postgres/pgvector with the pipeline's output: chunk records joined with their
embedding vectors. Fifth and last stage of the offline ingestion pipeline — it owns
everything database-shaped (schema, index, writes), so no other stage touches the store's
shape. After a run, the store mirrors the artifacts on disk exactly.

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

Per law (`<slug>`), both artifacts, joined by chunk `id`:

- `data/chunks/<slug>.jsonl` — the [chunk records](chunk.md#output); every field becomes a
  column.
- `data/embeddings/<slug>.jsonl` — the [embedding records](embed.md#output); contributes
  the vector.

The join is validated per law **before anything is written**: a chunk without a vector, a
vector without a chunk, embedding records that disagree on one model and dimension, or a
dimension that does not match the schema's vector column all fail the law with no partial
write.

## Owned schema

Created idempotently on every run (`CREATE ... IF NOT EXISTS`), pinned by the
[embedding-model decision](../roadmap.md#decisions):

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS chunks (
    id text PRIMARY KEY,      -- chunk id — the upsert key
    slug text, law text, unit text, section_path text[], citation text,
    source_url text, fetched_at text,   -- filter/citation metadata, verbatim from chunk
    part jsonb NULL,
    text text,                -- what retrieval returns and the generator reads
    embedding vector(1024) NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_embedding_idx
    ON chunks USING hnsw (embedding vector_cosine_ops);
```

One **HNSW index** with pgvector's default build parameters (`m=16`,
`ef_construction=64`) and the cosine operator class — the distance operator everywhere is
`<=>` (cosine distance), matching the model's normalized vectors. Why HNSW, and what the
parameters mean, is the [vector-indexes chapter](../theory/vector-indexes.md).

## Replace semantics (idempotency)

Per law, in one transaction:

1. **Upsert** every row by `id` (`INSERT ... ON CONFLICT (id) DO UPDATE`) — re-running
   with unchanged artifacts rewrites identical rows; changed chunks update in place.
2. **Prune** the law's stale rows (`DELETE ... WHERE slug = <slug> AND id NOT IN
   (current ids)`) — a chunk removed upstream disappears from the store.

The store therefore always mirrors the current artifacts and never accumulates stale rows.
A full reprocess on corpus change is the accepted model at MVP scale; incremental
ingestion is Backlog 13.

## Failure behaviour

Per-law isolation: a law whose artifacts fail validation (or whose write fails) is
reported on stderr (`✗ <slug>: <error>`), its transaction rolls back, and the remaining
laws still load; the exit code is non-zero if any law failed. A law present in only one of
the two input directories is an error for that law. When `--chunks-dir` is missing or
empty the stage exits non-zero with a hint to run `make chunk` first; when
`--embeddings-dir` is missing or empty, with a hint to run `make embed` first.

## Verification

The phase's success criterion — "plausible §§ for hand-written test queries" — is
reproducible with the dev query command (a thin verification tool, explicitly not the
Phase 4 retrieve stage; it will be superseded there):

```sh
make query Q="Wie müssen elektronische Kassen gesichert werden?"   # wraps:
uv run python -m rag.query "<question>"                            # options: --top-k 5
```

It embeds the question with the same pinned model and prints the top-k rows ordered by
`embedding <=> query` — rank, cosine distance, citation, and a text snippet per hit.

**Spot-check: pending.** The implementing cloud session (2026-07-14) could not download
the pinned model or fetch the live corpus (egress policy), so the dated hand-written-query
spot-check could not be recorded yet. It requires one local run of the full pipeline
(`make fetch && make convert && make chunk && make embed && make load`), then `make query`
with questions whose expected §§ are known (e.g. a Kassen question → KassenSichV §§, a
Steuer question → AO/UStG §§, a Grundrechte question → GG Art); record questions, top
hits, and date here.

## Downstream consumers

**Phase 4's retrieve stage** searches this table (`ORDER BY embedding <=> $query`) and
filters/cites on the metadata columns. The dev query command above is its minimal
predecessor.
