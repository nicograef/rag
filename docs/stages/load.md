# Stage contract: load

> Code: [`src/rag/load/`](../../src/rag/load/__init__.py) ·
> Roadmap: [Phase 3 — Embed & load](../roadmap.md) ·
> Theory: [vector indexes](../theory/vector-indexes.md)

Fills Postgres/pgvector with the pipeline's output: chunk records joined with their
embedding vectors. Fifth and last stage of the offline ingestion pipeline — it owns
everything database-shaped (schema, index, writes), so no other stage touches the store's
shape. After a run, every law present in the input mirrors its artifacts on disk exactly;
the mirror is deliberately per-law (see [replace semantics](#replace-semantics-idempotency)).

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
vector without a chunk, a chunk record whose `slug` contradicts the artifact file's name
(the prune keys on it), embedding records that disagree on one model and dimension, or a
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

The store therefore mirrors the current artifacts law by law and never accumulates stale
rows *within* a law. The mirror is deliberately per-law, not global: a law whose artifact
files are removed entirely is never visited, so its rows stay until the table is rebuilt —
the alternative, a global prune of every slug not in the input, would silently wipe the
rest of the store on a run against a partial `--chunks-dir`. A full reprocess on corpus
change is the accepted model at MVP scale; incremental ingestion is Backlog 13.

## Failure behaviour

Per-law isolation: a law whose artifacts fail validation (or whose write fails) is
reported on stderr (`✗ <slug>: <error>`), its transaction rolls back, and the remaining
laws still load; the exit code is non-zero if any law failed. A law present in only one of
the two input directories is an error for that law. When `--chunks-dir` is missing or
empty the stage exits non-zero with a hint to run `make chunk` first; when
`--embeddings-dir` is missing or empty, with a hint to run `make embed` first; when the
database is unreachable, with a hint to run `make db` first (the same hint the query
command prints).

## Verification

The phase's success criterion — "plausible §§ for hand-written test queries" — is
reproducible through the Phase 4 [retrieve stage](retrieve.md), which superseded the Phase 3
dev query command (same pinned model, same query shape):

```sh
make query Q="Wie müssen elektronische Kassen gesichert werden?"   # wraps:
uv run python -m rag.retrieve "<question>"                         # options: --top-k 5
```

It embeds the question with the same pinned model and prints the top-k rows ordered by
`embedding <=> query` — rank, cosine distance, citation, and a text snippet per hit.

**Spot-check (2026-07-14):** run against a store filled by the full pipeline on the live
corpus that day (`make fetch && make convert && make chunk && make embed && make load`,
1,225 chunks), using the since-superseded dev query command — same pinned model and query
shape as the retrieve stage, so the results still describe it. Four hand-written questions
with known expected §§ — **all four returned the expected § at rank 1** (distance = cosine,
lower is better):

| Question | Expected | Top hits (rank · distance · citation) |
| --- | --- | --- |
| „Wie müssen elektronische Kassen vor Manipulation geschützt werden?" | KassenSichV §§ / AO § 146a | 1 · 0.4013 · **§ 146a AO** — 2 · 0.4477 · § 146 AO — 3 · 0.4647 · § 146b AO |
| „Wann entsteht die Umsatzsteuer?" | UStG § 13 | 1 · 0.3185 · **§ 13 UStG** — 2 · 0.3357 · § 13b UStG — 3 · 0.3417 · § 26 UStG |
| „Ist die Würde des Menschen antastbar?" | GG Art 1 | 1 · 0.4350 · **Art 1, Art 2 GG** — 2 · 0.5621 · Art 4 GG — 3 · 0.5674 · § 380 AO |
| „Wer ist zum Vorsteuerabzug berechtigt?" | UStG § 15 | 1 · 0.3795 · **§ 15 UStG** — 2 · 0.3870 · § 18 UStG — 3 · 0.3982 · § 15 UStG |

Honest reading: the rank-1 hits are exactly the expected norms, and for the Vorsteuer
question four of the top five hits are parts of § 15 UStG. Two caveats worth recording:
the Kassen question surfaces only the AO anchor norms — no KassenSichV chunk reaches the
top 5, plausibly because the question's vocabulary matches § 146a's text more directly;
and the Grundrechte question shows the domain skew of the corpus — ranks 3–4 are
AO Ordnungswidrigkeiten noise, though the distance gap to rank 1 (0.435 vs ≥ 0.56) is
clear. Anecdotal plausibility only — no thresholds, no metrics; the evaluation harness is
Backlog 1.

## Downstream consumers

**Phase 4's [retrieve stage](retrieve.md)** searches this table (`ORDER BY embedding <=>
$query`) and filters/cites on the metadata columns.
