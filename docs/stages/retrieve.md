# Stage contract: retrieve

> Code: [`src/rag/retrieve/`](../../src/rag/retrieve/__init__.py) ·
> Roadmap: [Phase 4 — Online PoC](../roadmap.md) ·
> Theory: [embeddings](../theory/embeddings.md), [vector indexes](../theory/vector-indexes.md)

Embeds a question and returns the top-k nearest chunks from the vector store. First stage of
the online path (**retrieve → assemble → generate**): it turns a natural-language question
into ranked `RetrievedChunk` records that the assemble stage packs into a prompt. It reads
the `chunks` table the [load stage](load.md) owns and never writes. It **supersedes the
Phase 3 dev query command** (`rag.query`) — that tool was a thin retrieval spot-check, folded
into this stage; the standalone CLI here keeps its output shape so `make query` still works.

## Entry points

Three ways in, one code path:

```sh
make query Q="Wie müssen elektronische Kassen gesichert werden?"   # wraps:
uv run python -m rag.retrieve "<question>"                        # options: --top-k 5
```

```python
from rag.embed import SentenceTransformerEmbedder
from rag.retrieve import retrieve

hits = retrieve("Wann entsteht die Umsatzsteuer?", embedder=SentenceTransformerEmbedder())
```

- **`retrieve(question, *, embedder, top_k=TOP_K) -> list[RetrievedChunk]`** — the library
  entry the [ask composition](../../src/rag/ask/__init__.py) calls. The `Embedder` is injected (the same
  interface the [embed stage](embed.md) defines), so tests pass a deterministic fake.
- **`python -m rag.retrieve "<question>"`** — the standalone CLI; constructs the real
  `SentenceTransformerEmbedder` lazily only when no embedder is injected.
- **`make query Q="…"`** — the Makefile wrapper, kept from Phase 3 so the verification muscle
  memory carries over.

## The pinned-model coupling

The question **must** be embedded with the same model that embedded the corpus — nearest-
neighbour search is only meaningful when query and documents live in one vector space. That
model, its normalization, and the pgvector distance operator are one decision (the
[embedding-model decision](../roadmap.md#decisions), `BAAI/bge-m3`, cosine); the distance
operator is read from `rag.load.DISTANCE_OPERATOR`, never re-spelled here. The
[embeddings chapter](../theory/embeddings.md) explains why the spaces must match.

## Input

- **The question** — one string, embedded to a query vector.
- **The `chunks` table** — the store the [load stage](load.md) owns. The query consumes six
  columns: `id`, `law`, `citation`, `source_url`, `text`, and `embedding` (for the distance).
  Connection settings come from the environment (`POSTGRES_*`, see [`.env.example`](../../.env.example)) —
  the same mechanism load uses, via `rag.load.connection_conninfo()`.

The single query orders by the pinned distance operator and truncates to `top_k`:

```sql
SELECT id, law, citation, source_url, text, embedding <=> :question AS distance
FROM chunks ORDER BY distance LIMIT :top_k;
```

The HNSW index the load stage built serves this `ORDER BY` — the ANN search the
[vector-indexes chapter](../theory/vector-indexes.md) covers.

## Output

A list of `RetrievedChunk`, ascending by `distance` (nearest first) — the **downstream
contract** the assemble and ask stages consume:

| Field        | Type    | Value                                                        |
| ------------ | ------- | ------------------------------------------------------------ |
| `id`         | string  | The chunk id (its identity in the store)                     |
| `law`        | string  | The law the chunk belongs to (e.g. `UStG`)                   |
| `citation`   | string  | Human-readable § reference (e.g. `§ 13 UStG`) — shown in sources |
| `source_url` | string  | The chunk's source URL — cited alongside the answer          |
| `text`       | string  | The normative text — what the generator reads                |
| `distance`   | float   | Cosine distance to the question (lower is nearer)            |

## Top-k

`TOP_K = 5` is pinned in `src/rag/retrieve/` by the dated
[generation-model decision](../roadmap.md#decisions): k, the chunk sizes, and the generation
model's context length form one context budget. `--top-k` (and the `top_k` argument) overrides
it per run; a value below 1 is rejected at parse time (`--top-k must be at least 1`).

## Standalone output

The CLI prints one hit per rank to stdout, two lines each — rank, cosine distance, citation,
then an indented one-line snippet of the text (whitespace-flattened, truncated to 200 chars):

```
1. (0.4013) § 146a AO
   (1) Wer aufzeichnungspflichtige Geschäftsvorfälle … mit einem elektronischen …
```

The library `retrieve()` returns the full records; snippetting is a CLI-only affordance.

## Failure behaviour

Every failure raises `RetrieveError` (the CLI prints its message to stderr and exits 1). The
message carries the actionable hint:

- **Missing connection settings** — reuses `connection_conninfo()`'s `LoadError` wording
  (names the missing `POSTGRES_*` variable and `.env.example`), re-raised as `RetrieveError`.
- **Database unreachable** — `database connection failed: {error} — run \`make db\` first`.
- **No `chunks` table** — `no chunks table — run \`make load\` first`. The same hint covers
  a fresh database without the pgvector extension (load owns `CREATE EXTENSION`).
- **Empty `chunks` table** — `the chunks table is empty — run \`make load\` first`.

The connection settings are checked before the question is embedded — and in the CLIs even
before the embedding model is constructed — so a misconfigured store fails in milliseconds
instead of after the model load.

## Downstream consumers

**[assemble](assemble.md)** packs the returned `RetrievedChunk` records (citations + text)
into the prompt; **[ask](../../src/rag/ask/__init__.py)** composes retrieve → assemble →
generate and prints the answer with a numbered sources block built from `citation` and
`source_url`.
