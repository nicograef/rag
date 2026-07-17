# PRD: English Wikipedia Pivot — Lightweight Demo on Ordinary Hardware

> **Status: landed 2026-07-18.** All six pivot phases are on `main`; the target-state docs
> (README, roadmap, stage contracts, theory chapters) now carry the story. This PRD is kept
> as the record of the migration's rationale, verified facts, and accepted risks.

Repoints the playbook from German federal law to an **English Wikipedia corpus (current
Premier League football clubs)** and swaps the two heavy models for **CPU-light** ones,
lowering the design floor from 8-core/16 GB to **4-core/8 GB**. The execution plan (phase
re-baselining, dated decisions) lives in [../roadmap.md](../roadmap.md); this document states
the new target state and the honest blast radius. It supersedes the corpus, embedding-model,
generation-model, and hardware-floor decisions recorded to date — per the repo's
**prune-don't-archive** rule (below), implementing this PRD **rewrites** those decisions and
docs in place rather than adding new dated entries beside the stale ones.

All external facts below were **verified live 2026-07-17** against primary sources
(Wikipedia:Copyrights + Wikimedia Terms of Use + the CC BY-SA 4.0 deed; the MediaWiki Action
API docs and live API responses; the `BAAI/bge-small-en-v1.5` and `ibm-granite/granite-4.0-micro`
model cards; ollama.com library pages). Time-sensitive claims carry that date.

## Problem Statement

The playbook does not run comfortably on an ordinary computer, and its corpus limits its
reach — two problems that a learning-and-demo project cannot afford.

