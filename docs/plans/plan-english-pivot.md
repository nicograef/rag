# Plan: English Wikipedia Pivot — lightweight demo on ordinary hardware

> Source PRD: [../prds/prd-english-pivot.md](../prds/prd-english-pivot.md)

## Goal

Repoint the playbook from German federal law to a small **English Wikipedia corpus (the 20
current Premier League clubs' articles)** and swap the two heavy models for CPU-light ones —
**`BAAI/bge-small-en-v1.5`** (embeddings) and **`granite4:micro`** (generation) — lowering the
stated design floor from 8-core/16 GB to **4-core/8 GB without swap**. Everything that *defines*
the playbook is unchanged (framework-free, open-source-only, CPU-only, theory-next-to-code,
re-runnable, stage-as-module, honest-currency). Only the corpus, the two models, the hardware
floor, and the config surface change — and the pivot removes every trace of the old stack from
docs, code, data, DB, and Docker volumes, leaving a one-command reset to a clean slate.

The pivot is executed as **stage-scoped slices along the existing roadmap phase boundaries**
(fetch+convert → chunk → embed+load → online), bracketed by a foundation slice (reset tooling +
memory caps + a pipeline-wide field-rename sweep) and a wrap-up slice (holistic doc de-Germanization
+ clean-slate validation). Each stage slice re-lands its stage's definition of done under project
rule 5: code + tests + theory chapter + stage contract + dated README status.

## Architectural decisions

Durable decisions that apply across all phases.

### Corpus & fetch

- **Corpus config**: `laws.toml` is replaced by **`clubs.toml`** — a `[clubs]` table mapping a
  short, filesystem-safe **slug** (author-picked, like `laws.toml`'s keys) to the exact Wikipedia
  **article title** used as the API `titles=` value, e.g. `arsenal = "Arsenal F.C."`. Twenty
  entries (the verified 2025–26 set in the PRD). The slug is the stable key for
  `data/raw/<slug>/`, `data/corpus/<slug>.md`, and the DB `slug` column. Editing this file (or
  pointing `CORPUS_CONFIG` at another) is how the corpus list is tuned — this is the corpus knob.
- **Fetch transport**: MediaWiki **Action API**, read-only, no key/OAuth —
  `action=query&prop=extracts&explaintext=1&exsectionformat=wiki&titles=…&format=json`. Etiquette
  is a hard contract: a descriptive **`User-Agent`** (`rag-playbook/<version> (<contact>)`, never a
  spoofed browser), **sequential** requests, **`maxlag=5`**, `Accept-Encoding: gzip`, and
  **`exlimit` ≤ 20** titles/request. Fetch writes each article's plain-text extract plus a
  provenance JSON to `data/raw/<slug>/` (provenance: slug, article title, page id, revision id,
  article URL, `fetched_at`). A **20-article non-empty-extract smoke test** guards against empty
  extracts (an article with no lead paragraph yields an empty extract).

### Corpus structure & chunking

- **Section structure**: `article → == section == → === subsection ===`. Convert translates the
  extract's wiki-format headings (`== H ==`, `=== H ===`) to **ATX headings** (`## H`, `### H`) so
  the chunker's existing ATX heading parser is reused, and emits YAML front matter + an H1 article
  title — the same document shape convert writes today, retargeted from norm units to sections.
- **Chunk unit**: the Wikipedia **section** (`##`). Oversized sections are split by
  subsection/paragraph with overlap; tiny same-section units merge — the same algorithm the German
  chunker used, with the `(1)`-Absatz-marker grouping retargeted to subsection/paragraph grouping.
  **`max_chars` drops to fit bge-small-en's 512-token cap** (target ≈ 1,200–1,500 chars; the exact
  value is pinned in Phase 3, validated with bge-small-en's own tokenizer against the fetched
  corpus). The embed token-guard stays a hard failure, never silent truncation.

### Field rename (resolved in clarification)

- The law-flavored chunk field/column **`law` is renamed to `source_title`** (holds the article
  title); the convert-side front-matter key **`abbreviation` is likewise renamed `source_title`**;
  the generic `unit` stays (holds the section heading, e.g. `History`). The shared per-item helper
  **`run_per_law` is renamed `run_per_source`**. The rename spans `convert` (front-matter writer),
  `chunk` (`Chunk`, the front-matter reader `LAW_KEY`, `_build_chunk`), `load` (`Row` + the `chunks`
  table column), `retrieve` (`RetrievedChunk` + its `SELECT`), every stage `main()`, all tests and
  fixtures, the stage contracts, and the not-yet-built Python-backend plan's `Source`/`Hit` schemas
  (`docs/plans/plan-python-backend.md`).
- Because the rename couples a writer stage to a reader stage across phase boundaries (convert
  writes the key chunk reads; chunk's field feeds load/retrieve), it is done **atomically as a
  mechanical sweep in Phase 1**, before any corpus logic changes — renaming names only, leaving the
  still-German fixture *values* untouched so `make check` stays green. Later phases then change
  values and formats using the final names.
- **Citation format** (resolved in clarification): §-free — `citation = f"{source_title} —
  {unit}"` → e.g. `Arsenal F.C. — History` (using the existing `HEADING_SEPARATOR = " — "`). No
  literal `§` enters `src/`, so the cleanup grep stays literally true. This *format* change lands in
  Phase 3 (with the chunk-logic rewrite); Phase 1's rename keeps the old `f"{unit} {source_title}"`
  format intact.

### Fixture-chain sequencing

- The offline contract tests deliberately reuse upstream stage **outputs as downstream inputs**:
  `tests/fixtures/corpus/*.md` are both convert's golden output and chunk's input;
  `tests/fixtures/chunks/*.jsonl` feed `test_embed`/`test_load`; the dim-8 `FakeEmbedder` goldens in
  `tests/fixtures/embeddings/*.jsonl` derive from the chunk fixtures. Rewriting one stage's fixtures
  therefore ripples into the next stage's tests.
- To keep `make check` green **every** phase, each stage slice (i) introduces **new-slug** fixtures
  for the stage it rewrites, (ii) **leaves in place** the German fixtures that not-yet-pivoted
  downstream stages still consume, and (iii) **regenerates the derived dim-8 `FakeEmbedder`
  goldens** for any chunk fixtures it changes (mechanical, model-independent). A German fixture is
  retired only in the phase that pivots its consuming stage. The real-model
  `reference_vectors.json` is regenerated in Phase 4 and is `integration`-only (it skips cleanly in
  `make check` when the model cache is absent), so it never gates a phase boundary.

### Models & store

- **Embeddings**: **`BAAI/bge-small-en-v1.5`** — MIT, dim **384**, max **512 tokens**,
  L2-normalized → cosine. `EMBEDDING_DIM` becomes **384**; the store column becomes
  **`vector(384)`**; the pgvector operator stays cosine **`<=>`** with HNSW on `vector_cosine_ops`
  (pgvector defaults `m=16`, `ef_construction=64`, untouched). **Symmetric embedding path** — no
  instruction prefix for queries or passages (v1.5 makes the prefix optional; the query-only
  prefix is recorded as a documented recall-tuning option, not the default).
- **Generation**: **`granite4:micro`** — Apache-2.0, ~3 B dense (not the `-h` hybrid), Q4_K_M
  ≈ 2.1 GB, plain instruct (no `<think>`). `NUM_CTX` **4096**, `NUM_PREDICT` **512**, decoding per
  the Granite model card's guidance with a pinned seed (exact values pinned in Phase 5 against the
  card). The assemble character budget is recomputed from the new `NUM_CTX`/`NUM_PREDICT`.
- **Schema migration**: the dim change makes old `vector(1024)` rows incompatible. `load`'s schema
  becomes `vector(384)`; switching **requires a table reset first** (`make reset`, below). `load`
  gains a guard: if the existing `chunks.embedding` column dimension ≠ `EMBEDDING_DIM`, it raises
  `LoadError` hinting `make reset` rather than failing obscurely on insert.

### Config surface (env/Makefile overrides)

- Performance knobs move from hard-coded module constants to **env vars with the pinned value as
  default**, read via `os.environ.get(NAME, default)` and exported through the Makefile's existing
  `-include .env` / `export`:
  - `EMBED_MODEL_ID` (default `BAAI/bge-small-en-v1.5`), `EMBED_BATCH_SIZE` (default lowered to fit
    8 GB) — embed stage.
  - `LLM_MODEL_TAG` (default `granite4:micro`), `LLM_NUM_CTX` (default 4096), `LLM_NUM_PREDICT`
    (default 512) — generate stage. `make llm-pull` keeps deriving its tag from the constant.
  - `CORPUS_CONFIG` (default `clubs.toml`) — fetch stage.
  - New keys documented as optional in `.env.example`.

### Cleanup & infrastructure

- **`make clean`** removes the regenerable `data/` artifact dirs (`raw`, `corpus`, `chunks`,
  `embeddings`). **`make reset`** runs `make clean`, drops the `chunks` table, and removes the
  pinned model from the Ollama volume (`ollama rm` of the pinned tag, derived exactly as
  `make llm-pull` derives it — so it tracks the constant/override, never a hardcoded stale tag) — a
  documented clean slate the next full run rebuilds (re-pulling the ≈ 2.1 GB model and re-embedding).
  Both are documented in the README.
- **Docker `mem_limit`s** are set on the `postgres` and `ollama` services so neither thrashes
  silently on an 8 GB host. The pinned images stay (`pgvector/pgvector:pg17`,
  `ollama/ollama:0.32.1`) — same infrastructure, new contents.
- **One-time decommissions** (migration steps, distinct from the permanent targets): the bge-m3
  snapshot is removed **surgically** from the Hugging Face cache in Phase 4 (never `rm -rf
  ~/.cache/huggingface` — it is shared across projects), and `qwen3:4b-instruct` is removed from
  the Ollama volume in Phase 5 (`ollama rm`).

### Documentation discipline

- **Prune, don't archive**: superseded decisions and German-law references are **rewritten in
  place**, not left as dated history. Dated **verification** stamps of live facts stay (the rule
  prunes superseded content, not the verify-before-claiming discipline). Each stage slice rewrites
  the docs it owns (its stage contract, theory chapter, the dated decision it supersedes, its
  AGENTS.md rows, its README status line + first-run cost); the wrap-up slice does the holistic
  cross-cutting rewrites.
- **Honest sequencing**: `make check` passes at the end of **every** phase (kept green by the
  fixture-chain sequencing above). The end-to-end demo and the README's corpus-framing prose
  legitimately **lag the landed corpus mid-pivot** (a slice-based pivot cannot flip everything
  atomically); the full 4-core/8 GB clean-slate claim is only asserted in the wrap-up phase, after
  it is validated. This is the honest cost of stage-sliced execution and is preferred over claiming
  the 8 GB experience before it runs.

## Inventory

Existing code (all under `src/rag/`) the pivot rewrites, with the neutral seams that survive:

- `__init__.py — run_per_law()`, `RAW_DIR/CORPUS_DIR/CHUNKS_DIR/EMBEDDINGS_DIR`, `HEADING_SEPARATOR`
  (" — ") — shared plumbing; `run_per_law` → `run_per_source`, the rest unchanged.
- `fetch/__init__.py — load_laws()`, `fetch_law()`, `main()`, `DOWNLOAD_URL` — XML/ZIP fetcher over
  `laws.toml`; fully replaced by the MediaWiki Action API + `clubs.toml`.
- `convert/__init__.py — convert_law()`, `render_markdown()`, `_render_norm()/_render_section()`,
  the CALS-table/`<DL>` renderers, `Provenance` — GiI-Norm XML → Markdown; replaced by
  extract → section-Markdown. Front-matter writer (`_front_matter`, double-quote escaping) and the
  `HEADING_SEPARATOR` convention are reused.
- `chunk/__init__.py — chunk_corpus()`, `parse_norm_units()`, `_group_absaetze()`, `ABSATZ_MARKER`,
  `_split_body()`, `Chunk`, `DEFAULT_MAX_CHARS/DEFAULT_MERGE_FLOOR`, `parse_front_matter()`,
  `_heading_depth()` — the ATX-heading parser, split/merge algorithm, and no-silent-loss machinery
  are reused; the Absatz-marker grouping and `law`/`citation` construction are retargeted.
- `embed/__init__.py — SentenceTransformerEmbedder`, `Embedder` (Protocol), `MODEL_ID`,
  `EMBEDDING_DIM`, `BATCH_SIZE`, the token-guard in `embed_law()` — model id/dim/batch change; the
  `Embedder` interface and lazy-import/token-guard design are unchanged.
- `load/__init__.py — SCHEMA_SQL`, `Row`, `join_law()`, `load_law()`, `connection_conninfo()`,
  `DISTANCE_OPERATOR/HNSW_OPERATOR_CLASS`, `EMBEDDING_DIM` import — vector dim + `law` column
  change; replace-semantics and validation logic unchanged.
- `retrieve/__init__.py — retrieve()`, `RetrievedChunk`, `check_connection_settings()`, `TOP_K`,
  `format_hit()` — logic unchanged (dim flows from `EMBEDDING_DIM`); `law` → `source_title` in
  `RetrievedChunk`; German example queries → English.
- `assemble/__init__.py — assemble()`, `SYSTEM_PROMPT`, `Prompt`, `MAX_PROMPT_CHARS` (from
  `NUM_CTX`/`NUM_PREDICT`) — German prompt → English + CC BY-SA attribution; budget recomputed.
- `generate/__init__.py — MODEL_TAG`, `NUM_CTX`, `NUM_PREDICT`, decoding constants, `_chat_payload()`,
  `generate()`, `ollama_base_url()` — model tag + context/decoding change; streaming/HTTP unchanged.
- `ask/__init__.py — main()`, `_default_retrieve_fn()` — the `Quellen:` block → `Sources:` + a
  CC BY-SA licence notice; injectable seams unchanged.

Docs (blast radius from the doc scan): README, `docs/roadmap.md` (Decisions log + phase prose),
`docs/concepts.md`, `docs/prds/prd-rag-playbook.md`, all 8 `docs/stages/*.md`, the 5
`docs/theory/*.md` (`corpus-and-parsing`, `chunking`, `embeddings`, `vector-indexes`,
`llm-generation`), `AGENTS.md`, and `docs/prds/prd-embed-load.md` (**deleted** — bge-m3-specific).
Stage contracts share a skeleton (`# Stage contract` → blockquote code/roadmap/theory links →
`## Invocation`/`## Input`/`## Output` → stage-specific → `## Guarantees` → `## Failure behaviour`
→ optional `## Verification`/`## Current coverage` → `## Downstream consumers`); only `load.md`
(2026-07-14) and `generate.md` (2026-07-17) carry a dated `## Verification` section, with
`convert.md`/`chunk.md` carrying dated `## Current coverage` spot-checks. Theory chapters cross-link
their stage contract(s) and `src/rag/<stage>/__init__.py` both ways.

Tests (`tests/`, `test_*.py`): fakes via existing seams (`conftest.FakeEmbedder`, injected
`retrieve_fn`/`generate_fn`, `httpx.MockTransport`); `integration` marker for real model/DB. German
fixtures under `tests/fixtures/{raw,corpus,chunks,embeddings,prompts}` and
`tests/fixtures/reference_vectors.json` (regenerated by `tests/pin_reference_vectors.py`) are
replaced by Wikipedia-extract equivalents.

## Resolved decisions

- **`law` field → `source_title`** (clarification): rename to a corpus-neutral, domain-appropriate
  name across all stages, the DB column, tests, docs, and the future backend plan. `unit` stays.
- **Citation format** (clarification): §-free `"{source_title} — {unit}"` (e.g. `Arsenal F.C. —
  History`); no literal `§` in `src/`.
- **Config file**: `clubs.toml` with a `[clubs]` `slug → article-title` table (mirrors `laws.toml`).
- **`make reset` scope**: `make clean` + drop the `chunks` table + `ollama rm` of the pinned tag;
  Docker volumes (`pgdata`, `ollama`) and the pinned images are kept. The next full run re-pulls the
  model and re-embeds. (The one-time removal of the *superseded* qwen3/bge-m3 is a separate migration
  step in Phases 4–5, not `make reset` behaviour.)
- **Chunk-size ↔ embed-model dependency**: `max_chars` is pinned in Phase 3 (chunk) against
  bge-small-en's tokenizer. The model is already a PRD-level decision, so only its tokenizer is
  needed there — not the Phase 4 embed-stage code.
- **Model tags stay pinned as defaults**: exposed as env overrides (`EMBED_MODEL_ID`,
  `LLM_MODEL_TAG`) with the pinned value as the default, preserving the "pinned choice, dated
  decision" philosophy while enabling the PRD's noted 1 B-tier lever.
- **README sequencing**: status-table dates and per-model first-run costs update with each stage
  slice; the corpus-framing prose, floor claim, quick-start example questions, and corpus-swap
  section are rewritten in the wrap-up slice after the 8 GB validation.

## Open questions / Risks

- **Mid-pivot inconsistency** (accepted): between Phase 2 and Phase 6 the README prose and the
  end-to-end demo lag the landed corpus. `make check` passes every phase; the whole-loop claim is
  gated on the Phase 6 validation.
- **`max_chars` is validated, not assumed**: the exact value is pinned in Phase 3 with the model's
  tokenizer; if English section text is denser than expected, the token-guard (a hard failure) is
  the backstop, and `max_chars` is lowered.
- **CC BY-SA is not public domain** (accepted, PRD): neutralized by the gitignored, runtime-fetched
  corpus (no distribution) and the attribution posture on displayed excerpts. Stated plainly.
- **Volatile corpus** (accepted): squads/managers/membership change; fetch promises idempotence, not
  determinism. The ~3-title seasonal edit to `clubs.toml` is documented.
- **Aggregation limit** (accepted): cross-article "which club won the most titles?" is answered
  poorly by single-pass top-k — a named demo limitation, motivating the multi-hop/agentic backlog.
- **bge-small ≪ bge-m3 in absolute retrieval quality** (accepted): recorded as a trade for the
  factual, well-structured English demo, not a silent downgrade.
- **`make reset` re-pull cost**: removing the pinned model forces a ≈ 2.1 GB re-pull on the next run;
  documented so it is a choice, not a surprise.

---

## Phase 1: Foundation — reset tooling, memory caps, and the field-rename sweep

**User stories**: 3 (documented performance knobs), 7 (one-command reset to a clean slate); plus the
groundwork the resolved field-rename decision needs.

### Context

- `Makefile` — `-include .env` / `export`, the `db-shell`/`llm-pull` targets and their psql/ollama
  invocations; new `clean`/`reset` targets sit here (`reset` derives the model tag the same way
  `llm-pull` does).
- `docker-compose.yml` — the `postgres` and `ollama` services; `mem_limit` is added to each.
- `src/rag/load/__init__.py — connection_conninfo()` — the env-driven conninfo the `reset` target's
  table-drop reuses.
- `src/rag/__init__.py — run_per_law()`; `src/rag/convert/__init__.py — _front_matter()`;
  `src/rag/chunk/__init__.py — LAW_KEY`, `Chunk.law`, `_build_chunk()`;
  `src/rag/load/__init__.py — Row.law`, `SCHEMA_SQL`, `join_law()`, `load_law()`;
  `src/rag/retrieve/__init__.py — RetrievedChunk.law`, `retrieve()`'s `SELECT` — the rename targets.
- `docs/roadmap.md` (Decisions convention prose), `docs/prds/prd-rag-playbook.md` (pillar 3),
  `AGENTS.md` — where the append-only framing becomes current-state (prune) framing.

### What to build

Two coherent pieces of groundwork the rest of the pivot leans on, before any corpus logic changes.
**(a) Clean-slate plumbing**: `make clean` (wipe the regenerable `data/` dirs) and `make reset`
(`make clean` + drop the `chunks` table + remove the pinned model from the Ollama volume), Docker
`mem_limit`s on `postgres` and `ollama`, and the documentation-convention edits stating the
**prune-don't-archive** rule (roadmap Decisions convention + product-PRD pillar 3 + AGENTS.md).
**(b) The mechanical de-law rename sweep**: rename `law` → `source_title` (chunk field, `chunks`
column, `RetrievedChunk` + `SELECT`), the convert front-matter key `abbreviation` → `source_title`
(writer and chunk's `LAW_KEY` reader), and `run_per_law` → `run_per_source` — across all stage code,
the existing German fixtures (names only; values untouched), and the Python-backend plan's schemas.
No runnable-claim (README status/floor) change and no citation-format/value change yet.

### Acceptance criteria

- [ ] `make clean` removes `data/raw`, `data/corpus`, `data/chunks`, `data/embeddings` and is safe to
      run when they are absent.
- [ ] `make reset` runs `make clean`, drops the `chunks` table via psql against the env-configured
      database, and removes the currently pinned model from the Ollama volume (tag derived like
      `llm-pull`); it is documented as a clean slate the next full run rebuilds.
- [ ] `docker-compose.yml` sets a `mem_limit` on both `postgres` and `ollama`; `make db` and
      `make llm` still start cleanly.
- [ ] The `law` field/column and `RetrievedChunk.law` are renamed `source_title`, the convert
      front-matter key `abbreviation` → `source_title`, and `run_per_law` → `run_per_source`; no
      `run_per_law`, `Chunk.law`, or `abbreviation`-key reference remains in `src/`. Citation output
      is byte-identical to before the sweep (format unchanged).
- [ ] All existing (still-German) fixtures are updated to the renamed field/key names with unchanged
      values; the Python-backend plan's `Source`/`Hit` schemas use `source_title`.
- [ ] The prune-don't-archive convention is stated in `docs/roadmap.md` (Decisions convention),
      `docs/prds/prd-rag-playbook.md` (pillar 3 wording), and `AGENTS.md`, replacing the append-only
      framing — without yet asserting the new hardware floor as a validated fact.
- [ ] `make check` passes; `make help` lists the new targets.

---

## Phase 2: Fetch & convert — English Wikipedia corpus

**User stories**: 1 (clone-and-run), 2 (English question with a source link), 5 (superseded
German-law decisions removed), 6 (CC BY-SA obligation stated).

### Context

- `src/rag/fetch/__init__.py — load_laws()`, `fetch_law()`, `_fetch_all()`, `DOWNLOAD_URL`,
  `TIMEOUT_SECONDS` — the XML/ZIP fetcher and its `httpx.Client` injection seam (reused for tests).
- `src/rag/convert/__init__.py — convert_law()`, `render_markdown()`, `_front_matter()`,
  `Provenance`, `HEADING_SEPARATOR` — the front-matter writer (already emitting the `source_title`
  key after Phase 1) and heading-separator convention are reused; the XML tree walk is replaced by
  wiki-heading parsing.
- `laws.toml` → `clubs.toml`; `docs/stages/fetch.md`, `docs/stages/convert.md`,
  `docs/theory/corpus-and-parsing.md`; `docs/roadmap.md` (corpus-licensing + MVP-corpus decisions);
  `tests/test_fetch.py`, `tests/test_convert.py`, `tests/fixtures/{raw,corpus}/`.

### What to build

Fetch the 20 club articles from the MediaWiki Action API (TextExtracts, `explaintext=1`,
`exsectionformat=wiki`) under the etiquette contract (descriptive `User-Agent`, sequential,
`maxlag=5`, gzip, `exlimit` ≤ 20), writing each article's plain-text extract + provenance JSON to
`data/raw/<slug>/`, and convert those extracts into section-structured Markdown (`== H ==` → `##`,
`=== H ===` → `###`, H1 = article title, YAML front matter with `source_title`), one
`data/corpus/<slug>.md` per club. Replace `laws.toml` with `clubs.toml`. Rewrite the fetch + convert
contracts and the corpus-and-parsing theory chapter for Wikipedia; rewrite the corpus-licensing
decision in the roadmap to **CC BY-SA 4.0 with attribution** (removing the § 5 UrhG decision) and
the MVP-corpus decision to the clubs corpus. Per the fixture-chain sequencing rule, this phase gives
fetch/convert **new-slug** Wikipedia fixtures and leaves the German corpus/chunk fixtures in place
for the still-unpivoted chunk stage.

End-to-end verifiable: `make fetch` then `make convert` produces 20 readable, section-structured
Markdown files with correct provenance and source URLs.

### Acceptance criteria

- [ ] `clubs.toml` holds a `[clubs]` `slug → article-title` table with the 20 verified 2025–26 clubs;
      `make fetch` downloads all 20 via the Action API with a descriptive `User-Agent`, sequential
      requests, and `maxlag=5`.
- [ ] Fetch writes `data/raw/<slug>/` with the plain-text extract + provenance (slug, article title,
      page id, revision id, article URL, `fetched_at`); re-running replaces a slug's directory
      cleanly (idempotence), and a failing article never touches other articles' artifacts.
- [ ] A **20-article non-empty-extract smoke test** fails the stage if any article yields an empty
      extract.
- [ ] `make convert` emits one `data/corpus/<slug>.md` per club: YAML front matter (incl.
      `source_title`, `source_url`, `fetched_at`), an H1 article title, and ATX section headings
      translated from the extract's wiki headings; the transform is deterministic.
- [ ] `docs/stages/fetch.md` and `docs/stages/convert.md` are rewritten for the Wikipedia source;
      `docs/theory/corpus-and-parsing.md` retargets structure/licensing to Wikipedia sections and
      CC BY-SA; the roadmap's corpus-licensing decision states CC BY-SA 4.0 (§ 5 UrhG removed) and
      the MVP-corpus decision names the clubs corpus.
- [ ] `tests/test_fetch.py`/`test_convert.py` use new-slug Wikipedia fixtures (a small checked-in
      truncated-article extract → section Markdown); fetch tests assert the etiquette headers via
      `httpx.MockTransport`; the German chunk-stage fixtures are left untouched so `test_chunk`
      stays green.
- [ ] The README structure-table `laws.toml` row and the README **fetch & convert** status-row date
      are updated; `make check` passes.

---

## Phase 3: Structure-aware chunking on Wikipedia sections

**User stories**: 4 (the structure-aware-chunking lesson survives on sections).

### Context

- `src/rag/chunk/__init__.py — chunk_corpus()`, `parse_norm_units()`, `_group_absaetze()`,
  `ABSATZ_MARKER`, `_split_body()`/`_split_recursively()`, `body_from_parts()`, `_build_chunk()`,
  `Chunk`, `DEFAULT_MAX_CHARS`/`DEFAULT_MERGE_FLOOR`, `parse_front_matter()` — the ATX parser,
  split/merge algorithm, and no-silent-loss reconstruction are reused; the Absatz-marker grouping
  and `law`/`citation` construction are retargeted.
- `docs/stages/chunk.md`, `docs/theory/chunking.md`; `tests/test_chunk.py`,
  `tests/fixtures/{corpus,chunks}/`.

### What to build

Retarget the chunker from norm units to Wikipedia sections: chunk by `##` section, split oversized
sections by subsection (`###`)/paragraph with overlap (replacing the `(1)`-Absatz-marker grouping),
merge tiny same-section units, and drop `max_chars` to fit bge-small-en's 512-token cap (pin the
value ≈ 1,200–1,500 chars, **validated with bge-small-en's own tokenizer** against the fetched
corpus). Change the citation *format* to the §-free `"{source_title} — {unit}"` (the field was
already renamed in Phase 1), with `unit` = the section heading and `section_path` = the heading
trail. Rewrite the chunk contract and chunking theory chapter with Wikipedia examples; the general
chunking theory (fixed-size, recursive-character, structure-aware, overlap) is reused. Per the
fixture-chain rule, this phase replaces the German corpus/chunk fixtures with Wikipedia ones,
retires the German ones, and **regenerates the derived dim-8 `FakeEmbedder` goldens** and updates
`test_embed`'s `GOLDEN_SLUGS` / `test_load`'s fixture slugs so both stay green with chunk unchanged.

### Acceptance criteria

- [ ] `make chunk` produces `data/chunks/<slug>.jsonl` with one record per section (oversized
      sections split into ordered parts with overlap; tiny same-section units merged); output is
      deterministic and re-runnable.
- [ ] Each `Chunk` carries `source_title` (article title), `unit` (section heading), `section_path`
      (heading trail), `citation = "{source_title} — {unit}"`, `source_url`, `fetched_at`, `part` —
      no literal `§` anywhere in `src/rag/chunk/`.
- [ ] `max_chars` is pinned to fit the 512-token cap, with the pinning basis (bge-small-en tokenizer
      measurement over the fetched corpus) recorded in the chunk contract; no chunk exceeds 512
      tokens under bge-small-en.
- [ ] The Absatz-marker grouping and the `ABSATZ_MARKER` regex are removed; the no-silent-loss
      reconstruction (`body_from_parts`) invariant still holds under tests.
- [ ] `docs/stages/chunk.md` and `docs/theory/chunking.md` are rewritten with Wikipedia-section
      examples; the `Current coverage` spot-check is redone and dated.
- [ ] `tests/test_chunk.py` and the `chunks`/`corpus` fixtures are Wikipedia-based, asserted exactly;
      the German corpus/chunk fixtures are retired; `test_embed`/`test_load` still pass against
      regenerated dim-8 goldens and updated fixture slugs; the README **chunking** status-row date is
      updated; `make check` passes.

---

## Phase 4: Embed & load — bge-small-en, dim 384

**User stories**: 1 (fits 4-core/8 GB), 3 (embed batch size knob), 5 (superseded model decisions
removed), 7 (old weights/caches/DB state removed).

### Context

- `src/rag/embed/__init__.py — MODEL_ID`, `EMBEDDING_DIM`, `NORMALIZE_EMBEDDINGS`, `BATCH_SIZE`,
  `SentenceTransformerEmbedder`, `Embedder` (Protocol), the token-guard in `embed_law()` — model
  id/dim/batch change; the `Embedder` interface, lazy import, and token-guard are unchanged.
- `src/rag/load/__init__.py — SCHEMA_SQL`, `EMBEDDING_DIM` import, `join_law()`, `Row`,
  `DISTANCE_OPERATOR`, `HNSW_OPERATOR_CLASS` — vector column `vector(1024)` → `vector(384)` (the
  `source_title` column already renamed in Phase 1); add the dim-mismatch guard.
- `src/rag/retrieve/__init__.py — RetrievedChunk`, `retrieve()` — dim flows from `EMBEDDING_DIM`
  (logic unchanged); English CLI example query.
- `docs/stages/embed.md`, `docs/stages/load.md`, `docs/stages/retrieve.md`,
  `docs/theory/embeddings.md`, `docs/theory/vector-indexes.md`; `docs/roadmap.md` (embedding-model
  decision); `docs/concepts.md` (late-chunking + dense-embedding rows); `docs/prds/prd-embed-load.md`
  (**delete**); `tests/test_embed.py`, `test_load.py`, `test_retrieve.py`,
  `tests/pin_reference_vectors.py`, `tests/fixtures/embeddings/`, `tests/fixtures/reference_vectors.json`.

### What to build

Swap the embedder to `BAAI/bge-small-en-v1.5` (`EMBEDDING_DIM = 384`, symmetric path — no
instruction prefix), with `EMBED_BATCH_SIZE`/`EMBED_MODEL_ID` as env overrides (batch default
lowered for 8 GB). Change the store column to `vector(384)` and add a load guard that raises
`LoadError` (hinting `make reset`) when the existing column dimension disagrees with `EMBEDDING_DIM`.
Rewrite the bge-m3 decision in the roadmap to the bge-small-en
decision (dim 384, 512 tokens, MIT, symmetric path, ≈ 130 MB download, plus the 8 GB-floor embed
measurement recorded here), update the concept-map late-chunking row (condition now **unmet** — no
token/ColBERT vectors, 512 tokens) and dense-embedding row, and **delete `prd-embed-load.md`**.
One-time decommission: remove the bge-m3 snapshot surgically from the HF cache. Migration:
`make reset` (drops the old `vector(1024)` table) then re-run chunk → embed → load.

End-to-end verifiable: after `make reset` and a full offline re-run, `make query Q="<English
question>"` returns plausible club sections ranked by cosine distance.

### Acceptance criteria

- [ ] `make embed` uses `BAAI/bge-small-en-v1.5` (dim 384, normalized, symmetric — no instruction
      prefix for queries or passages) with `EMBED_BATCH_SIZE`/`EMBED_MODEL_ID` honored from the env;
      embedding records stamp `model`/`dim` = the new model/384.
- [ ] The token-guard remains a hard failure over bge-small-en's 512-token window; no chunk is
      silently truncated.
- [ ] `make load` creates `chunks` with `embedding vector(384)` (HNSW on `vector_cosine_ops`, cosine
      `<=>`); loading 384-dim vectors into a stale `vector(1024)` table raises `LoadError` hinting
      `make reset` rather than an obscure insert error.
- [ ] `make query` returns hits with `source_title`, `citation`, `source_url`, and cosine `distance`
      (dim 384 flows from `EMBEDDING_DIM`; no retrieve logic change beyond the English example).
- [ ] The roadmap's embedding-model decision is rewritten (bge-m3 → bge-small-en, dim 384/512 tokens,
      MIT, symmetric path, download size, 8 GB-floor embed measurement); `docs/concepts.md`
      late-chunking row states the condition is now unmet; `prd-embed-load.md` is deleted (with its
      README/roadmap references removed).
