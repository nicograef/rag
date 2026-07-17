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

Goal: a config-driven fetcher that turns official law XML into clean Markdown on disk.

- Download official XML from gesetze-im-internet.de per law
  (`https://www.gesetze-im-internet.de/<slug>/xml.zip`). These texts are amtliche Werke
  (§ 5 UrhG) — public domain.
- MVP corpus (config-driven, extensible): `ao_1977` (AO), `ustg_1980` (UStG),
  `kassensichv` (KassenSichV), `gg` (GG).
- Parse the `gii-norm` XML and emit one Markdown file per law under `data/corpus/`,
  preserving structure: law → Buch/Abschnitt → § → Absatz, plus a metadata header
  (law abbreviation, full title, fetch date, source URL).
- No Docling here: the official XML is already structured — parsing it ourselves is
  lossless and teaches real document parsing. Docling enters later for messy PDFs
  (see backlog).
- Verify: re-runnable from clean checkout; deterministic output; unit tests on the
  XML → Markdown converter with a small fixture.

## Phase 2 — Structure-aware chunking ✅

Stage: **chunk**

Goal: split the Markdown corpus into retrieval units without destroying legal structure.

- Chunk by **§ (Paragraph)** as the natural semantic unit; split oversized §§ by Absatz
  with overlap; merge tiny ones.
- Each chunk carries **metadata**: law, § number, heading path (Buch/Abschnitt), source
  URL, fetch date — the basis for later filtering and citations.
- Learn here: why chunk size matters, recursive character splitting as the baseline,
  and why structure-aware beats fixed-size for law texts.
- Later candidates: see backlog item 6 (advanced chunking strategies).

## Phase 3 — Embed & load (vector store) ✅

Stages: **embed**, **load**

Goal: embeddings for every chunk, stored and indexed in Postgres.

- Research and pick an **open-license, multilingual, CPU-capable** embedding model from
  Hugging Face via sentence-transformers (candidates to evaluate: multilingual-e5 family,
  jina-embeddings-de, bge-m3 — decide by German retrieval quality, dimension count, and
  CPU latency; verify against current model cards, not memory).
- Schema: `chunks` table (text, metadata columns, `vector` column); **HNSW index**
  (speed/recall trade-off — learn how it works). The theory chapter frames HNSW within
  approximate nearest neighbor search and contrasts IVF and vector quantization (both
  theory-only — see the [concept map](concepts.md)).
- The dated model decision pins **embedding normalization** and the pgvector distance
  operator together with the model choice (verified against the model card).
- Batch loader: idempotent re-runs (upsert by chunk identity), embedding in batches on CPU.
- Verify: `SELECT ... ORDER BY embedding <=> query` returns plausible §§ for hand-written
  test queries.

## Phase 4 — Online PoC (CLI question answering) ✅

Stages: **retrieve**, **assemble**, **generate**

Goal: close the loop — ask a legal question in the terminal, get a grounded answer.

- Runtime: **Ollama** (localhost HTTP API) serving an open-weight GGUF model that fits
  8-core/16 GB CPU (candidate size: 7–8B quantized; pick during the phase). Runs as a
  second Compose service next to Postgres, models in a named volume (see Docker decision
  below).
- Flow: CLI prompt → embed the question (same model as Phase 3) → top-k vector search →
  **prompt assembly** (system instructions + retrieved chunks with citations + question)
  → Ollama → print answer + sources to the terminal.
