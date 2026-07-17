# Plan: Roadmap Phase 4 — Online PoC (CLI question answering)

> Source PRD: n/a — planned from [../roadmap.md](../roadmap.md) "Phase 4 — Online PoC
> (CLI question answering)" and the playbook PRD's
> ["Architecture: stage = module"](../prds/prd-rag-playbook.md) section.

## Goal

Close the loop: land the three online stages — **retrieve** (question → ranked chunks),
**assemble** (question + ranked chunks → prompt), **generate** (prompt → grounded answer)
— plus one composing CLI, so `make ask Q="<question>"` embeds a German legal question with
the pinned bge-m3 model, pulls the top-k §§ from Postgres/pgvector, assembles a grounded
prompt with citations, streams an answer from a local open-weight LLM served by Ollama,
and prints the sources. Every intermediate is logged, failures are debuggable, and the
whole serving side remains `docker compose up` on the 8-core/16 GB CPU-only floor.

Per [AGENTS.md](../../AGENTS.md) rule 5, the phase is done only with all five
deliverables: code + tests + theory chapter (`docs/theory/llm-generation.md`) + stage
contracts (`docs/stages/retrieve.md`, `docs/stages/assemble.md`,
`docs/stages/generate.md`) + README status with a verification date. The work is broken
into six **slices** ("Phase" always means the roadmap phase).

## Architectural decisions

Durable decisions that apply across all slices:

- **Stage = subpackage; one composing CLI.** Three online stage subpackages —
  `src/rag/retrieve/`, `src/rag/assemble/`, `src/rag/generate/` — plus a thin composition
  package `src/rag/ask/` that runs the online path in one process per question:
  `uv run python -m rag.ask "<question>"`, wrapped by `make ask Q="..."`. The stages stay
  single-responsibility per the playbook PRD's taxonomy; `ask` is the documented entry
  point of the online path, not a fourth stage. retrieve additionally keeps a standalone
  runnable entry (`python -m rag.retrieve "<question>"`) because it directly supersedes
  the Phase 3 dev query tool (see Resolved decisions).

- **Ollama as the second Compose service** (per the dated 2026-07-10 Docker decision):
  service `ollama` in `docker-compose.yml` with a pinned image tag, models in a named
  volume, port 11434 published to localhost, and the same log-size caps as the postgres
  service. Makefile targets `make llm` (start the service) and `make llm-pull` (pull the
  pinned model into the volume); `make down` already stops everything. The model tag is
  single-sourced from the generate stage's pinned constant — the Makefile derives it
  instead of duplicating the string.

- **LLM pinned by a dated decision** (Slice 1, mirroring the Phase 3 embedding-model
  decision): one open-weight 7–8B-class instruct model under a **strict open-source
  license** (Apache-2.0/MIT-class weights), served as a quantized GGUF from the Ollama
  library. The decision entry pins together: model + exact quantized tag, context length
  (`num_ctx`), decoding parameters (temperature-0-style deterministic decoding unless the
  in-phase research records a reason to deviate), and the provisional top-k — because k,
  chunk sizes, and `num_ctx` form one context budget. Values live as constants in
  `src/rag/generate/` (and the pinned k in `src/rag/retrieve/`), reasoning lives in the
  roadmap entry — the same split as `MODEL_ID` in `src/rag/embed/`.

- **Question embedding = the pinned Phase 3 model.** retrieve embeds the question through
  the existing `Embedder` interface (`SentenceTransformerEmbedder`, bge-m3) and searches
  with the pinned cosine-distance operator `<=>` — the deliberate coupling named in the
  playbook PRD and the 2026-07-14 model decision. No second embedding code path.