- [ ] `docs/stages/embed.md`/`load.md`/`retrieve.md` and `docs/theory/embeddings.md`/`vector-indexes.md`
      are rewritten for bge-small-en/384/English; `load.md`'s `Verification` and retrieve examples use
      English questions.
- [ ] `tests/test_embed.py`/`test_load.py`/`test_retrieve.py`, the `embeddings` fixtures, and
      `reference_vectors.json` are regenerated at dim 384 (via `pin_reference_vectors.py`) and pass;
      the bge-m3 HF snapshot is removed (documented command); the README **embed & load** status row +
      first-run cost (the measured bge-small-en download footprint, replacing the ≈ 4.6 GB bge-m3
      figure) updated; `make check` passes.

---

## Phase 5: Online path — granite4:micro, English prompt with attribution

**User stories**: 2 (grounded English answer with citation + source link), 3 (`num_ctx`/`num_predict`/
model-tag knobs), 5 (qwen3 decision removed), 6 (CC BY-SA attribution satisfied at display), 7
(old LLM removed from the Ollama volume).

### Context

- `src/rag/generate/__init__.py — MODEL_TAG`, `NUM_CTX`, `NUM_PREDICT`, `TEMPERATURE`/`TOP_P`/`TOP_K`/
  `MIN_P`/`SEED`, `_chat_payload()`, `generate()`, `ollama_base_url()` — model tag + context/decoding
  change; streaming/HTTP/error-mapping unchanged.
