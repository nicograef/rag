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

Goal: a config-driven fetcher that turns English Wikipedia article extracts into clean
Markdown on disk.

- Download each article's plain-text extract from the English Wikipedia **MediaWiki
  Action API** (TextExtracts: `action=query&prop=extracts|info&explaintext=1&exsectionformat=wiki`,
  `https://en.wikipedia.org/w/api.php`) — no key, no OAuth, one request per article with a
  descriptive User-Agent and `maxlag=5`. This text is CC BY-SA 4.0, not public domain — see
  the corpus-licensing decision below.
- MVP corpus (config-driven, extensible via [`clubs.toml`](../clubs.toml)): the 20 current
  Premier League clubs' English Wikipedia articles, keyed by a filesystem-safe slug
  (`arsenal`, `chelsea`, …) mapped to the exact article title.
- Convert each article's extract plus its provenance record into one Markdown file under
  `data/corpus/`, preserving structure: article → `==section==`/`===subsection===` wiki
  headings translated to ATX (`##`/`###`), lead paragraphs promoted to an `##
  Introduction` section, plus a front-matter header (slug, source title, source URL, fetch
  date). Non-prose apparatus sections (References, External links, See also, …) are
  dropped so only article prose reaches chunking.
- No Docling here: the API extract is already clean plain text — parsing it ourselves is
  lossless and teaches real document parsing. Docling enters later for messy PDFs
  (see backlog).
- Verify: re-runnable from clean checkout (fetch is idempotent, not deterministic —
  Wikipedia is a living corpus; convert is deterministic); unit tests on the extract →
  Markdown converter with a small fixture.

## Phase 2 — Structure-aware chunking ✅

Stage: **chunk**

Goal: split the Markdown corpus into retrieval units without destroying article structure.

- Chunk by **Wikipedia section** (a `##` heading and everything under it, including its
  `###` subsections) as the natural semantic unit; split an oversized section into ordered
  parts — subsection groups with overlap, and a recursive-character fallback for a single
  overlong paragraph; merge consecutive tiny sections into one chunk.
- Each chunk carries **metadata**: `source_title`, `section` (the section name), `section_path`,
  a human-readable `citation`, source URL, fetch date — the basis for later filtering and
  citations.
- Learn here: why chunk size matters, recursive character splitting as the baseline,
  and why structure-aware beats fixed-size for Wikipedia's section structure.
- Later candidates: see backlog item 6 (advanced chunking strategies).

## Phase 3 — Embed & load (vector store) ✅

Stages: **embed**, **load**

Goal: embeddings for every chunk, stored and indexed in Postgres.

- Research and pick an **open-license, English, CPU-capable** embedding model from Hugging
  Face via sentence-transformers — decide by English retrieval quality, dimension count,
  and CPU latency; verify against current model cards, not memory (landed:
  `BAAI/bge-small-en-v1.5`, dense dimension 384 — see the dated decision below).
- Schema: `chunks` table (text, metadata columns, `vector(384)` column); **HNSW index**
  (speed/recall trade-off — learn how it works), cosine distance matching the model's
  normalized embeddings. The theory chapter frames HNSW within approximate nearest neighbor
  search and contrasts IVF and vector quantization (both theory-only — see the
  [concept map](concepts.md)).
- The dated model decision pins **embedding normalization** and the pgvector distance
  operator together with the model choice (verified against the model card).
- Batch loader: idempotent re-runs (upsert by chunk identity), embedding in batches on CPU.
- Verify: `SELECT ... ORDER BY embedding <=> query` returns plausible sections for
  hand-written test queries.

## Phase 4 — Online PoC (CLI question answering) ✅

Stages: **retrieve**, **assemble**, **generate**

Goal: close the loop — ask an English question about a football club in the terminal, get
a grounded answer.

- Runtime: **Ollama** (localhost HTTP API) serving an open-weight GGUF model that fits the
  4-core/8 GB CPU floor (landed: `granite4:micro`, ~3B dense, Q4_K_M ≈ 2.1 GB — see the
  dated decision below). Runs as a second Compose service next to Postgres, models in a
  named volume (see Docker decision below).
