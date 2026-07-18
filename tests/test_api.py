"""Contract tests for the FastAPI learner app — fakes only, no model, database, or network.

Builds the app via ``create_app`` with a fake embedder plus fake ``retrieve_fn``/``generate_fn``,
so the ``TestClient`` exercises the routes offline. Pins the wire contracts a UI depends on: the
``/health`` readiness shape, the ``/ask`` answer+sources shape and its stage-error → HTTP-status
mapping, and the ``/search`` hits shape. The ``TestClient`` context manager runs the lifespan,
so the warm-model state is populated exactly as in production.
"""

import pytest
from conftest import FakeEmbedder
from fastapi.testclient import TestClient

from rag.api import create_app
from rag.generate import GenerateError, GenerateResult, GenerationStats
from rag.retrieve import RetrievedChunk, RetrieveError

HITS = [
    RetrievedChunk(
        id="arsenal#Stadiums",
        source_title="Arsenal F.C.",
        citation="Arsenal F.C. — Stadiums",
        source_url="https://en.wikipedia.org/wiki/Arsenal_F.C.",
        text="Arsenal play at the Emirates Stadium.",
        distance=0.2,
    ),
]
DELTAS = ["Arsenal play ", "at the Emirates. [1]"]
ANSWER = "".join(DELTAS)
STATS = GenerationStats(
    prompt_tokens=42,
    answer_tokens=7,
    load_seconds=1.0,
    prompt_eval_seconds=0.5,
    eval_seconds=1.5,
    total_seconds=3.0,
    done_reason="stop",
)


def _retrieve_returning(hits):
    """A ``retrieve_fn`` that returns the first ``top_k`` of ``hits``."""

    def retrieve_fn(question: str, top_k: int) -> list[RetrievedChunk]:
        return list(hits[:top_k])

    return retrieve_fn


def _retrieve_raising(error):
    """A ``retrieve_fn`` that raises ``error``."""

    def retrieve_fn(question: str, top_k: int) -> list[RetrievedChunk]:
        raise error

    return retrieve_fn


def _generate_streaming(deltas, stats):
    """A ``generate_fn`` that forwards each delta live, then returns the joined answer."""

    def generate_fn(prompt, on_delta):
        for delta in deltas:
            on_delta(delta)
        return GenerateResult(answer="".join(deltas), stats=stats)

    return generate_fn


def _generate_raising(error):
    """A ``generate_fn`` that raises ``error``."""

    def generate_fn(prompt, on_delta):
        raise error

    return generate_fn


def _client(*, retrieve_fn=None, generate_fn=None) -> TestClient:
    """A ``TestClient`` over an app wired with fakes (default: one hit, a streamed answer)."""
    app = create_app(
        embedder=FakeEmbedder(),
        retrieve_fn=retrieve_fn if retrieve_fn is not None else _retrieve_returning(HITS),
        generate_fn=generate_fn if generate_fn is not None else _generate_streaming(DELTAS, STATS),
    )
    return TestClient(app)


def test_health_ok_when_model_warm_and_both_backends_reachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("rag.api._database_reachable", lambda: True)
    monkeypatch.setattr("rag.api._ollama_reachable", lambda: True)

    with _client() as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "model_loaded": True,
        "database": True,
        "ollama": True,
    }


def test_health_degraded_when_a_backend_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("rag.api._database_reachable", lambda: True)
    monkeypatch.setattr("rag.api._ollama_reachable", lambda: False)

    with _client() as client:
        body = client.get("/health").json()

    assert body == {"status": "degraded", "model_loaded": True, "database": True, "ollama": False}


def test_ask_returns_answer_numbered_sources_and_stats() -> None:
    with _client() as client:
        response = client.post("/ask", json={"question": "Where does Arsenal play?", "top_k": 1})

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == ANSWER
    assert body["sources"] == [
        {
            "n": 1,
            "citation": "Arsenal F.C. — Stadiums",
            "source_title": "Arsenal F.C.",
            "source_url": "https://en.wikipedia.org/wiki/Arsenal_F.C.",
        }
    ]
    assert body["stats"]["prompt_tokens"] == 42
    assert body["stats"]["answer_tokens"] == 7
    assert body["stats"]["done_reason"] == "stop"