- The system instructions carry **grounding and abstention directives** ("answer only from
  the provided §§; say so if they don't contain the answer") — the prompt-level half of
  hallucination prevention.
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
   its expected §§; measure context relevance, faithfulness/groundedness, and answer
   relevance with a local open-weight LLM judge via Ollama (LLM-as-a-judge,
   reference-free — built by hand, no RAGAS), plus deterministic rank metrics
   against the labeled §§ (Recall@K, Precision@K, MRR, NDCG) — so every later enhancement
   is measurable.
2. **Hybrid search:** Postgres full-text (BM25-style sparse retrieval) alongside dense
   vectors; fuse with **Reciprocal Rank Fusion (RRF)**. Theory contrasts lexical sparse
   retrieval with learned sparse embeddings (SPLADE) and RRF with score
   normalization / weighted fusion.
3. **Metadata filtering:** scoped retrieval — filter vector/hybrid search by chunk metadata
   (law, § number, heading path) via SQL predicates combined with pgvector; learn pre- vs
   post-filtering and how filters interact with HNSW recall.
4. **Query transformation:** query rewriting, query expansion (synonyms and domain
   abbreviations), multi-query retrieval (variants fused via RRF), HyDE, query
   decomposition — including sequential multi-hop retrieval — keyword extraction for hard
   facts (§ numbers, exact terms), and a lightweight query router (explicit § citations go
   to exact metadata lookup). Theory covers step-back prompting as the contrast.
5. **Cross-encoder reranking** of top-k results (open-source model, CPU). Theory maps the
   bi-encoder → late-interaction (ColBERT) → cross-encoder spectrum.
6. **Advanced chunking strategies:** semantic chunking, hierarchical parent-child chunks
   (Absatz-level children retrieved, §-level parents assembled), contextual chunk
   enrichment (prepend heading-path/law context; optionally local-LLM chunk summaries),
   and late chunking if the Phase 3 embedding model exposes token embeddings — each
   compared against the Phase 2 baseline via the evaluate harness.
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
11. **Graph-augmented retrieval (GraphRAG):** extract the explicit §-to-§ and law-to-law
    citation links into a lightweight graph (plain Postgres tables, no graph database) and
    expand retrieval hits with their graph neighbors. Theory contrasts full
    LLM-entity-extraction GraphRAG and its CPU cost.
12. **Docling ingestion path** for messy PDF sources — a second connector proving the
    pipeline's interfaces: layout analysis, reading order, tables, page-level chunking with
    page-number citations, and a dated scoping decision on figures/images. Theory covers
    classic OCR + layout analysis vs end-to-end document-intelligence VLMs.
13. **Incremental ingestion:** detect changed laws (XML builddate or content hash) and
    reprocess only those through convert → chunk → embed → load, skipping unchanged chunks
    at the upsert boundary.
14. **Drift detection:** embedding drift monitoring after model or corpus updates —
    amended laws are this corpus's real drift trigger; the gold-set metrics from item 1
    are the signal.
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
| Docs language       | English (German only for corpus + domain terms)               |
| LLM runtime (PoC)   | Ollama                                                        |
| Data in git         | None — `data/` fully gitignored, pipeline re-runnable         |
| Repository license  | MIT                                                           |
| Docker usage        | Stateful infrastructure only (see below)                      |
| Interim runtime     | GitHub Codespace (16 vCPU / 32 GB) until a VPS is available; the 8-core/16 GB CPU-only VM stays the design floor — nothing may require more |

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

> **Assumption:** the embedding model is chosen in Phase 3 after researching current model
> cards (open license, multilingual, CPU-capable) — not fixed now.

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
  the retrieve stage embeds questions with the same model. This supersedes the former
  multilingual bge-m3 choice, which was sized for a German corpus and a 16 GB floor.
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
  - **Fits the 8 GB floor.** A ≈ 130 MB download and a tiny CPU footprint — the swap-bound
    memory pressure of bge-m3 (≈ 9 GiB peak RSS, over the 8 GB floor) is gone.
  - **English, matched to the corpus.** The corpus is now English Wikipedia, so a strong
    English-only model beats a multilingual one carried for a language the corpus no longer uses.
  - **Symmetric path.** v1.5 makes the query instruction prefix optional, so the pipeline uses
    **no instruction** for queries or passages — ingest and question embedding share one
    interface. The query-only prefix (`"Represent this sentence for searching relevant
    passages:"`, never on passages) is recorded as a documented recall-tuning lever, not the
    default.
  - **MIT-licensed**, satisfying the open-source-only rule with no attribution burden on the
    weights.
- **The 512-token consequence — chunk sizing is now load-bearing.** bge-m3's 8192-token
  window left chunk size slack; bge-small-en's **512-token** cap does not. `max_chars` is
  pinned to 1200 characters, validated with the model's own tokenizer over the fetched corpus
  (densest ≈ 2.44 chars/token → ≤ ~492 tokens worst-case, observed max 375), and the embed
  token-guard is the hard backstop (see the [chunk contract](stages/chunk.md)).
- **8 GB-floor measurement (measured 2026-07-17, CPU-only, `make embed` over the 20-club
  corpus, 1333 chunks):** the model download is **≈ 130 MB** (129 MiB in
  `~/.cache/huggingface/`, a single snapshot — no double-fetch); the full embed run took
  **≈ 3 min 49 s** wall (≈ 5.8 chunks/s) at batch 16, comfortably within the 8 GB floor
  without swap. A `make query` spot-check returned plausible sections ranked by cosine
  distance (e.g. "Which stadium does Arsenal play at?" → the `Arsenal F.C. — Stadiums` chunks
  at distance ≈ 0.20).
- **Accepted trade:** bge-small is far weaker than bge-m3 in absolute retrieval quality — a
  deliberate trade for a model that fits 4-core/8 GB and reads to an English audience, not a
  silent downgrade. Exact-match on club names and years still motivates hybrid BM25 + RRF
  (Backlog 2).
- **Late chunking no longer applies.** Unlike bge-m3, bge-small-en exposes no token-level
  (ColBERT) vectors and caps at 512 tokens, so the Backlog 6 late-chunking precondition (a
  long-context model with token embeddings) is **no longer met** — recorded in the concept map.
- **Consequences:** the `chunks.embedding` column is `vector(384)`; a table left at an earlier
  dimension is refused with a `make reset` hint (the load dimension guard); question embedding
  in the retrieve stage uses the same pinned model; retrieval-quality claims stay anecdotal
  until the evaluation harness (Backlog 1); the model download cost is stated in the README.

**Generation model — qwen3:4b-instruct** (decided 2026-07-17): the LLM, its context
length, decoding parameters, and the retrieval top-k, pinned together for Phase 4's
online path.

- **Context:** Phase 4 needs an open-weight instruct model served by Ollama on the
  CPU-only floor, answering German legal questions grounded in retrieved §§. During
  planning the roadmap's "open-weight" wording was resolved to a **strict open-source
  bar** — Apache-2.0/MIT-class weights only, per rule 1 in [AGENTS.md](../AGENTS.md);
  community-licensed weights (Llama, Gemma) are out even where German benchmarks favor
  them. Retrieval k, chunk sizes, and the model's context length form one context
  budget, so they are pinned in one decision. All external claims below were verified
  live 2026-07-17 — Hugging Face model cards, ollama.com library pages, the EuroEval
  German leaderboard (v17.6.0), and the Ollama docs corroborated by an empirical probe
  of the pinned image.
- **Choice:** **`qwen3:4b-instruct`** (Qwen3-4B-Instruct-2507, Q4_K_M GGUF, 2.5 GB
  download, Apache-2.0 per the model card), served by the pinned Compose service
  `ollama/ollama:0.32.1`; **`num_ctx` 8192** with **`num_predict` 1024**; decoding per
  the model card's recommendation — **temperature 0.7, top_p 0.8, top_k 20, min_p 0** —
  plus a pinned **seed 42**; retrieval **top-k 5**. The values live as constants in
  [`src/rag/generate/`](../src/rag/generate/__init__.py) and
  [`src/rag/retrieve/`](../src/rag/retrieve/__init__.py); the Makefile derives the
  `make llm-pull` tag from the constant instead of duplicating the string.
- **Why qwen3:4b-instruct:**
  - **Strict license, verified.** Weights Apache-2.0 (HF card, 2026-07-17), in the
    official Ollama library — no community re-uploads of unverifiable provenance.
  - **Best verified German in its class.** EuroEval German (accessed 2026-07-17,
    mean rank, lower is better): Qwen3-4B no-thinking ≈ 2.22, ahead of
    granite-4.0-micro 2.28, SmolLM3-3B 2.75, EuroLLM-1.7B 3.42. The Apache 7–8B tier
    tops out at Qwen3-8B (2.09) — see the floor note below.
  - **Non-thinking by design.** The 2507 Instruct build emits no `<think>` traces, so
    the client stays minimal (no `think` field, nothing to strip) and chain-of-thought
    remains a deliberate theory topic ([llm-generation](theory/llm-generation.md))
    instead of an accidental default.
  - **Hardware honesty.** This phase was implemented and verified on an 8-core /
    5.7 GB machine — *below* the playbook's 16 GB design floor. A 7–8B Q4 model
    (4.4–5.2 GB weights) cannot serve there at all, and the playbook pins only what it
    actually ran end to end (verify before claiming). On a 16 GB machine,
    **`qwen3:8b`** (Apache-2.0, the best strict-open German score) is the documented
    upgrade path: swap `MODEL_TAG`, re-run `make llm-pull` and the spot-check.
- **Deviation from temperature 0:** the plan defaulted to greedy decoding unless
  in-phase research recorded a reason to deviate. It did: the Qwen3 cards recommend the
  sampled profile above and warn against greedy decoding for this family
  (degeneration/repetition). The pinned seed keeps runs repeatable on one machine and
  Ollama version instead.
- **Context budget (measured 2026-07-17):** all 1,225 corpus chunks through the pinned
  model's own tokenizer: min 13 / median 352 / max **4,717** tokens — the max is the
  atomic 13,011-char UStG "Anlage 2" table (2.758 chars/token; Qwen's BPE is denser on
  this corpus than bge-m3's XLM-R, which counted 3,784 for the same chunk). Budget:
  8,192 `num_ctx` − 1,024 reserved for the answer = 7,168 prompt tokens; assemble
  guards at **17,920 characters** = 7,168 × a 2.5 chars/token floor. The floor sits
  below the 2.758 minimum measured on any chunk large enough to matter near the
  boundary (tiny chunks reach ratios down to 1.85 but cannot approach the budget), so
  a prompt that passes the guard fits the window. The worst realistic k=5 prompt
  (Anlage 2 plus four median §§) ≈ 6.4 k tokens — fits; the pathological
  five-largest-chunks case (7,847 tokens of context alone) trips the loud
  `AssembleError` instead of being silently truncated. Token-exact budgeting with the
  served model's tokenizer stays Backlog 7.
- **Alternatives weighed:** `granite4:micro` (3B, Apache-2.0, German officially listed,
  2.1 GB — the RAM-friendliest fallback, EuroEval 2.28 just behind); `qwen3:8b` (best
  strict-open German, needs the 16 GB floor — recorded as the upgrade path, not
  rejected); `qwen2.5:7b` (Apache-2.0, card-only German evidence, superseded by Qwen3);
  `mistral:7b` (Apache-2.0 but no official multilingual/German claim and weak German
  scores); Teuken-7B-instruct-commercial-v0.4 (Apache-2.0 — its research sibling is
  not — but a 4k context too small for multi-§ prompts, not in the official Ollama
  library, and clearly weaker EuroEval German); EuroLLM-9B (Apache-2.0, 4k native
  context, not in the official library); SmolLM3-3B (Apache-2.0, weaker German,
  thinking on by default, community-only Ollama uploads).
- **Measured on this machine (8 cores, 5.7 GB RAM + 4 GB swap, CPU-only, 2026-07-17;
  the five spot-check runs in the [generate contract](stages/generate.md#verification)):**
  - **Serving memory:** `ollama ps` reports **3.9 GB** for qwen3:4b-instruct at
    `num_ctx` 8192 — 2.5 GB weights plus the f16 KV cache and runtime overhead.
  - **Throughput:** prefill **7–11 tok/s**, decode **2.0–3.5 tok/s**; per-question wall
    time 2:17–12:48 for prompts of 970–2,874 tokens and answers of 71–1,024 tokens.
    Streaming is what keeps those minutes legible. These numbers are swap-bound, not
    representative of the 16 GB floor: during generation the machine's RAM was
    effectively full with swap peaking at its 4 GB limit.
  - **Query-time coexistence:** the `ask` process peaked at 1.4–2.0 GiB RSS (bge-m3
    goes cold after embedding the one question and pages out while the LLM decodes);
    Ollama held 3.9 GB; Postgres was idle. The working sets sum to ≈ 8–9.5 GiB — inside
    the 16 GB floor with headroom, but only runnable here through swap, which is
    exactly where the low tok/s comes from.
  - **Offline caveat:** the pinned `make embed` batch size (32) OOM-crashed on this
    machine on UStG's long-sequence batch; a reduced batch (4) completed the identical
    artifacts in 1:08 h at 2.8 GiB peak RSS. The 16 GB-floor embed measurement remains
    the 2026-07-14 entry (9.1 GiB peak at batch 32).
- **Consequences:** Ollama is the second Compose service (image pinned, models in a
  named volume, same log caps — completing the 2026-07-10 Docker decision); `num_ctx`
  must be sent on every request — Ollama's CPU-only default context is 4,096 (verified
  2026-07-17), which would otherwise truncate silently; answer quality stays anecdotal
  until the evaluation harness (Backlog 1); the README quick start states the 2.5 GB
  model download and the image pull.

**Corpus licensing — English Wikipedia** (verified live 2026-07-17): the article text is
CC BY-SA 4.0, properly licensed for the playbook's gitignored, runtime-fetched use.

- **Context:** the fetch stage ingests article extracts from the English Wikipedia
  MediaWiki Action API, and rule 3 in [AGENTS.md](../AGENTS.md) demands that only
  public-domain or properly licensed sources enter the corpus. Wikipedia text is a genuine
  step down from the amtliche-Werke cleanliness of the former law corpus, so its terms are
  pinned here from primary sources rather than asserted.
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
  stage). This qualifies as "properly licensed" under rule 3; the § 5 UrhG public-domain
  basis of the former law corpus no longer applies.
- **Consequences:** convert emits **prose only** — TextExtracts already strips images,
  flattens tables and lists, and drops the reference apparatus, and convert drops the
  remaining non-prose apparatus sections (References, External links, See also, …); the
  online path carries the attribution obligation to the point of display; the
  [fetch contract](stages/fetch.md) links here as its licensing basis. Whether an LLM
  *paraphrase* is "adapted material" is legally unsettled; the attribution posture
  neutralizes it. If a future source's terms are less clear, it does not enter the corpus
  (rule: never ingest sources with unclear licensing).
