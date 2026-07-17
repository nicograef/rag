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
below; trivial choices stay one-line table rows.

| Decision            | Choice                                                        |
| ------------------- | ------------------------------------------------------------- |
| Python tooling      | uv (venv, lockfile, `uv run`)                                 |
| Corpus acquisition  | Official gesetze-im-internet.de XML → Markdown via Python     |
| MVP corpus          | AO, UStG, KassenSichV, GG                                     |
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

**Embedding model — BAAI/bge-m3** (decided 2026-07-14): the model, embedding
normalization, and pgvector distance operator, pinned together for Phase 3 and everything
downstream.

- **Context:** Phase 3 needs an open-license, multilingual, CPU-capable
  sentence-transformers model; the choice fixes the vector dimension, normalization, and
  distance operator for the store, and Phase 4 embeds questions with the same model.
  Candidates per roadmap: the multilingual-e5 family, jina-embeddings-v2-base-de, bge-m3.
  All facts below were verified live 2026-07-14 — primary reachable sources: the official
  MTEB model metadata and results repos, microsoft/unilm (E5), FlagOpen/FlagEmbedding
  (bge-m3); huggingface.co was unreachable from the implementing session (egress policy),
  so model-card claims were corroborated via the MTEB metadata and search snippets.
- **Choice:** **`BAAI/bge-m3`** — MIT license, 568 M parameters (≈ 2.2 GB fp32), dense
  dimension **1024**, input limit **8192 tokens**, no query/passage prefixes. Vectors are
  **normalized** (the model's own default; its README scores by inner product of normalized
  vectors, i.e. cosine), so the pinned pgvector operator is **cosine distance `<=>`** with
  an HNSW index on `vector_cosine_ops` (pgvector defaults `m=16`, `ef_construction=64` —
  no reason to deviate at MVP corpus scale). The values live as constants in
  [`src/rag/embed/`](../src/rag/embed/__init__.py) and [`src/rag/load/`](../src/rag/load/__init__.py).