- **Prompt shape (assemble):** a deterministic, stable-layout prompt for Ollama's chat
  API — a static German **system prompt** carrying the grounding and abstention
  directives ("answer only from the provided §§; say so if they don't contain the
  answer"), the answer-in-German directive, and the citation instruction; a **user
  message** of numbered context blocks (`[n]` + the chunk's `citation` label + its text,
  in rank order) followed by the question last. Static parts first, per-question parts
  last — the prompt-prefix-reuse motivation the theory chapter explains.
  Re-ordering against lost-in-the-middle stays Backlog 7.

- **No silent truncation, MVP guard:** assemble enforces a conservative character-based
  context budget derived from the pinned `num_ctx` and fails loudly (`AssembleError`)
  instead of letting Ollama silently truncate the context. Exact token counting with the
  served model's own tokenizer is deliberately Backlog 7; the guard's margin and its
  derivation are recorded in the Slice 1 decision entry.

- **Ollama over plain HTTP with httpx — no new dependencies.** generate talks to
  Ollama's localhost REST API with the already-present `httpx`, streaming the response
  (NDJSON chunks) so tokens print as they decode — on CPU the first token can take tens
  of seconds and streaming is the difference between a working tool and an apparently
  hung one. No `ollama` client package, no framework — the HTTP contract is part of what
  the playbook teaches. Endpoint and payload shape are verified against the current
  Ollama API docs in-phase, never from memory.

- **Connection configuration**, mirroring the Postgres pattern: `OLLAMA_HOST` (default
  `localhost`) and `OLLAMA_PORT` (default `11434`) from the environment, documented as
  commented-out defaults in `.env.example`. No other configuration mechanism.

- **Step-level logging lives in the composing CLI:** `rag.ask` logs every intermediate
  to stderr — the question, each retrieved chunk with distance and citation, the
  assembled prompt's size, and the generation stats Ollama reports (token counts,
  durations) — while the answer streams to stdout followed by a numbered sources block
  (citation + source URL per retrieved chunk). The full assembled prompt prints only
  with `--verbose`. The stage contracts document this as the online path's
  inspectability contract (the PRD's step-level-logging promise, seeding Backlog 8).

- **Failure hints follow the established pattern:** unreachable database → "run
  `make db` first" (existing), empty/missing `chunks` table → "run `make load` first"
  (existing), unreachable Ollama → "run `make llm` first", model not pulled → "run
  `make llm-pull` first". Non-zero exit on any failure.

- **Test tiers, unchanged pattern:** the default suite (`make test`, CI) uses the fake
  embedder, a fake generator behind an httpx `MockTransport`, and no database — golden
  prompt files for assemble, streaming/error-mapping tests for generate, composition
  tests for ask. Real-database and real-Ollama behavior live in opt-in tests marked
  `integration` (existing marker) that skip cleanly with a reason when the service is
  unavailable. Nothing in `make check` requires a download or a running container.

## Key models

- **`RetrievedChunk`** — one ranked hit as retrieve returns it and assemble/ask consume
  it: `id`, `law`, `citation`, `source_url`, `text`, `distance`. The fields downstream
  stages need — the citation label for context blocks and the sources output, the text
  for the prompt — nothing speculative.
- **`Prompt`** — assemble's output: the `system` and `user` message strings for the chat
  API. Deterministic for fixed inputs; the golden-file unit of the assemble tests.
- **Pinned constants** — `src/rag/generate/`: the Ollama model tag, `num_ctx`, decoding
  parameters; `src/rag/retrieve/`: the pinned top-k. Values in code, reasoning in the
  Slice 1 roadmap decision entry.

## Inventory

- `src/rag/query/__init__.py — main(), search(), format_hit()` — the dev query tool the
  retrieve stage supersedes: question embedding via injectable `Embedder`, the pinned
  `DISTANCE_OPERATOR` query, hit formatting, and the failure-hint wording to carry over.
- `src/rag/embed/__init__.py — Embedder, SentenceTransformerEmbedder, MODEL_ID` — the
  pinned question-embedding interface and the constants-in-code / reasoning-in-roadmap
  pattern the LLM pinning repeats.
- `src/rag/load/__init__.py — connection_conninfo(), DISTANCE_OPERATOR, SCHEMA_SQL` —
  the connection helper and the `chunks` table retrieve reads (`citation`, `law`,
  `source_url`, `text`, `embedding`).
- `src/rag/__init__.py — run_per_law()` — per-law CLI helper; the online path is
  per-question, not per-law, so it is *not* reused — noted so nobody force-fits it.
- `src/rag/fetch/__init__.py` — the existing httpx usage pattern (the dependency is
  already in `pyproject.toml`).
- `tests/test_query.py` — argument/formatting/failure-hint tests to adapt into
  `tests/test_retrieve.py`; `tests/fixtures/` — chunk fixtures reusable for assemble's
  golden prompts.
- `docker-compose.yml — postgres service` — the service shape (pinned image, log caps,
  named volume, healthcheck) the `ollama` service mirrors; `Makefile — db / db-shell /
  down targets` — the pattern for `llm` / `llm-pull`.
- `.env.example` — gains the commented `OLLAMA_HOST` / `OLLAMA_PORT` defaults.
- `docs/stages/load.md — "Verification"` — its spot-check section references the dev
  query tool; pointer updated when the tool is superseded (Slice 2).
- `docs/concepts.md` — the "Top-k results" row references `src/rag/query` (updated in
  Slice 2); the Phase 4 rows across "Retrieval & search" and "Generation & LLM
  interface" gain chapter/contract links in Slice 6.
- `docs/roadmap.md — "Decisions"` — the dated decision-block format; the LLM entry lands
  under it. `docs/plans/plan-embed-load.md` — the slice/decision structure this plan
  mirrors.
- `README.md — status table, quick start, pipeline overview` and `AGENTS.md — Commands
  table, tech-stack table` — updated in Slice 6.

## Resolved decisions

Clarified with the maintainer during planning (2026-07-17):

- **Strict open-source LLM license**: Apache-2.0/MIT-class weights only, resolving the
  roadmap's "open-weight" wording in favor of AGENTS.md rule 1. Community-licensed
  weights (Llama 3.x, Gemma) are out even if they benchmark better on German; the
  decision entry records the resolution.
- **Answers in German**: a single fixed directive in the system prompt — matches the
  corpus, the § citations, and reliability limits of a small quantized model.
- **Default output is concise steps to stderr**; the full assembled prompt only behind
  `--verbose`. No trace-file machinery — real tracing is Backlog 8.
- **retrieve replaces the dev query tool**: `src/rag/query/` is deleted, `make query`
  retargets to `python -m rag.retrieve` with the same UX, `make ask` runs the full loop.
  One retrieval code path.
- **No new dependencies**: Ollama over plain HTTP with the existing httpx; the official
  Python client would hide exactly the interface worth learning.
- **`rag.ask` composition package**: the one-process-per-question entry the PRD
  describes; stages stay importable, side-effect-free functions.

## Open questions / Risks

- **German quality under the strict license bar.** Candidate families to verify live in
  Slice 1 (all claims dated there, never from memory): Qwen and Mistral 7B-class
  instruct models (Apache-2.0), IBM Granite (Apache-2.0), and the German/European-focused
  Teuken-7B / EuroLLM (license and Ollama availability to verify). If no strictly-open
  model produces acceptable grounded German answers, the finding goes back to the
  maintainer as a recorded scope question — the license bar is not silently lowered.
- **CPU latency is real.** A 7–8B model on 8 CPU cores decodes on the order of a few to
  ~15 tokens/s, and prefill of a several-thousand-token prompt adds tens of seconds
  before the first token. Streaming plus step logs make the wait legible; measured
  prefill/decode numbers land in the decision entry, and the theory chapter explains
  them. If measurements are far worse, the fallback (smaller model or tighter k) is
  recorded in the entry.
- **Context budget vs the outlier chunk.** The corpus's largest atomic chunk is ≈ 3,784
  tokens (Phase 3 tokenizer check); k such chunks plus system prompt cannot all be
  assumed to fit an 8k context. The Slice 1 budget math must handle the worst case —
  larger `num_ctx` (KV-cache RAM cost measured), smaller k, or the loud assemble guard —
  and record the choice.
- **16 GB floor at query time** (playbook PRD risk item): bge-m3 question embedding +
  Ollama serving + Postgres must coexist. Slice 5 measures the peak during a real
  `make ask` run and records it in the decision entry.
- **Ollama API drift**: endpoint shape, streaming format, and defaults (`num_ctx`,
  `keep_alive`) are verified against current Ollama docs in Slice 1/4, dated in the
  contract.
- **CI has no Ollama, no database, no model cache** — integration tests always skip
  there; real-loop verification is the documented dated spot-check (Slice 5), per the
  established Phase 3 pattern.
- **Prompt injection via corpus text** is a named non-goal here: retrieved chunks enter
  the prompt verbatim; input/retrieval/output rails are Backlog 9. The assemble contract
  states this.

---

## Slice 1: LLM runtime + model decision (Compose service + dated roadmap entry)

**User stories** (playbook PRD): 1 (runs on an ordinary dev machine), 6 (dated decision
with reasoning).

### Context

- `docker-compose.yml — postgres service` — the service shape to mirror; this Compose
  change is the one AGENTS.md's ask-first rule covers, approved by this plan's review.
- `Makefile — db target` — pattern for `llm` / `llm-pull`.
- `docs/roadmap.md — "Decisions"` — where the dated entry lands, in the established
  context/choice/alternatives/consequences format.
- `docs/roadmap.md — "Embedding model — BAAI/bge-m3"` — the tokenizer measurements
  (median 256 / max 3,784 tokens per chunk) the context-budget math builds on.

### What to build

No pipeline code — the runtime and the decision the phase hangs on. Add the `ollama`
Compose service (pinned image tag, named model volume, localhost port 11434, log caps)
and the `make llm` / `make llm-pull` targets. Research candidate models under the strict
open-source bar from live sources (license text, parameter count, GGUF/Ollama-library
availability, published German-capability evidence — every claim dated); pull the
shortlisted model(s) and measure on this machine: prefill and decode tokens/s on a
representative assembled prompt, and serving memory. Work the context budget: pinned
`num_ctx`, provisional top-k, and the assemble guard margin, against the known chunk
token distribution including the 3,784-token outlier. Record the dated decision entry
pinning model + tag, `num_ctx`, decoding parameters, provisional k, and the budget math,
with alternatives weighed and the strict-license and German-answer resolutions noted.

### Acceptance criteria

- [ ] `make llm` starts the pinned Ollama service; `make llm-pull` pulls the pinned
      model into the named volume; a smoke prompt via the HTTP API answers.
- [ ] A dated decision entry in `docs/roadmap.md` pins model + quantized tag, `num_ctx`,
      decoding parameters, and provisional top-k together, with live-verified license and
      capability claims, measured prefill/decode throughput and serving memory from this
      machine, and the context-budget math.
- [ ] `make check` is green — no pipeline code changed.

---

## Slice 2: Retrieve stage — question → ranked chunks (supersedes the dev query tool)

**User stories** (playbook PRD): 3 (stage modules with contracts), 4 (documented
downstream field requirements).

### Context

- `src/rag/retrieve/` — new subpackage with `__main__.py`; created here.
- `src/rag/query/` — deleted here; its embedding injection, search SQL, formatting, and
  failure hints carry over.
- `src/rag/embed/__init__.py — Embedder` and `src/rag/load/__init__.py —
  connection_conninfo(), DISTANCE_OPERATOR` — the composed pieces.
- `tests/test_query.py` — adapted into `tests/test_retrieve.py`.
- `Makefile — query target`, `docs/stages/load.md — "Verification"`,
  `docs/concepts.md — "Top-k results" row` — pointers retargeted from `rag.query` to
  `rag.retrieve` in this slice.
- `docs/stages/retrieve.md` — the stage contract; created here.

### What to build

The retrieve stage: a public `retrieve(question, ...)` that embeds the question through
the injectable `Embedder` (real default: the pinned bge-m3) and returns the top-k
`RetrievedChunk` records ordered by the pinned cosine distance — plus a runnable
`python -m rag.retrieve "<question>"` printing rank, distance, citation, and snippet
exactly as the dev tool did (`--top-k` overrides the pinned default). Delete
`src/rag/query/`, retarget `make query`, and update the two doc pointers that name it.
The stage contract documents the entry point, the pinned-model coupling to embed, the
`chunks`-table fields it reads and the `RetrievedChunk` fields it promises downstream,
its logging when run standalone, and the failure hints.

Tests: the adapted argument/formatting/failure-hint tests with the fake embedder and no
database; the existing opt-in `integration` pattern against the Compose Postgres.

### Acceptance criteria

- [ ] `make query Q="..."` works unchanged in UX but runs `rag.retrieve`;
      `src/rag/query/` no longer exists; no doc references the deleted module.
- [ ] `retrieve()` returns `RetrievedChunk` records (`id`, `law`, `citation`,
      `source_url`, `text`, `distance`) in ascending-distance order, k pinned as a
      constant with a CLI override.
- [ ] `docs/stages/retrieve.md` documents entry point, model coupling, consumed table
      fields, promised record fields, logging, and failure behaviour.
- [ ] Default tests pass without model or database; `make check` is green.

---

## Slice 3: Assemble stage — question + ranked chunks → prompt

**User stories** (playbook PRD): 2 (grounded answer with citations — the prompt half),
3 (stage modules with contracts).

### Context

- `src/rag/assemble/` — new subpackage; created here.
- Architectural decisions — Prompt shape, No silent truncation.
- The Slice 1 decision entry — `num_ctx` and the guard margin the budget check uses.
- `tests/fixtures/chunks/` — fixture chunks for golden prompt files.
- `docs/stages/assemble.md` — the stage contract; created here.

### What to build

A pure `assemble(question, chunks) -> Prompt`: the static German system prompt with the
grounding, abstention, answer-in-German, and citation directives (wording drafted here,
refined against real model behaviour in Slice 5), and the user message of numbered
context blocks — `[n]`, the chunk's `citation`, its `text`, in rank order — with the
question last. Deterministic: same inputs, byte-identical prompt. At least one chunk is
required; a prompt over the character budget raises `AssembleError` naming the budget
and the actual size — never silent truncation. The stage contract documents the entry
point, the exact layout and its stable-prefix rationale, the budget guard and its
Backlog 7 boundary (token-exact counting deferred), the required `RetrievedChunk`
fields, and the Backlog 9 boundary (chunks enter verbatim; injection rails deferred).

Tests: golden prompt files from fixture chunks (byte-exact, run-twice determinism), the
budget guard, the no-chunks error, and citation-numbering correctness.

### Acceptance criteria

- [ ] `assemble()` produces the documented layout deterministically; golden-file tests
      pass byte-exactly with no model, database, or network.
- [ ] Over-budget input fails loudly with the measured size in the message; zero chunks
      is an error.
- [ ] `docs/stages/assemble.md` documents layout, rationale, guard, consumed fields, and
      deferred boundaries.
- [ ] `make check` is green.

---

## Slice 4: Generate stage — prompt → streamed grounded answer

**User stories** (playbook PRD): 2 (grounded answer — the inference half), 3 (stage
modules with contracts).

### Context

- `src/rag/generate/` — new subpackage; created here, holding the pinned constants from
  Slice 1.
- Architectural decisions — Ollama over plain HTTP, Connection configuration, Failure
  hints.
- `src/rag/load/__init__.py — connection_conninfo()` — the env-with-defaults helper
  pattern `ollama_base_url()` mirrors.
- `.env.example` — gains the commented `OLLAMA_HOST` / `OLLAMA_PORT` block.
- `docs/stages/generate.md` — the stage contract; created here.

### What to build

The generate stage: a public function that sends the `Prompt` to Ollama's chat endpoint
(payload and streaming format verified against current Ollama docs, dated in the
contract) with the pinned model tag, `num_ctx`, and decoding parameters, and yields the
answer text incrementally as it decodes, exposing Ollama's final stats (prompt/response
token counts, durations) for the caller's step logs. Failure mapping to `GenerateError`
with actionable hints: connection refused → "run `make llm` first"; unknown model → "run
`make llm-pull` first"; other non-success responses surface status and body. The stage
contract documents the entry point, the HTTP contract as taught material, configuration,
the pinned constants and their decision link, streaming semantics, and failure
behaviour.