def test_ask_top_k_defaults_to_the_shared_constant() -> None:
    seen: dict[str, int] = {}

    def retrieve_fn(question: str, top_k: int) -> list[RetrievedChunk]:
        seen["top_k"] = top_k
        return list(HITS)

    with _client(retrieve_fn=retrieve_fn) as client:
        client.post("/ask", json={"question": "q"})  # no top_k in the body

    from rag.retrieve import TOP_K

    assert seen["top_k"] == TOP_K


def test_ask_rejects_non_positive_top_k_with_422() -> None:
    with _client() as client:
        response = client.post("/ask", json={"question": "q", "top_k": 0})

    assert response.status_code == 422  # Pydantic ge=1, before any stage runs


def test_ask_rejects_top_k_over_the_cap_with_422() -> None:
    from rag.api import MAX_TOP_K

    with _client() as client:
        response = client.post("/ask", json={"question": "q", "top_k": MAX_TOP_K + 1})

    assert response.status_code == 422  # Pydantic le=MAX_TOP_K, before any stage runs


def test_ask_retrieve_error_maps_to_503() -> None:
    error = RetrieveError("the chunks table is empty — run `make load` first")
    with _client(retrieve_fn=_retrieve_raising(error)) as client:
        response = client.post("/ask", json={"question": "q"})

    assert response.status_code == 503
    assert "make load" in response.json()["detail"]


def test_ask_no_chunks_maps_assemble_error_to_422() -> None:
    with _client(retrieve_fn=_retrieve_returning([])) as client:
        response = client.post("/ask", json={"question": "q"})

    assert response.status_code == 422
    assert "at least one retrieved chunk" in response.json()["detail"]


def test_ask_generate_error_maps_to_503() -> None:
    error = GenerateError("Ollama not reachable at http://localhost:11434 — run `make llm` first")
    with _client(generate_fn=_generate_raising(error)) as client:
        response = client.post("/ask", json={"question": "q"})

    assert response.status_code == 503
    assert "make llm" in response.json()["detail"]


def test_search_returns_ranked_hits_with_distance_and_text() -> None:
    with _client() as client:
        response = client.post("/search", json={"question": "q", "top_k": 1})

    assert response.status_code == 200
    assert response.json()["hits"] == [
        {
            "n": 1,
            "citation": "Arsenal F.C. — Stadiums",
            "source_title": "Arsenal F.C.",
            "source_url": "https://en.wikipedia.org/wiki/Arsenal_F.C.",
            "distance": 0.2,
            "text": "Arsenal play at the Emirates Stadium.",
        }
    ]


def test_search_retrieve_error_maps_to_503() -> None:
    error = RetrieveError("database connection failed — run `make db` first")
    with _client(retrieve_fn=_retrieve_raising(error)) as client:
        response = client.post("/search", json={"question": "q"})

    assert response.status_code == 503
    assert "make db" in response.json()["detail"]


def test_index_serves_the_single_page_ui() -> None:
    with _client() as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "RAG Playbook" in response.text


def test_composition_seam_matches_the_cli_for_the_same_fakes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # /ask and the CLI both build on answer_question, so the same fakes yield the same answer
    # and the same numbered sources through either surface.
    from rag.ask import answer_question, main

    result = answer_question(
        "q",
        1,
        retrieve_fn=_retrieve_returning(HITS),
        generate_fn=_generate_streaming(DELTAS, STATS),
    )
    assert result.answer == ANSWER
    assert [hit.citation for hit in result.hits] == ["Arsenal F.C. — Stadiums"]

    main(
        ["q", "--top-k", "1"],
        retrieve_fn=_retrieve_returning(HITS),
        generate_fn=_generate_streaming(DELTAS, STATS),
    )
    out = capsys.readouterr().out
    assert ANSWER in out
    for hit in result.hits:
        assert f"[1] {hit.citation} — {hit.source_url}" in out
