# Plan: Python HTTP backend (FastAPI) over the online path

> Source PRD: n/a (from task description — "Python backend", clarified in conversation)

## Goal

Stand up a small, long-lived **Python HTTP backend** that serves the existing online path
(retrieve → assemble → generate) over HTTP, with the embedding model **loaded once at
startup and kept in-process** (the CLI reloads bge-small-en-v1.5 on every `make ask`/`make query`).
The backend becomes the substrate for the future React chat UI and for inspecting
retrieval/evaluation quality in a browser as later backlog items land.

Backend API only — no React frontend in this plan.

## Roadmap context (read first)

The chat web app is **Backlog #15** in `docs/roadmap.md` — deliberately the last
enhancement, after 14 learning phases (evaluation, hybrid search, reranking, …). The user
has decided to **pull the backend forward and build it next**, ahead of backlog items 1–14,
for two reasons: (1) a persistent process keeps the embedding model warm in memory, and
(2) a web surface makes the upcoming evaluation/tuning/hybrid work visible in a UI.

This is a conscious reprioritization of the roadmap order, not a skip — it must be
**recorded** (a dated decision entry + a note that #15 was pulled forward), and the Go→Python
stack change must be recorded in the same place. That paperwork is part of Phase 1.

Per project rule 5, a roadmap phase is done only with **code + tests + theory chapter +
documented contract + README status update (dated)**. The three phases below are *sub-slices
of one roadmap phase* (the backend). The phase-level deliverables — the serving theory
chapter, the README status update, and the concept-map update — land with the final
sub-phase (Phase 3); the HTTP API contract doc first appears in Phase 2 (covering `/health`
and `/ask`) and is extended in Phase 3.

## Architectural decisions

Durable decisions that apply across all phases:

- **Package**: new subpackage `src/rag/api/` (not a pipeline stage, so it sits beside the
  stage subpackages, not among them). `__init__.py` builds the FastAPI app (an app-factory
  function plus a module-level `app`); `__main__.py` runs uvicorn for `python -m rag.api`.
- **Framework**: **FastAPI** + **uvicorn** (chosen in clarification). New dependencies —
  see *Resolved decisions*. Tests use FastAPI's `TestClient` (httpx-based; httpx is already
  a dependency).
- **Routes**:
  - `GET /health` — liveness/readiness: is the model loaded, is Postgres reachable, is
    Ollama reachable.
  - `POST /ask` — the full RAG answer (blocking JSON).
  - `POST /search` — retrieval only (ranked chunks), for retrieval-quality inspection.
- **Request/response schemas** (Pydantic v2 models; these field names are durable):
  - `AskRequest { question: str, top_k: int = TOP_K }`
  - `AskResponse { answer: str, sources: list[Source], stats: GenerationStatsOut }`
  - `SearchRequest { question: str, top_k: int = TOP_K }`
  - `SearchResponse { hits: list[Hit] }`
  - `Source { n: int, citation: str, source_title: str, source_url: str }`
  - `Hit { n: int, citation: str, source_title: str, source_url: str, distance: float, text: str }`
  - `HealthResponse { status: str, model_loaded: bool, database: bool, ollama: bool }`
  - `GenerationStatsOut` mirrors the fields of `rag.generate.GenerationStats`.
  - `TOP_K` is the existing `rag.retrieve.TOP_K` (5) — the API does not introduce its own default.
- **Warm-model lifecycle**: the embedder (`rag.embed.SentenceTransformerEmbedder`) is
  constructed **once** in a FastAPI lifespan handler and stored on app state; every request
  reuses it. A retrieve function bound to that warm embedder (reusing the wiring in
  `rag.ask._default_retrieve_fn`) is built once at startup and reused per request.
- **Single worker**: uvicorn runs **one worker**. The embedding model and its torch
  runtime live in-process; multiple workers would each load their own copy, and the API
  shares the 4-core/8 GB floor with Postgres and the Ollama runtime. No `--reload`
  (a reload subprocess would reload the model).
- **Blocking, threadpool concurrency**: endpoints are defined as sync `def` so FastAPI runs
  them in its threadpool — `retrieve` (CPU embed + blocking DB call) and `generate`
  (minutes-long blocking HTTP stream) must not block the event loop. In practice a CPU-only
  box serves one generation at a time; that is acceptable.