- **Hardware.** The stack is pinned to an 8-core/16 GB CPU floor. `make embed` peaked at
  9.1 GiB RSS at batch 32 (OOM'd at 5.7 GB); `make ask` on the pinned `qwen3:4b-instruct`
  ran **swap-bound at 2.0–3.5 tok/s, minutes per question**, below the floor. A typical
  4-core/8 GB laptop is *below both dimensions* of the documented floor — the exact machine a
  learner is most likely to have.
- **Corpus reach.** German federal law is truly public-domain and richly structured, but its
  German language is a standing accepted-risk that limits relatability for a global learner
  audience. For a "clone it and see RAG work" demo, *runs anywhere* and *reads to anyone*
  matter more than corpus grandeur.

The point of the project is understanding every moving part on hardware a learner actually
owns. That goal now outranks the German-corpus and 16 GB-floor decisions.

## Solution

Repoint the playbook to a **small English Wikipedia corpus — the 20 current Premier League
clubs' articles** — and swap to **`BAAI/bge-small-en-v1.5`** (embeddings) and
**`granite4:micro`** (generation), both chosen to fit **4-core/8 GB without swap**. Everything
that *defines* the playbook is unchanged: framework-free, open-source-only, CPU-only,
theory-next-to-code, re-runnable, stage-as-module, honest-currency-not-state-of-the-art. Only
the corpus, the two models, and the hardware floor change — plus the config surface that makes
those knobs tunable instead of hard-coded.

The four pillars of the [product PRD](prd-rag-playbook.md) all survive. The one that changes
in kind, not spirit, is **theory-next-to-code**: structure-aware chunking retargets from
`law → § → Absatz` to `article → == section == → === subsection ===`. Wikipedia articles carry
a real heading hierarchy, so the Phase-2 lesson stays a genuine lesson rather than collapsing
to the fixed-size splitting the product PRD rightly disdains — it is simply a lighter version
of the same idea.

### Re-evaluation of the chosen directions

The directions are sound. The honest qualifications, now verified:

- **Corpus — strong demo, one licensing step-down.** Premier League clubs are relatable,
  factual, citable, and cross-linked (History, Stadium, Honours, Rivalries) — excellent for
  grounded Q&A with citations. But Wikipedia text is **CC BY-SA 4.0** (dual-listed legacy
  GFDL), **not public domain** — a real step down from amtliche Werke (§ 5 UrhG). It still
  satisfies rule 3 ("public-domain **or properly-licensed**"), and because `data/` is
  gitignored (the corpus is fetched at runtime, never redistributed in git) the repo incurs
  **no share-alike obligation**; the only live requirement is **attribution on displayed
  excerpts** (see the corpus decision).
- **bge-small-en changes two contracts.** Dimension 1024 → **384** (`vector(384)`) and max
  sequence 8192 → **512 tokens**. The 512 cap makes chunk sizing load-bearing: the embed
  token-guard, previously slack, now genuinely bounds chunk size.
- **granite4:micro's 8 GB win is swap-avoidance, not raw speed** (2.1 GB serving vs 3.9 GB) —
  both are ~3 B. Fitting RAM without swap is what lifts it off the 2–3.5 tok/s floor. If
  per-question latency is still too high on 8 GB, the next lever is the 1 B tier
  (e.g. `granite`/`llama` 1 B), not further config — noted, not adopted here.
- **The headline:** this pivot **lowers the stated design floor to 4-core/8 GB** — the actual goal.

## User Stories

1. As a learner, I want to clone the repo and run the whole pipeline on a **4-core/8 GB
   laptop with no GPU**, so I can work through a real RAG system on the machine I own.
2. As a learner, I want to ask an **English** question about a football club in the terminal
   and get a grounded answer with a citation and source link, so I experience the full RAG
   loop over content I can read and sanity-check myself.
3. As a learner, I want each performance knob (embed batch size, `num_ctx`, model tags, the
   corpus list) exposed as a documented override, so I can tune the system to my hardware
   without editing source.
4. As a learner, I want the structure-aware-chunking lesson to survive on Wikipedia sections,
   so I still learn why structure beats fixed-size splitting.
5. As the maintainer, I want the superseded German-law decisions removed rather than archived,
   so the public playbook stays clean and accurate (the prune rule).
6. As the maintainer, I want the CC BY-SA attribution obligation stated and satisfied in the
   generate stage, so the demo is licence-correct without over-claiming public domain.
7. As the maintainer, I want the pivot to remove the old models, weights, caches, data
   artifacts, DB state, and Docker volumes — not just the docs — so no German-law stack lingers
   on disk and the repo has a one-command reset to a clean slate.

## Implementation Decisions

**Positioning & hardware floor**

- The design floor drops to **4-core / 8 GB CPU-only**; nothing may require more. Every
  "constraints are features" statement updates: the corpus line becomes *English Wikipedia
  (Premier League clubs), CC BY-SA 4.0 with attribution*; the CPU-only line cites the new
  floor. The 16 GB-floor validation obligations in the product PRD are replaced by an
  **8 GB-floor** validation recorded when the pivot's model decisions land.

**Corpus — English Wikipedia, current Premier League clubs**

- **Selection.** There is **no clean Wikipedia category of the 20 current clubs' base
  articles** (verified: `Category:Premier League clubs` mixes current, former, and list pages;
  `Category:2025–26 Premier League by team` holds season articles, not club articles; broad
  football categories fan out to thousands). The corpus config is therefore an **explicit list
  of the 20 base article titles** (replacing `laws.toml`) — the stable, honest choice; it needs
  a ~3-title edit each season (promotion/relegation), documented as such. The verified 2025–26
  set: Arsenal, Aston Villa, Bournemouth, Brentford, Brighton & Hove Albion, Burnley, Chelsea,
  Crystal Palace, Everton, Fulham, Leeds United, Liverpool, Manchester City, Manchester United,
  Newcastle United, Nottingham Forest, Sunderland, Tottenham Hotspur, West Ham United,
  Wolverhampton Wanderers (article titles carry the `F.C.`/full-name form, e.g. `Arsenal F.C.`).
- **Fetch.** MediaWiki **Action API**, read-only, **no key/OAuth**. Clean text via the
  **TextExtracts** extension: `prop=extracts&explaintext=1&exsectionformat=wiki` returns the
  **full article as plain text with `== Heading ==` markers** (verified live on `Arsenal F.C.`).
  Etiquette is a real contract: a **descriptive `User-Agent`** is mandatory (format
  `rag-playbook/0.x (contact) …`; never spoof a browser), requests are **sequential** (not
  parallel), `maxlag=5` for courtesy, `Accept-Encoding: gzip`, `exlimit` ≤ 20 titles/request.
  Documented failure mode: an article with no lead paragraph yields an empty extract — the
  fetch/convert stage **smoke-tests all 20 for non-empty output**. TextExtracts **strips images
  and flattens tables/lists** — accepted: the corpus is prose, matching the law-text precedent
  (tables are out of scope here, unlike the CALS tables Phase 1 rendered).
- **Licensing (verified 2026-07-17).** English Wikipedia text is **CC BY-SA 4.0** (operative;
  GFDL dual-listed as legacy). Attribution is satisfied by a **hyperlink to the article** (its
  history lists all authors). Share-alike binds only **distributed adapted text**, not verbatim
  copies and not surrounding code; storing text in the gitignored, runtime-fetched DB is **not a
  distribution event**, so no copyleft attaches to the repo. Displaying a retrieved excerpt is a
  reproduction → it needs **attribution + a licence notice**, not copyleft. Whether an LLM
  *paraphrase* is "adapted material" is legally unsettled; the attribution posture below
  neutralizes it. This qualifies as "properly licensed" under rule 3 — recorded as the corpus
  licensing decision (the German-law § 5 UrhG decision is **removed**, not kept).

**Chunking — retargeted structure-aware**

- Chunk by **Wikipedia section** (`== level 2 ==`), splitting oversized sections by
  subsection/paragraph with overlap, merging tiny ones — the same algorithm the German chunker
  used, retargeted from norm units to heading levels. The chunk max size drops to **fit
  bge-small's 512-token cap** (target ≈ 1,200–1,500 chars; the exact value pinned when the stage
  lands, validated with the model's own tokenizer against the fetched corpus). The embed
  token-guard stays a hard failure, not silent truncation.
- **Citation fields** become corpus-neutral values on the existing contract: source identifier
  = article title, section path = heading trail, citation label = "Article § Section", source
  URL = the article URL. No new fields — the chunk-record contract already uses neutral names.

**Embedding model — `BAAI/bge-small-en-v1.5`** (verified 2026-07-17)

- **MIT**, 33.4 M params, **dim 384**, **max 512 tokens**, L2-normalized → cosine. The store
  becomes `vector(384)`; the pgvector operator stays **cosine `<=>`** with HNSW on
  `vector_cosine_ops` (pgvector defaults `m=16`, `ef_construction=64` — untouched at this
  corpus scale). Download ≈ 130 MB (vs bge-m3's ≈ 4.6 GB cache), CPU-fast.
- **One code path (decision):** v1.5 makes the query instruction prefix **optional** ("no
  instruction only has a slight degradation … you can generate embedding without instruction in
  all cases for convenience"). The pipeline uses a **symmetric path — no instruction for queries
  or passages** — preserving the "ingest and question-embedding share one interface" property
  the bge-m3 choice valued. The query-only instruction (`"Represent this sentence for searching
  relevant passages:"`, never on passages) is recorded as a documented recall-tuning option, not
  the default.
- **Consequence for the backlog:** bge-small exposes **no token-level/ColBERT vectors** and only
  512 tokens, so the Backlog-6 **late-chunking condition that bge-m3 met is no longer met** — the
  concept map and backlog entry are updated to say so.

**Generation model — `granite4:micro`** (verified 2026-07-17)

- **Apache-2.0**, ~3 B dense decoder-only (no Mamba — the `granite4:micro-h` hybrid is a
  *different* model, not used), **Q4_K_M ≈ 2.1 GB**, 128 K native context, **plain instruct —
  no `<think>` traces**. RAG is a documented intended use. Served by the pinned
  `ollama/ollama` Compose service, model in the named volume.
- **Config:** **`num_ctx` 4096** (down from 8192 — no giant table chunk to accommodate now;
  halves the KV cache and speeds prefill), **`num_predict` 512** (football answers are short),
  decoding per the Granite card's guidance with a pinned seed. The assemble character budget is
  recomputed from the new `num_ctx`/`num_predict`.
- **System prompt** is rewritten in **English** with the same grounding/abstention/citation
  directives, plus the CC BY-SA attribution line so every answer that surfaces an excerpt shows
  the article link and licence notice (satisfying the corpus licensing obligation at the point
  of display).

**Config & usability (the "easy to use on normal computers" half)**

- Expose the performance knobs as **env/Makefile overrides** instead of hard-coded module
  constants: embed batch size (default lowered to fit 8 GB), `num_ctx`, `num_predict`, model
  tags, and the corpus title list. Set **Docker `mem_limit`s** on the Postgres and Ollama
  services so neither thrashes silently on an 8 GB host. This is the change that turns "worked on
  the maintainer's 16 GB machine" into "clone and run anywhere."

**Prune-don't-archive (applying the new rule)**

- Implementing this PRD **removes or rewrites in place** the superseded content — the roadmap's
  MVP-corpus / bge-m3 / qwen3:4b / § 5 UrhG decisions, `prd-embed-load.md` (bge-m3-specific), and
  every German-law reference in the README, product PRD, concept map, stage contracts, and theory
  chapters — rather than leaving them as dated history. The roadmap's "Decisions" convention and
  the product PRD's pillar 3 wording, which currently lean append-only, are updated to
  current-state framing. Dated **verification** stamps of live facts stay (the rule prunes
  superseded *content*, not the verify-before-claiming discipline). This pivot PRD may itself be
  removed once the pivot has landed and the target-state docs carry the story.

**Decommissioning the old stack (cleanup)**

- The prune rule extends past docs to **runtime artifacts**: the pivot removes every trace of the
  German-law stack so nothing stale lingers on disk, in caches, in Docker volumes, or as dead
  code, and the repo gains a documented one-command reset so a learner can always return to a
  clean slate. The plan operationalizes each item below.
- **Model weights & caches.** Once bge-small-en and granite4:micro are pinned, remove the
  **bge-m3 snapshot** from the Hugging Face cache (≈ 4.6 GB — it lands twice) and delete
  `qwen3:4b-instruct` from the Ollama volume (`ollama rm`). Surgical, **not**
  `rm -rf ~/.cache/huggingface` — that cache is shared across projects (roadmap Docker decision).
  New footprint: bge-small-en ≈ 130 MB + granite4:micro ≈ 2.1 GB.
- **Data artifacts** (gitignored, re-runnable). Wipe `data/raw/`, `data/corpus/`, `data/chunks/`,
  `data/embeddings/` — the law XML, Markdown, §-based chunk JSONL, and **dim-1024** embedding
  JSONL — so the new corpus regenerates from scratch (the dimension change alone makes the old
  vectors unusable).
- **Database state.** The `chunks` table is `vector(1024)` with German-law rows and an HNSW index;
  the pivot's `vector(384)` schema is incompatible, so **load** resets it (drop + recreate the
  table and index, or a documented `pgdata` volume reset). No 1024-dim rows survive.
- **Docker.** The `ollama` and `pgdata` **volumes** are cleaned as above; the pinned **images**
  (`pgvector/pgvector:pg17`, `ollama/ollama:…`) stay — same infrastructure, new contents. The
  `mem_limit`s from the config decision are added here.
- **Code, config, fixtures.** German-specific code is replaced in place (XML fetch/convert,
  §/Absatz chunker); `laws.toml` is **deleted** in favor of the clubs config; the truncated-law-XML
  test fixtures are replaced by a Wikipedia-extract fixture. The plan explicitly greps for
  **orphaned** German-specific constants, helpers, and imports so none linger.
- **Documented reset.** Add a **`make clean`** (regenerable `data/` artifacts) and a
  **`make reset`** (also drops the DB table + removes the old Ollama model) target, so
  "re-runnable from a clean checkout" and "switching corpora leaves nothing behind" are one
  command, not a manual hunt. The plan lands these targets and the README documents them.

**Architecture / blast radius (honest)**

- **Code:** `fetch` (Wikipedia API + User-Agent/maxlag, replacing the XML/ZIP fetcher),
  `convert` (extracts → Markdown by section, replacing XML parsing), `chunk` (section-based,
  smaller size), `embed` (model id, dim 384, batch, symmetric path), `load` (schema
  `vector(384)`), `retrieve` (same, dim 384), `assemble` (English prompt + attribution, new
  budget), `generate` (`granite4:micro`, `num_ctx`/`num_predict`). Plus `laws.toml` → a clubs
  config and the Makefile/`llm-pull` tag.
- **Docs:** README (positioning, status dates, quick start, first-run costs, corpus section,
  licence note), roadmap (phase re-baselining + decision rewrites), concept map (de-Germanize:
  metadata filtering by *club/section* not §; query expansion of *club aliases* not AO↔
  Abgabenordnung; GraphRAG over *article links* not §→§; drift = *squad/season* changes;
  late-chunking condition now unmet; hybrid-search motivation shifts — dense retrieval works far
  better on English Wikipedia than it did on German legal text, but exact-match on club names/years
  still motivates BM25), product PRD (corpus, floor, accepted-risks), all 8 stage contracts, and
  the 5 theory chapters (corpus-and-parsing, chunking, embeddings, vector-indexes, llm-generation).
- **Execution:** re-baseline as stage-scoped slices along the existing phase boundaries
  (fetch+convert → chunk → embed+load → online), each re-verified and dated, one reviewable step
  at a time (rule 5). A follow-up implementation plan in `docs/plans/` sequences it; this PRD
  scopes it.

## Testing Decisions

- Contract tests adapt to the new corpus: **convert** and **chunk** stay pure transforms tested
  against a **small checked-in Wikipedia-extract fixture** (a truncated club article), asserted
  exactly; **embed** promises reproducibility within tolerance against checked-in **dim-384**
  reference vectors on the fixture; **fetch** promises idempotence, not determinism — Wikipedia
  is a living corpus (squads, managers, and honours change), so re-running legitimately changes
  output. Add the **20-article non-empty-extract smoke test** as a fetch guard.
- The quick start is re-verified **from a clean checkout on a 4-core/8 GB machine** when the
  pivot lands, with the date recorded in the README status — including that the whole loop now
  runs without swap.
- **Cleanup is verified, not assumed.** After the pivot, confirm no German-law artifacts remain:
  `data/` holds only the new corpus, the DB has only `vector(384)` rows, the Ollama volume has
  granite4:micro but not `qwen3:4b-instruct`, the HF cache has bge-small-en but not bge-m3, and a
  `grep` in `src/` for German-specific identifiers (`§`, `Absatz`, `laws.toml`, `bge-m3`, `qwen3`)
  comes back clean. `make reset` followed by the full pipeline reproduces the demo from nothing.

## Out of Scope

- **Implementing the pivot.** This PRD scopes the target state; a `docs/plans/` plan sequences
  the work, executed phase by phase under the existing definition of done (code + tests + theory
  chapter + stage contract + README status).
- **Tables / infoboxes / images** from Wikipedia — TextExtracts flattens them; the corpus is
  prose only (accepted, documented). No REST-HTML fidelity path.
- **New backlog items or a multi-corpus abstraction** — the backlog concepts are *retargeted*,
  not rebuilt; still one corpus, still no connector interface until a second implementation exists.
- **Keeping the German corpus as an alternative profile** — the pivot replaces it; a
  config-switchable dual corpus would be speculative generality (rule: simple over clever).

## Further Notes — risks accepted deliberately

- **CC BY-SA is not public domain.** A genuine step down from § 5 UrhG cleanliness; neutralized
  by the gitignored corpus (no distribution) and the attribution posture on displayed excerpts.
  Stated plainly in the docs, not glossed.
- **The corpus is volatile.** Squads, managers, and league membership change continuously —
  more so than law. Fetch is idempotent by design; this makes the incremental-ingestion and
  drift-detection backlog items *more* naturally motivated, not less.
- **Aggregation questions are a real limit.** "Which club has won the most titles?" needs
  cross-article aggregation a single-pass top-k retriever answers poorly — an honest demo
  limitation that motivates the multi-hop/agentic backlog rather than a defect to hide.
- **bge-small is far weaker than bge-m3 in absolute retrieval quality** — acceptable for a
  factual, well-structured English demo; recorded as a trade, not a silent downgrade.
- **A more generic demo domain.** Football-over-Wikipedia is closer to a conventional RAG demo
  than richly-structured law was; sectioned articles keep it above "toy blog post," but the
  domain-richness trade is real and named. The gain — runs on any laptop, reads to anyone — is
  the deliberate reason.