Tests: httpx `MockTransport` covering streamed-chunk reassembly, stats extraction, and
each failure mapping; one opt-in `integration` test against the real Ollama service
(skips with a reason when unreachable).

### Acceptance criteria

- [ ] `generate()` streams answer text against a mock transport, returns the reported
      stats, and maps each failure mode to its hint; default tests need no network.
- [ ] The integration test passes locally with `make llm` up and the model pulled, and
      skips cleanly elsewhere.
- [ ] `docs/stages/generate.md` documents entry point, HTTP contract (dated), pinned
      constants, streaming, configuration, and failure behaviour.
- [ ] `make check` is green.

---

## Slice 5: Ask CLI — compose the loop, verify end to end, dated spot-check

**User stories** (playbook PRD): 2 (ask a question in the terminal, grounded answer with
citations), 1 (documented commands on an ordinary machine).

### Context

- `src/rag/ask/` — new subpackage with `__main__.py`; created here.
- Architectural decisions — Step-level logging, Failure hints.
- `Makefile` — new `ask` target (`make ask Q="..."`).
- `docs/stages/generate.md` — gains the dated end-to-end spot-check section (the phase's
  proof, mirroring `load.md`'s verification section).
- The Slice 1 decision entry — updated here with the measured coexistence memory, answer
  latency, and the confirmed (or corrected) k.

### What to build

The composition: `python -m rag.ask "<question>"` runs retrieve → assemble → generate
with step logs to stderr (question; each hit's rank, distance, citation; prompt size;
generation stats), streams the answer to stdout, then prints the numbered sources block
(citation + source URL per retrieved chunk). `--top-k` overrides the pinned k,
`--verbose` additionally prints the full assembled prompt. Retrieval and generation are
injectable for tests, following the query tool's embedder-injection precedent.

Then the phase's proof: with the full corpus loaded and Ollama serving, run hand-written
German questions with known expected §§ (Kassen → KassenSichV, Steuer → AO/UStG,
Grundrechte → GG) plus one abstention probe the corpus cannot answer (e.g. a
Mietrecht/BGB question) and confirm the answer cites retrieved §§ or abstains as
directed — refining the system-prompt wording against real model behaviour if needed
(re-pinning assemble's golden files in the same change). Record questions, answers,
observed grounding/abstention behaviour, and the date as the spot-check section; measure
peak memory of the coexisting services and end-to-end latency during these runs and
update the decision entry. Anecdotal by design — metrics are Backlog 1.

Tests: composition tests with fake retrieval and a fake generator — step-log lines,
sources block, flag handling, failure propagation — no network, no database.

### Acceptance criteria

- [ ] `make ask Q="..."` streams a German answer and prints sources; steps log to
      stderr; `--verbose` shows the full prompt; every failure mode exits non-zero with
      its hint.
- [ ] The spot-check section in `docs/stages/generate.md` records dated questions,
      answers, grounding and abstention observations; the abstention probe abstains.
- [ ] The decision entry gains measured query-time peak memory (within the 16 GB floor),
      end-to-end latency, and the final k.
- [ ] Composition tests pass without services; `make check` is green.

---

## Slice 6: Theory chapter, cross-links, README status, phase wrap-up

**User stories** (playbook PRD): 5 (theory next to code), 7 (front door states landed vs
planned), 11 (pedagogy in the definition of done).

### Context

- `docs/theory/llm-generation.md` — the phase's theory chapter; created here.
- `docs/concepts.md` — the Phase 4 rows across "Retrieval & search" and "Generation &
  LLM interface" gain their links.
- `README.md — status table, quick start, pipeline overview`, `AGENTS.md — Commands +
  tech-stack tables`, `docs/roadmap.md — "Phase 4"` heading — the honesty artifacts.
- `src/rag/retrieve/ … src/rag/ask/` module docstrings — gain the contract/chapter
  cross-links.

### What to build

The pedagogy and honesty artifacts completing the definition of done.
`llm-generation.md`: CPU inference (prefill vs decode and why streaming matters), KV
caching, prompt caching / prompt-prefix reuse (why assemble's stable layout is cheap),
GGUF weight quantization, chain-of-thought cost/benefit for a small quantized model, and
the grounding/abstention prompt techniques — each concept exactly once, cross-linked
both ways with the code, contracts, and the decision entry. Concept-map updates:
Semantic search, Top-k results, Prompt assembly / context packing, Prompt caching,
KV caching, Groundedness, Hallucination prevention, Chain-of-thought (CoT), and
LLM weight quantization (GGUF) rows point at the landed chapter/stages; no concept
added, moved, or dropped.

README: Phase 4 row ✅ with a fresh clean-checkout verification date (the full offline
pipeline plus `make llm`, `make llm-pull`, `make ask`); quick start gains the three new
commands with the first-run LLM download size stated up front; the pipeline overview's
online rows link contract + chapter. AGENTS.md gains the new commands and the pinned
LLM in the tech-stack table; the roadmap Phase 4 heading flips to ✅.

### Acceptance criteria

- [ ] `docs/theory/llm-generation.md` exists, concise, each concept once, cross-linked
      both ways (docstrings → chapter; chapter → code, contracts, decision entry).
- [ ] The named `docs/concepts.md` rows link to the landed places; no concept added,
      moved, or dropped.
- [ ] README: Phase 4 ✅ with a clean-checkout verification date; quick start includes
      `make llm`, `make llm-pull`, `make ask` and states the LLM download cost; every
      relative link resolves.
- [ ] AGENTS.md lists the new commands and the pinned LLM; roadmap Phase 4 heading ✅.
- [ ] Definition-of-done audit against AGENTS.md rule 5 passes — code + tests + theory
      chapter + three stage contracts + README status with date; `make check` green.
