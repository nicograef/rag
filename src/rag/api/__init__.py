"""API — a small FastAPI service that puts the online path behind HTTP for the learner UI.

Track (b) tooling, not a pipeline stage and not a numbered roadmap phase (see
``docs/plans/plan-python-backend.md``): the learner-facing "surroundings" that make the RAG
system interactive, so the effects of the core work are visible as a user. It loads the
embedding model **once** at startup and keeps it warm — the reason a persistent process
exists at all, since the CLIs (``make ask``/``make query``) reload the model per run. Three
JSON endpoints reuse that warm model: ``/health`` (readiness), ``/ask`` (the full grounded
answer), and ``/search`` (retrieval only — ranked chunks with distances, the instrument that
makes a retrieval change visible). The static single-page UI lands at ``/`` in a later slice.
No theory chapter by design: this is tooling, not a learning building block.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import psycopg
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from rag.ask import (
    GenerateFn,
    RetrieveFn,
    answer_question,
    default_retrieve_fn,
    generate_via_ollama,
)
from rag.assemble import AssembleError
from rag.embed import Embedder, SentenceTransformerEmbedder
from rag.generate import GenerateError, GenerationStats, ollama_base_url
from rag.retrieve import TOP_K, RetrieveError, check_connection_settings

# /health is a readiness probe, not a place to block on a slow dependency, so its live checks
# use a short timeout.
HEALTH_TIMEOUT_SECONDS = 2.0


class AskRequest(BaseModel):
    """The ``/ask`` and ``/search`` request body: a question and how many chunks to retrieve."""

    question: str
    top_k: int = Field(default=TOP_K, ge=1)


class SearchRequest(BaseModel):
    """The ``/search`` request body (same shape as :class:`AskRequest`, kept separate by intent)."""

    question: str
    top_k: int = Field(default=TOP_K, ge=1)


class Source(BaseModel):
    """One cited source in an ``/ask`` answer — the citation and its article link, numbered."""

    n: int
    citation: str
    source_title: str
    source_url: str


class Hit(BaseModel):
    """One ranked ``/search`` hit — a :class:`Source` plus its distance and full chunk text."""

    n: int
    citation: str
    source_title: str
    source_url: str
    distance: float
    text: str


class GenerationStatsOut(BaseModel):
    """The generation accounting, mirroring :class:`rag.generate.GenerationStats` field for field."""

    prompt_tokens: int
    answer_tokens: int
    load_seconds: float
    prompt_eval_seconds: float
    eval_seconds: float
    total_seconds: float
    done_reason: str

    @classmethod
    def from_stats(cls, stats: GenerationStats) -> "GenerationStatsOut":
        """Project the internal dataclass onto the wire model."""
        return cls(
            prompt_tokens=stats.prompt_tokens,
            answer_tokens=stats.answer_tokens,
            load_seconds=stats.load_seconds,
            prompt_eval_seconds=stats.prompt_eval_seconds,
            eval_seconds=stats.eval_seconds,
            total_seconds=stats.total_seconds,
            done_reason=stats.done_reason,
        )


class AskResponse(BaseModel):
    """The ``/ask`` response: the grounded answer, its numbered sources, and the run's stats."""

    answer: str
    sources: list[Source]
    stats: GenerationStatsOut


class SearchResponse(BaseModel):
    """The ``/search`` response: the ranked hits, nearest first."""

    hits: list[Hit]


class HealthResponse(BaseModel):
    """The ``/health`` readiness snapshot: is the model warm, and are the two backends reachable."""

    status: str
    model_loaded: bool
    database: bool
    ollama: bool


def _database_reachable() -> bool:
    """True when the configured Postgres accepts a connection within the health timeout."""
    try:
        conninfo = check_connection_settings()
    except RetrieveError:
        return False
    try:
        with psycopg.connect(conninfo, connect_timeout=int(HEALTH_TIMEOUT_SECONDS)):
            return True
    except psycopg.OperationalError:
        return False


def _ollama_reachable() -> bool:
    """True when the configured Ollama server answers its root within the health timeout."""
    try:
        response = httpx.get(ollama_base_url(), timeout=HEALTH_TIMEOUT_SECONDS)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def create_app(
    *,
    embedder: Embedder | None = None,
    retrieve_fn: RetrieveFn | None = None,
    generate_fn: GenerateFn | None = None,
) -> FastAPI:
    """Build the FastAPI app, wiring the warm model and the online-path stages onto app state.

    The three injectable params keep tests offline: pass a fake ``retrieve_fn`` and
    ``generate_fn`` so no real model, database, or network is touched. The production ``app``
    below (what ``python -m rag.api`` serves) injects nothing, so the lifespan constructs the
    warm embedder once and binds the real stages to it.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        active_embedder = embedder if embedder is not None else SentenceTransformerEmbedder()
        app.state.embedder = active_embedder
        app.state.retrieve_fn = (
            retrieve_fn if retrieve_fn is not None else default_retrieve_fn(active_embedder)
        )
        app.state.generate_fn = generate_fn if generate_fn is not None else generate_via_ollama
        yield

    app = FastAPI(title="RAG Playbook", lifespan=lifespan)

    @app.get("/health")
    def health(request: Request) -> HealthResponse:
        model_loaded = getattr(request.app.state, "embedder", None) is not None
        database = _database_reachable()
        ollama = _ollama_reachable()
        status = "ok" if (model_loaded and database and ollama) else "degraded"
        return HealthResponse(
            status=status, model_loaded=model_loaded, database=database, ollama=ollama
        )

    @app.post("/ask")
    def ask(request: AskRequest, http_request: Request) -> AskResponse:
        try:
            result = answer_question(
                request.question,
                request.top_k,
                retrieve_fn=http_request.app.state.retrieve_fn,
                generate_fn=http_request.app.state.generate_fn,
            )
        except RetrieveError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        except AssembleError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except GenerateError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        return AskResponse(
            answer=result.answer,
            sources=[
                Source(
                    n=n,
                    citation=hit.citation,
                    source_title=hit.source_title,
                    source_url=hit.source_url,
                )
                for n, hit in enumerate(result.hits, start=1)
            ],
            stats=GenerationStatsOut.from_stats(result.stats),
        )

    @app.post("/search")
    def search(request: SearchRequest, http_request: Request) -> SearchResponse:
        try:
            hits = http_request.app.state.retrieve_fn(request.question, request.top_k)
        except RetrieveError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        return SearchResponse(
            hits=[
                Hit(
                    n=n,
                    citation=hit.citation,
                    source_title=hit.source_title,
                    source_url=hit.source_url,
                    distance=hit.distance,
                    text=hit.text,
                )
                for n, hit in enumerate(hits, start=1)
            ]
        )

    return app


# What `python -m rag.api` / `uvicorn rag.api:app` serve. Injects nothing, so the lifespan
# constructs the real warm embedder and binds the real stages.
app = create_app()