- Flow: CLI prompt → embed the question (same model as Phase 3) → top-k vector search →
  **prompt assembly** (English system instructions + retrieved chunks with citations and a
  CC BY-SA attribution notice + question) → Ollama → print answer + sources to the
  terminal.
- The system instructions carry **grounding and abstention directives** ("answer only from
  the provided excerpts; say so if they don't contain the answer") — the prompt-level half
  of hallucination prevention.
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
   its expected chunks (club/section); measure context relevance, faithfulness/groundedness,
   and answer relevance with a local open-weight LLM judge via Ollama (LLM-as-a-judge,
   reference-free — built by hand, no RAGAS), plus deterministic rank metrics
   against the labeled chunks (Recall@K, Precision@K, MRR, NDCG) — so every later
   enhancement is measurable.
2. **Hybrid search:** Postgres full-text (BM25-style sparse retrieval) alongside dense
   vectors; fuse with **Reciprocal Rank Fusion (RRF)** — motivated by exact-match on club
   names and years, where dense embeddings alone are weak (see the embedding-model
   decision's accepted trade). Theory contrasts lexical sparse retrieval with learned sparse
   embeddings (SPLADE) and RRF with score normalization / weighted fusion.
3. **Metadata filtering:** scoped retrieval — filter vector/hybrid search by chunk metadata
   (club, section, section path) via SQL predicates combined with pgvector; learn pre- vs
   post-filtering and how filters interact with HNSW recall.
4. **Query transformation:** query rewriting, query expansion (club nicknames and
   aliases, e.g. "Spurs" for Tottenham Hotspur), multi-query retrieval (variants fused via
   RRF), HyDE, query decomposition — including sequential multi-hop retrieval — keyword
   extraction for hard facts (club names, years, exact terms), and a lightweight query
   router (explicit club-name mentions go to exact metadata lookup). Theory covers
   step-back prompting as the contrast.
5. **Cross-encoder reranking** of top-k results (open-source model, CPU). Theory maps the
   bi-encoder → late-interaction (ColBERT) → cross-encoder spectrum.
6. **Advanced chunking strategies:** semantic chunking, hierarchical parent-child chunks
   (subsection-level children retrieved, section-level parents assembled), contextual
   chunk enrichment (prepend heading-path/article context; optionally local-LLM chunk
   summaries), and late chunking if a future embedding model exposes token embeddings
   (not met by the pinned bge-small-en-v1.5 — see its dated decision) — each compared
   against the Phase 2 baseline via the evaluate harness.
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
11. **Graph-augmented retrieval (GraphRAG):** extract the explicit article-to-article wiki
    links between club articles into a lightweight graph (plain Postgres tables, no graph
    database) and expand retrieval hits with their graph neighbors. Theory contrasts full
    LLM-entity-extraction GraphRAG and its CPU cost.
12. **Docling ingestion path** for messy PDF sources — a second connector proving the
    pipeline's interfaces: layout analysis, reading order, tables, page-level chunking with
    page-number citations, and a dated scoping decision on figures/images. Theory covers
    classic OCR + layout analysis vs end-to-end document-intelligence VLMs.
13. **Incremental ingestion:** detect changed articles (content hash, since Wikipedia
    extracts carry no stable build-date field) and reprocess only those through
    convert → chunk → embed → load, skipping unchanged chunks at the upsert boundary.
14. **Drift detection:** embedding drift monitoring after model or corpus updates —
    edited Wikipedia articles are this corpus's real drift trigger; the gold-set metrics
    from item 1 are the signal.
15. **Chat web app:** Go backend + React frontend on top of the proven pipeline.

## Decisions

Recorded as they are made, starting with the clarification rounds at project start (2026-07-10).
Convention: a decision that binds future work or makes a deliberate exception gets a dated entry
with its reasoning — context, choice, weighed alternatives, consequences — like the Docker block
below; trivial choices stay one-line table rows. **Prune, don't archive:** when a later decision
supersedes an earlier one, the earlier entry is rewritten in place to the choice that now holds —
not kept beside its replacement as dated history — so the log states what is true now, not an
audit trail of what was once thought. Dated *verification* stamps of live facts stay (the rule
prunes superseded decisions, not the verify-before-claiming discipline).

| Decision            | Choice                                                        |
| ------------------- | ------------------------------------------------------------- |
| Python tooling      | uv (venv, lockfile, `uv run`)                                 |
| Corpus acquisition  | English Wikipedia article extracts (MediaWiki Action API) → Markdown via Python |
| MVP corpus          | The 20 current Premier League clubs' English Wikipedia articles |
| Docs language       | English (docs, code, corpus, and domain terms)                |
| LLM runtime (PoC)   | Ollama                                                        |
| Data in git         | None — `data/` fully gitignored, pipeline re-runnable         |
| Repository license  | MIT                                                           |
| Docker usage        | Stateful infrastructure only (see below)                      |
| Design floor        | 4-core / 8 GB CPU-only, no GPU — nothing may require more; the pinned models (bge-small-en ≈ 130 MB, granite4:micro ≈ 2.1 GB served) fit the budget, re-verified end to end on 2026-07-18 (see the embedding- and generation-model decisions) |

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

- **Context:** an external list of RAG concepts (ingestion → chunking →
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

**Embedding model — BAAI/bge-small-en-v1.5** (decided 2026-07-17): the model, dimension,
embedding normalization, and pgvector distance operator, pinned together for the embed stage
and everything downstream.

- **Context:** the pipeline embeds English Wikipedia article sections and question text with
  one open-license, CPU-capable sentence-transformers model, on the 4-core/8 GB floor. The
  choice fixes the vector dimension, normalization, and distance operator for the store, and
  the retrieve stage embeds questions with the same model.
- **Choice:** **`BAAI/bge-small-en-v1.5`** — MIT license, 33.4 M parameters, dense dimension
  **384**, input limit **512 tokens**, English. Vectors are **L2-normalized**, so the pinned
  pgvector operator is **cosine distance `<=>`** with an HNSW index on `vector_cosine_ops`
  (pgvector defaults `m=16`, `ef_construction=64` — no reason to deviate at MVP corpus scale).
  The model tag and batch size are env-overridable (`EMBED_MODEL_ID` / `EMBED_BATCH_SIZE`) with
  the pinned value as the default. The values live as constants in
  [`src/rag/embed/`](../src/rag/embed/__init__.py) and
  [`src/rag/load/`](../src/rag/load/__init__.py).
- **Why bge-small-en (facts verified live 2026-07-17 against the model card; the 384/512
  properties re-confirmed at runtime with the loaded model):**
  - **Fits the 8 GB floor.** A ≈ 130 MB download and a tiny CPU footprint sit comfortably
    under the 8 GB floor.
  - **English, matched to the corpus.** The corpus is English Wikipedia, so a strong
    English-only model beats a multilingual one on this corpus.
  - **Symmetric path.** v1.5 makes the query instruction prefix optional, so the pipeline uses
    **no instruction** for queries or passages — ingest and question embedding share one
    interface. The query-only prefix (`"Represent this sentence for searching relevant
    passages:"`, never on passages) is recorded as a documented recall-tuning lever, not the
    default.
  - **MIT-licensed**, satisfying the open-source-only rule with no attribution burden on the
    weights.
- **The 512-token consequence — chunk sizing is load-bearing.** bge-small-en's **512-token**
  cap leaves chunk size no slack. `max_chars` is
  pinned to 1200 characters, validated with the model's own tokenizer over the fetched corpus
  (densest ≈ 2.44 chars/token → ≤ ~492 tokens worst-case, observed max 375), and the embed
  token-guard is the hard backstop (see the [chunk contract](stages/chunk.md)).
- **8 GB-floor measurement (measured 2026-07-18, CPU-only, `make embed` over the 20-club
  corpus, 1333 chunks, on the available 8-core/5.7 GB machine — a tighter RAM budget than the
  8 GB floor):** the model download is **≈ 130 MB** (129 MiB in `~/.cache/huggingface/`, a
  single snapshot — no double-fetch); the full embed run took **≈ 3 min 49 s** wall (≈ 5.8
  chunks/s) at batch 16. bge-small-en's tiny footprint fits the
  8 GB budget with room to spare. A `make query` spot-check returned plausible sections ranked
  by cosine distance (e.g. "Which stadium does Arsenal play at?" → the `Arsenal F.C. — Stadiums`
  chunks at distance ≈ 0.20).
- **Accepted trade:** a small English model like bge-small has modest absolute retrieval
  quality — a deliberate trade for a model that fits 4-core/8 GB and reads to an English
  audience, not a silent downgrade. Exact-match on club names and years still motivates hybrid
  BM25 + RRF (Backlog 2).
- **Late chunking does not apply.** bge-small-en exposes no token-level (ColBERT) vectors and
  caps at 512 tokens, so the Backlog 6 late-chunking precondition (a long-context model with
  token embeddings) is **not met** — recorded in the concept map.
- **Consequences:** the `chunks.embedding` column is `vector(384)`; a table left at an earlier
  dimension is refused with a `make reset` hint (the load dimension guard); question embedding
  in the retrieve stage uses the same pinned model; retrieval-quality claims stay anecdotal
  until the evaluation harness (Backlog 1); the model download cost is stated in the README.

**Generation model — granite4:micro** (decided 2026-07-18): the LLM, its context length,
decoding parameters, and the retrieval top-k, pinned together for the online path.

- **Context:** the online path answers English questions about football clubs, grounded in
  the retrieved Wikipedia sections, with an open-weight instruct model served by Ollama on
  the **4-core/8 GB** floor. Rule 1 in [AGENTS.md](../AGENTS.md) holds to a strict open-source
  bar (Apache-2.0/MIT-class weights only). Retrieval k, chunk size, and the model's served
  context length form one context budget, so they are pinned in one decision. External claims
  below were verified live 2026-07-18 against the Granite model card and the ollama.com library,
  and re-confirmed by an empirical run of the pinned image.
- **Choice:** **`granite4:micro`** (IBM Granite-4.0-Micro, ~3 B dense decoder-only, Q4_K_M
  GGUF ≈ 2.1 GB, Apache-2.0 per the model card), served by the pinned Compose service
  `ollama/ollama:0.32.1`; **`num_ctx` 4096** with **`num_predict` 512**; **greedy decoding**
  (temperature 0.0, top_p 1.0, top_k 0, min_p 0.0) plus a pinned **seed 42**; retrieval
  **top-k 5**. The model tag and context/answer budget are env-overridable (`LLM_MODEL_TAG` /
  `LLM_NUM_CTX` / `LLM_NUM_PREDICT`) with the pinned value as the default. The values live as
  constants in [`src/rag/generate/`](../src/rag/generate/__init__.py) and
  [`src/rag/retrieve/`](../src/rag/retrieve/__init__.py); the Makefile derives the
  `make llm-pull` tag from the constant instead of duplicating the string.
- **Why granite4:micro (verified live 2026-07-18):**
  - **Fits the 8 GB floor.** Q4_K_M ≈ 2.1 GB of weights plus the KV cache for a 4096-token
    window fit the 8 GB budget with headroom. This RAM fit, not raw speed, is the win.
  - **Strict license, verified.** Weights Apache-2.0 (model card, 2026-07-18), in the official
    Ollama library — no community re-uploads of unverifiable provenance.
  - **Plain instruct — no reasoning traces.** Granite-4.0-Micro emits no `<think>`/reasoning
    output, so the client stays minimal and chain-of-thought remains a deliberate theory topic
    ([llm-generation](theory/llm-generation.md)) rather than an accidental default. RAG is a
    documented intended use of the model.
- **Greedy decoding (a deliberate deviation to record):** the Granite card gives **no
  task-specific sampling guidance**. A grounded, citation-bound task wants the single
  most-likely, reproducible answer over sampled variety, so decoding is **greedy** (temperature
  0). At temperature 0 the top_p/top_k/min_p knobs are inert; the seed is pinned so the rare
  tie breaks identically across runs.
- **Context budget (measured 2026-07-18):** `num_ctx` 4096 − `num_predict` 512 reserved for
  the answer = 3,584 prompt tokens. Assemble guards at **8,243 characters** = 3,584 × a
  **2.3 chars/token** floor, where 2.3 sits below the densest chunk measured with granite's
  own tokenizer over the corpus (≈ 2.31 chars/token; median ≈ 4.45), so a prompt that passes
  the guard fits the window. A realistic k=5 prompt is ≈ 1,040 tokens — far under the budget;
  an oversized prompt trips the loud `AssembleError` rather than being silently truncated.
  Token-exact budgeting with the served model's tokenizer stays Backlog 7.
- **Serving validation (measured 2026-07-18, CPU-only, on the available 8-core/5.7 GB machine
  — a tighter RAM budget than the 8 GB floor):** `make llm-pull` (granite4:micro) then
  `make ask Q="Which stadium does Arsenal play at?"` streamed a grounded, cited answer —
  **"Arsenal plays at the Emirates Stadium [3]."** — with an 840-token prompt in ≈ 51 s prefill
  + ≈ 1.6 s decode (≈ 58 s total). An abstention probe, `make ask Q="What is the capital of
  France?"`, correctly declined ("the excerpts provided do not contain any information about
  the capital of France … impossible to answer this question"). granite is **prefill-bound, not
  swap-bound** (decode ≈ 7 tok/s) — the
  RAM fit that motivated the choice, confirmed. A pristine no-swap run on exact 4-core/8 GB
  hardware was not performed; the 4-core/8 GB is the design floor, justified by the ≈ 2.1 GB
  served footprint.
- **Consequences:** the online path pins greedy, num_ctx 4096; the model download cost
  (≈ 2.1 GB) is stated in the
  README quick start; if per-question latency on 8 GB is still too high, the next lever is the
  1 B tier (a smaller Granite/Llama), not further config — noted, not adopted.

**Corpus licensing — English Wikipedia** (verified live 2026-07-17): the article text is
CC BY-SA 4.0, properly licensed for the playbook's gitignored, runtime-fetched use.

- **Context:** the fetch stage ingests article extracts from the English Wikipedia
  MediaWiki Action API, and rule 3 in [AGENTS.md](../AGENTS.md) demands that only
  public-domain or properly licensed sources enter the corpus. Wikipedia text is a genuine
  step down from a public-domain corpus, so its terms are pinned here from primary sources
  rather than asserted.
- **Facts (all verified live 2026-07-17):** English Wikipedia prose is licensed
  **CC BY-SA 4.0** (Wikipedia:Copyrights + the Wikimedia Terms of Use; the GFDL is
  dual-listed as a legacy licence), **not public domain**. Attribution is satisfied by a
  **hyperlink to the article** — its history page lists every author. Share-alike (copyleft)
  binds only **distributed adapted text**: not verbatim copies, and not the surrounding code.
- **Choice:** the corpus is **CC BY-SA 4.0, used under attribution**. Because `data/` is
  gitignored and the corpus is **fetched at runtime, never redistributed in git**, storing
  the text in the local database is **not a distribution event** — no copyleft attaches to
  the repo. Displaying a retrieved excerpt *is* a reproduction, so the online path shows the
  **article link and a CC BY-SA licence notice at the point of display** (the assemble/ask
  stage). This qualifies as "properly licensed" under rule 3.
- **Consequences:** convert emits **prose only** — TextExtracts already strips images,
  flattens tables and lists, and drops the reference apparatus, and convert drops the
  remaining non-prose apparatus sections (References, External links, See also, …); the
  online path carries the attribution obligation to the point of display; the
  [fetch contract](stages/fetch.md) links here as its licensing basis. Whether an LLM
  *paraphrase* is "adapted material" is legally unsettled; the attribution posture
  neutralizes it. If a future source's terms are less clear, it does not enter the corpus
  (rule: never ingest sources with unclear licensing).
