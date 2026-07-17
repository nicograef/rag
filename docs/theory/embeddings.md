# Embeddings

Why a fixed-length list of floats can stand in for meaning — the theory behind the
[embed stage](../stages/embed.md) ([`src/rag/embed/`](../../src/rag/embed/__init__.py)).
The stage contract documents *what* the stage produces; this chapter is the *why*. Every
concept below is explained exactly once, here; the [concept map](../concepts.md) points to
this chapter as its place.

## Vector embeddings: meaning as geometry

A **vector embedding** is a numeric vector representing a piece of text in a
high-dimensional space, built so that semantically similar texts lie close together. That
single property is the whole trick: once "similar meaning" has been turned into "small
distance", the fuzzy problem *find text that means roughly this* becomes the mechanical
problem *find the nearest vectors* — something a database can index and search. Retrieval
by meaning is called **semantic search**, and it needs no keyword overlap: „Kasse muss
manipulationssicher sein" can land near § 146a AO even though the § never uses those
words, because the model maps both to nearby points.

Everything downstream inherits this geometry. The chunk was the unit of retrieval
([chunking chapter](chunking.md)); the embedding decides *where that unit lives* in
search space. A bad chunk boundary blurs a vector; a bad embedding model blurs the whole
space.

## Dense embeddings, and how a sentence-transformers model produces one

The vectors this pipeline stores are **dense embeddings**: comparatively compact
(1024 dimensions here), with every dimension carrying some learned value. No dimension
means anything by itself — the *directions* in the space, learned from data, encode
concepts.

The producing models are transformer encoders. sentence-transformers wraps the recipe
into one call, but the steps are worth seeing once:

1. **Tokenize** — the text becomes subword tokens (the pinned model's XLM-RoBERTa
   tokenizer covers 100+ languages, German included). Models have a token limit, and an
   encoder left to itself silently truncates anything over it — which is why the model
   decision checked the Phase 2 chunk size against the limit (2000 chars ≈ 500–700 tokens
   vs an 8192-token window). Most chunks sit far under it, but chunking deliberately emits
   one class above 2000 chars — the atomic oversized table (largest measured: UStG
   „Anlage 2", 13,011 chars ≈ 3,784 tokens, still well inside 8192). So the
   [embed stage](../stages/embed.md) does not lean on that margin: it enforces the window
   in code — a chunk over the limit fails the law with an `EmbedError` naming it, never
   silently cut.
2. **Encode** — the transformer produces one contextual vector *per token*; the same word
   gets different vectors in different sentences.
3. **Pool** — the per-token vectors are collapsed into one text-level vector: many models
   average them (mean pooling); the pinned bge-m3 instead reads out its **CLS token**, the
   position whose attention has already blended the whole text's meaning into one vector.
   Either way the result is one fixed-length vector — which is where "one chunk = one
   vector" comes from, and why an over-stuffed chunk dilutes: a longer text packs more
   meaning into the same fixed width, so its vector lands close to no query in particular.
4. **Normalize** — optionally scale the vector to unit length (next section).

An encoder trained only to reconstruct language is not yet good at retrieval; embedding
models are additionally trained with **contrastive objectives** — pull (query, relevant
passage) pairs together, push unrelated pairs apart — which is what makes question
vectors land near answer-passage vectors. It is also why the question must be embedded
with the **same model** as the corpus: two different models produce two unrelated
geometries, and distances between them are meaningless. The pipeline pins one model for
both sides — chosen, with measurements and alternatives, in the dated
[embedding-model decision](../roadmap.md#decisions).

## Normalization and the distance operator: one decision, not two

Similarity needs a measure. The three usual candidates — Euclidean distance (`<->`),
inner product (`<#>`), cosine distance (`<=>`) — are genuinely different functions on raw
vectors: a long vector can have a huge inner product with everything while cosine ignores
length entirely.

**Embedding normalization** — scaling every vector to unit length — collapses the
choices: on unit vectors, cosine, inner product, and Euclidean distance produce the same
neighbor *ranking*. That is why normalization is not a cosmetic detail but one half of a
pair: the model card says whether its training assumed normalized vectors, and the
distance operator must match. Pinning them separately invites the quiet failure mode
where ingestion normalizes and search assumes it didn't. The
[model decision](../roadmap.md#decisions) therefore pins model + normalization + operator
as **one** decision: `BAAI/bge-m3`, normalized, cosine distance `<=>` — and the constants
live next to each other in [`src/rag/embed/`](../../src/rag/embed/__init__.py) and
[`src/rag/load/`](../../src/rag/load/__init__.py). Cosine distance is `1 − cosine
similarity`: 0 means same direction, 1 means orthogonal — the numbers `make query` prints.

A worked case on two unit vectors makes that identity concrete:

```text
a = (1.0, 0.0)   b = (0.6, 0.8)     # both unit length: 0.6² + 0.8² = 1
a · b   = 1.0·0.6 + 0.0·0.8 = 0.6   # dot product = cosine similarity, since both are unit
1 − 0.6 = 0.4                       # cosine distance — the number <=> returns
```

## Dense vs sparse: what dense retrieval is bad at

The mirror image of dense is the **sparse embedding**: a vocabulary-sized, mostly-zero
vector that weights explicit terms — classically BM25's term statistics, or a learned
expansion (SPLADE). Sparse retrieval is literal: it excels exactly where dense retrieval
is weakest — exact identifiers, rare terms, „§ 146a" as a string — and fails where dense
shines (paraphrase, synonymy, cross-lingual drift). The benchmark numbers in the model
decision make the trade concrete: strong dense models score high on question-answer
retrieval yet collapse on the legal-domain GerDaLIR task, and a bigger dense model does
not fix it. The recorded answer is not a better vector but **both**: dense + BM25-style
sparse retrieval fused with RRF — Backlog 2, with its own chapter when it lands.

## Where this leaves the pipeline

The [embed stage](../stages/embed.md) maps each chunk record to one normalized
1024-float vector and writes them as inspectable JSON lines; the
[load stage](../stages/load.md) stores them next to the chunk's text and metadata and
indexes them for nearest-neighbor search — how a database searches millions of vectors
without comparing against every one is the [vector-indexes chapter](vector-indexes.md).