- `src/rag/assemble/__init__.py — SYSTEM_PROMPT`, `assemble()`, `Prompt`, `MAX_PROMPT_CHARS`,
  `GENERATION_RESERVE_TOKENS`, `CHARS_PER_TOKEN_FLOOR` — German prompt → English + CC BY-SA line;
  budget recomputed from the new `NUM_CTX`/`NUM_PREDICT`.
- `src/rag/ask/__init__.py — main()`, the `Quellen:` block — → `Sources:` + a CC BY-SA licence notice
  alongside the article links.
- `Makefile — llm-pull` (derives the tag from the constant); `docs/stages/assemble.md`,
  `docs/stages/generate.md`, `docs/theory/llm-generation.md`; `docs/roadmap.md` (generation-model
  decision); `tests/test_assemble.py`, `test_generate.py`, `test_ask.py`,
  `tests/fixtures/prompts/`.

### What to build

Swap generation to `granite4:micro` (`NUM_CTX = 4096`, `NUM_PREDICT = 512`, decoding per the Granite
card + a pinned seed), with `LLM_MODEL_TAG`/`LLM_NUM_CTX`/`LLM_NUM_PREDICT` as env overrides. Rewrite
the system prompt in English with the same grounding/abstention/citation directives **plus a CC
BY-SA attribution directive**, translate the user template (`Sources:` / `Question:`), and recompute
`MAX_PROMPT_CHARS` from the new context budget (measured against granite's tokenizer over the corpus).
Make `make ask` print `Sources:` with the article links and a CC BY-SA licence notice so every
answer surfacing an excerpt satisfies attribution at the point of display. Rewrite the qwen3 decision
in the roadmap to the granite4:micro decision, **recording the 8 GB-floor serving validation here**.
One-time decommission: `ollama rm qwen3:4b-instruct`. Migration: `make llm-pull` the new model, then
`make ask`.

End-to-end verifiable: `make llm && make llm-pull && make ask Q="<English club question>"` streams a
grounded English answer with numbered citations, article links, and a CC BY-SA notice — on
4-core/8 GB without swap.

### Acceptance criteria

- [ ] `make ask` generates with `granite4:micro`, `num_ctx` 4096, `num_predict` 512, and the Granite
      card's decoding + a pinned seed; `LLM_MODEL_TAG`/`LLM_NUM_CTX`/`LLM_NUM_PREDICT` override from
      the env; `make llm-pull` pulls the pinned tag.
- [ ] The system prompt is English (grounding, abstention, verbatim-citation, brevity, **CC BY-SA
      attribution** directives); the user template reads `Sources:` / `Question:`; `MAX_PROMPT_CHARS`
      is recomputed from the new budget with the granite-tokenizer basis recorded in the assemble
      contract.
- [ ] `make ask` prints a numbered `Sources:` block with each excerpt's article link and a CC BY-SA
      licence notice; the answer streams token by token as today.
- [ ] The roadmap's generation-model decision is rewritten (qwen3 → granite4:micro, 4096/512, Apache-2.0,
      decoding, the **8 GB-floor serving validation**); `qwen3:4b-instruct` is removed from the Ollama
      volume (documented command).