- **Shared composition seam**: extract the retrieve → assemble → generate wiring currently
  inlined in `rag.ask.main()` into a reusable composition function in `rag/ask/` that returns
  a structured result (answer text, the ranked `RetrievedChunk` list, and `GenerationStats`)
  and threads generate's existing `on_delta` callback through (default no-op). The CLI passes
  a printing `on_delta` (keeps its live streaming); the `/ask` handler passes a no-op and
  returns the assembled result. One reference implementation, no duplicated wiring.
- **Error mapping** (stage exceptions → HTTP status):
  - `rag.retrieve.RetrieveError` → **503** (store not reachable / not loaded — infra).
  - `rag.assemble.AssembleError` → **422** (prompt over budget — client retries a smaller
    `top_k`).
  - `rag.generate.GenerateError` → **503** (Ollama unreachable / model missing — infra).
  - Malformed body / `top_k < 1` → **422** via Pydantic validation (automatic).
- **Config** (env vars, mirroring the existing `POSTGRES_*` / `OLLAMA_*` pattern):
  `API_HOST` (default `127.0.0.1`), `API_PORT` (default `8000`). Postgres and Ollama config
  are the existing variables, unchanged. New keys added to `.env.example` as optional.
- **Make target**: `make serve` — `uv run uvicorn rag.api:app --host $(API_HOST) --port
  $(API_PORT)` (or `uv run python -m rag.api`). Runs on the host like `make ask`; the app is
  **not** containerized in this plan (deployment-time dockerization stays deferred, per the
  roadmap's "future app dockerized at deployment time").
- **No auth, localhost only**: the backend binds `127.0.0.1` and is unauthenticated by
  design — a local dev tool, single user. Auth/exposure is out of scope.

## Inventory

Relevant existing code the backend reuses (all under `src/rag/`):

- `ask/__init__.py — main()` — the current retrieve → assemble → generate composition; its
  `_default_retrieve_fn(embedder)` builds a retrieve function bound to an embedder, and its
  `on_delta` streams tokens to stdout. The composition body is what Phase 2 extracts.
- `retrieve/__init__.py — retrieve()`, `check_connection_settings()`, `RetrievedChunk`
  (`id, source_title, citation, source_url, text, distance`), `TOP_K` (5), `RetrieveError`.
- `assemble/__init__.py — assemble()`, `Prompt`, `AssembleError` (raises when no chunks or
  prompt over `MAX_PROMPT_CHARS`).
- `generate/__init__.py — generate(prompt, *, on_delta)`, `GenerateResult` (`answer`,
  `stats`), `GenerationStats` (token counts + phase timings), `ollama_base_url()`,
  `GenerateError`.
- `embed/__init__.py — SentenceTransformerEmbedder`, `Embedder` (Protocol) — the warm model.
- `load/__init__.py — connection_conninfo()` — Postgres conninfo from env; raises `LoadError`.
- `Makefile` — `.env` is `-include`d and `export`ed; targets like `ask:` run
  `uv run python -m rag.<stage>`. Add `serve:` alongside.
- `pyproject.toml` — `[project.dependencies]` (add fastapi + uvicorn); CPU-only torch pin
  stays untouched.
- `.env.example` — documents `POSTGRES_*` / `OLLAMA_*`; add optional `API_HOST` / `API_PORT`.
- `docker-compose.yml` — Postgres + Ollama services (unchanged; the API is not containerized here).

Testing conventions to follow: `tests/`, `test_*.py`, fakes injected through the existing
seams (fake `Embedder`, fake `retrieve_fn`/`generate_fn`), no DB/model/network in unit tests;
`integration` marker for anything needing the real model or a reachable DB.

## Resolved decisions

- **Stack change**: the future backend is **Python (FastAPI)**, not Go. React stays as the
  future frontend. To be recorded in `docs/roadmap.md` (dated decision entry), and reflected
  in `AGENTS.md` (tech-stack table row "Future app", and rule 4 "Go/React are reserved for
  the future web app" → "React is reserved for the future web app").
- **Sequencing**: backend pulled ahead of backlog items 1–14 (conscious reprioritization;
  recorded, not silent).
- **Framework**: FastAPI + uvicorn — new dependencies, explicitly approved in clarification.
- **Response mode**: blocking JSON now; token streaming (SSE) is a later phase, out of scope
  here.
- **Scope**: backend API only; React frontend is a separate future plan.
- **DoD adaptation**: the roadmap-phase deliverables are met in web-app-appropriate form — a
  **serving theory chapter** (`docs/theory/serving.md`) as the "theory chapter", an **HTTP
  API contract doc** (`docs/api.md`) as the "documented contract", plus the README status
  update and `docs/concepts.md` update. These land with Phase 3.

## Open questions / Risks

- **DB connection per request**: `retrieve()` opens a fresh `psycopg.connect` per call. Fine
  at this scale; a connection pool (`psycopg_pool`) is a natural later optimization, out of
  scope here.
- **CORS**: a browser frontend on another dev origin will need CORS middleware. No consumer
  exists yet, so it is deferred to the frontend plan (flagged, not built).
- **DoD wording**: project rule 5 is phrased for pipeline stages ("stage contract"). The
  adaptation above (API contract doc + serving theory chapter) should be confirmed as an
  acceptable reading of rule 5 for the web-app phase before Phase 3 closes.
- **Startup readiness**: the server is not ready until the model finishes loading (seconds
  when cached). `/health` must report `model_loaded` so a caller can distinguish "starting"
  from "ready".

---

## Phase 1: Service skeleton + warm model + `/health`

### Context

- `src/rag/embed/__init__.py — SentenceTransformerEmbedder` — constructed once in the
  lifespan handler; the core new mechanism this phase proves.
- `src/rag/retrieve/__init__.py — check_connection_settings()` and
  `src/rag/generate/__init__.py — ollama_base_url()` — the readiness probes reuse these to
  report database/Ollama reachability.
- `Makefile` (`ask:` target), `pyproject.toml` (`[project.dependencies]`), `.env.example` —
  the wiring to extend.

### What to build

A runnable FastAPI app under `src/rag/api/` that, on startup, constructs the embedding model
**once** and stores it on app state, and exposes `GET /health` reporting whether the model is
loaded and whether Postgres and Ollama are reachable. `make serve` starts it on
`API_HOST:API_PORT` with a single uvicorn worker. Add `fastapi` and `uvicorn` to the project
and `API_HOST`/`API_PORT` to `.env.example`.

This phase also records the governance change that authorizes building now: the Go→Python
decision entry and the "backend pulled forward" note in `docs/roadmap.md`, and the
`AGENTS.md` tech-stack/rule-4 edits.

No RAG flow yet — the tracer proves the warm-model lifecycle and the service boots and
answers a readiness probe.

### Acceptance criteria

- [ ] `make serve` starts a FastAPI app on `API_HOST:API_PORT` (defaults `127.0.0.1:8000`),
      single worker, no reload.
- [ ] The embedding model is constructed exactly once at startup (lifespan handler) and held
      on app state; no per-request model construction.
- [ ] `GET /health` returns `HealthResponse { status, model_loaded, database, ollama }`:
      `model_loaded` reflects the warm embedder; `database`/`ollama` reflect a live
      reachability check (`check_connection_settings()` succeeds / Ollama base URL reachable).
- [ ] `fastapi` and `uvicorn[standard]` added to `[project.dependencies]`; `uv sync` succeeds;
      the CPU-only torch pin is untouched.
- [ ] `API_HOST` / `API_PORT` documented as optional in `.env.example`.
- [ ] `docs/roadmap.md` has a dated decision entry recording the Go→Python stack change and
      the reprioritization of backlog #15 ahead of items 1–14.
- [ ] `AGENTS.md` tech-stack "Future app" row and rule 4 updated (React-only future frontend;
      Python backend).
- [ ] Tests: `GET /health` returns the readiness shape with fakes injected (fake embedder;
      database/Ollama probes stubbed) — no real model, DB, or network in the unit test.
- [ ] `make check` passes.

---

## Phase 2: `POST /ask` (blocking JSON) over the warm model

### Context

- `src/rag/ask/__init__.py — main()` — the composition to extract; note its injectable
  `retrieve_fn`/`generate_fn` and the `on_delta` streaming callback.
- `src/rag/assemble/__init__.py — assemble()`, `Prompt`, `AssembleError`;
  `src/rag/generate/__init__.py — GenerateResult`, `GenerationStats`, `GenerateError`;
  `src/rag/retrieve/__init__.py — RetrievedChunk`, `RetrieveError` — the flow and the error
  types to map.

### What to build

Extract the retrieve → assemble → generate wiring from `rag.ask.main()` into a shared
composition function in `rag/ask/` that returns a structured result (answer text, the ranked
`RetrievedChunk` list, and `GenerationStats`) and threads generate's `on_delta` through
(default no-op). Rewire the CLI `main()` to call it (passing its printing `on_delta`, so CLI
streaming is unchanged). Add `POST /ask`: validate `AskRequest`, run the composition against
the warm retrieve function and the real generate function, and return `AskResponse`
(`answer`, numbered `sources`, `stats`). Map stage errors to HTTP status per the mapping
table.

End-to-end demoable: `make serve`, then `curl -X POST /ask -d '{"question":"…"}'` returns a
grounded English answer with numbered sources — reusing the model loaded in Phase 1, no
reload.

### Acceptance criteria

- [ ] A shared composition function in `rag/ask/` returns `{ answer, hits: list[RetrievedChunk],
      stats: GenerationStats }` and accepts an `on_delta` callback (default no-op).
- [ ] `rag.ask.main()` (the CLI) is rewired to call it and still streams tokens to stdout and
      prints the `Sources:` block — existing `ask` behaviour and tests unchanged.
- [ ] `POST /ask` accepts `AskRequest { question, top_k=TOP_K }` and returns
      `AskResponse { answer, sources: [{n, citation, source_title, source_url}], stats }`.
- [ ] The endpoint reuses the warm embedder from startup (no per-request model construction).
- [ ] Error mapping holds: `RetrieveError` → 503, `AssembleError` → 422, `GenerateError` →
      503, invalid body / `top_k < 1` → 422.
- [ ] Tests: `/ask` happy path with fake `retrieve_fn` + fake `generate_fn` (no DB/model/
      network) returns the answer + sources shape; one test per error mapping; a test that the
      composition function and the CLI produce the same answer/sources for the same fakes.
- [ ] `docs/api.md` documents `/health` and `/ask` (request/response schemas, status codes).
- [ ] `make check` passes.

---

## Phase 3: `POST /search` (retrieval only) + phase-level docs

### Context

- `src/rag/retrieve/__init__.py — retrieve()`, `RetrievedChunk`, `format_hit()` — the
  retrieval the endpoint exposes; `format_hit` shows the existing snippet convention for
  reference.
- `docs/theory/` (existing chapters, e.g. `embeddings.md`, `llm-generation.md`) and
  `docs/concepts.md` — the patterns the new theory chapter and concept entry follow.
- `README.md` status table — the row/date to update.

### What to build

Add `POST /search`: embed the question with the warm model and return the ranked chunks
(`Hit` with `distance` and full `text`) without assembling or generating — the substrate for
inspecting retrieval quality in a future eval/tuning UI (the stated reason for building the
backend now). Then complete the roadmap-phase deliverables: a serving theory chapter, the
concept-map entry, and the README status update.

### Acceptance criteria

- [ ] `POST /search` accepts `SearchRequest { question, top_k=TOP_K }` and returns
      `SearchResponse { hits: [{n, citation, source_title, source_url, distance, text}] }`, nearest
      first, reusing the warm embedder; `RetrieveError` → 503.
- [ ] Tests: `/search` happy path with a fake `retrieve_fn` (no DB/model/network) and the
      `RetrieveError` → 503 mapping.
- [ ] `docs/theory/serving.md` written: online serving of a RAG system — persistent process
      vs CLI, the warm model, blocking vs streaming, single-worker CPU concurrency, request
      lifecycle. Cross-linked both ways with the `src/rag/api/` module docstring
      (theory ↔ code), matching the repo's theory-chapter convention.
- [ ] `docs/concepts.md` updated with the new concept(s) this phase introduces (e.g. online
      serving / warm model residency), one-line definition plus placement.
- [ ] `docs/api.md` extended with `/search`; it now documents the full API surface.
- [ ] `README.md` status table updated: the backend is listed with a dated verification of
      `make serve` + a real `/ask` (and `/search`) round-trip.
- [ ] `make check` passes.
