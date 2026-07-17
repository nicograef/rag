# Stage contract: embed

> Code: [`src/rag/embed/`](../../src/rag/embed/__init__.py) ·
> Roadmap: [Phase 3 — Embed & load](../roadmap.md) ·
> Theory: [embeddings](../theory/embeddings.md)

Turns each article's chunk records under `data/chunks/` into one JSONL file of embedding
vectors in `data/embeddings/`. Fourth stage of the offline ingestion pipeline: it maps
every chunk's text into the vector space retrieval searches, using the pinned model from
the [embedding-model decision](../roadmap.md#decisions) (`BAAI/bge-small-en-v1.5`,
normalized, cosine — model, normalization, and distance operator are one decision).

## Invocation

```sh
make embed                      # wraps:
uv run python -m rag.embed      # options: --chunks-dir data/chunks --embeddings-dir data/embeddings
```

The first run downloads the pinned model (≈ 130 MB, CPU-only) into the Hugging Face cache
(`~/.cache/huggingface/`); afterwards the stage is offline. `MODEL_ID` and `BATCH_SIZE` are
env-overridable — `EMBED_MODEL_ID` (default `BAAI/bge-small-en-v1.5`) and `EMBED_BATCH_SIZE`
(default 16) — with the pinned value as the default (the "pinned choice, tunable knob"
rule). Embedding runs in batches on CPU; per-article progress is printed to stdout.

## Input

One `data/chunks/<slug>.jsonl` per article, as produced by [chunk](chunk.md). Of the chunk
record's fields the stage consumes exactly two: **`id`** (carried through to key the
vector) and **`text`** (what is embedded). All other fields pass untouched through the
chunk file to [load](load.md) — embed does not copy them into its artifact.

## Output

One `data/embeddings/<slug>.jsonl` per article: one JSON record per chunk, in the chunk
file's order, the file ending with a trailing newline. The record schema — the artifact
[load](load.md) joins back to the chunk records — is:

| Field       | Type           | Value                                                    |
| ----------- | -------------- | -------------------------------------------------------- |
| `id`        | string         | The chunk `id` this vector belongs to                     |
| `model`     | string         | Hugging Face model id that produced the vector (`BAAI/bge-small-en-v1.5`) |
| `dim`       | integer        | Vector dimension count (384)                               |
| `embedding` | list of floats | The vector itself, normalized to unit length              |

Every record is self-describing (`model` + `dim` on each line), so load can validate that
all records agree on one model and dimension before writing anything, and a learner can
inspect real vectors with standard tools (`head`, `jq`) — the artifact is deliberately
plain JSON lines, not a binary matrix.

## Guarantees

- **Order-preserving.** Record *n* of the output embeds record *n* of the input.
- **Reproducible within tolerance — never bitwise.** Re-embedding the same text yields a
  vector of cosine similarity ≈ 1.0, not byte-identical floats: results vary across
  hardware and library versions (see the [playbook PRD](../prds/prd-rag-playbook.md)'s
  determinism promises). The golden-file tests therefore run a deterministic fake embedder
  through the same `Embedder` interface; the real model is covered by an opt-in
  `integration` test asserting cosine similarity > 0.999 against checked-in dim-384
  reference vectors.
- **Symmetric path.** `bge-small-en-v1.5` uses one encoding path for both corpus chunks and
  questions — no query/passage instruction prefix — so ingest and question embeddings share
  one interface (see [retrieve](retrieve.md)).
- **All-or-nothing per article.** The output file is written only after the whole article
  embedded successfully.
- **No partial reads.** An invalid chunk record (broken JSON, missing `id`/`text`) fails
  the article instead of silently skipping the line.
- **No silent truncation.** A chunk whose text exceeds the model's token window (512 for
  the pinned model) fails the article — naming the chunk id — before anything is embedded,
  instead of letting the encoder silently cut it off. This extends the chunk stage's
  no-silent-loss guarantee across the chunk→embed boundary; the [chunk stage's size
  pinning](chunk.md#verification) keeps ordinary chunks well under this window.

## Failure behaviour

Per-article isolation, like the earlier stages: an article that cannot be embedded — an
invalid chunk record, or a chunk over the model's token window — is reported on stderr
(`✗ <slug>: <error>`) and produces no output file; the remaining articles still embed.
The exit code is non-zero if any article failed. When `--chunks-dir` is missing or empty,
embed exits non-zero with a hint to run `make chunk` first.

## Downstream consumers

**[load](load.md)** joins these records with the chunk records by `id` and writes both
into the vector store. **[retrieve](retrieve.md)** must embed questions with the same
pinned model — the deliberate coupling recorded in the model decision.