- [ ] `docs/stages/assemble.md`/`generate.md` and `docs/theory/llm-generation.md` are rewritten
      (English prompt sentences, granite pins, new budget); `generate.md`'s `Verification` uses
      English questions incl. an abstention probe.
- [ ] `tests/test_assemble.py`/`test_generate.py`/`test_ask.py` and the `prompts` fixtures are
      English/granite-based and pass; the README **online-path** status row + first-run cost (≈ 2.1 GB
      model) updated; `make check` passes.

---

## Phase 6: Wrap-up — holistic de-Germanization, floor claim, clean-slate validation

**User stories**: 1 (runs on a 4-core/8 GB laptop, validated), 5 (public docs stay clean and
accurate), 7 (one-command reset verified to leave nothing behind).

### Context

- `README.md` — positioning line, "constraints are features" list, status table, quick start, "The
  corpus — and swapping it" section, first-run costs, licence note, structure table.
- `docs/prds/prd-rag-playbook.md` — corpus, floor, accepted-risks, pillar wording.
- `docs/concepts.md` — de-Germanize the remaining rows (metadata filtering by club/section, query
  expansion of club aliases, GraphRAG over article links, drift = squad/season, hybrid-search
  motivation).
- `docs/roadmap.md` — phase prose re-baselining and the hardware-floor decision (`Interim runtime` /
  8-core-16 GB row → 4-core/8 GB, validated).
