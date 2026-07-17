# Vector indexes

Why nearest-neighbor search needs an index at all, and how HNSW earns its place — the
theory behind the [load stage](../stages/load.md)
([`src/rag/load/`](../../src/rag/load/__init__.py)), which creates the one index this
pipeline uses. The stage contract documents *what* schema and index exist; this chapter is
the *why*. Every concept below is explained exactly once, here; the
[concept map](../concepts.md) points to this chapter as its place.

## Exact kNN vs approximate nearest neighbor: the recall/latency trade-off

The [embeddings chapter](embeddings.md) reduced "find text that means this" to "find the
k nearest vectors". The honest way to do that is **exact k-nearest-neighbor search**:
compute the distance from the query to *every* stored vector, keep the k smallest. Exact
kNN has perfect recall, no index to build — and cost linear in the corpus. In pgvector it
is simply an `ORDER BY embedding <=> $q LIMIT k` with no index: fine at this project's
MVP scale (thousands of chunks), untenable at millions, where every query would grind
through gigabytes of floats.

There is no clever exact shortcut in high dimensions — space-partitioning trees that work
in 2D degenerate as dimensions grow (the curse of dimensionality). So production systems
accept **approximate nearest neighbor (ANN)** search: an index that returns *almost
always* the true neighbors in a *tiny fraction* of the time. The quality measure is
**recall** — the fraction of true top-k the index actually returned. ANN turns search
into a dial between recall and latency; every index type is a different mechanism for
that dial.

## HNSW: layered proximity graphs, searched greedily

**HNSW** (Hierarchical Navigable Small World) is the graph-based ANN index — the recorded
choice here, and the current default answer in most vector stores.

The base layer is a **proximity graph**: every vector is a node, connected to its `m`
nearest-ish neighbors (pgvector default: 16). Searching a proximity graph is greedy
navigation: start anywhere, repeatedly hop to the neighbor closest to the query, stop when
no neighbor improves. One flat graph has a flaw — from a random start, greedy hops crawl
across the space in small steps. HNSW fixes that with **layers**, the "hierarchical": each
vector is also inserted, with exponentially decreasing probability, into sparser upper
layers — an expressway network over the dense base graph. A search enters at the sparse
top, where each hop covers a huge stretch of the space, and descends layer by layer,
navigating ever finer; on the base layer it maintains a candidate list (size
`hnsw.ef_search`, default 40) instead of a single point, and returns the best k. Search
cost grows roughly logarithmically with corpus size.

A sketch of one descent — a sparse upper layer over the dense base:

```text
                        · q           the query lands nearest (E)
upper:   (A)────────►(D)              sparse layer: few nodes, long hops from entry (A)
          │           │
          ▼           ▼               descend one layer at the closest node
base:    (A)─(B)─(C)─(D)─(E)─(F)      dense layer: every vector is a node
                       └──►(E)        greedy hops to the query's nearest neighbor
```

The dial settings are explicit: bigger `m`/`ef_construction` (build-time candidate list,
default 64) buy a better-connected graph — higher recall, more memory, slower builds;
bigger `ef_search` buys recall per query at latency cost. The
[load stage](../stages/load.md) keeps pgvector's defaults: at MVP corpus scale the index
is present to be *learned from* — measured tuning belongs to the evaluation harness
(Backlog 1). Two operational properties matter even now: HNSW handles inserts and
deletes incrementally (no periodic retraining — fits the load stage's idempotent
re-runs), and the graph lives in RAM to be fast — the index memory *is* the price of the
speed.

There is a build-order twist here. pgvector's own README recommends creating an HNSW index
*after* loading the initial data — like any index, one bulk build over rows already present
is faster than growing the graph insert by insert. The load stage does the opposite on
purpose: a resident `CREATE INDEX IF NOT EXISTS` so every re-run is idempotent, paying the
slower incremental build in exchange. At MVP scale (~1,225 rows) that build is seconds and
the trade costs nothing; it is the knob to revisit if the corpus ever grows orders of
magnitude.

## IVF: the clustering alternative (theory-only contrast)

**IVF** (inverted file index, pgvector's `ivfflat`) is the other classic mechanism:
cluster all vectors into `nlists` partitions (k-means at build time), remember each
cluster's centroid, and at query time scan only the `nprobe` clusters whose centroids are
nearest the query. It is an *inverted file* in the IR sense — centroid → list of members —
and its dial is `nprobe`: more clusters scanned, more recall, more latency.

The trade against HNSW: IVF builds faster and uses less memory (no graph, just lists),
but its recall ceiling is lower at equal latency — a true neighbor sitting just across a
cluster boundary is invisible unless its cluster is probed — and the centroids are frozen
at build time, so heavy inserts degrade the clustering until a rebuild. HNSW costs more
memory and build time and gives better recall/latency plus incremental growth. This
pipeline records HNSW as the choice and does not build IVF; it exists here as the
contrast that shows *why*.

## Vector quantization: paying for scale with precision (theory-only contrast)

At real scale the vectors themselves become the problem: a million 1024-dim fp32 vectors
are ~4 GB before any index structure. **Vector quantization** compresses the stored
vectors — **SQ** (scalar: each float → int8, ~4×), **PQ** (product: split the vector into
subvectors, replace each with a codebook id, order-of-magnitude compression), **BQ**
(binary: one bit per dimension, ~32×) — trading a controlled amount of recall for memory,
usually recovered by *rescoring* the shortlist against full-precision vectors. Not to be
confused with LLM weight quantization (GGUF — Phase 4's chapter): same idea, applied to
model weights instead of stored vectors. This corpus is thousands of vectors, far below
the scale where quantization pays; it is tracked as theory so the omission is a decision,
not a blind spot.

## A vector database is more than an index

An index answers exactly one question — *which stored vectors are nearest to this one?* A
**vector database** is a datastore that persists vectors *alongside the text and metadata
they belong to* and keeps them consistent: transactional writes (load's per-law replace
is one transaction), an upsert key, filtering on metadata in the same query (Backlog 3),
joins, backups. That is why this pipeline's choice is Postgres + pgvector rather than a
bare index library (FAISS et al.): the chunk row — text, citation metadata, provenance,
vector — is one record in one system, and SQL composes over it. Dedicated open-source
engines (Qdrant, Milvus, Weaviate) make the opposite bet — a separate, vector-first
system with its own query API, worth it when vector traffic dominates; proprietary cloud
services (Pinecone) are non-options here by rule. For a corpus that is also relational
data, one boring Postgres is the production-shaped answer.

## Where this leaves the pipeline

The [load stage](../stages/load.md) creates the `chunks` table and its HNSW index
idempotently and fills both from the artifacts; `make query` demonstrates the search
(`ORDER BY embedding <=> $q LIMIT k`) end to end. Phase 4's retrieve stage will run that
same query for real questions — and the recall/latency dial stays untouched until the
evaluation harness (Backlog 1) can measure what a turn of it actually does.
