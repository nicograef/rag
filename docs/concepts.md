# RAG Concept Map

Every RAG concept, technique, and term the playbook tracks — one line of definition and the
place where it lives, or the recorded reason it deliberately doesn't. This is the project's
ubiquitous language: docs, code, and commits use exactly these names. Coverage audited
2026-07-11 against a comprehensive external RAG concept list; phases and backlog items
refer to the [roadmap](roadmap.md).

How to read the **Place** column:

- **Phase 1–4** — implemented by that core roadmap phase.
- **Backlog N** — planned by Phase 5+ backlog item N (implemented when that phase lands).
- **Theory** — explained in a named theory chapter (`docs/theory/`, written in the phase
  that lands it — chapters are not linked here until they exist), deliberately not implemented.
- **Glossary** — defined here for completeness; no implementation or chapter planned.
- **Out of scope** — deliberately excluded; the rationale is the entry.

A concept is explained exactly once — in its theory chapter; this map only defines and
points. When a phase or backlog change adds, moves, or drops a concept, this map is updated
in the same change.

## Ingestion & preprocessing (offline)

| Concept | Definition | Place |
| ------- | ---------- | ----- |
| Connector | A source-specific adapter that extracts documents from an external system into the ingestion pipeline behind a stable interface. | Phase 1 (fetch + convert is the first connector); Backlog 12 lands the second, proving the interfaces. Enterprise sources (CRM, SharePoint, S3) stay out — a single-user CLI over a public corpus has none. |
| Multimodal extraction | Pulling all content modalities — text, tables, figures, images — out of source documents so nothing is silently dropped at ingestion. | Phase 1 (text and tables — CALS-table rendering is pinned in the [convert contract](stages/convert.md)); Backlog 12 records a dated scoping decision on figures/images. |
| OCR 2.0 / document intelligence | End-to-end vision-language models that read a page image directly into structured text (layout, tables, formulas) in one pass, replacing OCR-then-layout pipelines. | Theory — document-parsing chapter (Backlog 12). The corpus is born-digital; there is nothing to OCR. |
| Document layout analysis (DLA) | Detecting the visual structure of a page — headings, columns, paragraphs, tables, footnotes — so each region is treated according to its role. | Backlog 12 (named in the item). Phase 1's [corpus & parsing chapter](theory/corpus-and-parsing.md) contrasts it with lossless XML parsing. |
| Reading order | Reconstructing the correct human reading sequence of layout regions (across columns, around figures and footnotes) so extracted text flows as intended. | Backlog 12. Not a problem for the XML corpus — norm order is explicit. |
| Text cleaning & normalization | Stripping non-content noise (boilerplate, navigation, editorial apparatus) and normalizing encoding so only meaningful text reaches chunking. | Phase 1 — convert emits normative text only; the exact exclusions (footnotes, editorial apparatus, the XML's own tables of contents) are pinned in the [convert contract](stages/convert.md); the [corpus & parsing chapter](theory/corpus-and-parsing.md) explains why. |
| Metadata enrichment / tagging | Attaching structured attributes (source, date, section path) to each document and chunk so retrieval can filter, cite, and trace on more than raw text. | Phase 1 (document front matter) + Phase 2 (per-chunk metadata: law, § number, heading path, source URL, fetch date). |
| Data lineage | Recording where each piece of indexed data came from and through which transformations, so any chunk traces back to its exact source and fetch time. | Phases 1–3 — source URL and fetch date flow from front matter through chunk metadata into the database row; the concept is named in Phase 1's [corpus & parsing chapter](theory/corpus-and-parsing.md). |
| Change data capture (CDC) | Streaming row- or event-level changes from a source system in near real time so downstream consumers stay continuously synchronized. | Out of scope — the source is a periodic static ZIP download; there is no upstream change stream and no freshness requirement. Nearest planned concept: Backlog 13 (incremental ingestion). |
| Incremental sync | Detecting which source documents changed since the last run and reprocessing only those, instead of rebuilding the whole index. | Backlog 13. |
| PII handling | Detecting and masking personally identifiable information in documents before indexing or in answers before display. | Backlog 9 lands the output-side checks; the guardrails chapter covers ingestion-time PII and notes this corpus contains none (impersonal norm texts). |

## Chunking

| Concept | Definition | Place |
| ------- | ---------- | ----- |
| Fixed-size chunking | Splitting text into chunks of a fixed character or token count, regardless of content or structure. | Theory — chunking chapter (Phase 2), as the baseline structure-aware chunking beats. |
| Recursive character splitting | Hierarchically splitting on an ordered separator list — paragraphs, then sentences, then words — until every piece fits the size limit. | Theory — chunking chapter (Phase 2), as the standard tutorial baseline. |
| Semantic chunking | Splitting where embedding similarity between adjacent sentences drops, so each chunk covers one coherent topic. | Backlog 6. |
| Sliding window / overlap | Making adjacent chunks share overlapping text so information on a chunk boundary is not lost to retrieval. | Phase 2 — oversized §§ are split by Absatz with overlap. |
| Hierarchical (parent-child) chunking | Indexing small child chunks for precise search while handing their larger parents to the LLM for fuller context. | Backlog 6. |
| Page-level chunking | Using the physical page (typically of a PDF) as the chunk unit, with page numbers as natural citation anchors. | Backlog 12 — pages don't exist in the XML corpus. |
| Structure-aware / element-based chunking | Splitting a document along its own structural elements (sections, headings, paragraphs, tables, lists) instead of at arbitrary positions. | Phase 2 — the phase itself: chunk by §, split by Absatz, heading-path metadata. |
| Code-aware chunking | Splitting source code along syntactic units (functions, classes) derived from the AST rather than by lines. | Glossary — the code-corpus analogue of structure-aware chunking; this playbook has no code corpus. |
| Contextual enrichment | Prepending document-level context (heading path or an LLM-generated situating summary) to each chunk before embedding, so it is retrievable in isolation. | Backlog 6 — Phase 2's metadata fields are the raw material. |
| Late chunking | Embedding a long window with a long-context model first, then pooling token embeddings into per-chunk vectors that retain whole-document context. | Backlog 6, conditional on the Phase 3 model exposing token embeddings and a long context. |

## Vectorization & storage

| Concept | Definition | Place |
| ------- | ---------- | ----- |
| Vector embedding | A numeric vector representation of text in a high-dimensional space where semantically similar items lie close together. | Phase 3 — embed stage; embeddings chapter. |
| Dense embedding | A compact vector where every dimension carries a learned value, produced by neural encoders (e.g. E5, BGE) to capture conceptual similarity. | Phase 3 — the sentence-transformers model chosen there; dense-vs-sparse contrast in the embeddings chapter. |
| Sparse embedding | A very high-dimensional, mostly-zero vector weighting explicit vocabulary terms (BM25) or learned term expansions (SPLADE), excelling at exact matches. | Backlog 2 — lexical sparse retrieval (BM25-style) is implemented; learned sparse (SPLADE) is theory contrast. |
| Multimodal embedding | A model (e.g. CLIP) mapping text, images, and audio into one shared vector space for cross-modal search. | Out of scope — the corpus is text-only German law; no image or audio modality exists anywhere in the system. |
| Embedding normalization | Scaling vectors to unit length so dot product, cosine, and Euclidean distance produce consistent neighbor rankings. | Phase 3 — pinned together with the model choice and pgvector distance operator in the dated model decision. |
| Vector database | A datastore persisting vectors alongside text and metadata, serving fast similarity search through specialized indexes. | Phase 3 — load stage owns Postgres + pgvector; theory positions it against dedicated open-source engines (Qdrant, Milvus, Weaviate); proprietary cloud services (Pinecone) are non-options by rule. |
| Approximate nearest neighbor (ANN) | Index-based search trading exact top-k accuracy for large speed gains in high-dimensional spaces. | Phase 3 — the vector-indexes chapter opens with exact kNN vs ANN and the recall/latency trade-off. |
| HNSW | A graph-based ANN index of layered proximity graphs searched greedily from sparse top layers down, giving fast high-recall search. | Phase 3 — the index the load stage creates. |
| IVF (inverted file index) | An ANN index that clusters vectors into partitions and scans only the partitions nearest the query. | Theory — vector-indexes chapter (Phase 3), contrasted via pgvector's `ivfflat`; HNSW is the recorded choice. |
| Vector quantization (PQ, SQ, binary) | Compressing stored vectors (product/scalar/binary quantization) to cut index memory at some recall cost. | Theory — vector-indexes chapter (Phase 3); the MVP corpus is far too small to need it. Not to be confused with LLM weight quantization (below). |

## Retrieval & search (online)

| Concept | Definition | Place |
| ------- | ---------- | ----- |
| Semantic search | Finding documents by meaning: query and documents embedded into one vector space, ranked by similarity (typically cosine). | Phase 3 (similarity queries verified against the store) + Phase 4 (retrieve stage end to end). |
| Keyword / lexical retrieval (BM25) | Matching on exact terms scored by term statistics (TF, IDF, length normalization), with BM25 as the standard function. | Backlog 2 — Postgres full-text as BM25-style retrieval. |
| Hybrid search | Running dense and sparse retrieval in parallel and merging results, so paraphrases and exact terms are both catchable. | Backlog 2. |
| Reciprocal rank fusion (RRF) | Merging ranked lists by summing 1/(k + rank) per document — no score calibration between retrievers needed. | Backlog 2. |
| Score normalization / weighted fusion | Merging results by normalizing incomparable raw scores to a common scale and combining as a weighted sum. | Theory — hybrid-search chapter (Backlog 2), as the alternative RRF deliberately sidesteps. |
| Top-k results | Truncating a ranked list to the k highest-scoring chunks, trading recall against prompt size and noise. | Phase 4 — k is pinned as a dated decision when the phase lands. |
| Metadata filtering | Restricting retrieval to chunks whose metadata matches structured conditions, applied before or after the vector search. | Backlog 3. |
| Cross-encoder reranking | A second precision pass scoring each (query, candidate) pair jointly through one transformer forward pass. | Backlog 5. |
| ColBERT / late interaction | Storing one embedding per token and scoring by token-level query-document interactions (MaxSim) — between bi-encoders and cross-encoders. | Theory — cross-encoders chapter (Backlog 5), as part of the retrieval-architecture spectrum. |

## Query transformation & expansion

| Concept | Definition | Place |
| ------- | ---------- | ----- |
| Query rewriting | Reformulating the user's raw question (phrasing, ambiguity, terminology) into a form that retrieves better. | Backlog 4. |
| Query expansion | Augmenting a query with synonyms, acronyms, and domain terms (e.g. Abgabenordnung ↔ AO) so differently-phrased documents match. | Backlog 4. |
| Multi-query retrieval | Generating several question variants, retrieving for each, and fusing the result lists to raise recall. | Backlog 4 — variants fused with RRF from Backlog 2. |
| HyDE (hypothetical document embeddings) | Having the LLM write a hypothetical answer and searching with its embedding, so the query lives in answer-shaped vector space. | Backlog 4. |
| Query decomposition | Splitting a complex question into simpler sub-questions retrieved independently before the answer is composed. | Backlog 4. |
| Step-back prompting | Abstracting a specific question to its underlying principle, retrieving on the general question first. | Theory — query-transformation chapter (Backlog 4), as the contrast to HyDE and decomposition. |
| Query routing | Classifying a query and directing it to the retrieval strategy or index best suited to it. | Backlog 4 — the lightweight router; multi-index/learned routing stays out (single corpus, single-user CLI). |
| Keyword extraction | Pulling hard facts out of the question — § numbers, exact identifiers, rare terms — to use as exact-match constraints alongside semantic retrieval. | Backlog 4. |

## Generation & LLM interface

| Concept | Definition | Place |
| ------- | ---------- | ----- |
| Prompt assembly / context packing | Deterministically formatting instructions, question, and retrieved chunks (with citations) into one stable prompt. | Phase 4 — the assemble stage. |
| Context window management | Deciding how much and which content fits the model's token budget without truncation or waste. | Backlog 7. |
| Lost in the middle | The finding that LLMs use information at the edges of a long context far better than information buried in the middle. | Backlog 7 — ordering as the counter-measure; the chapter cites the primary source. |
| Prompt caching | Reusing the computed state of a static prompt prefix across requests, cutting latency (and cost on metered APIs). | Theory — llm-generation chapter (Phase 4): the cloud-cost variant is inapplicable; the local analogue (prompt-prefix/KV reuse in llama.cpp) motivates the assemble stage's stable layout. |
| KV caching | Storing key/value attention tensors of processed tokens so each new token attends to cached state instead of recomputing the prefix. | Theory — llm-generation chapter (Phase 4): managed by llama.cpp inside Ollama; nothing to build, everything to understand (prefill vs decode speed on CPU). |
| Groundedness | The property that an answer's claims are supported solely by the retrieved context, not the model's parametric memory. | Phase 4 (the generate contract: grounded answer with citations) + Backlog 1 (measured) + Backlog 9 (runtime check). |
| Hallucination prevention | Prompt-level techniques reducing fabricated answers: grounding phrasing ("according to …") and explicit abstention instructions. | Phase 4 — abstention and grounding directives in the assembled system prompt; techniques named in the phase's chapter. |
| Chain-of-thought (CoT) | Instructing the model to produce explicit intermediate reasoning before its final answer. | Theory — llm-generation chapter (Phase 4): why it helps a small quantized model and what it costs in output tokens on CPU. |
| Chain-of-verification (CoVe) | Draft an answer, generate verification questions about its claims, answer them against the context, revise. | Backlog 9 — the groundedness output check is CoVe-style self-checking with the local LLM. |
| LLM weight quantization (GGUF) | Compressing model weights to lower precision (e.g. 4-bit GGUF) so a 7–8B model fits in RAM and runs on CPU. | Theory — llm-generation chapter (Phase 4); the served model is a quantized GGUF by design. |

## Security & guardrails

| Concept | Definition | Place |
| ------- | ---------- | ----- |
| Input guardrails | Inspecting the user's question before retrieval/generation and blocking or sanitizing attacks and off-policy inputs. | Backlog 9. |
| Output guardrails | Validating the answer before display: groundedness against the retrieved context, PII, toxicity. | Backlog 9. |
| Retrieval rails | Dropping irrelevant or unsafe retrieved chunks (e.g. below a score threshold) before they enter the prompt. | Backlog 9. |
| Prompt injection | Instructions embedded in the query (direct) or in retrieved documents (indirect) that subvert the system prompt. | Backlog 9 — with the guardrails theory chapter. |
| Permission preservation | Propagating source-document access rights onto chunks and enforcing them at query time. | Out of scope — a single-user CLI over a uniformly public-domain corpus has no users, tenants, or access tiers; chunk-level ACLs would be speculative generality. |
| Dialog rails | Declarative policies constraining multi-turn conversation flow (NeMo Guardrails / Colang). | Theory — guardrails chapter (Backlog 9) presents the rails taxonomy and why dialog rails are not built: no multi-turn dialog to steer, and NeMo Guardrails is a framework. |

## Evaluation & monitoring

| Concept | Definition | Place |
| ------- | ---------- | ----- |
| RAG triad | Scoring a RAG interaction on context relevance, faithfulness/groundedness, and answer relevance. | Backlog 1. |
| Retrieval metrics (Recall@K, Precision@K, MRR, NDCG) | Rank-based IR measures scoring, against labeled relevance judgments, how many relevant chunks the retriever returns and how highly it ranks them. | Backlog 1 — computed deterministically against gold questions labeled with their expected §§. |
| LLM-as-a-judge | Using a language model as an automated grader for qualitative criteria instead of exact-match comparison. | Backlog 1 — a local open-weight judge via Ollama with hand-written grading prompts; no cloud judge models. |
| Reference-free evaluation | Judging quality from the relations among question, context, and answer — no hand-written gold answers needed. | Backlog 1 — the concept RAGAS packages, built by hand per the no-framework rule. |
| Golden set / ground truth | A curated, version-controlled test dataset serving as fixed ground truth for regression-testing every change. | Backlog 1 — the checked-in gold-question set. |
| Embedding drift detection | Monitoring shifts in embedding distributions after corpus or model updates to catch silent retrieval degradation. | Backlog 14 — amended laws are this corpus's real drift trigger. |
| Observability & tracing | Capturing step-level traces of every intermediate so failures along the chain are visible, not silent. | Backlog 8 — seeded by Phase 4's step-level logging contract. |
| Continuous learning from human feedback | A closed loop where human ratings of answers systematically improve retrieval, prompts, data, or models. | Out of scope — no operated service, no user base, no feedback stream; fine-tuning from feedback would also strain the CPU-only floor. |

## Advanced architectures

| Concept | Definition | Place |
| ------- | ---------- | ----- |
| Agentic RAG | An LLM-driven agent plans retrieval steps, runs searches, and reflects on intermediate results instead of one fixed retrieve-then-generate pass. | Backlog 10 — a hand-built plan → retrieve → reflect loop with the local LLM. |
| GraphRAG | Building a knowledge graph of entities and relationships from the corpus and retrieving over it, catching connections flat similarity search misses. | Backlog 11 — a citation-link graph (§→§, law→law) in plain Postgres; the chapter contrasts full LLM-entity-extraction GraphRAG and its CPU cost. |
| Corrective RAG (CRAG) | A retrieval evaluator grades retrieved documents and triggers corrective actions (rewrite, alternative retrieval) when they are insufficient. | Backlog 10 — the self-correction half of the loop; corpus-internal correction replaces the canonical web-search fallback (self-hosted constraint). |
| Self-RAG | A model fine-tuned with reflection tokens decides at generation time whether to retrieve and critiques what it retrieved. | Theory — agentic-retrieval chapter (Backlog 10): the trained-model end of the self-reflection spectrum; fine-tuning is infeasible on the CPU floor. |
| Multi-hop retrieval | Answering through chained retrieval steps, each follow-up query built from the previous step's results. | Backlog 4 (sequential, decomposition-driven) + Backlog 10 (LLM-driven chaining). |
| Long-context RAG | Retrieval strategies for models with very large context windows — fine-grained retrieval vs stuffing whole documents into the prompt. | Theory — context-windows chapter (Backlog 7): why million-token windows don't obsolete retrieval; 1M-token inference is unreachable on the target hardware anyway. |