- `AGENTS.md` — tech-stack table (corpus/embeddings/LLM/target-runtime rows), rule 3 (corpus line),
  rule 8 (corpus language), target-runtime floor.
- `docs/prds/prd-english-pivot.md` — optionally removed once target-state docs carry the story.

### What to build

Finish the cross-cutting docs that no single stage owns, and prove the clean slate. Rewrite the
README (English Wikipedia framing, the "corpus & swapping it" section, the **4-core/8 GB** floor,
consolidated first-run costs, CC BY-SA licence note, English quick-start questions), the product PRD
(corpus, floor, accepted-risks), the concept map's remaining de-Germanized rows, the roadmap phase
prose and the hardware-floor decision, and the AGENTS.md tech-stack/rule/floor lines. Then verify the
pivot left nothing behind: a `grep` in `src/` for German-specific identifiers (`§`, `Absatz`,
`laws.toml`, `bge-m3`, `qwen3`) comes back clean, `data/` holds only the new corpus, the DB has only
`vector(384)` rows, the Ollama volume has `granite4:micro` but not `qwen3:4b-instruct`, and the HF
cache has bge-small-en but not bge-m3. Finally, run `make reset` followed by the full pipeline on a
4-core/8 GB machine to reproduce the demo from nothing **without swap**, and record the date.
Optionally remove this pivot PRD.

### Acceptance criteria

- [ ] The README describes the English Wikipedia (Premier League) corpus, the 4-core/8 GB floor, the
      CC BY-SA licence note, English quick-start example questions, and consolidated first-run costs;
      the status table carries the dated clean-checkout verification.
- [ ] `docs/prds/prd-rag-playbook.md`, `docs/concepts.md`, `docs/roadmap.md` (phase prose +
      hardware-floor decision), and `AGENTS.md` (tech-stack, rule 3, rule 8, target runtime) are fully
      de-Germanized and state the 4-core/8 GB floor.
- [ ] `grep -rE '§|Absatz|laws\.toml|bge-m3|qwen3' src/` returns nothing; `data/` holds only the new
      corpus; the DB has only `vector(384)` rows; the Ollama volume has `granite4:micro` and not
      `qwen3:4b-instruct`; the HF cache has bge-small-en and not bge-m3.
- [ ] `make reset` followed by the full pipeline (`make fetch` → `load`, `make llm-pull`, `make ask`)
      reproduces the demo from nothing on a 4-core/8 GB machine **without swap**; the date is recorded
      in the README status.
- [ ] `docs/prds/prd-english-pivot.md` is removed (or a note records why it is kept); `make check`
      passes.
