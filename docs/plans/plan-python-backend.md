# Plan: Learner-facing mini web app (track b) — FastAPI + plain HTML

> Source: conversation, 2026-07-18. Not a PRD-driven roadmap phase.

## Framing — two parallel tracks

The repo has two kinds of work:

- **(a) the project core** — the RAG learning content itself (evaluation, hybrid search,
  reranking, …), the numbered roadmap backlog. Each item lands with the full
  definition-of-done (code + tests + theory chapter + stage contract + dated README status).
- **(b) the surroundings for learners** — tooling that makes the system *experienceable*, so
  the effects of the (a) work are visible as a user. **This plan is (b).**

Because (b) is tooling, not a learning building block, it is **exempt from the (a) DoD**: no
theory chapter, no concept-map entry, no stage contract. It still carries tests (they are part
of `make check`, not learning ballast) and the minimal doc edits needed to keep the checked-in
instructions honest after the stack change.

The mini app's real value for (a): its **retrieval view** (`/search` — ranked chunks with
distances) is the instrument that makes a retrieval improvement from (a) visible at a glance.

## Resolved decisions

- **Backend stack**: **Python + FastAPI + uvicorn** (single worker). Replaces the earlier
  "Go backend" intent. New deps `fastapi` + `uvicorn` (approved).
- **Frontend**: **one static `index.html` + vanilla JS**, served by FastAPI. No React, no npm,
  no build step — matches the repo's framework-free ethos and the "minimal" goal. A richer
  React frontend stays a *later* option (the full chat app, roadmap #15), not this plan.
- **Warm model**: the embedder is constructed **once** at startup (FastAPI lifespan) and reused
  per request — the reason to have a persistent process at all (the CLIs reload the model per
  `make ask`/`make query`).
- **Response mode**: blocking JSON. Token streaming (SSE) is out of scope.
- **Scope of governance**: record the stack change as a dated roadmap decision and fix the two
  now-stale `AGENTS.md` references (tech-stack row + rule 4). Nothing more.
- **No auth, localhost only**: binds `127.0.0.1`, single user, unauthenticated by design.

## Architecture

- **Package** `src/rag/api/` (beside the stage packages, not among them — it is not a stage).
  `create_app(*, embedder=None, retrieve_fn=None, generate_fn=None)` builds the FastAPI app;
  a module-level `app = create_app()` is what `uvicorn`/`python -m rag.api` serve. The
  injectable params let tests run with fakes and no real model/DB/network.
- **Shared composition seam**: extract the retrieve → assemble → generate wiring inlined in
  `rag.ask.main()` into `rag.ask.answer_question(question, top_k, *, retrieve_fn, generate_fn,
  on_delta=…, on_retrieved=…, on_assembled=…) -> Answer` where
  `Answer { answer: str, hits: list[RetrievedChunk], stats: GenerationStats }`. The CLI passes
  its printing/logging callbacks (streaming + per-step stderr log preserved byte-for-byte); the
  API passes none. One wiring, two callers. `_default_retrieve_fn` → public `default_retrieve_fn`
  (now used by both CLI and API).
- **Routes**:
  - `GET /health` → `HealthResponse { status, model_loaded, database, ollama }` — model loaded?
    Postgres reachable? Ollama reachable? (live checks, short timeouts).
  - `POST /ask` → `AskResponse { answer, sources: [Source], licence, stats }` — the full RAG
    answer; `licence` carries the CC BY-SA notice the UI displays.
  - `POST /search` → `SearchResponse { hits: [Hit] }` — retrieval only, with `distance` + `text`.
  - `GET /` → the static `index.html` (Slice 2).
- **Schemas** (Pydantic v2): `AskRequest { question, top_k=TOP_K }` — the shared `/ask` and
  `/search` request body — `Source { n, citation, source_title, source_url }`,
  `Hit { n, citation, source_title, source_url, distance, text }`,
  `GenerationStatsOut` mirrors `rag.generate.GenerationStats`. `TOP_K` is the existing
  `rag.retrieve.TOP_K` (5); `top_k` validated `1 <= top_k <= MAX_TOP_K` (50).
- **Error mapping**: `RetrieveError` → 503, `AssembleError` → 422, `GenerateError` → 503,
  invalid body / `top_k < 1` → 422 (Pydantic).
- **Endpoints are sync `def`**: FastAPI runs them in its threadpool, so the CPU embed, the
  blocking DB call, and the minutes-long Ollama stream never block the event loop.
- **Config**: `API_HOST` (default `127.0.0.1`), `API_PORT` (default `8000`); Postgres/Ollama
  config unchanged. `make serve` → `uv run python -m rag.api`.

## Slice 1 — backend + governance

FastAPI app under `src/rag/api/` with `/health`, `/ask`, `/search`; the extracted
`answer_question` seam; error mapping; `make serve`; `fastapi`/`uvicorn` deps;
`API_HOST`/`API_PORT` in `.env.example`; the roadmap decision entry + the two `AGENTS.md`
edits. Tests: `/health` shape (fake embedder, probes stubbed), `/ask` happy path + each error
mapping, `/search` happy path + `RetrieveError` → 503, and one test that `answer_question` and
the CLI agree for the same fakes. `make check` passes. Demoable with `curl`.

## Slice 2 — static frontend + verify

One self-contained `src/rag/api/static/index.html` (inline CSS/JS) served at `GET /`: a
question box, the streamed answer text, a numbered sources list with links + the CC BY-SA
notice, and a **retrieval-view table** (`/search`: distance · citation · text). README quick-start
line for `make serve`. End-to-end verification against the running stack, dated.

## Out of scope

Token streaming (SSE); auth/exposure; CORS + a React/SPA frontend; a psycopg connection pool;
containerizing the app; a serving theory chapter; a `concepts.md` entry. All deferred or
declined per the framing above.