- **Why bge-m3:**
  - **Chunk fit.** The Phase 2 chunk size (2000 chars ≈ 500–700 XLM-RoBERTa tokens for
    German prose; the one atomic 13 k-char table ≈ 5 k tokens) fits 8192 tokens with 4–8×
    headroom even at a conservative 2 chars/token — no chunk-stage change, no silent
    truncation. The e5 family's 512-token cap would truncate the largest chunks or force a
    token-measured re-chunk.
  - **German retrieval, verified.** Best verified German score among the candidates:
    MIRACL-de dense nDCG@10 **56.7** vs multilingual-e5-large 56.4, -base 52.1, -small 48.8
    (bge-m3 paper Table 1 as corrected 2024-07-01, table image in the FlagEmbedding repo;
    e5 numbers from the MTEB results repo).
  - **One code path.** No `query:`/`passage:` prefixes ("no longer requires adding
    instructions to the queries" — bge-m3 README), so ingest and question embedding share
    one interface with nothing to get wrong between them.
  - **Late chunking stays live.** bge-m3 is the only candidate exposing token-level
    (ColBERT) vectors and sparse lexical weights from the same pass — the Backlog 6
    late-chunking condition is met, and built-in hybrid scoring is a future option.
- **Alternatives weighed:** `multilingual-e5-base` — the family's best size/quality ratio
  (278 M, MIRACL-de 52.1) and strong GermanQuAD (nDCG@10 0.94), but the 512-token cap
  conflicts with the Phase 2 chunk size and the required prefixes split the embedding code
  path; `jina-embeddings-v2-base-de` — the right shape (8192 tokens, 161 M, Apache-2.0)
  but its German benchmark numbers could not be verified from any reachable primary source
  (absent from the MTEB results repo) — rejected as unverifiable, not as deficient;
  `multilingual-e5-small/large` — same 512-token cap, small measurably weaker, large costs
  as much as bge-m3 without its features. Sobering context: every e5 size collapses on the
  legal-domain GerDaLIR benchmark (nDCG@10 0.065–0.157, and larger is not better) — dense
  retrieval has a domain ceiling here, and the recorded answer is hybrid BM25 + RRF
  (Backlog 2), not a bigger dense model.
- **16 GB floor & measurements** (measured 2026-07-14 on a 4-core Intel Xeon @ 2.80 GHz,
  16 GB RAM, CPU-only — a `make embed` run over the full four-law corpus, 1,225 chunks):
  - **Throughput:** 17 min 26 s wall with the model cached — ≈ **1.2 chunks/s**; the first
    run including the model download took 18 min 30 s.
  - **Peak memory:** ≈ **9.1 GiB** peak RSS of the embed process (`/usr/bin/time -v`,
    both runs within 1 %) — CPU batch inference over sequences up to 3,784 tokens, well
    above the idle fp32 footprint. Fits the 16 GB floor with ≈ 6.5 GiB headroom, and the
    peak is confined to offline ingestion: the online path embeds one short question at a
    time, so it never coexists with Phase 4's ≈ 5 GB LLM at this level.
  - **Download size:** the weights are 2.27 GB, but the first `make embed` filled
    `~/.cache/huggingface/` with **≈ 4.6 GB** — sentence-transformers resolved
    `pytorch_model.bin` (2.27 GB) and transformers additionally fetched the safetensors
    conversion from its own snapshot (2.27 GB), so the weights land twice. The README
    states the measured total.
  - **Tokenizer check on real chunks:** all 1,225 chunk texts through the model's own
    tokenizer (`XLMRobertaTokenizer`): min 11 / median 256 / max **3,784** tokens —
    **zero chunks above the 8,192-token limit**. The max is the atomic 13,011-char
    UStG "Anlage 2" table chunk (the entry's ≈ 5 k-token estimate above was
    conservative); the Phase 2 chunk size is confirmed, no chunk-stage change needed.
- **Consequences:** the `chunks.embedding` column is `vector(1024)`; question embedding in
  Phase 4 must use the same pinned model; retrieval-quality claims stay anecdotal until the
  evaluation harness (Backlog 1); the model download cost is stated in the README quick
  start.

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

**Corpus licensing — gesetze-im-internet.de** (verified live 2026-07-12): only the
normative law texts enter the corpus; footnotes and editorial apparatus stay out.

- **Context:** Phase 1's fetch stage ingests XML from gesetze-im-internet.de, and rule 3
  in [AGENTS.md](../AGENTS.md) demands that only public-domain or properly licensed
  sources enter the corpus. The licensing facts had so far been asserted from memory and
  a mirror; this entry pins them from the live site.
- **Facts (all verified live 2026-07-12):** the site's start page states: „Die
  Rechtsnormen in deutscher Sprache stehen in allen angebotenen Formaten zur freien
  Nutzung und Weiterverwendung zur Verfügung." (<https://www.gesetze-im-internet.de/> —
  note the wording differs slightly from the often-cited „in ihrer deutschsprachigen
  Fassung" variant). The statutory basis is § 5 Abs. 1 UrhG: „Gesetze, Verordnungen,
  amtliche Erlasse und Bekanntmachungen sowie Entscheidungen und amtlich verfaßte
  Leitsätze zu Entscheidungen genießen keinen urheberrechtlichen Schutz."
  (<https://www.gesetze-im-internet.de/urhg/__5.html>). The Hinweise page (section 6,
  „Download und Weiterverwertung der Normen") documents the XML offering and its DTD
  (`/dtd/1.01/gii-norm.dtd`) and adds no further licence terms. The download URLs of all
  four MVP laws respond and contain the expected GiI-Norm XML (`make fetch` run live the
  same day).
- **Choice:** the corpus may include the **normative texts** — norm headings and body
  text of the German-language laws. **Footnotes (`<fussnoten>`) and editorial apparatus
  (`<standangabe>`, status notes) are excluded**: they are editorial additions of the
  Dokumentationsstelle, covered neither clearly by § 5 Abs. 1 UrhG („Gesetze,
  Verordnungen …") nor unambiguously by the free-reuse statement („Die Rechtsnormen …"),
  so the conservative reading wins.
- **Consequences:** convert emits normative text only; the README's licensing wording
  (amtliche Werke, § 5 UrhG, public domain) is confirmed unchanged; the
  [fetch contract](stages/fetch.md) links here as its licensing basis. If a future
  source's terms are less clear, it does not enter the corpus (rule: never ingest
  sources with unclear licensing).
